"""Read Arbin's Jet 3/4 database tables without Access or MDBTools.

Page and row layouts follow MDBTools, copyright (C) 2000 Brian Bruns,
LGPL-2.0-or-later. Rewritten in Python 2026-11-02 under GPL-3.0-or-later.
This is a measurement-table reader, not a general Access database engine.
"""

from dataclasses import dataclass
import struct

import numpy as np

from .core import Dataset, Experiment, ReadError, UnsupportedFormatError


def _u16(raw, offset=0):
    return struct.unpack_from("<H", raw, offset)[0]


def _u32(raw, offset=0):
    return struct.unpack_from("<I", raw, offset)[0]


def _rc4(raw, key):
    state = list(range(256))
    j = 0
    for i in range(256):
        j = (j + state[i] + key[i % len(key)]) & 255
        state[i], state[j] = state[j], state[i]
    i = j = 0
    result = bytearray(raw)
    for k in range(len(result)):
        i = (i + 1) & 255
        j = (j + state[i]) & 255
        state[i], state[j] = state[j], state[i]
        result[k] ^= state[(state[i] + state[j]) & 255]
    return bytes(result)


@dataclass
class _Column:
    kind: int
    number: int
    variable: int
    fixed: bool
    offset: int
    size: int
    name: str = ""


class _Jet:
    def __init__(self, raw):
        if len(raw) < 2048 or raw[4:20] != b"Standard Jet DB\0":
            raise ReadError("Invalid Arbin Jet database signature")
        self.raw = raw
        version = raw[20]
        if version not in (0, 1):
            raise UnsupportedFormatError(f"RES database version {version} is unsupported (expected Jet 3/4)")
        self.jet3 = version == 0
        self.size = 2048 if self.jet3 else 4096
        if len(raw) % self.size:
            raise ReadError("Truncated RES database page")
        header = raw[:24] + _rc4(raw[24:24 + (126 if self.jet3 else 128)], b"\xc7\xda\x39\x6b")
        self.key = _u32(header, 0x3e)
        self.row_offset = 8 if self.jet3 else 12
        # Build the table-to-data-page index once, avoiding a scan per table.
        self.pages = {}
        for page in range(1, len(raw) // self.size):
            data = self.page(page)
            if data[0] == 1:
                self.pages.setdefault(_u32(data, 4), []).append(page)

    def page(self, number):
        if not 0 <= number < len(self.raw) // self.size:
            raise ReadError(f"RES page {number} is outside the file")
        data = self.raw[number * self.size:(number + 1) * self.size]
        return _rc4(data, struct.pack("<I", self.key ^ number)) if self.key and number else data

    def text(self, raw):
        if self.jet3:
            return raw.decode("cp1252")
        if raw.startswith(b"\xff\xfe"):
            output = bytearray()
            compressed = True
            i = 2
            while i < len(raw):
                if raw[i] == 0:
                    compressed = not compressed
                    i += 1
                elif compressed:
                    output.extend((raw[i], 0))
                    i += 1
                else:
                    output.extend(raw[i:i + 2])
                    i += 2
            raw = bytes(output)
        return raw.decode("utf-16-le")

    def schema(self, number):
        data = self.page(number)
        if data[0] != 2:
            raise ReadError(f"RES page {number} is not a table definition")
        seen = {number}
        next_page = _u32(data, 4)
        while next_page:
            if next_page in seen:
                raise ReadError("Cyclic RES table definition")
            seen.add(next_page)
            part = self.page(next_page)
            data += part[8:]
            next_page = _u32(part, 4)
        if self.jet3:
            ncols, nvar = _u16(data, 25), _u16(data, 23)
            offset, entry_size = 43 + _u32(data, 31) * 8, 18
            num, var, flags, fixed, size = 1, 3, 13, 14, 16
        else:
            ncols, nvar = _u16(data, 45), _u16(data, 43)
            offset, entry_size = 63 + _u32(data, 51) * 12, 25
            num, var, flags, fixed, size = 5, 7, 15, 21, 23
        if not 0 < ncols < 1024:
            raise ReadError("Invalid RES column count")
        columns = []
        for _ in range(ncols):
            item = data[offset:offset + entry_size]
            columns.append(_Column(item[0], item[num], _u16(item, var), bool(item[flags] & 1),
                                   _u16(item, fixed), _u16(item, size) if item[0] != 1 else 0))
            offset += entry_size
        for column in columns:
            length = data[offset] if self.jet3 else _u16(data, offset)
            offset += 1 if self.jet3 else 2
            column.name = self.text(data[offset:offset + length])
            offset += length
        return sorted(columns, key=lambda c: c.number), nvar

    def rows(self, table):
        for page in self.pages.get(table, ()):
            data = self.page(page)
            count = _u16(data, self.row_offset)
            if self.row_offset + 2 + count * 2 > self.size:
                raise ReadError("Invalid RES row count")
            end = self.size
            for i in range(count):
                pointer = _u16(data, self.row_offset + 2 + i * 2)
                start = pointer & 0x1fff
                if start > end or start < self.row_offset + 2 + count * 2:
                    raise ReadError("Invalid RES row bounds")
                if not pointer & 0x4000:
                    if pointer & 0x8000:
                        # A relocated row stores a page/row pointer.
                        target = _u32(data, start)
                        yield self._overflow(target)
                    else:
                        yield data[start:end]
                end = start

    def _overflow(self, target):
        seen = set()
        while target not in seen:
            seen.add(target)
            page, row = target >> 8, target & 255
            data = self.page(page)
            if row >= _u16(data, self.row_offset):
                raise ReadError("Invalid RES overflow row")
            pointer = _u16(data, self.row_offset + 2 + row * 2)
            start = pointer & 0x1fff
            end = self.size if row == 0 else _u16(data, self.row_offset + row * 2) & 0x1fff
            if not start <= end <= self.size:
                raise ReadError("Invalid RES overflow bounds")
            if not pointer & 0x8000:
                return data[start:end]
            target = _u32(data, start)
        raise ReadError("Cyclic RES overflow row")

    def values(self, row, columns, nvar):
        width = 1 if self.jet3 else 2
        count = row[0] if self.jet3 else _u16(row)
        mask_size = (count + 7) // 8
        if not count or mask_size + width > len(row):
            raise ReadError("Invalid RES row column count")
        mask = row[-mask_size:]
        offsets = []
        if nvar:
            if self.jet3:
                num_var = row[-mask_size - 1]
                jumps = (len(row) - 1) // 256
                cursor = len(row) - mask_size - jumps - 2
                if (cursor - num_var) // 256 < jumps:
                    jumps -= 1
                used = 0
                for i in range(num_var + 1):
                    while used < jumps and i == row[len(row) - mask_size - used - 2]:
                        used += 1
                    offsets.append(row[cursor - i] + used * 256)
            else:
                num_var = _u16(row, len(row) - mask_size - 2)
                offsets = [_u16(row, len(row) - mask_size - 4 - 2 * i) for i in range(num_var + 1)]
        result = {}
        for column in columns:
            present = column.number < count and bool(mask[column.number // 8] & (1 << (column.number % 8)))
            if column.kind == 1:
                result[column.name] = present
                continue
            if not present:
                result[column.name] = None
                continue
            if column.fixed:
                start, end = column.offset + width, column.offset + width + column.size
            elif column.variable + 1 < len(offsets):
                start, end = offsets[column.variable:column.variable + 2]
            else:
                result[column.name] = None
                continue
            if not 0 <= start <= end <= len(row):
                raise ReadError(f"Invalid RES field bounds for {column.name}")
            data = row[start:end]
            fmt = {2: "<B", 3: "<h", 4: "<i", 5: "<q", 6: "<f", 7: "<d", 8: "<d"}.get(column.kind)
            if fmt:
                value = struct.unpack(fmt, data)[0]
                result[column.name] = value / 10000 if column.kind == 5 else value
            elif column.kind == 10:
                result[column.name] = self.text(data)
            else:
                # Unselected Access memo/OLE fields are deliberately left opaque.
                result[column.name] = data
        return result

    def table(self, number, selected=None):
        columns, nvar = self.schema(number)
        if selected is not None:
            columns = [c for c in columns if c.name in selected]
        return columns, [self.values(row, columns, nvar) for row in self.rows(number)]


def read_res(raw):
    database = _Jet(raw)
    _, catalog = database.table(2, {"Id", "Name", "Type"})
    tables = {row["Name"]: int(row["Id"]) & 0xffffff for row in catalog
              if row.get("Type") is not None and int(row["Type"]) & 0x7f == 1}
    name = next((n for n in tables if n.lower() == "channel_normal_table"), None)
    if name is None:
        raise UnsupportedFormatError("RES database has no Channel_Normal_Table")
    schema, _ = database.schema(tables[name])
    for column in schema:
        if column.kind not in (*range(1, 9), 10):
            raise UnsupportedFormatError(f"Unsupported RES measurement column type {column.kind}: {column.name}")
    columns = _fixed_table(database, tables[name], schema)
    if columns is None:
        columns = _general_table(database, tables[name], schema)
    keys = [columns[key] for key in ("Data_Point", "Test_ID") if key in columns]
    if keys:
        order = np.lexsort(keys)
        columns = {name: values[order] for name, values in columns.items()}
    units = {"Test_Time": "s", "Step_Time": "s", "Voltage": "V", "Current": "A",
             "Charge_Capacity": "Ah", "Discharge_Capacity": "Ah",
             "Charge_Energy": "Wh", "Discharge_Energy": "Wh"}
    return Experiment((Dataset(columns, units, name),), "res", {"instrument": "Arbin", "tables": tuple(tables)})


def _dates(days):
    valid = np.isfinite(days)
    array = np.full(len(days), "NaT", dtype="datetime64[ms]")
    array[valid] = np.datetime64("1899-12-30", "ms") + np.rint(days[valid] * 86400000).astype("timedelta64[ms]")
    return array


def _fixed_table(database, number, schema):
    """Decode the usual fixed-width numeric measurement table in one batch."""
    if not all(c.fixed and 1 <= c.kind <= 8 for c in schema):
        return None
    rows = list(database.rows(number))
    if not rows or len({len(row) for row in rows}) != 1:
        return None
    width = 1 if database.jet3 else 2
    size = len(rows[0])
    formats = {2: "u1", 3: "<i2", 4: "<i4", 5: "<i8", 6: "<f4", 7: "<f8", 8: "<f8"}
    numeric = [c for c in schema if c.kind != 1]
    if any(c.offset + width + np.dtype(formats[c.kind]).itemsize > size for c in numeric):
        raise ReadError("RES fixed column extends past its row")
    dtype = np.dtype({"names": [c.name for c in numeric], "formats": [formats[c.kind] for c in numeric],
                      "offsets": [width + c.offset for c in numeric], "itemsize": size})
    buffer = b"".join(rows)
    records = np.frombuffer(buffer, dtype=dtype)
    bytes_view = np.frombuffer(buffer, dtype="u1").reshape(-1, size)
    counts = np.frombuffer(buffer, dtype=np.dtype({"names": ["n"], "formats": ["u1" if width == 1 else "<u2"],
                                                 "offsets": [0], "itemsize": size}))["n"]
    if not np.all(counts == counts[0]):
        return None
    mask_size = (int(counts[0]) + 7) // 8
    if not mask_size or mask_size + width > size:
        raise ReadError("Invalid RES null mask")
    columns = {}
    for column in schema:
        present = (bytes_view[:, size - mask_size + column.number // 8] & (1 << (column.number % 8))) != 0 if column.number < counts[0] else np.zeros(len(rows), dtype=bool)
        if column.kind == 1:
            values = present
        else:
            values = records[column.name]
            if column.kind == 5:
                values = values / 10000
            if not np.all(present):
                values = values.astype(float)
                values[~present] = np.nan
            if column.kind == 8:
                values = _dates(values)
        columns[column.name] = values
    return columns


def _general_table(database, number, schema):
    _, rows = database.table(number)
    columns = {}
    for column in schema:
        values = [row[column.name] for row in rows]
        if column.kind == 8:
            days = np.array([np.nan if v is None else v for v in values])
            array = _dates(days)
        elif column.kind == 10:
            array = np.array(["" if v is None else v for v in values], dtype=str)
        elif column.kind in (1, 2, 3, 4) and all(v is not None for v in values):
            array = np.array(values, dtype=bool if column.kind == 1 else "i8")
        else:
            array = np.array([np.nan if v is None else v for v in values], dtype=float)
        columns[column.name] = array
    return columns
