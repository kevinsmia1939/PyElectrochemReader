"""Public dispatcher; readers accept bytes and never modify the source."""

from pathlib import Path
import struct
import zipfile
from xml.etree.ElementTree import ParseError

from .core import ReadError, UnsupportedFormatError

supported_formats = (".nox", ".mpr", ".mpt", ".dta", ".nda", ".ndax", ".res")


def read(source, *, format=None, encoding=None):
    """Read a path or binary stream into an :class:`Experiment`.

    Extensions are case insensitive. Use ``format='mpr'`` for an unnamed
    stream or a file with a different extension. ``encoding`` overrides text
    decoding (by default UTF-8, falling back to Windows-1252).
    Streams are read from their current position and are left open.
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
        elif suffix == ".nox":
            from .nova import read_nox
            result = read_nox(raw)
        elif suffix in (".nda", ".ndax"):
            from .neware import read_nda, read_ndax
            result = (read_nda if suffix == ".nda" else read_ndax)(raw)
        else:
            from .arbin import read_res
            result = read_res(raw)
    except ReadError:
        raise
    except (ValueError, IndexError, KeyError, StopIteration, struct.error,
            EOFError, UnicodeError, zipfile.BadZipFile, ParseError) as exc:
        raise ReadError(f"Invalid {suffix} file: {exc}") from exc
    if not result.datasets or not any(len(dataset) for dataset in result):
        raise ReadError(f"No measurement records in {suffix} file")
    result.source = name
    return result
