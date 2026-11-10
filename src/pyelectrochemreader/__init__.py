"""Read electrochemical files using only Python and NumPy."""

from .core import Configuration, Dataset, Experiment, ReadError, UnsupportedFormatError
from .reader import read, supported_formats

__version__ = "0.3.0"
__all__ = ["read", "supported_formats", "Dataset", "Experiment", "Configuration", "ReadError",
           "UnsupportedFormatError"]
