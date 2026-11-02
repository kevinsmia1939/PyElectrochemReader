"""Shared conversion of named text/spreadsheet columns to NumPy datasets."""

import csv
from io import StringIO
import re

import numpy as np

from .core import Dataset, ReadError


def number(value):
    if isinstance(value, (int, float, np.number, bool)):
        return float(value)
    if value is None or str(value).strip().lower() in ("", "na", "n/a", "nan"):
        return np.nan
    return float(str(value).strip().replace(",", "."))


def is_number(value):
    try:
        number(value)
        return value is not None and str(value).strip() != ""
    except (ValueError, TypeError):
        return False


def signal(name, unit=""):
    """Recognize measurement labels and explicit units without renaming them."""
    match = re.fullmatch(r"(.*?)\s*\(([^()]*)\)\s*", name)
    if match:
        base, recorded_unit = match.groups()
        unit = unit or recorded_unit
    elif "/" in name:
        base, recorded_unit = name.split("/", 1)
        unit = unit or recorded_unit
    else:
        base = name
    base = re.sub(r"[\s<>_.-]", "", base).lower()
    unit = unit.strip().replace("μ", "µ").replace("²", "2").replace("^", "")
    unit = {"Volts": "V", "volts": "V", "Seconds": "s", "seconds": "s", "sec": "s"}.get(unit, unit)
    families = (
        ("voltage", {"e", "ewe", "ecell", "potential", "voltage", "we(1)potential"}, {"V": 1, "mV": .001}),
        ("current", {"i", "im", "idc", "current", "we(1)current"}, {"A": 1, "mA": .001, "uA": 1e-6, "µA": 1e-6, "nA": 1e-9}),
        ("time", {"t", "time", "testtime", "elapsedtime", "elapsedtimes"}, {"s": 1, "ms": .001, "min": 60, "h": 3600}),
        ("frequency", {"freq", "frequency"}, {"Hz": 1, "kHz": 1000}),
        ("capacity", {"capacity", "q"}, {"Ah": 1, "mAh": .001, "A.h": 1, "mA.h": .001}),
    )
    if base in {"i", "current", "currentdensity", "j"} and unit in ("A/cm2", "mA/cm2", "A/m2"):
        return "current_density", {"A/cm2": 1, "mA/cm2": .001, "A/m2": .0001}[unit], unit
    for alias, names, scales in families:
        if base in names:
            unit = unit or {"voltage": "V", "current": "A", "time": "s", "frequency": "Hz", "capacity": "Ah"}[alias]
            if unit in scales:
                return alias, scales[unit], unit
    return None


def elapsed_seconds(value):
    """Decode explicit [days-]hours:minutes:seconds duration strings."""
    try:
        return number(value)
    except ValueError:
        match = re.fullmatch(r"(?:(\d+)-)?(\d+):(\d{2}):(\d{2}(?:[.,]\d+)?)", str(value).strip())
        if not match:
            raise ValueError(f"Invalid elapsed time {value!r}") from None
        days, hours, minutes, seconds = match.groups()
        if int(minutes) >= 60 or float(seconds.replace(",", ".")) >= 60:
            raise ValueError(f"Invalid elapsed time {value!r}")
        return int(days or 0) * 86400 + int(hours) * 3600 + int(minutes) * 60 + float(seconds.replace(",", "."))


def dataset_from_rows(names, rows, *, units=None, name="", metadata=None, numeric=False):
    names = [str(n).strip() if n is not None else "" for n in names]
    counts = {}
    unique = []
    for i, field in enumerate(names, 1):
        field = field or f"column_{i}"
        counts[field] = counts.get(field, 0) + 1
        candidate = field if counts[field] == 1 else f"{field} {counts[field]}"
        while candidate in unique:
            counts[field] += 1
            candidate = f"{field} {counts[field]}"
        unique.append(candidate)
    for i, row in enumerate(rows, 1):
        if len(row) != len(unique):
            raise ReadError(f"Table row {i}: expected {len(unique)} columns, found {len(row)}")
    columns, aliases, signal_units = {}, {}, dict(units or {})
    for i, field in enumerate(unique):
        values = [row[i] for row in rows]
        recognized = signal(field, signal_units.get(field, ""))
        converter = elapsed_seconds if recognized and recognized[0] == "time" else number
        try:
            array = np.array([converter(v) for v in values], dtype=float)
        except (ValueError, TypeError) as exc:
            if numeric or recognized:
                raise ReadError(f"Invalid numeric value in column {field!r}: {exc}") from exc
            array = np.array(["" if v is None else str(v) for v in values], dtype=str)
        columns[field] = array
        if recognized:
            alias, factor, unit = recognized
            aliases.setdefault(alias, (field, factor))
            signal_units[field] = unit
    return Dataset(columns, signal_units, name, metadata or {}, aliases)


def rectangular_rows(rows):
    """Drop empty outer padding while keeping missing cells inside a table."""
    rows = [list(row) for row in rows if any(value is not None and str(value).strip() for value in row)]
    if not rows:
        return []
    width = max(len(row) for row in rows)
    rows = [row + [None] * (width - len(row)) for row in rows]
    while width and all(row[width - 1] is None or str(row[width - 1]).strip() == "" for row in rows):
        width -= 1
    return [row[:width] for row in rows]


def table_from_rows(rows, *, name="", metadata=None, header=None):
    rows = [list(row) for row in rows if any(v is not None and str(v).strip() for v in row)]
    first_width = len(rows[0]) if rows else 0
    rows = rectangular_rows(rows)
    if not rows:
        return Dataset({}, name=name, metadata=metadata or {})
    if header is None:
        # Named column rows are all text, or contain a recognized measurement
        # label. A mixed data row (filename plus numbers) is not a header.
        header = (not any(is_number(v) for v in rows[0]) or
                  any(signal(str(v)) is not None for v in rows[0] if v is not None))
    if header:
        if len(rows[0]) > first_width:
            raise ReadError("Table data has more columns than its header")
        names, values = rows[0], rows[1:]
    else:
        names = [f"column_{i}" for i in range(1, len(rows[0]) + 1)]
        if len(names) >= 2 and is_number(rows[0][0]) and is_number(rows[0][1]):
            names[:2] = ["Voltage", "Current"]
        values = rows
    return dataset_from_rows(names, values, name=name, metadata=metadata)


def split_table(text, delimiter=None):
    """Read quoted CSV, tab/semicolon-separated tables, or whitespace text."""
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not lines:
        raise ReadError("No table rows")
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff("\n".join(lines[:30]), delimiters=",;\t").delimiter
        except csv.Error:
            delimiter = max((",", ";", "\t"), key=lambda d: sum(line.count(d) for line in lines[:30]))
            if not any(delimiter in line for line in lines[:30]):
                delimiter = "whitespace"
    if delimiter == "whitespace":
        return [line.split() for line in lines], delimiter
    if not isinstance(delimiter, str) or len(delimiter) != 1:
        raise ValueError("delimiter must be one character or 'whitespace'")
    return list(csv.reader(StringIO("\n".join(lines)), delimiter=delimiter, skipinitialspace=True)), delimiter
