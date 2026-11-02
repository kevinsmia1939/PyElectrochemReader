"""Bio-Logic ASCII and Gamry EXPLAIN text tables."""

from io import StringIO
import re

import numpy as np

from .core import Dataset, Experiment, ReadError


def _decode(raw, encoding):
    if encoding:
        return raw.decode(encoding).lstrip("\ufeff")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


def _names(names):
    counts = {}
    result = []
    for name in names:
        name = name.strip()
        if not name:
            raise ReadError("Empty column name")
        counts[name] = counts.get(name, 0) + 1
        result.append(name if counts[name] == 1 else f"{name} {counts[name]}")
    return result


def _table(names, rows, *, whitespace=False):
    names = _names(names)
    if not rows:
        return {name: np.empty(0) for name in names}
    if not whitespace:
        # A trailing separator is common in EC-Lab exports. Remove only an
        # extra separator; an empty recorded cell must remain a missing value.
        rows = [row[:-1] if row.endswith("\t") and row.count("\t") == len(names) else row for row in rows]
    normalized = [row.replace(",", ".") for row in rows]
    try:
        values = np.loadtxt(StringIO("\n".join(normalized)), delimiter=None if whitespace else "\t",
                            comments=None, ndmin=2)
        if values.shape[1] != len(names):
            raise ReadError(f"Expected {len(names)} columns, found {values.shape[1]}")
        return dict(zip(names, values.T))
    except ValueError as exc:
        # Some tables include overload flags or missing values. Preserve text
        # columns and represent missing numeric cells as NaN.
        fields = [row.split() if whitespace else row.split("\t") for row in rows]
        for index, row in enumerate(fields, 1):
            if len(row) != len(names):
                raise ReadError(f"Table row {index}: expected {len(names)} columns, found {len(row)}") from exc
        columns = {}
        for name, values in zip(names, zip(*fields)):
            try:
                columns[name] = np.array([v.replace(",", ".") if v.strip() else "nan" for v in values], dtype=float)
            except ValueError:
                columns[name] = np.array(values, dtype=str)
        return columns


def read_mpt(raw, encoding=None):
    lines = _decode(raw, encoding).splitlines()
    if lines[0].strip() not in ("EC-Lab ASCII FILE", "BT-Lab ASCII FILE"):
        raise ReadError("Invalid Bio-Logic ASCII signature")
    match = re.fullmatch(r"Nb header lines\s*:\s*(\d+)\s*", lines[1])
    if not match:
        raise ReadError("Missing MPT header length")
    count = int(match[1])
    if not 3 <= count <= len(lines):
        raise ReadError("Invalid MPT header length")
    names = lines[count - 1].strip().split("\t")
    rows = [line.strip(" ") for line in lines[count:] if line.strip()]
    columns = _table(names, rows)
    metadata = {"header": "\n".join(lines[:count - 1]), "instrument": "Bio-Logic"}
    return Experiment((Dataset(columns, name="EC-Lab"),), "mpt", metadata)


def read_dta(raw, encoding=None):
    lines = _decode(raw, encoding).splitlines()
    if lines[0].strip() not in ("EXPLAIN", "VFP600"):
        raise ReadError("Invalid Gamry EXPLAIN/VFP600 signature")
    metadata = {"instrument": "Gamry"}
    datasets = []
    i = 1
    while i < len(lines):
        fields = lines[i].split("\t")
        if len(fields) >= 2 and fields[1].strip() == "TABLE":
            if i + 2 >= len(lines):
                raise ReadError("Truncated Gamry table header")
            name = fields[0].strip()
            names = lines[i + 1].strip().split("\t")
            units = lines[i + 2].strip().split("\t")
            if len(names) != len(units):
                # Several Gamry exports separate the first unit with spaces.
                units = lines[i + 2].split()
            i += 3
            rows = []
            while i < len(lines):
                line = lines[i].strip()
                if not line:
                    i += 1
                    continue
                if not re.match(r"^[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][+-]?\d+)?(?:\s|$)", line):
                    break
                rows.append(line)
                i += 1
            # Data records use whitespace even in files with tabbed headers.
            columns = _table(names, rows, whitespace=True)
            datasets.append(Dataset(columns, dict(zip(columns, units)), name))
            continue
        if fields and fields[0].strip():
            metadata[fields[0].strip()] = fields[2] if len(fields) > 2 else fields[-1]
        i += 1
    return Experiment(tuple(datasets), "dta", metadata)
