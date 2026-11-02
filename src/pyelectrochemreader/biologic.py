"""Bio-Logic modular binary files, using Galvani's documented column layouts.

Rewritten 2026-11-02. SPDX-License-Identifier: GPL-3.0-or-later
"""

from collections import Counter
from datetime import datetime, timedelta
import struct

import numpy as np

from ._biologic_columns import VMPdata_colID_dtype_map, VMPdata_colID_flag_map
from .core import Dataset, Experiment, ReadError, UnsupportedFormatError

MAGIC = b"BIO-LOGIC MODULAR FILE\x1a".ljust(48) + b"\0" * 4
HEADER = struct.Struct("<10s25sII8s")
HEADER_NEW = struct.Struct("<10s25sIIII8s")


def read_mpr(raw):
    if not raw.startswith(MAGIC):
        raise ReadError("Invalid Bio-Logic MPR signature")
    offset = len(MAGIC)
    datasets = []
    metadata = {"instrument": "Bio-Logic", "modules": []}
    while offset < len(raw):
        if raw[offset:offset + 6] != b"MODULE":
            raise ReadError(f"Expected MPR module at byte {offset}")
        offset += 6
        header = HEADER_NEW if raw[offset + 35:offset + 39] == b"\xff" * 4 else HEADER
        values = header.unpack_from(raw, offset)
        short, long = values[:2]
        length, version = values[3:5] if header is HEADER_NEW else values[2:4]
        date = values[-1].decode("ascii").strip("\0 ")
        offset += header.size
        if offset + length > len(raw):
            raise ReadError(f"Truncated MPR module {short!r}")
        payload = memoryview(raw)[offset:offset + length]
        offset += length
        name = short.decode("ascii").strip()
        metadata["modules"].append({"name": name, "version": version, "date": date})
        if name == "VMP data":
            datasets.append(_data(payload, version, long.decode("latin1").strip()))
        elif name == "VMP Set":
            metadata["start_date"] = date
        elif name == "VMP loop" and version == 0 and len(payload) >= 4:
            metadata["loop_index"] = np.trim_zeros(np.frombuffer(payload[4:], dtype="<u4"), "b")
        elif name == "VMP LOG":
            metadata["end_date"] = date
            for position in (465, 469, 473, 585):
                if position + 8 <= len(payload):
                    days = struct.unpack_from("<d", payload, position)[0]
                    if 40000 < days < 100000:
                        metadata["timestamp"] = datetime(1899, 12, 30) + timedelta(days=days)
                        break
    return Experiment(tuple(datasets), "mpr", metadata)


def _data(payload, version, name):
    if len(payload) < 6:
        raise ReadError("Truncated MPR data header")
    count, ncols = struct.unpack_from("<IB", payload)
    if version == 0:
        if payload[5]:
            ids = np.frombuffer(payload, dtype="u1", count=ncols, offset=5)
            start = 100
        else:
            ids = np.frombuffer(payload, dtype="u1", count=ncols * 2, offset=5)[1::2]
            start = 1007
    elif version in (2, 3):
        ids = np.frombuffer(payload, dtype="<u2", count=ncols, offset=5)
        start = 405 if version == 2 else 406
    else:
        raise UnsupportedFormatError(f"MPR data module version {version} is unsupported")
    fields = []
    flags = {}
    counts = Counter()
    for col_id in ids:
        col_id = int(col_id)
        if col_id in VMPdata_colID_flag_map:
            if not flags:
                fields.append(("flags", "u1"))
            flag_name, mask, dtype = VMPdata_colID_flag_map[col_id]
            flags[flag_name] = (mask, dtype)
        elif col_id in VMPdata_colID_dtype_map:
            field_name, dtype = VMPdata_colID_dtype_map[col_id]
            counts[field_name] += 1
            if counts[field_name] > 1:
                field_name += f" {counts[field_name]}"
            fields.append((field_name, dtype))
        else:
            raise UnsupportedFormatError(f"Unknown MPR column ID {col_id}; its byte width cannot be inferred safely")
    dtype = np.dtype(fields)
    expected = start + count * dtype.itemsize
    if dtype.itemsize == 0 or len(payload) != expected:
        raise ReadError(f"MPR data size mismatch: expected {expected} bytes, found {len(payload)}")
    records = np.frombuffer(payload, dtype=dtype, count=count, offset=start)
    columns = {key: records[key] for key in dtype.names}
    for key, (mask, dtype) in flags.items():
        columns[key] = (records["flags"] & mask).astype(dtype)
    return Dataset(columns, name=name, metadata={"version": version, "column_ids": ids})
