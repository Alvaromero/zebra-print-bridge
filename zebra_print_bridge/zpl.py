"""Minimal ZPL analysis for the viewer: classify jobs, split labels, graphics and encoding lint."""

import re

# ~HQES flags (group 1 = last block of 8 hex digits), same as zplHQES in the web app:
# key -> (description, digit position counted from the end, value).
ERROR_FLAGS = {
    "media_out": ("Media Out", 1, 1),
    "ribbon_out": ("Ribbon Out", 1, 2),
    "head_open": ("Head Open", 1, 4),
    "cutter_fault": ("Cutter Fault", 1, 8),
    "printhead_over_temp": ("Printhead Over Temperature", 2, 1),
    "motor_over_temp": ("Motor Over Temperature", 2, 2),
    "bad_printhead_element": ("Bad Printhead Element", 2, 4),
    "printhead_detection": ("Printhead Detection Error", 2, 8),
    "invalid_firmware_config": ("Invalid Firmware Config", 3, 1),
    "printhead_thermistor_open": ("Printhead Thermistor Open", 3, 2),
    "paper_jam_retract": ("Paper Jam during Retract", 4, 1),
    "presenter_not_running": ("Presenter Not Running", 4, 2),
    "paper_feed_error": ("Paper Feed Error", 4, 4),
    "clear_paper_path_failed": ("Clear Paper Path Failed", 4, 8),
    "paused": ("Paused", 5, 1),
    "retract_timeout": ("Retract Function timed out", 5, 2),
    "black_mark_calibrate": ("Black Mark Calibrate Error", 5, 4),
    "black_mark_not_found": ("Black Mark not Found", 5, 8),
}
WARNING_FLAGS = {
    "need_calibrate": ("Need to Calibrate Media", 1, 1),
    "clean_printhead": ("Clean Printhead", 1, 2),
    "printhead_under_voltage": ("Printhead Under Voltage", 1, 4),
    "paper_near_end": ("Paper-near-end Sensor", 1, 8),
    "paper_before_head": ("Paper before head", 2, 1),
    "black_mark": ("Black mark", 2, 2),
    "paper_after_head": ("Paper after head", 2, 4),
    "loop_ready": ("Loop ready", 2, 8),
}


def flags_hex(selected, table) -> str:
    """Build "00000000 0000000X" from the active flag keys."""
    digits = [0] * 8
    for name in selected:
        if name in table:
            _, position, value = table[name]
            digits[8 - position] |= value
    return "00000000 " + "".join(f"{d:X}" for d in digits)


def hqes_response(errors=(), warnings=()) -> bytes:
    error_hex = flags_hex(errors, ERROR_FLAGS)
    warning_hex = flags_hex(warnings, WARNING_FLAGS)
    return (
        "\x02\r\n\r\n"
        "  PRINTER STATUS                            \r\n"
        f"   ERRORS:         {1 if errors else 0} {error_hex}     \r\n"
        f"   WARNINGS:       {1 if warnings else 0} {warning_hex}     \r\n"
        "\x03\r\n"
    ).encode("ascii")


LABEL_RE = re.compile(r"\^XA.*?\^XZ", re.S | re.I)
# ~DG<name>,<total bytes>,<bytes per row>,<hex data> up to the next command.
DG_RE = re.compile(r"~DG([^,]+),\s*(\d+)\s*,\s*(\d+)\s*,([^~^]*)", re.I)
XG_RE = re.compile(r"\^XG([^,^~]+)", re.I)
PW_RE = re.compile(r"\^PW(\d+)", re.I)
LL_RE = re.compile(r"\^LL(\d+)", re.I)
CI_RE = re.compile(r"\^CI(\d+)", re.I)
FIELD_RE = re.compile(r"\^F[DV](.*?)(?=\^FS|\^XZ|$)", re.S | re.I)
DOWNLOADED_FONT_RE = re.compile(r"\^A@|\^CW", re.I)

UTF8_CHARSET = 28


def graphic_key(name: str) -> str:
    """R:ICON.GRF, ICON.GRF and ICON refer to the same graphic."""
    name = name.strip().upper()
    if ":" in name:
        name = name.split(":", 1)[1]
    return name.rsplit(".", 1)[0]


def extract_graphics(text: str) -> dict:
    return {graphic_key(m.group(1)): m.group(0) for m in DG_RE.finditer(text)}


def labels(text: str) -> list:
    """^XA...^XZ blocks that draw something (setup-only blocks are skipped)."""
    return [block for block in LABEL_RE.findall(text)
            if re.search(r"\^(FD|GF|XG|GB|BC|BQ|B3|BX|FO)", block, re.I)]


def referenced_graphics(label: str) -> set:
    return {graphic_key(name) for name in XG_RE.findall(label)}


def label_size_inches(label: str, dpi: int):
    """Size from ^PW/^LL, or None when the label does not declare it."""
    width = PW_RE.search(label)
    length = LL_RE.search(label)
    if not width or not length:
        return None
    return round(int(width.group(1)) / dpi, 2), round(int(length.group(1)) / dpi, 2)


def last_charset(text: str):
    """Last ^CI in the text. ^CI persists on the printer until changed or power-cycled."""
    found = CI_RE.findall(text)
    return int(found[-1]) if found else None


def _is_latin(char: str) -> bool:
    code = ord(char)
    # Basic Latin, Latin-1, Latin Extended-A/B, general punctuation and the euro sign:
    # what the printer's built-in fonts usually cover.
    return code <= 0x24F or 0x2000 <= code <= 0x206F or code == 0x20AC


def lint(text: str, charset=None, encoding="utf-8") -> list:
    """Encoding problems that make a Zebra print wrong characters or blanks.

    `charset` is the ^CI active on the printer before this job. Returns
    [{"code": ..., "chars": [...], ...}] with one entry per problem type.
    """
    found = {}
    events = sorted(
        [(m.start(), "ci", int(m.group(1))) for m in CI_RE.finditer(text)]
        + [(m.start(), "field", m.group(1)) for m in FIELD_RE.finditer(text)]
    )
    label_spans = [(m.start(), m.end(), m.group(0)) for m in LABEL_RE.finditer(text)]
    sends_utf8 = encoding.lower().replace("_", "-") in ("utf-8", "utf8")

    for position, kind, value in events:
        if kind == "ci":
            charset = value
            continue
        non_ascii = [c for c in value if ord(c) > 127]
        if not non_ascii:
            continue
        if sends_utf8 and charset != UTF8_CHARSET:
            entry = found.setdefault("not_utf8", {"code": "not_utf8", "charset": charset, "chars": []})
            entry["chars"] += non_ascii
        non_latin = [c for c in non_ascii if not _is_latin(c)]
        if non_latin:
            label = next((body for start, end, body in label_spans if start <= position < end), text)
            if not DOWNLOADED_FONT_RE.search(label):
                entry = found.setdefault("font_coverage", {"code": "font_coverage", "chars": []})
                entry["chars"] += non_latin

    for entry in found.values():
        entry["chars"] = list(dict.fromkeys(entry["chars"]))[:12]
    return list(found.values())


def classify(text: str) -> str:
    upper = text.upper()
    if labels(text):
        return "label"
    if "~HQES" in upper or "~HS" in upper:
        return "status"
    if "~DG" in upper:
        return "graphics"
    if "~JC" in upper:
        return "calibrate"
    if "~WC" in upper:
        return "config"
    return "command"


# --- printer responses ------------------------------------------------------------

HQES_LINE_RE = re.compile(r"(ERRORS|WARNINGS):\s*(\d)\s+([0-9A-Fa-f]{8})\s+([0-9A-Fa-f]{8})")
DPMM_TO_DPI = {6: 152, 8: 203, 12: 300, 24: 600}


def _decode_flags(hex_group: str, table: dict) -> list:
    """Bitwise decode of the last 8-hex-digit group (group 1) of a ~HQES line."""
    digits = [int(c, 16) for c in hex_group]
    return [key for key, (_, position, value) in table.items() if digits[8 - position] & value]


def parse_hqes(text: str):
    """{"errors": [keys], "warnings": [keys]} from a ~HQES response, or None if there is none."""
    found = {kind: (flag, group1) for kind, flag, _, group1 in HQES_LINE_RE.findall(text or "")}
    if not found:
        return None
    result = {}
    for kind, key, table in (("ERRORS", "errors", ERROR_FLAGS), ("WARNINGS", "warnings", WARNING_FLAGS)):
        flag, group1 = found.get(kind, ("0", "0" * 8))
        result[key] = _decode_flags(group1, table) if flag == "1" else []
    return result


def parse_hi(text: str):
    """~HI answers "<model>,<firmware>,<dots/mm>,<memory>" between STX and ETX."""
    body = (text or "").replace("\x02", "").split("\x03")[0].strip()
    parts = [p.strip() for p in body.split(",")]
    if len(parts) < 3 or not parts[0]:
        return None
    info = {"model": parts[0], "firmware": parts[1], "memory": parts[3] if len(parts) > 3 else ""}
    try:
        info["dpi"] = DPMM_TO_DPI.get(int(parts[2]))
    except ValueError:
        info["dpi"] = None
    return info


def _field_text(text: str) -> str:
    """Make arbitrary text safe inside ^FD (^ and ~ would start a command)."""
    return re.sub(r"[\^~\r\n]", " ", text)


def test_label(name: str, uid: str, dpi: int, when: str) -> str:
    """Small test label (fits 2.25 x 1.25 in), scaled to the printer density.

    It includes accented characters so the operator can check UTF-8 (^CI28) on paper.
    """
    s = dpi / 203

    def d(value):
        return max(1, round(value * s))

    def text(y, size, value):
        return f"^FO{d(20)},{d(y)}^A0N,{d(size)},{d(size)}^FD{_field_text(value)}^FS"

    return "".join([
        "^XA^CI28",
        text(14, 30, "Zebra Print Bridge"),
        text(50, 22, name),
        text(76, 18, f"{uid} · {dpi} dpi"),
        text(100, 18, when),
        text(124, 22, "ÁÉÍÓÚ ñ ü ç €"),
        f"^FO{d(20)},{d(154)}^BY{d(2)}^BCN,{d(46)},N,N,N^FDZPB-TEST^FS",
        "^XZ",
    ])
