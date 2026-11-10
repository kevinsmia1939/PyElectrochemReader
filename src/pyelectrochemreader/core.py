"""Small, instrument-independent containers. Native column names are preserved."""

from dataclasses import dataclass, field
from typing import Any

import numpy as np


class ReadError(ValueError):
    """The file is malformed, truncated, or contains no readable records."""


class UnsupportedFormatError(ReadError):
    """The extension or a binary format variant is not implemented."""


# Aliases are resolved lazily: no copies of every column during loading.
ALIASES = {
    "time": [("time/s", 1), ("CalcTime", 1), ("Test_Time", 1), ("Time", 1), ("T", 1)],
    "voltage": [("Ewe/V", 1), ("<Ewe>/V", 1), ("Ecell/V", 1), ("<Ewe/V>", 1),
                ("EI_0.CalcPotential", 1), ("Voltage", 1), ("Vf", 1), ("Vdc", 1)],
    "current": [("I/A", 1), ("I/mA", .001), ("<I>/mA", .001),
                ("EI_0.CalcCurrent", 1), ("Current", 1), ("Current(mA)", .001),
                ("Im", 1), ("Idc", 1)],
    "frequency": [("freq/Hz", 1), ("Freq", 1)],
    "cycle": [("cycle number", 1), ("Cycle", 1), ("Cycle_Index", 1)],
    "step": [("Ns", 1), ("Step", 1), ("Step_Index", 1)],
    "charge_capacity": [("Q charge/mA.h", .001), ("Charge_Capacity(mAh)", .001),
                        ("Charge_Capacity", 1)],
    "discharge_capacity": [("Q discharge/mA.h", .001), ("Discharge_Capacity(mAh)", .001),
                           ("Discharge_Capacity", 1)],
}
ALIAS_UNITS = dict(time="s", voltage="V", current="A", frequency="Hz",
                   cycle="", step="", capacity="Ah", current_density="A/cm²",
                   charge_capacity="Ah", discharge_capacity="Ah")


def infer_units(names):
    """Infer units only where the native column name contains them."""
    result = {}
    for name in names:
        if name.endswith(")") and "(" in name:
            result[name] = name.rsplit("(", 1)[1][:-1]
        elif "/" in name and name != "ox/red":
            result[name] = name.rsplit("/", 1)[1]
        else:
            result[name] = ""
    return result


@dataclass
class Dataset:
    """Recorded columns for one table or command; ``len`` is the row count.

    Index by native name or an SI alias (``time``, ``voltage``, ``current``).
    NOVA commands can contain signals of different lengths; these are kept
    intact, and conversion to a rectangular table then raises ValueError.
    """

    columns: dict[str, np.ndarray]
    units: dict[str, str] = field(default_factory=dict)
    name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    aliases: dict[str, tuple[str, float]] = field(default_factory=dict)

    def __post_init__(self):
        self.columns = {key: np.asarray(value) for key, value in self.columns.items()}
        if any(value.ndim != 1 for value in self.columns.values()):
            raise ValueError("Dataset columns must be one-dimensional")
        self.units = {**infer_units(self.columns), **self.units}

    def _source(self, name):
        if name in self.columns:
            return name, 1
        if name in self.aliases:
            source, factor = self.aliases[name]
            if source in self.columns:
                return source, factor
        for source, factor in ALIASES.get(name, ()):
            if source in self.columns:
                return source, factor
        raise KeyError(name)

    def __getitem__(self, name):
        source, factor = self._source(name)
        values = self.columns[source]
        return values if factor == 1 else values * factor

    def __contains__(self, name):
        try:
            self._source(name)
        except KeyError:
            return False
        return True

    def __len__(self):
        return max((len(value) for value in self.columns.values()), default=0)

    @property
    def column_names(self):
        return tuple(self.columns)

    def unit(self, name):
        """Return a native column's unit, or the standard unit for an alias."""
        source, _ = self._source(name)
        return self.units[source] if name in self.columns else ALIAS_UNITS[name]

    def to_numpy(self, columns=None):
        """Copy selected columns into a structured NumPy array."""
        names = self.column_names if columns is None else tuple(columns)
        arrays = [self[name] for name in names]
        sizes = {len(array) for array in arrays}
        if len(sizes) > 1:
            raise ValueError("Signals have different lengths; select aligned columns")
        result = np.empty(next(iter(sizes), 0), dtype=[(n, a.dtype) for n, a in zip(names, arrays)])
        for name, array in zip(names, arrays):
            result[name] = array
        return result


@dataclass
class Experiment:
    """A file's datasets in recorded order, plus instrument metadata."""

    datasets: tuple[Dataset, ...]
    format: str
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str | None = None

    def __getitem__(self, index):
        return self.datasets[index]

    def __iter__(self):
        return iter(self.datasets)

    def __len__(self):
        return len(self.datasets)


@dataclass
class Configuration:
    """Settings or graph configuration, represented as source text and tables.

    ``print(configuration)`` displays the decoded source text. Indexing and
    iteration access settings tables, not recorded measurement signals.
    """

    text: str
    tables: tuple[Dataset, ...]
    format: str
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str | None = None

    def __getitem__(self, index):
        return self.tables[index]

    def __iter__(self):
        return iter(self.tables)

    def __len__(self):
        return len(self.tables)

    def __str__(self):
        return self.text
