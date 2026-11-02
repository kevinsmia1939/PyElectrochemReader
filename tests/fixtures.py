"""Small wire-format examples with independently specified expected values."""

from io import BytesIO
import struct
from zipfile import ZipFile


def mpr(version=2, new_header=False, column_id=8):
    records = b"".join(struct.pack("<Bdf", flag, time, current) for flag, time, current in [(4, 0., 100.), (0, 1., -200.)])
    ids = [2, 4, column_id]
    if version == 0 and new_header:
        header = struct.pack("<IB", 2, 3) + b"".join(bytes([0, i]) for i in ids)
        payload = header.ljust(1007, b"\0") + records
    elif version == 0:
        payload = (struct.pack("<IB", 2, 3) + bytes(ids)).ljust(100, b"\0") + records
    else:
        header = struct.pack("<IB3H", 2, 3, *ids)
        payload = header.ljust(406 if version == 3 else 405, b"\0") + records
    if new_header:
        module = struct.pack("<10s25sIIII8s", b"VMP data  ", b"measurement", 0xffffffff, len(payload), version, 11, b"11/02/26")
    else:
        module = struct.pack("<10s25sII8s", b"VMP data  ", b"measurement", len(payload), version, b"11/02/26")
    return b"BIO-LOGIC MODULAR FILE\x1a".ljust(48) + b"\0" * 4 + b"MODULE" + module + payload


def nda29():
    header = bytearray(1024)
    header[:6] = b"NEWARE"
    header[14] = 29
    rows = []
    for index, voltage, current in [(2, 32000, -10000), (1, 31000, 10000)]:
        row = bytearray(86)
        row[:2] = b"\x55\0"
        struct.pack_into("<IIHBBQii", row, 2, index, 2, 7, 1 if current > 0 else 2, 0, index * 1000, voltage, current)
        struct.pack_into("<4q", row, 38, 360000, 720000, 1080000, 1440000)
        struct.pack_into("<H5B", row, 70, 2025, 6, 7, 8, 9, 10)
        struct.pack_into("<i", row, 78, 100)
        rows.append(bytes(row))
    return bytes(header) + b"".join(rows)


def nda130(bts91=True):
    header = bytearray(1024)
    header[:6] = b"NEWARE"
    header[14] = 130
    rows = []
    for index in (1, 2):
        if bts91:
            row = bytearray(56)
            row[:4] = b"\x55\x06\x07\x01"
            struct.pack_into("<III4f", row, 8, index, index, 500000000, 100., 3., -3600., -7200.)
            struct.pack_into("<II", row, 44, 1700000000, 100)
            struct.pack_into("<f", row, 52, 25.)
        else:
            row = bytearray(88)
            row[:6] = b"\x12\x50\0\x07\x55\x81"
            struct.pack_into("<BB", row, 9, 7, 1)
            struct.pack_into("<I", row, 16, index)
            struct.pack_into("<Qff", row, 28, index * 1000000, 3., 100.)
            struct.pack_into("<4fQ", row, 52, 3600., 7200., 10800., 14400., 1700000000000000)
        rows.append(bytes(row))
    return bytes(header) + b"".join(rows)


def _page(kind, version, records, *, begin=132, size=None):
    raw = bytearray(8192)
    raw[0], raw[2] = kind, version
    for i, record in enumerate(records):
        offset = 4096 + begin + i * (size or len(record))
        raw[offset:offset + len(record)] = record
    return bytes(raw)


def ndax(version=14):
    factor = 1 if version == 14 else 10000
    current_factor = 1 if version == 14 else 1000
    main = _page(1, version, [struct.pack("<ff", v * factor, i * current_factor) for v, i in [(3., .1), (0., .2), (3.2, -.3)]])
    run_records = []
    for index, ms, step in [(1, 0, 7), (1, 100, 7), (3, 2000, 8)]:
        run_records.append(struct.pack("<is4f12siiih" + ("8s" if version == 14 else ""),
            ms, b"\0", 1., 2., 3., 4., b"", 1700000000, step, index, 100, *([b""] if version == 14 else [])))
    run = _page(18, version, run_records)
    steps = _page(7, version, [struct.pack("<ii16sb12s", 1, step, b"", status, b"") for step, status in [(7, 1), (8, 2)]])
    result = BytesIO()
    with ZipFile(result, "w") as archive:
        archive.writestr("data.ndc", main)
        archive.writestr("data_runInfo.ndc", run)
        archive.writestr("data_step.ndc", steps)
    return result.getvalue()


def ndax_legacy(version=2):
    size = 94 if version == 2 else 87
    records = []
    for index in (1, 2):
        row = bytearray(size)
        row[:8] = b"\x55\x02\x03\x04\x05\x06\x07\x55"
        struct.pack_into("<IIBB", row, 8, index, 2, 7, 1)
        struct.pack_into("<Qii", row, 23, index * 1000, 31000, 10000)
        struct.pack_into("<4q", row, 43, 360000, 720000, 1080000, 1440000)
        struct.pack_into("<H5B", row, 75, 2025, 6, 7, 8, 9, 10)
        struct.pack_into("<i", row, 82, 100)
        records.append(bytes(row))
    if version == 2:
        raw = bytearray(517)
        raw[0], raw[2] = 1, 2
        raw.extend(b"".join(records))
    else:
        raw = _page(1, 5, records, begin=125)
    result = BytesIO()
    with ZipFile(result, "w") as archive:
        archive.writestr("data.ndc", raw)
    return result.getvalue()


def _rc4(raw, key):
    # Reference implementation used only to encode a zero-key Jet header.
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) % 256
        s[i], s[j] = s[j], s[i]
    out = bytearray()
    i = j = 0
    for byte in raw:
        i = (i + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        out.append(byte ^ s[(s[i] + s[j]) % 256])
    return out


def res(jet3=False, *, null_current=False):
    size = 2048 if jet3 else 4096
    raw = bytearray(size * 6)
    raw[:4] = b"\0\x01\0\0"
    raw[4:20] = b"Standard Jet DB\0"
    raw[20] = 0 if jet3 else 1
    raw[24:24 + (126 if jet3 else 128)] = _rc4(bytes(126 if jet3 else 128), b"\xc7\xda\x39\x6b")
    width = 1 if jet3 else 2

    def definition(page, columns, variable=0):
        start = page * size
        raw[start] = 2
        col_count, real_indexes, col_start, entry = (25, 31, 43, 18) if jet3 else (45, 51, 63, 25)
        struct.pack_into("<HH", raw, start + col_count - 2, variable, len(columns))
        struct.pack_into("<I", raw, start + real_indexes, 0)
        names = []
        for i, (name, kind, fixed, offset, length) in enumerate(columns):
            pos = start + col_start + i * entry
            raw[pos] = kind
            raw[pos + (1 if jet3 else 5)] = i
            struct.pack_into("<H", raw, pos + (3 if jet3 else 7), 0)
            raw[pos + (13 if jet3 else 15)] = int(fixed)
            struct.pack_into("<H", raw, pos + (14 if jet3 else 21), offset)
            struct.pack_into("<H", raw, pos + (16 if jet3 else 23), length)
            text = name.encode("cp1252" if jet3 else "utf-16-le")
            names.append(struct.pack("<B" if jet3 else "<H", len(text)) + text)
        pos = start + col_start + len(columns) * entry
        blob = b"".join(names)
        raw[pos:pos + len(blob)] = blob

    def page_rows(page, owner, rows):
        start = page * size
        raw[start] = 1
        struct.pack_into("<I", raw, start + 4, owner)
        rco = 8 if jet3 else 12
        struct.pack_into("<H", raw, start + rco, len(rows))
        end = size
        for i, row in enumerate(rows):
            end -= len(row)
            struct.pack_into("<H", raw, start + rco + 2 + 2 * i, end)
            raw[start + end:start + end + len(row)] = row

    definition(2, [("Id", 4, True, 0, 4), ("Type", 3, True, 4, 2), ("Name", 10, False, 0, 255)], 1)
    text = b"Channel_Normal_Table" if jet3 else b"\xff\xfeChannel_Normal_Table"
    base = bytes([3]) if jet3 else struct.pack("<H", 3)
    row = base + struct.pack("<ih", 4, 1) + text
    tail = struct.pack("<3B" if jet3 else "<3H", len(row), width + 6, 1)
    page_rows(3, 2, [row + tail + b"\x07"])
    definition(4, [("Data_Point", 4, True, 0, 4), ("Test_Time", 7, True, 4, 8),
                   ("Voltage", 7, True, 12, 8), ("Current", 7, True, 20, 8)])
    rows = []
    for index, voltage, current in [(2, 3.2, -.2), (1, 3.1, .1)]:
        base = bytes([4]) if jet3 else struct.pack("<H", 4)
        rows.append(base + struct.pack("<iddd", index, float(index), voltage, current) + bytes([7 if null_current else 15]))
    page_rows(5, 4, rows)
    return bytes(raw)
