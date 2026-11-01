# SPDX-License-Identifier: Apache-2.0
"""The annotations file: who owns the objects that cross the C boundary.

C++ does not say it. A function that returns ``Node*`` may return a new object
that the caller deletes, or one that its class keeps owning; a function that takes
``Node*`` may keep the pointer. The file says it, one table per generated C
function (the name that the generated C header prints)::

    [demo_Tree_find_int32]
    return = "borrowed"           # the result of a raw pointer to a wrapped class

    [demo_Pool_add]
    item = "transfer"             # a parameter, by its C++ name...
    2 = ["nullable", "retain"]    # ...or by its position, counted from 1

Everything is checked: an unknown function (an overload that was added renames the
C functions of the others), parameter or word, a word that does not apply to the
type, or two that exclude each other, is an error.
"""

from __future__ import annotations

import difflib
import re
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .errors import ConfigurationError, Diagnostic, Location

RESULT_WORDS = frozenset({"owned", "borrowed", "bytes"})
PARAMETER_WORDS = frozenset({"nullable", "transfer", "retain", "call"})

_POSITION = re.compile(r"[1-9][0-9]*")
# Python 3.12 only puts the position of a TOML error in its message.
_AT = re.compile(r"^(?P<message>.*) \(at line (?P<line>\d+), column (?P<column>\d+)\)$")


@dataclass(frozen=True, slots=True)
class FunctionAnnotations:
    """What a file says about one generated C function."""

    source: str
    """The file, for the messages."""

    line: int = 0
    """Where its table starts in the file."""

    result: frozenset[str] = frozenset()
    parameters: Mapping[str, tuple[frozenset[str], int]] = field(default_factory=dict)
    """By C++ name or position, as written: the words and the line."""

    result_line: int = 0

    def where(self, line: int = 0) -> str:
        line = line or self.line
        return f"{self.source}:{line}" if line else self.source


def _line_of(lines: Sequence[str], pattern: str, start: int = 0, stop: int | None = None) -> int:
    """The 1-based number of the first line from ``start`` that matches, else 0."""
    compiled = re.compile(pattern)
    for number in range(start, len(lines) if stop is None else stop):
        if compiled.match(lines[number]):
            return number + 1
    return 0


def _words(
    value: object, allowed: frozenset[str], where: str, problems: list[str]
) -> frozenset[str]:
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not items or not all(isinstance(i, str) for i in items):
        problems.append(f"{where}: expected a word or a non-empty list of words")
        return frozenset()
    if len(set(items)) != len(items):
        problems.append(f"{where}: a word is repeated")
    unknown = sorted(set(items) - allowed)
    if unknown:
        problems.append(
            f"{where}: unknown annotation {', '.join(map(repr, unknown))} "
            f"(expected: {', '.join(sorted(allowed))})"
        )
    return frozenset(items) & allowed


def load(paths: Sequence[Path]) -> dict[str, FunctionAnnotations]:
    """Read the annotation files and check their syntax.

    Raises:
        ConfigurationError: a file that cannot be read or is not valid, or the
            same function annotated twice.
    """
    table: dict[str, FunctionAnnotations] = {}
    problems: list[str] = []
    for path in paths:
        try:
            text = path.read_bytes().decode("utf-8")
            data = tomllib.loads(text)
        except OSError as error:
            raise ConfigurationError(f"cannot read the annotations file {path}: {error}") from error
        except UnicodeDecodeError as error:
            raise ConfigurationError(f"{path}: not UTF-8 text ({error})") from error
        except tomllib.TOMLDecodeError as error:
            match = _AT.match(str(error))
            where = f"{path}:{match['line']}:{match['column']}" if match else str(path)
            message = match["message"] if match else str(error)
            raise ConfigurationError(f"{where}: invalid TOML: {message}") from error
        lines = text.splitlines()
        headers = [
            (
                name,
                _line_of(lines, rf'\s*\[\s*"?{re.escape(name)}"?\s*\]')
                or _line_of(lines, rf'\s*"?{re.escape(name)}"?\s*='),
            )
            for name in data
        ]
        for index, (function, header) in enumerate(headers):
            entries = data[function]
            at = f"{path}:{header}" if header else str(path)
            if not isinstance(entries, dict):
                problems.append(
                    f'{at}: expected a table, e.g. [{function}] followed by return = "owned"'
                )
                continue
            if function in table:
                problems.append(
                    f"{at}: [{function}] is also annotated in {table[function].where()}"
                )
                continue
            stop = next((line for _, line in headers[index + 1 :] if line), None)
            result: frozenset[str] = frozenset()
            result_line = 0
            parameters: dict[str, tuple[frozenset[str], int]] = {}
            for key, value in entries.items():
                line = _line_of(lines, rf'\s*"?{re.escape(key)}"?\s*=', header, stop and stop - 1)
                here = f"{path}:{line or header}: [{function}]"
                if key == "return":
                    result = _words(value, RESULT_WORDS, f"{here} return", problems)
                    result_line = line
                else:
                    parameters[key] = (
                        _words(value, PARAMETER_WORDS, f"{here} {key}", problems),
                        line,
                    )
            table[function] = FunctionAnnotations(
                str(path), header, result, parameters, result_line
            )
    if problems:
        raise ConfigurationError("\n".join(problems))
    return table


class Kind(Enum):
    """What the plan knows of a parameter or a result."""

    OTHER = "value"
    OBJECT_REFERENCE = "reference to a wrapped class"
    OBJECT_VALUE = "wrapped class by value"
    OBJECT_POINTER = "pointer to a wrapped class"
    OBJECT_CONST_POINTER = "pointer to a const wrapped class"
    UNIQUE_PTR = "std::unique_ptr"
    C_STRING = "const char*"
    STRING = "string"
    CALLBACK = "callback"


@dataclass(frozen=True, slots=True)
class Param:
    name: str
    """The C++ name; empty if the parameter is unnamed."""

    kind: Kind


@dataclass(frozen=True, slots=True)
class Resolved:
    """The policy of one function: what the file says and the rules that follow
    from the types. It is printed in the C header, so it is part of the fingerprint."""

    result: str | None
    """``owned`` or ``borrowed`` for a result that is a wrapped class, else None."""

    result_bytes: bool
    parameters: tuple[frozenset[str], ...]
    """Per parameter: ``nullable``, ``transfer``, ``retain``, ``call``."""


_POINTERS = frozenset({Kind.OBJECT_POINTER, Kind.OBJECT_CONST_POINTER})
_OBJECTS = frozenset({Kind.OBJECT_REFERENCE}) | _POINTERS

_PARAMETER_RULES: dict[str, frozenset[Kind]] = {
    "nullable": _POINTERS | {Kind.UNIQUE_PTR, Kind.C_STRING},
    "transfer": frozenset({Kind.OBJECT_POINTER}),
    "retain": _OBJECTS,
    "call": _OBJECTS,
}
_EXCLUSIVE = (
    ("transfer", "retain"),
    ("transfer", "call"),
    ("retain", "call"),
)
_IMPLIED_RESULT = {
    Kind.OBJECT_REFERENCE: "borrowed",
    Kind.OBJECT_VALUE: "owned",
    Kind.UNIQUE_PTR: "owned",
}
_HINTS = {
    ("transfer", Kind.UNIQUE_PTR): "a std::unique_ptr always transfers the ownership",
    ("transfer", Kind.OBJECT_CONST_POINTER): "an object that is taken cannot be const",
    ("nullable", Kind.OBJECT_REFERENCE): "a reference is never NULL",
    ("nullable", Kind.CALLBACK): "a callback may always be None (an empty std::function)",
    ("retain", Kind.CALLBACK): "a callback lives as long as the C++ code holds it",
}


def resolve(
    c_name: str,
    what: str,
    params: Sequence[Param],
    result: Kind | None,
    *,
    constructor: bool,
    annotations: Mapping[str, FunctionAnnotations],
    location: Location | None,
) -> tuple[Resolved, list[Diagnostic]]:
    """The policy of one generated C function.

    ``what`` names the function in the messages.
    """
    errors: list[Diagnostic] = []
    entry = annotations.get(c_name)

    def error(message: str, line: int = 0) -> None:
        source = f" ({entry.where(line)})" if entry is not None else ""
        errors.append(Diagnostic(f"{what}: {message}{source}", location))

    words: list[frozenset[str]] = [frozenset()] * len(params)
    lines = [0] * len(params)
    if entry is not None:
        by_name = {param.name: index for index, param in enumerate(params) if param.name}
        for key, (given, line) in entry.parameters.items():
            index = by_name.get(key)
            if index is None and _POSITION.fullmatch(key) and int(key) <= len(params):
                index = int(key) - 1
            if index is None:
                names = ", ".join(p.name or f"#{i + 1}" for i, p in enumerate(params)) or "none"
                error(f"there is no parameter '{key}' (parameters: {names})", line)
                continue
            if words[index]:
                error(f"parameter '{key}' is annotated twice (by name and by position)", line)
            words[index] = given
            lines[index] = line
    resolved: list[frozenset[str]] = []
    for index, (param, given) in enumerate(zip(params, words, strict=True)):
        label = f"parameter '{param.name or index + 1}'"
        for word in sorted(given):
            if param.kind not in _PARAMETER_RULES[word]:
                hint = _HINTS.get((word, param.kind))
                note = f" ({hint})" if hint else ""
                error(
                    f"{label}: '{word}' does not apply to a {param.kind.value}{note}", lines[index]
                )
        for first, second in _EXCLUSIVE:
            if {first, second} <= given:
                error(f"{label}: '{first}' and '{second}' exclude each other", lines[index])
        effective = set(given)
        if param.kind is Kind.UNIQUE_PTR:
            effective.add("transfer")
        if constructor and param.kind in _OBJECTS and not effective & {"call", "transfer"}:
            # A constructor often keeps what it is given.
            effective.add("retain")
        resolved.append(frozenset(effective))

    policy, result_bytes = _resolve_result(c_name, result, entry, error)
    return Resolved(policy, result_bytes, tuple(resolved)), errors


def _resolve_result(
    c_name: str,
    result: Kind | None,
    entry: FunctionAnnotations | None,
    error: Callable[[str, int], None],
) -> tuple[str | None, bool]:
    """Whether the caller owns the result (``owned``), and whether it is returned
    as bytes."""
    given = entry.result if entry is not None else frozenset()
    line = entry.result_line if entry is not None else 0
    ownership = given & {"owned", "borrowed"}
    policy: str | None = None
    if len(ownership) > 1:
        error("'owned' and 'borrowed' exclude each other", line)
    implied = _IMPLIED_RESULT.get(result) if result is not None else None
    if result in _POINTERS:
        if not ownership:
            error(
                "returns a raw pointer to a wrapped class: say who owns it with "
                f'[{c_name}] return = "owned" (the caller deletes it) or "borrowed" '
                "in the annotations file",
                0,
            )
        elif "owned" in ownership and result is Kind.OBJECT_CONST_POINTER:
            error("an owned result cannot point to const", line)
        else:
            policy = min(ownership)
    elif ownership:
        if implied is None:
            error(
                f"'{min(ownership)}' only applies to a result that is a raw pointer to a "
                "wrapped class",
                line,
            )
        else:
            error(f"the result is always {implied}: remove the annotation", line)
    else:
        policy = implied
    result_bytes = "bytes" in given
    if result_bytes and result not in (Kind.STRING, Kind.C_STRING):
        error("'bytes' only applies to a string result", line)
    return policy, result_bytes


def unknown_functions(
    annotations: Mapping[str, FunctionAnnotations], generated: Sequence[str]
) -> list[Diagnostic]:
    """The annotated names that no generated C function has."""
    known = set(generated)
    problems = []
    for name, entry in annotations.items():
        if name in known:
            continue
        close = difflib.get_close_matches(name, generated, n=3)
        hint = f"; did you mean {', '.join(close)}?" if close else ""
        problems.append(
            Diagnostic(
                f"{entry.where()}: [{name}] is not a generated C function that can be annotated"
                f"{hint} (adding an overload renames the C functions of the others)"
            )
        )
    return problems
