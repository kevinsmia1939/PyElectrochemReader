"""Read OOXML and OpenDocument tables directly with ZIP/XML, without pandas.

This reads stored cell values and cached formula results, not a calculation
engine. Older OLE/BIFF Excel workbooks are deliberately not guessed as OOXML.
"""

from io import BytesIO
import posixpath
import re
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from .core import Experiment, ReadError, UnsupportedFormatError
from .tables import dataset_from_rows, rectangular_rows, signal, table_from_rows


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _children(node, name):
    return [child for child in node if _local(child.tag) == name]


def _text(node):
    return "".join(child.text or "" for child in node.iter() if _local(child.tag) == "t")


def _sheet_tables(rows, name):
    rows = rectangular_rows(rows)
    headers = []
    for i, row in enumerate(rows):
        signals = {found[0] for value in row if value is not None
                   and (found := signal(str(value))) is not None}
        if "voltage" in signals and ("current" in signals or "current_density" in signals):
            headers.append(i)
    if not headers:
        return [table_from_rows(rows, name=name)] if rows else []
    datasets = []
    context_start = 0
    for i, start in enumerate(headers):
        names = list(rows[start])
        while names and names[-1] is None:
            names.pop()
        limit = headers[i + 1] if i + 1 < len(headers) else len(rows)
        stop = start + 1
        while stop < limit:
            # Battery exports interleave Index/Mode/Period summary tables with
            # repeated measurement tables. Summaries are context, not samples.
            if str(rows[stop][0]) == str(names[0]):
                break
            stop += 1
        values = [row[:len(names)] for row in rows[start + 1:stop]]
        if values:
            details = {"sheet": name, "header_rows": rows[context_start:start]}
            label = name if len(headers) == 1 else f"{name} [{i + 1}]"
            datasets.append(dataset_from_rows(names, values, name=label, metadata=details))
        context_start = stop
    return datasets


def _column(reference):
    match = re.fullmatch(r"([A-Za-z]+)\d+", reference)
    if not match:
        raise ReadError(f"Invalid spreadsheet cell reference {reference!r}")
    number = 0
    for char in match[1].upper():
        number = number * 26 + ord(char) - ord("A") + 1
    if number > 16384:
        raise ReadError("Spreadsheet exceeds the supported column count")
    return number - 1


def _xlsx_sheets(archive):
    names = archive.namelist()
    if "xl/workbook.xml" not in names:
        raise UnsupportedFormatError("Archive is not an XLSX workbook")
    strings = []
    if "xl/sharedStrings.xml" in names:
        strings = [_text(node) for node in ET.fromstring(archive.read("xl/sharedStrings.xml"))]
    time_styles = set()
    if "xl/styles.xml" in names:
        styles = ET.fromstring(archive.read("xl/styles.xml"))
        formats = {int(node.get("numFmtId")): node.get("formatCode", "")
                   for node in styles.iter() if _local(node.tag) == "numFmt"}
        for section in _children(styles, "cellXfs"):
            for i, node in enumerate(section):
                fmt = int(node.get("numFmtId", "0"))
                custom = formats.get(fmt, "").lower()
                if fmt in (18, 19, 20, 21, 45, 46, 47) or (
                    re.search(r"\[[hms]+\]", custom) or ("h" in custom and "y" not in custom and "d" not in custom)
                ):
                    time_styles.add(i)
    relationships = {}
    for node in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels")):
        if node.get("TargetMode") != "External":
            target = node.get("Target", "")
            relationships[node.get("Id")] = (target.lstrip("/") if target.startswith("/")
                                              else posixpath.normpath("xl/" + target))
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    for sheet in workbook.iter():
        if _local(sheet.tag) != "sheet":
            continue
        relation = next((value for key, value in sheet.attrib.items() if _local(key) == "id"), None)
        if relation not in relationships:
            raise ReadError(f"Missing worksheet relationship for {sheet.get('name')}")
        root = ET.fromstring(archive.read(relationships[relation]))
        rows = []
        for row in root.iter():
            if _local(row.tag) != "row":
                continue
            values = []
            for cell in _children(row, "c"):
                col = _column(cell.get("r", "")) if cell.get("r") else len(values)
                if col < len(values):
                    raise ReadError("Worksheet cells are not in column order")
                values.extend([None] * (col + 1 - len(values)))
                value_nodes = _children(cell, "v")
                value = value_nodes[0].text if value_nodes else None
                kind = cell.get("t")
                if kind == "inlineStr":
                    value = _text(cell)
                elif kind == "s" and value is not None:
                    index = int(value)
                    if not 0 <= index < len(strings):
                        raise ReadError("Invalid shared string index")
                    value = strings[index]
                elif kind == "e":
                    raise ReadError(f"Excel error in cell {cell.get('r')}: {value}")
                elif kind == "b" and value is not None:
                    value = bool(int(value))
                elif kind not in ("str", "d") and value is not None:
                    value = float(value)
                    if int(cell.get("s", "0")) in time_styles:
                        value *= 86400
                values[col] = value
            rows.append(values)
        yield sheet.get("name", "Sheet"), rows


def _duration(value):
    match = re.fullmatch(r"(-)?P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", value)
    if not match:
        raise ReadError(f"Unsupported spreadsheet duration {value!r}")
    negative, days, hours, minutes, seconds = match.groups()
    total = int(days or 0) * 86400 + int(hours or 0) * 3600 + int(minutes or 0) * 60 + float(seconds or 0)
    return -total if negative else total


def _ods_sheets(archive):
    table_ns = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
    office_ns = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
    root = ET.fromstring(archive.read("content.xml"))
    for table in root.iter(table_ns + "table"):
        rows = []
        for row in table.iter(table_ns + "table-row"):
            values, empty = [], 0
            for cell in row:
                if _local(cell.tag) not in ("table-cell", "covered-table-cell"):
                    continue
                repeat = int(cell.get(table_ns + "number-columns-repeated", "1"))
                if repeat < 1:
                    raise ReadError("Invalid repeated spreadsheet column count")
                kind = cell.get(office_ns + "value-type")
                if kind in ("float", "currency", "percentage"):
                    value = float(cell.get(office_ns + "value"))
                elif kind == "boolean":
                    value = cell.get(office_ns + "boolean-value") == "true"
                elif kind == "time":
                    value = _duration(cell.get(office_ns + "time-value", ""))
                elif kind == "date":
                    value = cell.get(office_ns + "date-value")
                else:
                    paragraphs = ["".join(node.itertext()) for node in cell if _local(node.tag) == "p"]
                    value = "\n".join(paragraphs) if paragraphs else cell.get(office_ns + "string-value")
                if value is None or value == "":
                    empty += repeat
                    continue
                if len(values) + empty + repeat > 16384:
                    raise ReadError("Spreadsheet exceeds the supported column count")
                values.extend([None] * empty + [value] * repeat)
                empty = 0
            repeat = int(row.get(table_ns + "number-rows-repeated", "1"))
            if repeat < 1 or (values and len(rows) + repeat > 1048576):
                raise ReadError("Invalid repeated spreadsheet row count")
            # Empty trailing grid rows/columns are formatting, not observations.
            if values:
                rows.extend([values] * repeat)
        yield table.get(table_ns + "name", "Sheet"), rows


def read_spreadsheet(raw, file_format):
    if raw.startswith(b"\xd0\xcf\x11\xe0"):
        raise UnsupportedFormatError("Legacy binary XLS (OLE/BIFF) is unsupported; export as XLSX, ODS, or CSV")
    datasets, names = [], []
    with ZipFile(BytesIO(raw)) as archive:
        is_xlsx = "xl/workbook.xml" in archive.namelist()
        if not is_xlsx and "content.xml" not in archive.namelist():
            raise UnsupportedFormatError("Archive contains neither XLSX nor ODS spreadsheet data")
        for name, rows in (_xlsx_sheets(archive) if is_xlsx else _ods_sheets(archive)):
            names.append(name)
            datasets.extend(dataset for dataset in _sheet_tables(rows, name) if len(dataset))
    return Experiment(tuple(datasets), file_format, {"instrument": "Spreadsheet", "sheets": names,
        "container": "xlsx" if is_xlsx else "ods", "formula_policy": "cached values only",
        "time_cells_unit": "s"})
