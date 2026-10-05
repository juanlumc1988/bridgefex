# SPDX-License-Identifier: Apache-2.0
"""High-level entry point: from header paths to generated files."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from . import naming
from .errors import ConfigurationError, Diagnostic, GenerationError
from .generator import LANGUAGES, render
from .model import Header, Module
from .parser import ParseOptions, parse_header, system_names
from .plan import build_plan
from .verify import verify

_STANDARD = re.compile(r"(c|gnu)\+\+[0-9a-z]+")
_BEFORE_CXX11 = re.compile(r"(c|gnu)\+\+(98|03)")


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

    verify: bool = True
    """Compile the generated C layer with libclang before returning it (see
    :mod:`bridgefex.verify`)."""


@dataclass(frozen=True, slots=True)
class Result:
    files: dict[str, str]
    """Generated files: relative POSIX path -> content."""

    warnings: tuple[Diagnostic, ...]
    """Warnings reported by libclang while parsing, and warnings that the
    generated code triggers."""


def _relative_spelling(header: Path, include_root: Path) -> str | None:
    # Lexically first: in include trees made of symbolic links, the real file
    # of a header is often somewhere else.
    path, root = Path(os.path.abspath(header)), Path(os.path.abspath(include_root))
    if not path.is_relative_to(root):
        path, root = header.resolve(), include_root.resolve()
        if not path.is_relative_to(root):
            return None
    return path.relative_to(root).as_posix()


def include_spelling(header: Path, include_root: Path | None) -> str:
    """How the generated C++ source includes ``header``."""
    spelling = header.name if include_root is None else _relative_spelling(header, include_root)
    if spelling is None:
        raise ConfigurationError(f"header {header} is not inside the include root {include_root}")
    # The spelling goes into an #include line and into a /* */ comment.
    unsafe = any(
        character in '"\\' or ord(character) < 0x20 or ord(character) == 0x7F
        for character in spelling
    )
    if unsafe or "*/" in spelling:
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
    if _BEFORE_CXX11.fullmatch(options.std):
        raise ConfigurationError(
            f"the generated C++ code needs C++11 or later, not {options.std}; "
            "use --std=c++11 or newer"
        )
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
    include_dirs: dict[str, Path] = {}
    for header in headers:
        include_dirs[header.stem] = (
            options.include_root if options.include_root is not None else header.parent
        ).resolve()
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

    module = Module(name=options.module, headers=tuple(parsed), system_names=system_names())
    plan = build_plan(module)
    files = render(plan, options.languages)
    if options.verify:
        warnings.extend(verify(plan, files, include_dirs, parse_options))
    return Result(files=files, warnings=tuple(warnings))
