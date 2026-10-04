# SPDX-License-Identifier: Apache-2.0
"""Error types reported by bridgefex."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Location:
    """A position in an input file (1-based line and column)."""

    file: str
    line: int
    column: int

    def __str__(self) -> str:
        return f"{self.file}:{self.line}:{self.column}"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """A problem found while reading the input headers."""

    message: str
    location: Location | None = None

    def __str__(self) -> str:
        if self.location is None:
            return self.message
        return f"{self.location}: {self.message}"


class BridgefexError(Exception):
    """Base class of every error raised by bridgefex."""


class ConfigurationError(BridgefexError):
    """Invalid options, such as a bad module name or a missing input file."""


class LibclangError(BridgefexError):
    """libclang cannot be found or loaded, or its version is not supported."""


class GenerationError(BridgefexError):
    """The input cannot be translated. Carries every problem that was found."""

    def __init__(self, diagnostics: Iterable[Diagnostic]) -> None:
        self.diagnostics = tuple(diagnostics)
        if not self.diagnostics:
            raise ValueError("GenerationError needs at least one diagnostic")
        super().__init__("\n".join(str(diagnostic) for diagnostic in self.diagnostics))
