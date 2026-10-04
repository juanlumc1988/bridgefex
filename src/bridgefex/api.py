# SPDX-License-Identifier: Apache-2.0
"""High-level entry point: from header paths to generated files."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from . import naming
from .errors import ConfigurationError, Diagnostic, GenerationError
from .generator import LANGUAGES, render
from .model import Header, Module
from .parser import ParseOptions, parse_header
from .plan import build_plan

_STANDARD = re.compile(r"(c|gnu)\+\+[0-9a-z]+")


@dataclass(frozen=True, slots=True)
class Options:
    module: str
    """Module name: prefix of the runtime C symbols and name of the Python package."""

    std: str = "c++17"
    """C++ standard used to parse the headers."""

    include_dirs: tuple[Path, ...] = ()
    defines: tuple[str, ...] = ()
    clang_args: tuple[str, ...] = ()

    include_root: Path | None = None
    """Directory that the ``#include`` paths of the generated sources are relative
    to. By default each header is included by its file name."""

    languages: tuple[str, ...] = LANGUAGES


@dataclass(frozen=True, slots=True)
class Result:
    files: dict[str, str]
    """Generated files: relative POSIX path -> content."""

    warnings: tuple[Diagnostic, ...]
    """Warnings reported by libclang while parsing."""


def include_spelling(header: Path, include_root: Path | None) -> str:
    """How the generated C++ source includes ``header``."""
    if include_root is None:
        spelling = header.name
    else:
        try:
            spelling = header.resolve().relative_to(include_root.resolve()).as_posix()
        except ValueError:
            raise ConfigurationError(
                f"header {header} is not inside the include root {include_root}"
            ) from None
    if any(character in spelling for character in '"\\\n'):
        raise ConfigurationError(f"cannot write an #include for {header}")
    return spelling


def generate(headers: Sequence[Path], options: Options) -> Result:
    """Parse ``headers`` and generate the C layer and the requested bindings.

    libclang is loaded on first use; call :func:`bridgefex.libclang.load`
    first to choose the library.

    Raises:
        ConfigurationError: invalid options or missing headers.
        LibclangError: libclang cannot be used.
        GenerationError: the headers contain errors or unsupported constructs.
    """
    naming.check_module_name(options.module)
    if not headers:
        raise ConfigurationError("no input headers")
    if not _STANDARD.fullmatch(options.std):
        raise ConfigurationError(f"invalid C++ standard '{options.std}' (expected e.g. c++17)")
    unknown = sorted(set(options.languages) - set(LANGUAGES))
    if unknown:
        raise ConfigurationError(f"unknown languages: {', '.join(unknown)}")

    parse_options = ParseOptions(
        std=options.std,
        include_dirs=tuple(str(directory) for directory in options.include_dirs),
        defines=options.defines,
        extra_args=options.clang_args,
    )
    parsed: list[Header] = []
    warnings: list[Diagnostic] = []
    problems: list[Diagnostic] = []
    for header in headers:
        try:
            result = parse_header(
                header, include_spelling(header, options.include_root), parse_options
            )
        except GenerationError as error:
            problems.extend(error.diagnostics)
            continue
        parsed.append(result.header)
        warnings.extend(result.warnings)
    if problems:
        raise GenerationError(problems)

    plan = build_plan(Module(name=options.module, headers=tuple(parsed)))
    return Result(files=render(plan, options.languages), warnings=tuple(warnings))
