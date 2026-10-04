# SPDX-License-Identifier: Apache-2.0
"""Compile the generated code with libclang before anything is written.

The parser rejects every construct it knows to be unsupported, but some C++
rules (overload resolution with default arguments, deleted special members,
consteval, access to operator new...) are only checked by a compiler. So the
generated C++ sources are compiled against the user's headers, and the
generated C headers as C, with libclang. An error becomes a bridgefex error
that points at the wrapped declaration, and a warning in the generated code is
reported, instead of the user finding them when building.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from clang import cindex

from . import libclang
from .errors import Diagnostic, GenerationError, Location
from .generator import write_files
from .parser import ParseOptions
from .plan import CFunction, HeaderPlan, ModulePlan


def verify(
    plan: ModulePlan,
    files: Mapping[str, str],
    include_dirs: Mapping[str, Path],
    options: ParseOptions,
) -> tuple[Diagnostic, ...]:
    """Compile the C layer in ``files`` with libclang.

    ``include_dirs`` maps each header stem to the directory its generated
    source needs on the include path. Returns warnings found in generated code.

    Raises:
        GenerationError: the generated code does not compile.
    """
    libclang.load()
    c_files = {path: content for path, content in files.items() if path.startswith("c/")}
    errors: list[Diagnostic] = []
    warnings: list[Diagnostic] = []
    with tempfile.TemporaryDirectory(prefix="bridgefex-", ignore_cleanup_errors=True) as temp:
        root = Path(temp)
        write_files(c_files, root)
        generated = root / "c"
        index = cindex.Index.create()
        for header in plan.headers:
            source = generated / header.c_source
            args = [
                "-x",
                "c++",
                f"-std={options.std}",
                "-Wall",
                "-Wextra",
                f"-I{generated}",
                f"-I{include_dirs[header.stem]}",
                *(f"-I{directory}" for directory in options.include_dirs),
                *(f"-D{definition}" for definition in options.defines),
                *options.extra_args,
            ]
            unit = index.parse(str(source), args=args)
            functions = _function_lines(plan, header, c_files[f"c/{header.c_source}"])
            for diagnostic in unit.diagnostics:
                _report(
                    diagnostic,
                    root=root,
                    source=source,
                    functions=functions,
                    errors=errors,
                    warnings=warnings,
                )
            del unit

        consumer = root / "consumer.c"
        consumer.write_text(
            "".join(f'#include "{header.c_header}"\n' for header in plan.headers),
            encoding="utf-8",
        )
        args = ["-x", "c", "-std=c2x", f"-I{generated}"]
        unit = index.parse(str(consumer), args=args)
        for diagnostic in unit.diagnostics:
            if diagnostic.severity >= cindex.Diagnostic.Error:
                errors.append(
                    Diagnostic(
                        "the generated C headers do not compile as C: "
                        f"{_describe(diagnostic, root)}"
                    )
                )
        del unit
    if errors:
        raise GenerationError(errors)
    return tuple(warnings)


def _function_lines(
    plan: ModulePlan, header: HeaderPlan, source: str
) -> list[tuple[int, CFunction]]:
    """Line of the definition of every wrapper in a generated source, in order."""
    definitions = {
        f"{plan.status_type if function.returns_status else 'void'} {function.signature}": function
        for function in header.c_functions
    }
    return [
        (number, definitions[line])
        for number, line in enumerate(source.splitlines(), start=1)
        if line in definitions
    ]


def _describe(diagnostic: cindex.Diagnostic, root: Path) -> str:
    location = diagnostic.location
    if location.file is None:
        return str(diagnostic.spelling)
    path = Path(str(location.file.name))
    try:
        shown = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        shown = str(path)
    return f"{shown}:{location.line}:{location.column}: {diagnostic.spelling}"


def _wrapper_at(
    location: cindex.SourceLocation, source: Path, functions: Sequence[tuple[int, CFunction]]
) -> CFunction | None:
    """The wrapper whose definition contains ``location``, if it is in ``source``."""
    if location.file is None or Path(str(location.file.name)).resolve() != source.resolve():
        return None
    wrapper = None
    for line, function in functions:
        if line <= location.line:
            wrapper = function
    return wrapper


def _report(
    diagnostic: cindex.Diagnostic,
    *,
    root: Path,
    source: Path,
    functions: Sequence[tuple[int, CFunction]],
    errors: list[Diagnostic],
    warnings: list[Diagnostic],
) -> None:
    severity = diagnostic.severity
    if severity < cindex.Diagnostic.Warning:
        return
    location = diagnostic.location
    file = Path(str(location.file.name)).resolve() if location.file is not None else None
    in_generated = file is not None and file.is_relative_to(root.resolve())
    if severity < cindex.Diagnostic.Error and not in_generated:
        return  # warnings of the user's own headers were reported by the parser
    kind = "does not compile" if severity >= cindex.Diagnostic.Error else "triggers a warning"
    wrapper = _wrapper_at(location, source, functions)
    if wrapper is None:
        # An error inside a generated helper (the exception guard, for
        # example) has notes that point back at the wrapper that used it.
        for note in diagnostic.children:
            wrapper = _wrapper_at(note.location, source, functions)
            if wrapper is not None:
                break
    if wrapper is not None:
        message = (
            f"the generated wrapper {wrapper.name}() for '{wrapper.description}' {kind}: "
            f"{diagnostic.spelling}"
        )
        diagnostic_location: Location | None = wrapper.location
    else:
        message = f"the generated code {kind}: {_describe(diagnostic, root)}"
        diagnostic_location = None
    target = errors if severity >= cindex.Diagnostic.Error else warnings
    target.append(Diagnostic(message, diagnostic_location))
