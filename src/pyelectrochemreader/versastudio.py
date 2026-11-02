"""VersaStudio PAR and CorrWare COR ASCII measurement tables.

Implemented against the native examples and format handling in PySimpleEChem.
The parsers preserve all data columns and use only the standard library/NumPy.
"""

import csv
import re

from .core import Experiment, ReadError
from .tables import dataset_from_rows, split_table, table_from_rows
from .text import _decode, read_mpt


def read_par(raw, encoding=None):
    text = _decode(raw, encoding)
    starts = list(re.finditer(r"(?mi)^\s*<Segment(\d+)>\s*$", text))
    if not starts:
        raise ReadError("No VersaStudio Segment blocks")
    header = text[:starts[0].start()]
    rates = [float(v.replace(",", ".")) for v in re.findall(r"Scan Rate \(V/s\)\s*=\s*([+\-\d.eE,]+)", header)]
    metadata = {"instrument": "Princeton Applied Research", "header": header, "scan_rates": rates}
    if len(rates) == 1:
        metadata["scan_rate"] = rates[0]
    datasets = []
    for i, start in enumerate(starts):
        end = re.search(rf"(?mi)^\s*</Segment{start[1]}>\s*$", text[start.end():])
        if end is None:
            raise ReadError(f"Unclosed VersaStudio Segment{start[1]}")
        stop = start.end() + end.start()
        if i + 1 < len(starts) and stop > starts[i + 1].start():
            raise ReadError(f"Unclosed VersaStudio Segment{start[1]}")
        details, names, rows = {}, None, []
        for line in text[start.end():stop].splitlines():
            line = line.strip()
            if not line:
                continue
            if names is None:
                if "=" not in line:
                    raise ReadError("Expected VersaStudio Definition header")
                key, value = line.split("=", 1)
                details[key.strip()] = value.strip()
                if key.strip() == "Definition":
                    names = next(csv.reader([value], skipinitialspace=True))
            else:
                rows.append(next(csv.reader([line], skipinitialspace=True)))
        if names is None:
            raise ReadError("Missing VersaStudio Definition header")
        # Version 3 appends a literal zero to its definition as a format flag,
        # rather than a data column. Retain the original definition in metadata.
        if rows and len(names) == len(rows[0]) + 1 and names[-1].strip() == "0":
            names = names[:-1]
        datasets.append(dataset_from_rows(names, rows, name=f"Segment{start[1]}", metadata=details, numeric=True))
    return Experiment(tuple(datasets), "par", metadata)


def read_cor(raw, encoding=None):
    lines = _decode(raw, encoding).splitlines()
    if not lines or lines[0].strip() != "CORRW ASCII":
        raise ReadError("Invalid CorrWare ASCII signature")
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == "End Comments")
    except StopIteration:
        raise ReadError("CorrWare header has no End Comments marker") from None
    names = lines[start - 1].strip().split("\t")
    if len(names) < 2:
        raise ReadError("Missing CorrWare column names")
    rows = [line.split() for line in lines[start + 1:] if line.strip()]
    header = "\n".join(lines[:start])
    metadata = {"instrument": "CorrWare", "header": header}
    rate = re.search(r"Scan Rate:\s*([+\-\d.,eE]+)", header)
    if rate:
        metadata["scan_rate"] = float(rate[1].replace(",", ".")) / 1000
        metadata["scan_rate_unit"] = "V/s"
    declared = re.search(r"Data Points:\s*(\d+)", header)
    if declared:
        metadata["declared_points"] = int(declared[1])
    dataset = dataset_from_rows(names, rows, name="CorrWare", numeric=True)
    return Experiment((dataset,), "cor", metadata)


def read_delimited(raw, file_format, encoding=None, delimiter=None, header=None):
    text = _decode(raw, encoding)
    if text.startswith(("EC-Lab ASCII FILE", "BT-Lab ASCII FILE")):
        result = read_mpt(raw, encoding)
        result.format = file_format
        return result
    rows, separator = split_table(text, delimiter)
    if max(map(len, rows), default=0) < 2:
        raise ReadError("Expected at least two table columns")
    metadata = {"delimiter": separator, "instrument": "Delimited table"}
    dataset = table_from_rows(rows, name="Data", header=header)
    # Unlabelled CV data follows PySimpleEChem's potential/current convention.
    if dataset.column_names[:2] == ("Voltage", "Current"):
        metadata["unlabelled_columns"] = "potential in V, current in A"
    return Experiment((dataset,), file_format, metadata)
