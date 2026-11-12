"""Image and PDF to ZPL, for Browser Print's /convert, convertAndSendFile and sendUrl.

PNG and BMP are decoded with the standard library. JPG, GIF, TIFF and WebP need Pillow, and
PDF needs `pdftoppm` (poppler-utils); both are optional and listed in /config only when present.
Images become a 1-bit ^GF graphic (luminance threshold, transparency over white).
"""

import json
import re
import shutil
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

try:  # optional: more formats and faster
    from PIL import Image as _PIL
except ImportError:  # pragma: no cover - depends on the machine
    _PIL = None

PILLOW_FORMATS = ("jpg", "jpeg", "gif", "tif", "tiff", "webp")
FORMAT_ALIASES = {"jpeg": "jpg", "tiff": "tif", "x-png": "png", "x-bmp": "bmp", "bitmap": "bmp"}
TO_FORMATS = ("zpl", "none")
ACTIONS = ("print", "store", "return")
MAX_PIXELS = 40_000_000
THRESHOLD = 128


class ConversionError(ValueError):
    pass


class Bitmap:
    """Grayscale image: `pixels` is a bytearray of width * height values (0 = black)."""

    def __init__(self, width: int, height: int, pixels: bytearray):
        if width <= 0 or height <= 0:
            raise ConversionError("Empty image")
        if width * height > MAX_PIXELS:
            raise ConversionError(f"Image too large ({width} x {height})")
        self.width, self.height, self.pixels = width, height, pixels


# --- capabilities -------------------------------------------------------------------


def pdf_available() -> bool:
    return shutil.which("pdftoppm") is not None


def supported_conversions() -> dict:
    """For /config: input format -> output formats."""
    formats = ["png", "bmp"] + (list(PILLOW_FORMATS) if _PIL else []) + (["pdf"] if pdf_available() else [])
    return {fmt: ["zpl"] for fmt in formats}


# --- format detection -------------------------------------------------------------


def detect_format(data: bytes, hint: str = "") -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:2] == b"BM":
        return "bmp"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "tif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:5] == b"%PDF-":
        return "pdf"
    hint = (hint or "").lower().strip().lstrip(".")
    hint = hint.split("/")[-1].split(";")[0]
    return FORMAT_ALIASES.get(hint, hint)


def is_convertible(data: bytes, hint: str = "") -> bool:
    return detect_format(data, hint) in supported_conversions()


# --- decoders ---------------------------------------------------------------------


def _luma(r, g, b):
    return (r * 299 + g * 587 + b * 114) // 1000


def _over_white(value, alpha):
    return (value * alpha + 255 * (255 - alpha)) // 255


def decode(data: bytes, fmt: str, dpi: int = 203) -> list:
    """Return a list of Bitmaps (one per PDF page, one otherwise)."""
    if fmt == "pdf":
        return _decode_pdf(data, dpi)
    if _PIL is not None and fmt in ("png", "bmp") + PILLOW_FORMATS:
        return [_decode_pillow(data)]
    if fmt == "png":
        return [decode_png(data)]
    if fmt == "bmp":
        return [decode_bmp(data)]
    if fmt in PILLOW_FORMATS:
        raise ConversionError(f"Converting {fmt} needs Pillow (sudo apt install python3-pil)")
    raise ConversionError(f"Unsupported format: {fmt or 'unknown'}")


def _decode_pillow(data: bytes) -> Bitmap:
    import io

    try:
        image = _PIL.open(io.BytesIO(data))
        image.load()
    except Exception as exc:
        raise ConversionError(f"Cannot read the image: {exc}") from exc
    if image.width * image.height > MAX_PIXELS:
        raise ConversionError(f"Image too large ({image.width} x {image.height})")
    image = image.convert("RGBA")
    background = _PIL.new("RGBA", image.size, (255, 255, 255, 255))
    gray = _PIL.alpha_composite(background, image).convert("L")
    return Bitmap(gray.width, gray.height, bytearray(gray.tobytes()))


def decode_png(data: bytes) -> Bitmap:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ConversionError("Not a PNG file")
    pos, idat, palette, trns, header = 8, bytearray(), None, None, None
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        chunk = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", chunk)
        elif kind == b"PLTE":
            palette = [tuple(chunk[i : i + 3]) for i in range(0, len(chunk), 3)]
        elif kind == b"tRNS":
            trns = chunk
        elif kind == b"IDAT":
            idat += chunk
        elif kind == b"IEND":
            break
    if header is None:
        raise ConversionError("PNG without IHDR")
    width, height, depth, color, _comp, _filter, interlace = header
    if interlace:
        raise ConversionError("Interlaced PNG needs Pillow (sudo apt install python3-pil)")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color)
    if channels is None or depth not in (1, 2, 4, 8, 16):
        raise ConversionError(f"Unsupported PNG (color type {color}, depth {depth})")
    if width * height > MAX_PIXELS:
        raise ConversionError(f"Image too large ({width} x {height})")
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise ConversionError(f"Corrupt PNG: {exc}") from exc
    bits_pp = channels * depth
    stride = (width * bits_pp + 7) // 8
    bpp = max(1, bits_pp // 8)
    if len(raw) < (stride + 1) * height:
        raise ConversionError("Truncated PNG")

    rows, prev = [], bytearray(stride)
    for y in range(height):
        kind = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1 : (y + 1) * (stride + 1)])
        _unfilter(kind, line, prev, bpp)
        rows.append(line)
        prev = line

    max_value = (1 << depth) - 1
    pixels = bytearray(width * height)
    for y, line in enumerate(rows):
        samples = _samples(line, depth, width * channels)
        out = y * width
        for x in range(width):
            s = samples[x * channels : (x + 1) * channels]
            if color == 3:
                index = s[0]
                r, g, b = palette[index] if palette and index < len(palette) else (0, 0, 0)
                alpha = trns[index] if trns and index < len(trns) else 255
                value = _over_white(_luma(r, g, b), alpha)
            else:
                scaled = [v * 255 // max_value for v in s]
                if color == 0:
                    value = scaled[0]
                    if trns and len(trns) >= 2 and s[0] == struct.unpack(">H", trns[:2])[0]:
                        value = 255
                elif color == 2:
                    value = _luma(*scaled)
                    if trns and len(trns) >= 6 and tuple(s) == struct.unpack(">HHH", trns[:6]):
                        value = 255
                elif color == 4:
                    value = _over_white(scaled[0], scaled[1])
                else:
                    value = _over_white(_luma(*scaled[:3]), scaled[3])
            pixels[out + x] = value
    return Bitmap(width, height, pixels)


def _unfilter(kind, line, prev, bpp):
    n = len(line)
    if kind == 0:
        return
    if kind == 1:
        for i in range(bpp, n):
            line[i] = (line[i] + line[i - bpp]) & 0xFF
    elif kind == 2:
        for i in range(n):
            line[i] = (line[i] + prev[i]) & 0xFF
    elif kind == 3:
        for i in range(n):
            left = line[i - bpp] if i >= bpp else 0
            line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
    elif kind == 4:
        for i in range(n):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            pred = a if pa <= pb and pa <= pc else b if pb <= pc else c
            line[i] = (line[i] + pred) & 0xFF
    else:
        raise ConversionError(f"Corrupt PNG (filter {kind})")


def _samples(line, depth, count):
    if depth == 8:
        return line[:count]
    if depth == 16:
        return [line[i] << 8 | line[i + 1] for i in range(0, count * 2, 2)]
    per_byte, mask = 8 // depth, (1 << depth) - 1
    out = []
    for byte in line:
        for k in range(per_byte):
            out.append((byte >> (8 - depth * (k + 1))) & mask)
    return out[:count]


def decode_bmp(data: bytes) -> Bitmap:
    if data[:2] != b"BM" or len(data) < 54:
        raise ConversionError("Not a BMP file")
    offset = struct.unpack("<I", data[10:14])[0]
    header_size = struct.unpack("<I", data[14:18])[0]
    if header_size < 40:
        raise ConversionError("Unsupported BMP header (OS/2)")
    width, height, _planes, bits, compression = struct.unpack("<iiHHI", data[18:34])
    colors_used = struct.unpack("<I", data[46:50])[0]
    if compression not in (0, 3) or bits not in (1, 4, 8, 24, 32):
        raise ConversionError(f"Unsupported BMP ({bits} bits, compression {compression})")
    top_down = height < 0
    height = abs(height)
    if width <= 0 or width * height > MAX_PIXELS:
        raise ConversionError(f"Invalid BMP size ({width} x {height})")
    palette = []
    if bits <= 8:
        count = colors_used or (1 << bits)
        start = 14 + header_size
        palette = [
            (data[start + i * 4 + 2], data[start + i * 4 + 1], data[start + i * 4])
            for i in range(count)
            if start + i * 4 + 3 <= len(data)
        ]
    stride = ((width * bits + 31) // 32) * 4
    if offset + stride * height > len(data):
        raise ConversionError("Truncated BMP")
    pixels = bytearray(width * height)
    for row in range(height):
        y = row if top_down else height - 1 - row
        line = data[offset + row * stride : offset + (row + 1) * stride]
        out = y * width
        if bits >= 24:
            step = bits // 8
            for x in range(width):
                b, g, r = line[x * step : x * step + 3]
                value = _luma(r, g, b)
                if bits == 32 and compression == 0 and header_size >= 56:
                    value = _over_white(value, line[x * step + 3])
                pixels[out + x] = value
        else:
            for x, index in enumerate(_samples(line, bits, width)):
                r, g, b = palette[index] if index < len(palette) else (0, 0, 0)
                pixels[out + x] = _luma(r, g, b)
    return Bitmap(width, height, pixels)


def _decode_pdf(data: bytes, dpi: int) -> list:
    if not pdf_available():
        raise ConversionError("Converting PDF needs pdftoppm (sudo apt install poppler-utils)")
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "in.pdf"
        source.write_bytes(data)
        try:
            subprocess.run(
                ["pdftoppm", "-r", str(dpi), "-gray", "-png", str(source), str(Path(tmp) / "page")],
                check=True,
                capture_output=True,
                timeout=60,
            )
        except subprocess.CalledProcessError as exc:
            raise ConversionError(f"Cannot read the PDF: {exc.stderr.decode(errors='replace').strip()}") from exc
        except subprocess.TimeoutExpired:
            raise ConversionError("The PDF took too long to render") from None
        pages = sorted(Path(tmp).glob("page*.png"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
        if not pages:
            raise ConversionError("The PDF has no pages")
        return [decode(page.read_bytes(), "png")[0] for page in pages]


# --- sizing -------------------------------------------------------------------------


def _int_option(value, name):
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        raise ConversionError(f"Invalid {name}: {value!r}") from None
    if number <= 0:
        raise ConversionError(f"Invalid {name}: {value!r}")
    return number


def target_size(bitmap: Bitmap, options: dict) -> tuple:
    """resize (exact), fitTo (keep the aspect ratio) or scale (factor), in dots."""
    w, h = bitmap.width, bitmap.height
    resize, fit, scale = options.get("resize"), options.get("fitTo"), options.get("scale")
    if isinstance(resize, dict) and (resize.get("width") or resize.get("height")):
        nw = _int_option(resize["width"], "resize.width") if resize.get("width") else None
        nh = _int_option(resize["height"], "resize.height") if resize.get("height") else None
        return nw or max(1, round(w * nh / h)), nh or max(1, round(h * nw / w))
    if isinstance(fit, dict) and (fit.get("width") or fit.get("height")):
        fw = _int_option(fit["width"], "fitTo.width") if fit.get("width") else None
        fh = _int_option(fit["height"], "fitTo.height") if fit.get("height") else None
        factor = min(f for f in (fw and fw / w, fh and fh / h) if f)
        # round, but never past the box (float error: 812 / 400 * 400 = 811.99...)
        nw, nh = round(w * factor), round(h * factor)
        return max(1, min(nw, fw or nw)), max(1, min(nh, fh or nh))
    if scale not in (None, ""):
        try:
            factor = float(scale)
        except (TypeError, ValueError):
            raise ConversionError(f"Invalid scale: {scale!r}") from None
        if not 0 < factor <= 20:
            raise ConversionError(f"Invalid scale: {scale!r}")
        return max(1, round(w * factor)), max(1, round(h * factor))
    return w, h


def resample(bitmap: Bitmap, width: int, height: int) -> Bitmap:
    """Area average when shrinking (keeps thin lines visible), nearest pixel when enlarging."""
    if (width, height) == (bitmap.width, bitmap.height):
        return bitmap
    if width * height > MAX_PIXELS:
        raise ConversionError(f"Result too large ({width} x {height})")
    sw, sh, src = bitmap.width, bitmap.height, bitmap.pixels
    xs = [(x * sw // width, max(x * sw // width + 1, (x + 1) * sw // width)) for x in range(width)]
    out = bytearray(width * height)
    for y in range(height):
        y0 = y * sh // height
        y1 = max(y0 + 1, (y + 1) * sh // height)
        rows = [src[r * sw : (r + 1) * sw] for r in range(y0, y1)]
        base = y * width
        for x, (x0, x1) in enumerate(xs):
            total = 0
            for row in rows:
                total += sum(row[x0:x1])
            out[base + x] = total // ((x1 - x0) * len(rows))
    return Bitmap(width, height, out)


# --- ZPL --------------------------------------------------------------------------


def monochrome(bitmap: Bitmap, threshold: int = THRESHOLD) -> tuple:
    """Return (bytes per row, packed rows) with 1 = black, as ^GF and ~DG expect."""
    row_bytes = (bitmap.width + 7) // 8
    packed = bytearray(row_bytes * bitmap.height)
    w = bitmap.width
    for y in range(bitmap.height):
        line = bitmap.pixels[y * w : (y + 1) * w]
        base = y * row_bytes
        for x, value in enumerate(line):
            if value < threshold:
                packed[base + (x >> 3)] |= 0x80 >> (x & 7)
    return row_bytes, bytes(packed)


def graphic_field(bitmap: Bitmap) -> str:
    row_bytes, packed = monochrome(bitmap)
    return f"^GFA,{len(packed)},{len(packed)},{row_bytes},{packed.hex().upper()}"


def label(bitmap: Bitmap) -> str:
    return f"^XA^FO0,0{graphic_field(bitmap)}^FS^XZ\n"


def storage_name(name: str) -> str:
    """Printer path for ~DG: drive letter (E: flash by default), name of up to 8 characters, .GRF."""
    name = (name or "IMAGE").strip().upper()
    drive = "E:"
    if re.match(r"^[A-Z]:", name):
        drive, name = name[:2], name[2:]
    stem = re.sub(r"\.[A-Z0-9]*$", "", name)
    stem = re.sub(r"[^A-Z0-9_]", "", stem)[:8] or "IMAGE"
    return f"{drive}{stem}.GRF"


def store_command(bitmap: Bitmap, path: str) -> str:
    row_bytes, packed = monochrome(bitmap)
    return f"~DG{path},{len(packed)},{row_bytes},{packed.hex().upper()}\n"


# --- request handling ---------------------------------------------------------------


def normalize_options(options) -> dict:
    options = dict(options or {})
    action = str(options.get("action") or "print").lower()
    if action not in ACTIONS:
        raise ConversionError(f"Unknown action: {action}")
    to_format = str(options.get("toFormat") or "zpl").lower()
    if to_format not in TO_FORMATS:
        raise ConversionError(f"Converting to {to_format} is not supported (only zpl)")
    options.update(action=action, toFormat=to_format)
    return options


def convert(data: bytes, options: dict, dpi: int = 203) -> dict:
    """Return {"action", "zpl" (text to send or return), "path" (store), "pages", "size"}."""
    options = normalize_options(options)
    if options["toFormat"] == "none":
        if options["action"] != "print":
            raise ConversionError("toFormat 'none' only works with action 'print'")
        return {"action": "print", "raw": data, "pages": 0}
    fmt = detect_format(data, str(options.get("fromFormat") or ""))
    pages = [resample(b, *target_size(b, options)) for b in decode(data, fmt, dpi)]
    result = {
        "action": options["action"],
        "format": fmt,
        "pages": len(pages),
        "size": [pages[0].width, pages[0].height],
    }
    if options["action"] == "store":
        if len(pages) > 1:
            raise ConversionError("Only single-page resources can be stored")
        path = storage_name(options.get("storageName") or options.get("storeName") or "")
        result.update(path=path, zpl=store_command(pages[0], path))
    else:
        result["zpl"] = "".join(label(page) for page in pages)
    return result


def parse_multipart(content_type: str, body: bytes) -> dict:
    """{field name: bytes} from a multipart/form-data body (FormData: "json" and "blob")."""
    from email import policy
    from email.parser import BytesParser

    message = BytesParser(policy=policy.HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    )
    if not message.is_multipart():
        raise ConversionError("Invalid multipart body")
    fields = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name:
            payload = part.get_payload(decode=True)
            fields[name] = payload if payload is not None else b""
            fields[f"{name}.type"] = part.get_content_type()
    return fields


def parse_form_json(fields: dict) -> dict:
    try:
        return json.loads(fields.get("json", b"{}") or b"{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ConversionError("Invalid json field") from None
