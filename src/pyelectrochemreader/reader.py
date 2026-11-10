"""Public dispatcher; readers accept bytes and never modify the source."""

from pathlib import Path
import struct
import zipfile
from xml.etree.ElementTree import ParseError

from .core import Configuration, ReadError, UnsupportedFormatError

supported_formats = (".nox", ".mpr", ".mpt", ".dta", ".nda", ".ndax", ".res",
                     ".par", ".cor", ".csv", ".txt", ".tsv", ".xlsx", ".xls", ".ods",
                     ".mps", ".mgr")


def read(source, *, format=None, encoding=None, delimiter=None, header=None):
    """Read a path or binary stream into an Experiment or Configuration.

    Extensions are case insensitive. Use ``format='mpr'`` for an unnamed
    stream or a file with a different extension. ``encoding`` overrides text
    decoding (by default UTF-8, falling back to Windows-1252).
    Streams are read from their current position and are left open.
    For CSV/TXT/TSV, ``delimiter`` overrides separator detection and ``header``
    explicitly enables/disables a column-name row. Both default to detection.
    MPS/MGR files return Configuration objects with source text and tables.
    """
    name = None
    if isinstance(source, (str, Path)) or hasattr(source, "__fspath__"):
        path = Path(source)
        name = str(path)
        suffix = path.suffix.lower()
    else:
        name = getattr(source, "name", None)
        name = str(name) if isinstance(name, (str, Path)) else None
        suffix = Path(name).suffix.lower() if name else ""
    suffix = "." + format.lower().lstrip(".") if format else suffix
    if suffix not in supported_formats:
        raise UnsupportedFormatError(f"Unsupported format {suffix!r}; expected {', '.join(supported_formats)}")
    if (delimiter is not None or header is not None) and suffix not in (".csv", ".txt", ".tsv"):
        raise TypeError("delimiter and header are only supported for CSV/TXT/TSV")
    if header is not None and not isinstance(header, bool):
        raise TypeError("header must be True, False, or None")
    if isinstance(source, (str, Path)) or hasattr(source, "__fspath__"):
        raw = path.read_bytes()
    else:
        raw = source.read()
    if not isinstance(raw, bytes):
        raise TypeError("Use a path or a stream opened in binary mode")
    if not raw:
        raise ReadError("File is empty")

    try:
        if suffix in (".mpt", ".dta"):
            from .text import read_mpt, read_dta
            result = (read_mpt if suffix == ".mpt" else read_dta)(raw, encoding)
        elif suffix == ".mpr":
            from .biologic import read_mpr
            result = read_mpr(raw)
        elif suffix in (".mps", ".mgr"):
            from .biologic_settings import read_mps, read_mgr
            result = (read_mps if suffix == ".mps" else read_mgr)(raw, encoding)
        elif suffix == ".nox":
            from .nova import read_nox
            result = read_nox(raw)
        elif suffix in (".nda", ".ndax"):
            from .neware import read_nda, read_ndax
            result = (read_nda if suffix == ".nda" else read_ndax)(raw)
        elif suffix in (".par", ".cor"):
            from .versastudio import read_par, read_cor
            result = (read_par if suffix == ".par" else read_cor)(raw, encoding)
        elif suffix in (".csv", ".txt", ".tsv"):
            from .versastudio import read_delimited
            result = read_delimited(raw, suffix[1:], encoding, delimiter, header)
        elif suffix in (".xlsx", ".xls", ".ods"):
            from .spreadsheets import read_spreadsheet
            result = read_spreadsheet(raw, suffix[1:])
        else:
            from .arbin import read_res
            result = read_res(raw)
    except ReadError:
        raise
    except (ValueError, IndexError, KeyError, StopIteration, struct.error,
            EOFError, UnicodeError, zipfile.BadZipFile, ParseError) as exc:
        raise ReadError(f"Invalid {suffix} file: {exc}") from exc
    if not isinstance(result, Configuration) and (not result.datasets or not any(len(dataset) for dataset in result)):
        raise ReadError(f"No measurement records in {suffix} file")
    result.source = name
    return result
