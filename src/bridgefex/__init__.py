# SPDX-License-Identifier: Apache-2.0
"""bridgefex: generate a pure C API from C++ headers, plus bindings for other languages."""

__version__ = "0.1.0"

from .api import Options, Result, generate
from .errors import (
    BridgefexError,
    ConfigurationError,
    Diagnostic,
    GenerationError,
    LibclangError,
    Location,
)
from .generator import LANGUAGES, write_files

__all__ = [
    "LANGUAGES",
    "BridgefexError",
    "ConfigurationError",
    "Diagnostic",
    "GenerationError",
    "LibclangError",
    "Location",
    "Options",
    "Result",
    "__version__",
    "generate",
    "write_files",
]
