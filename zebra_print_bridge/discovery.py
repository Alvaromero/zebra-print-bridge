"""Find network printers with mDNS / DNS-SD (standard library only).

Zebra Link-OS printers advertise their raw port as `_pdl-datastream._tcp.local`. The query goes
to the mDNS group from an ephemeral port, so responders answer us by unicast ("legacy unicast",
RFC 6762 section 6.7) and port 5353 (owned by avahi-daemon) is never bound.

Zebra's own UDP discovery (port 4201) is not implemented: its packet layout is not public.
"""

import logging
import socket
import struct
import time

log = logging.getLogger(__name__)

MDNS_ADDR = ("224.0.0.251", 5353)
SERVICE = "_pdl-datastream._tcp.local"
TYPE_A, TYPE_PTR, TYPE_TXT, TYPE_SRV = 1, 12, 16, 33
ZEBRA_HINTS = ("zebra", "ztc")


class DnsError(ValueError):
    pass


def build_query(name: str, qtype: int = TYPE_PTR) -> bytes:
    header = struct.pack("!6H", 0, 0, 1, 0, 0, 0)
    return header + encode_name(name) + struct.pack("!2H", qtype, 1)


def encode_name(name: str) -> bytes:
    out = b""
    for label in name.rstrip(".").split("."):
        raw = label.encode()
        if not 0 < len(raw) < 64:
            raise DnsError(f"invalid label in {name!r}")
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def _read_name(packet: bytes, offset: int) -> tuple:
    """Return (name, offset after the name), following compression pointers."""
    labels, end, jumps = [], None, 0
    while True:
        if offset >= len(packet):
            raise DnsError("name past the end of the packet")
        length = packet[offset]
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(packet):
                raise DnsError("truncated pointer")
            if end is None:
                end = offset + 2
            offset = ((length & 0x3F) << 8) | packet[offset + 1]
            jumps += 1
            if jumps > 32:
                raise DnsError("pointer loop")
            continue
        if length == 0:
            return ".".join(labels), (end if end is not None else offset + 1)
        offset += 1
        labels.append(packet[offset : offset + length].decode("utf-8", errors="replace"))
        offset += length


def parse_packet(packet: bytes) -> list:
    """Return the resource records (answers + authority + additional) as dicts."""
    if len(packet) < 12:
        raise DnsError("packet too short")
    _id, _flags, qd, an, ns, ar = struct.unpack("!6H", packet[:12])
    offset = 12
    for _ in range(qd):
        _name, offset = _read_name(packet, offset)
        offset += 4
    records = []
    for _ in range(an + ns + ar):
        name, offset = _read_name(packet, offset)
        if offset + 10 > len(packet):
            raise DnsError("truncated record")
        rtype, _rclass, _ttl, length = struct.unpack("!HHIH", packet[offset : offset + 10])
        offset += 10
        rdata_at, offset = offset, offset + length
        if offset > len(packet):
            raise DnsError("truncated rdata")
        record = {"name": name.lower(), "type": rtype}
        if rtype == TYPE_PTR:
            record["target"] = _read_name(packet, rdata_at)[0]
        elif rtype == TYPE_SRV:
            _prio, _weight, record["port"] = struct.unpack("!3H", packet[rdata_at : rdata_at + 6])
            record["target"] = _read_name(packet, rdata_at + 6)[0]
        elif rtype == TYPE_TXT:
            record["txt"] = _parse_txt(packet[rdata_at:offset])
        elif rtype == TYPE_A and length == 4:
            record["address"] = socket.inet_ntoa(packet[rdata_at:offset])
        else:
            continue
        records.append(record)
    return records


def _parse_txt(data: bytes) -> dict:
    txt, i = {}, 0
    while i < len(data):
        length = data[i]
        entry = data[i + 1 : i + 1 + length].decode("utf-8", errors="replace")
        i += 1 + length
        key, _, value = entry.partition("=")
        if key:
            txt.setdefault(key.lower(), value)
    return txt


def collect(responses: list, service: str = SERVICE) -> list:
    """Join PTR/SRV/TXT/A records from every (packet, sender_ip) into printers."""
    ptr, srv, txt, addresses, sender = set(), {}, {}, {}, {}
    for packet, sender_ip in responses:
        try:
            records = parse_packet(packet)
        except DnsError as exc:
            log.debug("ignoring malformed mDNS packet from %s: %s", sender_ip, exc)
            continue
        for r in records:
            if r["type"] == TYPE_PTR and r["name"] == service.lower():
                ptr.add(r["target"])
                sender.setdefault(r["target"].lower(), sender_ip)
            elif r["type"] == TYPE_SRV:
                srv[r["name"]] = (r["target"].lower(), r["port"])
                sender.setdefault(r["name"], sender_ip)
            elif r["type"] == TYPE_TXT:
                txt[r["name"]] = r["txt"]
            elif r["type"] == TYPE_A:
                addresses.setdefault(r["name"], r["address"])

    printers = {}
    for instance in ptr:
        key = instance.lower()
        hostname, port = srv.get(key, ("", 9100))
        host = addresses.get(hostname) or sender.get(key)
        if not host:
            continue
        info = txt.get(key, {})
        name = instance[: -len(service) - 1] if instance.lower().endswith("." + service.lower()) else instance
        model = info.get("ty") or info.get("product", "").strip("()")
        hints = " ".join([name, model, info.get("usb_mfg", ""), info.get("note", "")]).lower()
        printers[(host, port)] = {
            "name": name,
            "host": host,
            "port": port,
            "hostname": hostname.rstrip("."),
            "model": model,
            "zebra": any(h in hints for h in ZEBRA_HINTS),
        }
    return sorted(printers.values(), key=lambda p: (not p["zebra"], p["name"].lower()))


def discover(timeout: float = 2.0, address: tuple = None, service: str = SERVICE) -> list:
    """Ask the network for `service` and return the printers that answered within `timeout`."""
    address = address or MDNS_ADDR
    query = build_query(service)
    responses = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        sock.bind(("", 0))
        deadline = time.monotonic() + timeout
        resend_at = time.monotonic() + timeout / 2  # one retry: mDNS over Wi-Fi drops packets
        sock.sendto(query, address)
        while True:
            now = time.monotonic()
            if now >= deadline:
                break
            if resend_at and now >= resend_at:
                sock.sendto(query, address)
                resend_at = None
            sock.settimeout(min(deadline, resend_at or deadline) - now)
            try:
                packet, sender = sock.recvfrom(9000)
            except socket.timeout:
                continue
            except OSError as exc:
                log.debug("mDNS receive failed: %s", exc)
                break
            responses.append((packet, sender[0]))
    return collect(responses, service)
