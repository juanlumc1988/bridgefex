# SPDX-License-Identifier: Apache-2.0
"""The C# names of the C++ declarations, and the names the generated code defines."""

from __future__ import annotations

import re
from collections.abc import Iterable

CS_KEYWORDS = frozenset(
    """
    abstract as base bool break byte case catch char checked class const continue decimal
    default delegate do double else enum event explicit extern false finally fixed float for
    foreach goto if implicit in int interface internal is lock long namespace new null object
    operator out override params private protected public readonly ref return sbyte sealed
    short sizeof stackalloc static string struct switch this throw true try typeof uint ulong
    unchecked unsafe ushort using virtual void volatile while
    """.split()
)
"""The reserved words of C#: an identifier of that name is written with a leading ``@``."""

# The locals of the generated bodies. No parameter takes them: the C layer renames a
# parameter of the C++ declaration that is called so.
LOCAL_PREFIX = "__"

# Members that every object of a generated class has: a wrapped method cannot take their names.
RESERVED_MEMBERS = frozenset(
    """
    Dispose Close Equals GetHashCode GetType ToString Finalize MemberwiseClone ReferenceEquals
    IsClosed Handle Hold Owners ReadOnly Retained Origin CppType Retain Attach Surrender KeepOf
    ClaimToGive CollectOwning RetainForever Borrow
    """.split()
)

_WORDS = re.compile(r"[A-Za-z0-9]+")


def pascal(name: str) -> str:
    """``echo_int8`` becomes ``EchoInt8`` and ``liveInstances`` ``LiveInstances``: each
    part between underscores starts with a capital, and the rest of it is kept."""
    parts = _WORDS.findall(name)
    result = "".join(part[0].upper() + part[1:] for part in parts)
    if not result:
        return "_"
    return "_" + result if result[0].isdigit() else result


def identifier(name: str) -> str:
    """A C++ name as a C# identifier (``event`` becomes ``@event``)."""
    return f"@{name}" if name in CS_KEYWORDS else name


def namespace_word(stem: str) -> str:
    """The segment of a namespace for a module or a header."""
    return pascal(stem)


def locals_of(count: int) -> frozenset[str]:
    """The locals of the bindings of a function with ``count`` parameters."""
    names = {f"{LOCAL_PREFIX}{word}" for word in ("result", "status", "self", "size", "text")}
    for position in range(1, 3 * count + 5):
        names.update(
            f"{LOCAL_PREFIX}{word}{position}" for word in ("h", "b", "p", "n", "s", "o", "t")
        )
    return frozenset(names)


def module_problem(name: str) -> str | None:
    """Why the C# namespace cannot be named for the module ``name``, or None."""
    word = pascal(name)
    if word in {"System", "Microsoft", "Windows"}:
        return f"the C# namespace '{word}' would hide the one of .NET"
    return None


def header_problem(stem: str) -> str | None:
    """Why a header called ``stem`` cannot have a C# namespace, or None."""
    word = pascal(stem)
    if word in {"Runtime", "Native"}:
        return f"its C# namespace would hide the '{word}' class of the generated code"
    return None


def names_of(headers: Iterable[str]) -> frozenset[str]:
    return frozenset(pascal(header) for header in headers)
