# SPDX-License-Identifier: Apache-2.0
"""The Rust names of the C++ declarations, and the names the generated crate defines."""

from __future__ import annotations

import re

LOCAL_PREFIX = "__"
"""The locals of the generated bodies start with this: no identifier of the C layer does (a name
with a double underscore is reserved in C and C++, and the plan refuses it), and the names of the
Rust API are made of single underscores."""

KEYWORDS = frozenset(
    """
    as async await break const continue crate dyn else enum extern false fn for if impl in let
    loop match mod move mut pub ref return self Self static struct super trait true type unsafe
    use where while abstract become box do final macro override priv typeof unsized virtual
    yield try gen union
    """.split()
)
"""The keywords of Rust (strict, reserved and weak ones that a name had better avoid)."""

# What the crate of the bindings defines at its root, and the modules that it has besides the one
# of each header: a header with one of these names would be one of them.
CRATE_MODULES = frozenset(
    {"native", "runtime", "lib", "main", "Error", "ErrorKind", "Owned", "Result", "Class"}
)

# The names that a crate cannot have: those of the crates that the compiler brings.
CRATE_NAMES = frozenset({"core", "std", "alloc", "test", "proc_macro", "crate", "self", "super"})

# The libraries that every program links (the ones that the standard library of Rust needs on the
# systems of GNU): a library of the module with one of these names, in the directory that the
# linker searches first, would be linked in place of the system's.
SYSTEM_LIBRARIES = frozenset({"c", "m", "dl", "rt", "pthread", "gcc_s"})

# The names of the constructors of the prelude that the generated bodies use. A type that is a
# value too (an enum is a tuple struct) with one of them would hide the constructor.
PRELUDE_VALUES = frozenset({"Ok", "Err", "Some", "None"})

_WORD = re.compile(r"[A-Z]+[0-9]*(?![a-z])|[A-Z]?[a-z]+[0-9]*|[0-9]+|[A-Z]")


def _words(name: str) -> list[str]:
    """The words of a C++ name: ``isZero`` is ``is``, ``Zero``; ``HTTPServer`` is ``HTTP``,
    ``Server``. A number stays with the word before it (``int8``, ``Vec3``), as in the names of
    Rust."""
    return _WORD.findall(name)


def snake(name: str) -> str:
    """The name of a function, method, parameter or field in snake case. Underscores at the
    start are kept (``_x`` is ``_x``), and a name made of nothing is ``_x``."""
    stripped = name.lstrip("_")
    lead = name[: len(name) - len(stripped)]
    words = [word.lower() for word in _words(stripped)]
    result = lead + "_".join(words)
    if not result.strip("_"):
        return "_x"
    return "_" + result if result[0].isdigit() else result


def upper_snake(name: str) -> str:
    """The name of a constant: ``Red`` is ``RED`` and ``kLowLevel`` is ``K_LOW_LEVEL``."""
    return snake(name).upper()


def pascal(name: str) -> str:
    """The name of a type: ``echo_int8`` is ``EchoInt8``, ``liveInstances`` is ``LiveInstances``,
    and ``HTTPServer`` stays. Each part between underscores starts with a capital."""
    parts = [part for part in name.split("_") if part]
    result = "".join(part[0].upper() + part[1:] for part in parts)
    if not result:
        return "X"
    return "X" + result if result[0].isdigit() else result


def identifier(name: str) -> str:
    """A name as a Rust identifier (``type`` becomes ``type_``)."""
    return f"{name}_" if name in KEYWORDS else name


def type_name(name: str) -> str:
    """The Rust name of a class, a struct or an enum (``Self`` and ``None`` get a trailing
    underscore, as the keywords do)."""
    word = pascal(name)
    return f"{word}_" if word in KEYWORDS or word in PRELUDE_VALUES else word


def parameter(name: str) -> str:
    """The Rust name of a parameter. It never starts with two underscores: that is how the locals
    of the generated bodies start, and a parameter would be hidden by one of them."""
    word = identifier(snake(name))
    if word.startswith(LOCAL_PREFIX):
        word = "_" + word.lstrip("_")
    return word


def module_problem(name: str) -> str | None:
    """Why the crate cannot be called ``name``, or None."""
    if name in CRATE_NAMES:
        return "it is the name of a crate that the compiler brings, or a path word of Rust"
    if name in KEYWORDS:
        return "it is a keyword of Rust"
    if name in SYSTEM_LIBRARIES:
        return (
            f"the program links the system library '{name}' (-l{name}), and would find the one "
            "of the module instead"
        )
    return None


def header_problem(stem: str) -> str | None:
    """Why a header called ``stem`` cannot have a module of the crate, or None."""
    word = identifier(snake(stem))
    if word in CRATE_MODULES:
        return f"its module '{word}' would be one that the generated crate defines"
    return None
