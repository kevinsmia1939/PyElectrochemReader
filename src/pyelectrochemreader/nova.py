"""NOVA signal extraction adapted from Galvani (modified 2026-11-02).
SPDX-FileCopyrightText: 2026 OpenAI
SPDX-License-Identifier: GPL-3.0-or-later
"""
from datetime import datetime, timedelta

import numpy as np

from ._nrbf import NRBFParser, NRBFError
from .core import Dataset, Experiment, ReadError

_PARAMETER_DATA_ARRAY = "EcoChemie.Utils.Sequencer.CommandParameterDataArray"
_GENERIC_LIST = "System.Collections.Generic.List`1"

def _string(parser, value, default=None):
    value = parser.resolve(value)
    return value if isinstance(value, str) else default

def _datetime_from_binary(value):
    """Convert the ticks portion of a System.DateTime binary value."""
    if not isinstance(value, (int, np.integer)):
        return None
    ticks = int(value) & 0x3fffffffffffffff
    try:
        return datetime(1, 1, 1) + timedelta(microseconds=ticks // 10)
    except (OverflowError, ValueError):
        return None

def _numeric_list(parser, value, seen=None):
    """Find the numeric List<T> wrapped by a NOVA parameter object."""
    if seen is None:
        seen = set()

    if isinstance(value, dict) and set(value) == {"__ref__"}:
        object_id = value["__ref__"]
        if object_id in seen:
            return None
        seen.add(object_id)
        return _numeric_list(parser, parser.objects.get(object_id), seen)

    value = parser.resolve(value)
    if isinstance(value, dict):
        marker = id(value)
        if marker in seen:
            return None
        seen.add(marker)
        class_name = value.get("__class__", "")
        if class_name.startswith(_GENERIC_LIST) and (
            "[[System.Double," in class_name or "[[System.Int32," in class_name
        ):
            items = parser.resolve(value.get("_items"))
            if isinstance(items, np.ndarray):
                size = value.get("_size", len(items))
                if not isinstance(size, int) or size < 0 or size > len(items):
                    raise NRBFError("invalid size in NOVA numeric list")
                return items[:size], id(items)

        # The useful route is normally ParameterObject._value.  Trying it
        # first avoids walking repeated base-class parameter references.
        keys = ["_value", "ParameterObject+_value"]
        keys.extend(key for key in value if key not in keys and key != "__class__")
        for key in keys:
            if key in value:
                result = _numeric_list(parser, value[key], seen)
                if result is not None:
                    return result
    return None


def read_nox(raw):
    offset = 0
    stream_count = 0
    # Release preceding object graphs as soon as their stream ends. The final
    # graph contains the procedure and the calculated measurement signals.
    while offset < len(raw):
        parser = NRBFParser(raw, offset)
        root = parser.parse()
        if parser.offset <= offset:
            raise ReadError("NOVA parser made no progress")
        offset = parser.offset
        stream_count += 1
    if not isinstance(root, dict):
        raise ReadError("NOVA file does not contain a procedure")
    metadata = {name: _string(parser, root.get(key), "") for name, key in
                (("name", "_name"), ("description", "_text"),
                 ("instrument", "_instrument"), ("remarks", "_remarks"))}
    metadata["timestamp"] = _datetime_from_binary(root.get("_timeStamp"))
    metadata["modified_timestamp"] = _datetime_from_binary(root.get("_modifiedTimeStamp"))
    metadata["stream_count"] = stream_count
    groups = {}
    for object_id, value in parser.objects.items():
        if not isinstance(value, dict) or value.get("__class__") != _PARAMETER_DATA_ARRAY:
            continue
        result = _numeric_list(parser, value.get("_parameter"))
        if result is None or not len(result[0]):
            continue
        array, source_id = result
        name = _string(parser, value.get("CommandParameter+_name"))
        if not name:
            continue
        parent = parser.resolve(value.get("CommandParameter+_parent"))
        marker = id(parent) if isinstance(parent, dict) else object_id
        if marker not in groups:
            command = _string(parser, parent.get("FunctionHandler+_name"), "") if isinstance(parent, dict) else ""
            details = {"command_id": object_id}
            if isinstance(parent, dict):
                details["timestamp"] = parent.get("FunctionHandler+_timeStampEmbedded2")
            groups[marker] = (Dataset({}, name=command, metadata=details), {})
        dataset, sources = groups[marker]
        dataset.columns[name] = array
        dataset.units[name] = _string(parser, value.get("CommandParameter+_unit"), "")
        dataset.metadata.setdefault("labels", {})[name] = _string(parser, value.get("CommandParameter+_text"), name)
        sources[name] = (source_id, len(array))
    # Repeated command templates can reference exactly the same arrays. Use
    # object identity, not equal timestamps, to distinguish independent runs.
    unique = {}
    for dataset, sources in groups.values():
        key = tuple(sorted(sources.items()))
        unique.setdefault(key, dataset)
    for dataset in unique.values():
        dataset.columns = {name: array.copy() for name, array in dataset.columns.items()}
    return Experiment(tuple(unique.values()), "nox", metadata)
