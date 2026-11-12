# SPDX-License-Identifier: Apache-2.0
"""Names in the Java bindings: identifiers, packages and what the generated code reserves."""

from __future__ import annotations

import re
from collections.abc import Iterable

LOCAL_PREFIX = "__"
"""The locals of the generated bodies start with this: a parameter cannot take their names."""

JAVA_KEYWORDS = frozenset(
    """
    abstract assert boolean break byte case catch char class const continue default do double
    else enum extends final finally float for goto if implements import instanceof int interface
    long native new package private protected public return short static strictfp super switch
    synchronized this throw throws transient try void volatile while true false null _
    record var yield sealed permits when non
    """.split()
)

# The classes of the runtime, in the package of the module: a class of a header cannot have their
# names, because the generated code of the headers imports them.
RUNTIME_TYPES = frozenset(
    """
    BridgefexRuntime BridgefexNative BridgefexObject BridgefexException BridgefexFunction
    BridgefexUnsigned BridgefexHandle BridgefexLease BridgefexKeep
    """.split()
)

# What every object has: a method of the bindings cannot take these names (``Object`` has them,
# and so does every generated class).
RESERVED_MEMBERS = frozenset(
    """
    equals hashCode toString getClass notify notifyAll wait clone finalize
    close isClosed isReadOnly hold retain attach surrender keepOf borrow borrowed
    setOrigin retainForever checkExact collectOwning cppType wrap
    """.split()
)

# A parameter cannot take the names that the bodies use unqualified, nor the first segment of a
# package that they name in full (a variable hides a package).
PARAMETER_RESERVED = frozenset({"java", "javax", "jdk", "this", *RUNTIME_TYPES})

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
        return "_x"
    return "_" + result if result[0].isdigit() else result


def camel(name: str) -> str:
    """The name of a method: ``echo_int8`` is ``echoInt8`` and ``IOError`` is ``ioError``."""
    word = pascal(name)
    run = re.match(r"[A-Z]+", word)
    if run is None:
        return word
    capitals = run.group()
    if len(capitals) == len(word):
        return word.lower()
    if len(capitals) == 1:
        return word[0].lower() + word[1:]
    # An acronym followed by a word (``IOError``): the last capital starts the word.
    return capitals[:-1].lower() + word[len(capitals) - 1 :]


def upper_snake(name: str) -> str:
    """The name of an enum constant: ``LowLevel`` and ``low_level`` are ``LOW_LEVEL``."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", spaced)
    parts = _WORDS.findall(spaced)
    result = "_".join(part.upper() for part in parts)
    if not result:
        return "_X"
    return "_" + result if result[0].isdigit() else result


def identifier(name: str) -> str:
    """A name as a Java identifier (``class`` becomes ``class_``)."""
    return f"{name}_" if name in JAVA_KEYWORDS else name


def parameter(name: str) -> str:
    """The Java name of a parameter: it must not hide what the body of the wrapper uses."""
    word = camel(name) if "_" in name or name[:1].isupper() else name
    if word.startswith(LOCAL_PREFIX):
        word = f"p{word}"
    return identifier(f"{word}_" if word in PARAMETER_RESERVED else word)


def package_word(stem: str) -> str:
    """The segment of a package for a module or a header."""
    word = "_".join(_WORDS.findall(stem)).lower() or "_x"
    if word[0].isdigit():
        word = "_" + word
    return identifier(word)


def locals_of(count: int) -> frozenset[str]:
    """The locals of the bindings of a function with ``count`` parameters."""
    names = {
        f"{LOCAL_PREFIX}{word}"
        for word in (
            "arena",
            "result",
            "status",
            "self",
            "size",
            "text",
            "count",
            "items",
            "i",
            "new",
            "call",
            "scope",
        )
    }
    for position in range(1, 3 * count + 5):
        names.update(
            f"{LOCAL_PREFIX}{word}{position}" for word in ("h", "b", "p", "n", "s", "o", "t")
        )
    return frozenset(names)


def module_problem(name: str) -> str | None:
    """Why the Java package cannot be named for the module ``name``, or None."""
    word = package_word(name)
    if word in {"java", "javax", "jdk", "sun", "com", "org"}:
        return f"the Java package '{word}' would clash with the platform"
    return None


def names_of(headers: Iterable[str]) -> frozenset[str]:
    return frozenset(package_word(header) for header in headers)
