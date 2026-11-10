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

# The types of the generated runtime that the bodies of the wrappers name: a member or a parameter
# called so would hide them there.
BODY_TYPES = frozenset(
    """
    Runtime Native Block StringBlock Lease Keep BridgefexObject BridgefexException ObjectHandle
    BridgefexCallbacks CallScope CallbackBox Thunks
    """.split()
)

# Parameters cannot have those names, nor those of the members that the bodies call.
PARAMETER_RESERVED = BODY_TYPES | frozenset("Handle Hold Retain System nameof".split())

# Members that every object of a generated class has: a wrapped method cannot take their names.
RESERVED_MEMBERS = BODY_TYPES | frozenset(
    """
    Dispose Close Equals GetHashCode GetType ToString Finalize MemberwiseClone ReferenceEquals
    IsClosed Handle Hold Owners ReadOnly Retained Origin CppType Retain Attach Surrender KeepOf
    ClaimToGive CollectOwning RetainForever Borrow
    """.split()
)

_WORDS = re.compile(r"[A-Za-z0-9]+")


def pascal(name: str) -> str:
    """``echo_int8`` becomes ``EchoInt8`` and ``liveInstances`` ``LiveInstances``: each part
    between underscores starts with a capital, and the rest of it is kept. In a name of several
    parts, a part in capitals of three letters or more is a word, not an acronym:
    ``LOW_LEVEL`` is ``LowLevel`` (``IO_ERROR`` is ``IOError``)."""
    parts = _WORDS.findall(name)
    words = [
        part[0] + part[1:].lower()
        if len(parts) > 1 and len(part) >= 3 and part.isupper()
        else part[0].upper() + part[1:]
        for part in parts
    ]
    result = "".join(words)
    if not result:
        return "_"
    return "_" + result if result[0].isdigit() else result


def identifier(name: str) -> str:
    """A C++ name as a C# identifier (``event`` becomes ``@event``)."""
    return f"@{name}" if name in CS_KEYWORDS else name


def parameter(name: str) -> str:
    """The C# name of a parameter: it must not hide what the body of the wrapper uses."""
    return identifier(f"{name}_" if name in PARAMETER_RESERVED else name)


def namespace_word(stem: str) -> str:
    """The segment of a namespace for a module or a header."""
    return pascal(stem)


def locals_of(count: int) -> frozenset[str]:
    """The locals of the bindings of a function with ``count`` parameters."""
    names = {
        f"{LOCAL_PREFIX}{word}"
        for word in (
            "result",
            "status",
            "self",
            "size",
            "text",
            "o",
            "count",
            "items",
            "i",
            "b",
            "scope",
            "call",
            "new",
        )
    }
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


# Namespaces and files of the generated project that a header cannot have: its namespace would hide
# a type or the namespace of .NET, and its file would replace one of the runtime.
HEADER_RESERVED = BODY_TYPES | frozenset(
    "Objects System Microsoft BridgefexFunction BridgefexFunctionAttribute".split()
)


def header_problem(stem: str) -> str | None:
    """Why a header called ``stem`` cannot have a C# namespace, or None."""
    word = pascal(stem)
    if word in HEADER_RESERVED:
        return f"its C# namespace and file '{word}' would clash with the generated code or .NET"
    return None


def names_of(headers: Iterable[str]) -> frozenset[str]:
    return frozenset(pascal(header) for header in headers)
