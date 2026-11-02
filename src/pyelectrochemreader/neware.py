"""NumPy readers for NDA and zipped NDC records.

Layouts adapted from NewareNDA by Daniel Cogswell, copyright 2022-2024
SES AI Corporation (BSD-3-Clause, see licenses/NewareNDA.txt).
Rewritten 2026-11-02: vectorized decoding, no pandas or temporary files.
"""

from io import BytesIO
import re
import struct
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import numpy as np

from .core import Dataset, Experiment, ReadError, UnsupportedFormatError
from ._neware_columns import state_dict, multiplier_dict

COUNTERS = ("Charge_Capacity(mAh)", "Discharge_Capacity(mAh)",
            "Charge_Energy(mWh)", "Discharge_Energy(mWh)")
UNITS = {"Time": "s", "Voltage": "V", "Current(mA)": "mA", "Timestamp": "",
         "Charge_Capacity(mAh)": "mAh", "Discharge_Capacity(mAh)": "mAh",
         "Charge_Energy(mWh)": "mWh", "Discharge_Energy(mWh)": "mWh"}


def _layout(size, fields):
    return np.dtype({"names": [x[0] for x in fields], "formats": [x[1] for x in fields],
                     "offsets": [x[2] for x in fields], "itemsize": size})


def _changes(values):
    return np.cumsum(np.r_[True, values[1:] != values[:-1]]) if len(values) else np.empty(0, dtype=int)


_STATE_NAMES = np.array([state_dict.get(v, f"Unknown({v})") for v in range(256)])


def _status(values):
    return _STATE_NAMES[values]


def _scales(values):
    result = np.empty(len(values))
    for value in np.unique(values):
        if int(value) not in multiplier_dict:
            raise UnsupportedFormatError(f"Unknown Neware current range {value}")
        result[values == value] = multiplier_dict[int(value)]
    return result


def _calendar(records):
    year = records["year"].astype("i8")
    month = records["month"].astype("i8")
    if np.any((year < 1) | (month < 1) | (month > 12) | (records["day"] < 1) | (records["day"] > 31)):
        raise ReadError("Invalid date in Neware record")
    date = ((year - 1970) * 12 + month - 1).astype("datetime64[M]").astype("datetime64[s]")
    return date + ((records["day"].astype("i8") - 1) * 86400 + records["hour"].astype("i8") * 3600
                   + records["minute"].astype("i8") * 60 + records["second"]).astype("timedelta64[s]")


def _legacy(records):
    factor = _scales(records["range"])
    columns = {"Index": records["index"], "Cycle": records["cycle"].astype("u8") + 1,
               "Step": records["step"], "Status": _status(records["status"]),
               "Time": records["time"] / 1000, "Voltage": records["voltage"] / 10000,
               "Current(mA)": records["current"] * factor, "Timestamp": _calendar(records)}
    for name, field in zip(COUNTERS, ("cc", "dc", "ce", "de")):
        columns[name] = records[field] * factor / 3600
    return columns


def _legacy_layout(size=86, ndc=False):
    if ndc:
        fields = [("index", "<u4", 8), ("cycle", "<u4", 12), ("step", "u1", 16),
                  ("status", "u1", 17), ("time", "<u8", 23), ("voltage", "<i4", 31),
                  ("current", "<i4", 35), ("cc", "<i8", 43), ("dc", "<i8", 51),
                  ("ce", "<i8", 59), ("de", "<i8", 67), ("range", "<i4", 82)]
        date_offset = 75
    else:
        fields = [("index", "<u4", 2), ("cycle", "<u4", 6), ("step", "<u2", 10),
                  ("status", "u1", 12), ("time", "<u8", 14), ("voltage", "<i4", 22),
                  ("current", "<i4", 26), ("cc", "<i8", 38), ("dc", "<i8", 46),
                  ("ce", "<i8", 54), ("de", "<i8", 62), ("range", "<i4", 78)]
        date_offset = 70
    fields += [("year", "<u2", date_offset)]
    fields += [(name, "u1", date_offset + i) for i, name in enumerate(("month", "day", "hour", "minute", "second"), 2)]
    return _layout(size, fields)


def _aux_legacy(records, ndc=False):
    # Records are an array of bytes (N, record size).
    raw = np.ascontiguousarray(records)
    size = raw.shape[1]
    fields = [("Aux", "u1", 3 if ndc else 1), ("Index", "<u4", 8 if ndc else 2),
              ("V", "<i4", 31 if ndc else 22), ("T", "<i2", 41 if ndc else 34)]
    values = raw.view(_layout(size, fields)).reshape(-1)
    columns = {"Index": values["Index"], "Aux": values["Aux"], "V": values["V"] / 10000,
               "T": values["T"] / 10}
    if ndc:
        marker = raw[:, 0] if size == 94 else raw[:, 7]
        if np.any(marker == 0x74):
            temperatures = raw[:, 43:45].copy().view("<i2").reshape(-1) / 10
            temperatures[marker != 0x74] = np.nan
            columns["t"] = temperatures
    return columns


def _join(columns, extra, key="Index", forward=False):
    """Left join sorted keys without pandas; sparse observations stay missing."""
    if not len(extra.get(key, ())):
        return
    order = np.argsort(extra[key], kind="stable")
    keys = extra[key][order]
    position = np.searchsorted(keys, columns[key], side="right" if forward else "left")
    if forward:
        position -= 1
    valid = (position >= 0) & (position < len(keys))
    safe = np.clip(position, 0, len(keys) - 1)
    if not forward:
        valid &= keys[safe] == columns[key]
    for name, values in extra.items():
        if name == key:
            continue
        if values.dtype.kind == "M":
            target = np.full(len(position), "NaT", dtype=values.dtype)
        elif values.dtype.kind in "US":
            target = np.full(len(position), "", dtype=values.dtype)
        else:
            target = np.full(len(position), np.nan)
        target[valid] = values[order[safe[valid]]]
        columns[name] = target


def _join_aux(columns, aux):
    for channel in np.unique(aux.get("Aux", [1])):
        select = aux["Aux"] == channel if "Aux" in aux else slice(None)
        extra = {"Index": aux["Index"][select]}
        extra.update({f"{name}{int(channel)}": values[select] for name, values in aux.items() if name not in ("Index", "Aux")})
        _join(columns, extra)


def _units(columns):
    units = dict(UNITS)
    for name in columns:
        if re.fullmatch(r"[Tt]\d+", name):
            units[name] = "°C"
        elif re.fullmatch(r"V\d+", name):
            units[name] = "V"
    return units


def _nda_rows(raw, start, size, markers):
    count, remainder = divmod(len(raw) - start, size)
    tail = raw[start + count * size:]
    if remainder and any(tail.startswith(marker) for marker in markers):
        raise ReadError("Truncated NDA record")
    return np.frombuffer(raw, dtype="u1", offset=start, count=count * size).reshape(-1, size)


def read_nda(raw):
    if len(raw) < 1024 or not raw.startswith(b"NEWARE"):
        raise ReadError("Invalid Neware NDA header")
    version = raw[14]
    auxiliary = []
    if version == 29:
        start = raw.find(b"\0\0\0\0\x55\0")
        while start >= 0:
            candidate = start + 4
            if candidate + 86 <= len(raw) and raw[candidate + 12] and (candidate + 86 == len(raw) or raw[candidate + 86] in (0x55, 0x65)):
                break
            start = raw.find(b"\0\0\0\0\x55\0", start + 4)
        if start < 0:
            raise ReadError("No NDA records found")
        rows = _nda_rows(raw, start + 4, 86, (b"\x55", b"\x65"))
        valid = (rows[:, 0] == 0x55) & (rows[:, 1] == 0) & np.all(rows[:, 82:86] == 0, axis=1)
        records = np.ascontiguousarray(rows[valid]).view(_legacy_layout()).reshape(-1)
        records = records[(records["index"] != 0) & (records["status"] != 0)]
        columns = _legacy(records)
        aux = rows[(rows[:, 0] == 0x65) & np.all(rows[:, 82:86] == 0, axis=1)]
        if len(aux):
            auxiliary.append(_aux_legacy(aux))
    elif version == 130:
        start = 1024
        if raw[start] == 0x55:
            size = raw.find(raw[start:start + 2], start + 2) - start
            if size < 0 and len(raw) - start in (52, 56, 60, 64, 88):
                size = len(raw) - start
            if size not in (52, 56, 60, 64, 88):
                raise UnsupportedFormatError(f"Unsupported NDA 130 record length {size}")
            rows = _nda_rows(raw, start, size, (b"\x55",))
            rows = np.ascontiguousarray(rows[rows[:, 0] == 0x55])
            fields = [("Step", "u1", 2), ("status", "u1", 3), ("Index", "<u4", 8),
                      ("seconds", "<u4", 12), ("ns", "<u4", 16), ("Current(mA)", "<f4", 20),
                      ("Voltage", "<f4", 24), ("capacity", "<f4", 28), ("energy", "<f4", 32),
                      ("date", "<u4", 44), ("date_ns", "<u4", 48)]
            values = rows.view(_layout(size, fields)).reshape(-1)
            columns = {n: values[n] for n in ("Index", "Step", "Voltage", "Current(mA)")}
            columns.update(Status=_status(values["status"]),
                           Time=values["seconds"].astype(float) + values["ns"] * 1e-9,
                           Timestamp=values["date"].astype("datetime64[s]").astype("datetime64[ns]") + values["date_ns"].astype("timedelta64[ns]"))
            for name, field, sign in zip(COUNTERS, ("capacity", "capacity", "energy", "energy"), (1, -1, 1, -1)):
                columns[name] = np.maximum(sign * values[field], 0) / 3600
            if size == 56:
                columns["T1"] = rows[:, 52:56].copy().view("<f4").reshape(-1)
        else:
            size = 88
            rows = _nda_rows(raw, start, size, (raw[start:start + 6], b"\0\0\0\0\x65"))
            select = np.all(rows[:, :6] == np.frombuffer(raw[start:start + 6], dtype="u1"), axis=1)
            values = np.ascontiguousarray(rows[select, 4:]).view(_layout(84, [
                ("Step", "u1", 5), ("status", "u1", 6), ("Index", "<u4", 12),
                ("time", "<u8", 24), ("Voltage", "<f4", 32), ("Current(mA)", "<f4", 36),
                ("cc", "<f4", 48), ("ce", "<f4", 52), ("dc", "<f4", 56),
                ("de", "<f4", 60), ("date", "<u8", 64)])).reshape(-1)
            columns = {n: values[n] for n in ("Index", "Step", "Voltage", "Current(mA)")}
            columns.update(Status=_status(values["status"]),
                           Time=values["time"] / 1e6, Timestamp=values["date"].astype("datetime64[us]"))
            for name, field in zip(COUNTERS, ("cc", "dc", "ce", "de")):
                columns[name] = values[field] / 3600
            aux = rows[np.all(rows[:, :5] == [0, 0, 0, 0, 0x65], axis=1), 4:]
            if len(aux):
                auxiliary.append(_aux_legacy(aux))
    else:
        raise UnsupportedFormatError(f"NDA version {version} is unsupported")
    # Keep the first occurrence of each recorded index, in increasing order.
    _, indices = np.unique(columns["Index"], return_index=True)
    columns = {name: values[indices] for name, values in columns.items()}
    for aux in auxiliary:
        _join_aux(columns, aux)
    return Experiment((Dataset(columns, _units(columns), "Neware"),), "nda", {
        "instrument": "Neware", "version": version,
        "timestamp_timezone": "unspecified" if version == 29 else "UTC"})


def _paged(raw, dtype, begin=132, end=4):
    if len(raw) < 4096 or len(raw) % 4096:
        raise ReadError("Truncated NDC page")
    available = 4096 - begin - end
    if available % dtype.itemsize:
        raise ReadError("Invalid NDC record layout")
    n_pages = len(raw) // 4096 - 1
    return np.ndarray((n_pages, available // dtype.itemsize), dtype=dtype, buffer=raw,
                      offset=4096 + begin if n_pages else begin, strides=(4096, dtype.itemsize)).reshape(-1)


def _ndc(raw):
    if len(raw) < 3:
        raise ReadError("Truncated NDC header")
    kind, version = raw[0], raw[2]
    if version in (2, 5) and kind in (1, 5):
        if version == 2:
            if len(raw) < 611:
                raise ReadError("Truncated NDC header")
            marker = raw[517:525]
            positions = []
            position = raw.find(marker, 517)
            while position != -1:
                if position + 94 > len(raw):
                    raise ReadError("Truncated NDC record")
                positions.append(position)
                position = raw.find(marker, position + 94)
            rows = np.array([np.frombuffer(raw, "u1", 94, p) for p in positions], dtype="u1").reshape(-1, 94)
            size = 94
        else:
            size = 87
            rows = _paged(raw, np.dtype("V87"), 125, 56).view("u1").reshape(-1, 87)
        if kind == 1:
            select = rows[:, 0 if version == 2 else 7] == 0x55
            values = np.ascontiguousarray(rows[select]).view(_legacy_layout(size, True)).reshape(-1)
            return _legacy(values)
        markers = rows[:, 0 if version == 2 else 7]
        return _aux_legacy(rows[np.isin(markers, [0x65, 0x74])], True)
    if version not in (11, 14):
        raise UnsupportedFormatError(f"NDC version {version}, file type {kind} is unsupported")
    if kind == 1:
        values = _paged(raw, np.dtype([("v", "<f4"), ("i", "<f4")]))
        # Padding is at the end; preserve zero-valued measurements inside a run.
        nonzero = np.flatnonzero((values["v"] != 0) | (values["i"] != 0))
        values = values[:nonzero[-1] + 1] if len(nonzero) else values[:0]
        return {"Index": np.arange(1, len(values) + 1), "Voltage": values["v"] * (1e-4 if version == 11 else 1),
                "Current(mA)": values["i"] * (1 if version == 11 else 1000)}
    if kind == 7:
        dtype = _layout(37, [("Cycle", "<i4", 0), ("Step_Index", "<i4", 4), ("status", "u1", 24)])
        values = _paged(raw, dtype, end=5)
        values = values[values["Step_Index"] != 0]
        return {"Step": np.arange(1, len(values) + 1), "Cycle": values["Cycle"].astype("i8") + 1,
                "Step_Index": values["Step_Index"], "Status": _status(values["status"])}
    if kind == 18:
        dtype = _layout(55 if version == 14 else 47, [
            ("time", "<i4", 0), ("cc", "<f4", 5), ("dc", "<f4", 9),
            ("ce", "<f4", 13), ("de", "<f4", 17), ("date", "<i4", 33),
            ("Step", "<i4", 37), ("Index", "<i4", 41), ("ms", "<i2", 45)])
        values = _paged(raw, dtype, end=59 if version == 14 else 63)
        values = values[values["Index"] != 0]
        columns = {"Index": values["Index"], "Step": _changes(values["Step"]), "Time": values["time"] / 1000,
                   "Timestamp": values["date"].astype("datetime64[s]").astype("datetime64[ms]") + values["ms"].astype("timedelta64[ms]")}
        for name, field in zip(COUNTERS, ("cc", "dc", "ce", "de")):
            columns[name] = values[field] * (1000 if version == 14 else 1 / 3600)
        return columns
    if kind == 5:
        if version == 14:
            values = _paged(raw, np.dtype("<f4"))
            return {"Index": np.arange(1, len(values) + 1), "T": values}
        marker = raw[4096 + 132] if len(raw) > 4096 + 132 else 0
        if marker == 0x65:
            dtype = _layout(7, [("marker", "u1", 0), ("V", "<f4", 1), ("T", "<i2", 5)])
            values = _paged(raw, dtype, end=2)
            values = values[values["marker"] == marker]
            return {"Index": np.arange(1, len(values) + 1), "V": values["V"] / 10000, "T": values["T"] / 10}
        if marker == 0x74:
            dtype = _layout(87, [("marker", "u1", 0), ("Index", "<i4", 1), ("Aux", "u1", 5), ("T", "<i2", 35)])
            values = _paged(raw, dtype)
            values = values[values["marker"] == marker]
            return {"Index": values["Index"], "Aux": values["Aux"], "T": values["T"] / 10}
    raise UnsupportedFormatError(f"NDC version {version}, file type {kind} is unsupported")


def read_ndax(raw):
    metadata = {"instrument": "Neware", "missing_values": "NaN/NaT; no interpolation"}
    tables = []
    with ZipFile(BytesIO(raw)) as archive:
        names = archive.namelist()
        if "data.ndc" not in names:
            raise UnsupportedFormatError("NDAX archive has no data.ndc member")
        columns = _ndc(archive.read("data.ndc"))
        if "data_runInfo.ndc" in names and "data_step.ndc" in names:
            run = _ndc(archive.read("data_runInfo.ndc"))
            step = _ndc(archive.read("data_step.ndc"))
            # Preserve every supplemental observation, including duplicate
            # indices. The primary table keeps one row per sampled V/I pair.
            tables.extend((Dataset(run, UNITS, "run_info"), Dataset(step, name="steps")))
            _join(columns, {"Index": run["Index"], "Step": run["Step"]}, forward=True)
            _join(columns, {n: v for n, v in run.items() if n != "Step"})
            _join(columns, step, "Step")
        channels = {}
        if "TestInfo.xml" in names:
            root = ET.fromstring(archive.read("TestInfo.xml").decode("gb18030"))
            for node in root.iter():
                if "RealChlID" in node.attrib and "AuxID" in node.attrib:
                    channels[int(node.attrib["RealChlID"])] = int(node.attrib["AuxID"])
        for name in names:
            match = re.search(r"data_AUX_(\d+)_\d+_\d+\.ndc$", name)
            simple = re.search(r"_(\d+)\.ndc$", name)
            if match or simple:
                aux = _ndc(archive.read(name))
                channel = channels.get(int(match[1]), int(match[1])) if match else int(simple[1])
                aux["Aux"] = np.full(len(aux["Index"]), channel)
                _join_aux(columns, aux)
                tables.append(Dataset(aux, {"T": "°C", "V": "V", "t": "°C"}, f"aux_{channel}"))
        for name in ("VersionInfo.xml", "Step.xml", "TestInfo.xml"):
            if name in names:
                metadata[name] = archive.read(name).decode("gb18030")
    return Experiment((Dataset(columns, _units(columns), "Neware"), *tables), "ndax", metadata)
