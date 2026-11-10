"""Bio-Logic EC-Lab/BT-Lab MPS settings and MGR graph configuration.

MPS is a text document with fixed-width or tab-delimited technique tables.
MGR is XML. Original text and uninterpreted parameter/attribute names are kept.
"""

from collections import Counter, OrderedDict
import re
import xml.etree.ElementTree as ET

import numpy as np

from .core import Configuration, Dataset, ReadError
from .text import _decode


def _column(values):
    """Keep numeric/boolean columns typed, without inventing missing values."""
    nonempty = [value for value in values if value not in (None, "")]
    if not nonempty:
        return np.array(["" for _ in values], dtype=str)
    if nonempty and all(value in ("true", "false") for value in nonempty):
        converted = [None if value in (None, "") else value == "true" for value in values]
        return np.array(converted, dtype=object if len(nonempty) != len(values) else bool)
    if nonempty and all(re.fullmatch(r"[+-]?\d+", value) for value in nonempty) and len(nonempty) == len(values):
        try:
            return np.array(values, dtype="i8")
        except (ValueError, OverflowError):
            # Large identifiers remain strings, rather than losing precision.
            return np.array(values, dtype=str)
    try:
        return np.array([np.nan if value in (None, "") else float(value.replace(",", ".")) for value in values])
    except ValueError:
        return np.array(["" if value is None else value for value in values], dtype=str)


def _names(names):
    result, counts = [], Counter()
    for name in names:
        counts[name] += 1
        unique = name if counts[name] == 1 else f"{name} {counts[name]}"
        while unique in result:
            counts[name] += 1
            unique = f"{name} {counts[name]}"
        result.append(unique)
    return result


def _mps_layout(line):
    if "\t" in line:
        fields = [part.strip() for part in line.split("\t")]
        while fields and not fields[-1]:
            fields.pop()
        values = fields[1:]
        layout = None
    else:
        tokens = list(re.finditer(r"\S+", line))
        if len(tokens) < 2:
            raise ReadError("MPS Ns header contains no sequence numbers")
        start = tokens[1].start()
        width = tokens[2].start() - start if len(tokens) > 2 else start
        if width < 1 or any(token.start() != start + i * width for i, token in enumerate(tokens[1:])):
            raise ReadError("MPS sequence columns have inconsistent widths")
        values = [token.group() for token in tokens[1:]]
        layout = (start, width)
    if not values or any(not re.fullmatch(r"\d+", value) for value in values):
        raise ReadError("MPS Ns header must contain integer sequence numbers")
    return layout, values


def _mps_row(line, layout, count):
    if layout is None:
        fields = [part.strip() for part in line.split("\t")]
        label, values = fields[0], fields[1:]
        if any(values[count:]):
            raise ReadError(f"Too many MPS sequence values for {label!r}")
        values = values[:count] + [""] * max(0, count - len(values))
    else:
        start, width = layout
        label = line[:start].strip()
        if line[start + width * count:].strip():
            raise ReadError(f"Too many MPS sequence values for {label!r}")
        values = [line[start + i * width:start + (i + 1) * width].strip() for i in range(count)]
    if not label:
        raise ReadError("Missing MPS parameter name")
    return label, values


def read_mps(raw, encoding=None):
    text = _decode(raw, encoding)
    lines = text.splitlines()
    if not lines or lines[0].strip().upper() not in ("EC-LAB SETTING FILE", "BT-LAB SETTING FILE"):
        raise ReadError("Invalid Bio-Logic MPS settings signature")
    techniques = [(i, int(match[1])) for i, line in enumerate(lines)
                  if (match := re.fullmatch(r"\s*Technique\s*:\s*(\d+)\s*", line))]
    header_end = techniques[0][0] if techniques else len(lines)
    properties = {}
    for line in lines[1:header_end]:
        match = re.match(r"^(.+?)\s+:\s*(.*)$", line)
        if match:
            key, value = match[1].strip(), match[2]
            if key in properties:
                previous = properties[key]
                properties[key] = (previous if isinstance(previous, list) else [previous]) + [value]
            else:
                properties[key] = value
    declared = properties.get("Number of linked techniques")
    if isinstance(declared, list):
        raise ReadError("MPS repeats the linked-technique count")
    if declared is not None and int(declared) != len(techniques):
        raise ReadError(f"MPS declares {declared} linked techniques, found {len(techniques)}")
    metadata = {"instrument": "Bio-Logic", "software": lines[0].split()[0],
                "header": "\n".join(lines[:header_end]), "settings": properties, "techniques": []}
    tables = []
    for index, (start, number) in enumerate(techniques):
        stop = techniques[index + 1][0] if index + 1 < len(techniques) else len(lines)
        body = [(i, line) for i, line in enumerate(lines[start + 1:stop]) if line.strip()]
        if not body:
            raise ReadError(f"MPS Technique {number} has no name")
        title = body[0][1].strip()
        details = {"number": number, "name": title}
        metadata["techniques"].append(details)
        headers = [i for i, (_, line) in enumerate(body[1:], 1) if re.match(r"^\s*Ns(?:\s|$)", line)]
        if not headers:
            # Single-value techniques such as Loop have no Ns matrix.
            keys, values = [], []
            for _, line in body[1:]:
                if "\t" in line:
                    key, value = line.split("\t", 1)
                elif len(line) > 20:
                    key, value = line[:20], line[20:]
                else:
                    continue  # Unstructured content is retained in source text.
                keys.append(key.strip())
                values.append(value.strip())
            if keys:
                tables.append(Dataset({"Parameter": np.array(keys), "Value": np.array(values)},
                                      name=f"Technique {number}: {title}", metadata=details))
            continue
        for group, begin in enumerate(headers):
            end = headers[group + 1] if group + 1 < len(headers) else len(body)
            layout, sequence_numbers = _mps_layout(body[begin][1])
            fields = [("Ns", sequence_numbers)]
            fields.extend(_mps_row(line, layout, len(sequence_numbers)) for _, line in body[begin + 1:end])
            names = _names([key for key, _ in fields])
            columns = {name: _column(values) for name, (_, values) in zip(names, fields)}
            label = f"Technique {number}: {title}" + (f" [{group + 1}]" if len(headers) > 1 else "")
            info = {**details, "parameter_names": [key for key, _ in fields]}
            tables.append(Dataset(columns, name=label, metadata=info))
    return Configuration(text, tuple(tables), "mps", metadata)


def _xml_text(raw, encoding):
    if encoding:
        return _decode(raw, encoding)
    if raw.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return raw.decode("utf-32")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    declaration = re.match(rb"\s*<\?xml[^>]*\bencoding\s*=\s*['\"]([^'\"]+)['\"]", raw)
    if declaration:
        try:
            return raw.decode(declaration[1].decode("ascii")).lstrip("\ufeff")
        except LookupError as exc:
            raise ReadError("Unsupported MGR XML encoding") from exc
    return _decode(raw, None)


def read_mgr(raw, encoding=None):
    text = _xml_text(raw, encoding)
    # The text is already Unicode. Remove only the declaration for XML parsing,
    # so an encoding override cannot cause a second interpretation of the bytes.
    xml = re.sub(r"^\s*<\?xml\b.*?\?>", "", text, count=1, flags=re.S)
    root = ET.fromstring(xml)
    local = lambda tag: tag.rsplit("}", 1)[-1]
    if local(root.tag) != "Configuration":
        raise ReadError("Expected a Bio-Logic MGR Configuration root")
    groups = OrderedDict()

    def visit(node, path, tags):
        tag = local(node.tag)
        tags = (*tags, tag)
        if node.attrib or (node.text or "").strip():
            group = "/".join(tags[-2:])
            groups.setdefault(group, []).append((path, node))
        tree = {"tag": node.tag, "attributes": dict(node.attrib), "text": node.text or "", "children": []}
        if node.tail:
            tree["tail"] = node.tail
        counts = Counter()
        for child in node:
            counts[child.tag] += 1
            child_path = f"{path}/{local(child.tag)}[{counts[child.tag]}]"
            tree["children"].append(visit(child, child_path, tags))
        return tree

    tree = visit(root, f"/{local(root.tag)}[1]", ())
    tables = []
    for label, rows in groups.items():
        attributes = list(dict.fromkeys(key for _, node in rows for key in node.attrib))
        path_column = "xml_path"
        while path_column in attributes:
            path_column = "_" + path_column
        columns = {path_column: np.array([path for path, _ in rows])}
        columns.update({key: _column([node.get(key) for _, node in rows]) for key in attributes})
        if any((node.text or "").strip() for _, node in rows):
            text_column = "xml_text"
            while text_column in columns:
                text_column = "_" + text_column
            columns[text_column] = np.array([(node.text or "").strip() for _, node in rows])
        tables.append(Dataset(columns, units={key: "" for key in columns}, name=label,
                              metadata={"path_column": path_column}))
    metadata = {"instrument": "Bio-Logic", "tree": tree}
    return Configuration(text, tuple(tables), "mgr", metadata)
