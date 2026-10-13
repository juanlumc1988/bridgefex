# SPDX-License-Identifier: Apache-2.0
"""Language-neutral description of the API found in the input headers.

The parser builds these objects from the libclang AST; the generators only
read them. Nothing in this module depends on libclang.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .errors import Location


class TypeCategory(Enum):
    """How a scalar type behaves at the C boundary."""

    VOID = "void"
    BOOL = "bool"
    SIGNED_INTEGER = "signed integer"
    UNSIGNED_INTEGER = "unsigned integer"
    FLOATING = "floating"


@dataclass(frozen=True, slots=True)
class ScalarType:
    """A type that crosses the C boundary by value.

    The same spelling is valid in C and C++, so it is used on both sides.
    """

    c_name: str
    """Spelling in C and C++, for example ``int32_t`` or ``unsigned long``."""

    category: TypeCategory

    ctypes_name: str
    """Name of the matching type in Python's ctypes module, for example ``c_int32``."""

    token: str
    """Stable short name used to build overload suffixes, for example ``int32``."""

    @property
    def is_void(self) -> bool:
        return self.category is TypeCategory.VOID

    @property
    def cpp_spelling(self) -> str:
        return self.c_name


class StringKind(Enum):
    """The string types that cross the C boundary as UTF-8 bytes."""

    STRING = "string"
    """``std::string``."""

    STRING_VIEW = "string_view"
    """``std::string_view``."""

    C_STRING = "cstr"
    """``const char*``, NUL-terminated."""


@dataclass(frozen=True, slots=True)
class StringType:
    kind: StringKind
    by_reference: bool = False
    """True for ``const std::string&`` and ``const std::string_view&``."""

    @property
    def is_void(self) -> bool:
        return False

    @property
    def token(self) -> str:
        """Overload token: the value, ``const`` and ``const&`` forms share it."""
        return self.kind.value

    @property
    def sized(self) -> bool:
        """True if it crosses as a pointer and a size, False if NUL-terminated."""
        return self.kind is not StringKind.C_STRING

    @property
    def cpp_spelling(self) -> str:
        # Fixed, never a libclang spelling: that one depends on the standard
        # library ('std::basic_string<char>' with libstdc++).
        if self.kind is StringKind.C_STRING:
            return "const char*"
        name = f"std::{self.kind.value}"
        return f"const {name}&" if self.by_reference else name


@dataclass(frozen=True, slots=True)
class EnumRef:
    """A use of an enum type; the plan finds its definition (:class:`Enumeration`) by USR,
    in any wrapped header."""

    usr: str
    qualified_name: str
    definition: Location | None
    """Where the definition seen by the using header is, if it sees one."""

    @property
    def is_void(self) -> bool:
        return False

    @property
    def cpp_spelling(self) -> str:
        return self.qualified_name


Type = ScalarType | StringType | EnumRef
"""Every type that can cross the C boundary."""


@dataclass(frozen=True, slots=True)
class Enumerator:
    name: str
    value: int
    deprecated: bool = False


@dataclass(frozen=True, slots=True)
class Enumeration:
    """An enum definition."""

    name: str
    namespace: tuple[str, ...]
    scope: tuple[str, ...]
    """The class that the enum is nested in, or ()."""

    usr: str
    scoped: bool
    """``enum class``."""

    underlying: ScalarType
    """The C type: the fixed underlying type, or ``int``."""

    fixed: bool
    """True if the underlying type is fixed: then every value of it is valid."""

    enumerators: tuple[Enumerator, ...]
    location: Location

    @property
    def qualified_name(self) -> str:
        return "::".join((*self.namespace, *self.scope, self.name))

    @property
    def valid_range(self) -> tuple[int, int] | None:
        """The values of an enum without a fixed underlying type ([dcl.enum]/8),
        or None when the underlying type is fixed."""
        if self.fixed:
            return None
        values = [enumerator.value for enumerator in self.enumerators]
        low, high = min(values, default=0), max(values, default=0)
        if low >= 0:
            bits = max(high.bit_length(), 1)
            return 0, (1 << bits) - 1
        bits = max(max(high, 0).bit_length(), (-low - 1).bit_length())
        return -(1 << bits), (1 << bits) - 1

    @property
    def description(self) -> str:
        """The declaration, spelled the same on every platform."""
        key = "enum class" if self.scoped else "enum"
        base = f" : {self.underlying.c_name}" if self.fixed else ""
        return f"{key} {self.qualified_name}{base}"


@dataclass(frozen=True, slots=True)
class Parameter:
    name: str
    """Name in the C++ declaration; empty when the parameter is unnamed."""

    type: Type


@dataclass(frozen=True, slots=True)
class Function:
    """A free (non-member) function."""

    name: str
    namespace: tuple[str, ...]
    parameters: tuple[Parameter, ...]
    result: Type
    location: Location

    @property
    def qualified_name(self) -> str:
        return "::".join((*self.namespace, self.name))


@dataclass(frozen=True, slots=True)
class Constructor:
    parameters: tuple[Parameter, ...]
    location: Location
    implicit: bool = False
    """True for the default constructor that the compiler declares implicitly."""


@dataclass(frozen=True, slots=True)
class Method:
    name: str
    parameters: tuple[Parameter, ...]
    result: Type
    is_const: bool
    is_static: bool
    location: Location


@dataclass(frozen=True, slots=True)
class Class:
    name: str
    namespace: tuple[str, ...]
    class_key: str
    """``class`` or ``struct``, as written in the definition."""

    constructors: tuple[Constructor, ...]
    methods: tuple[Method, ...]
    location: Location
    enums: tuple[Enumeration, ...] = ()
    """Public enums nested in the class."""

    @property
    def qualified_name(self) -> str:
        return "::".join((*self.namespace, self.name))


Declaration = Class | Function | Enumeration


@dataclass(frozen=True, slots=True)
class VisibleNames:
    """Names that a translation unit declares at global scope, and its macros.

    Generated C names must not clash with them.
    """

    global_names: frozenset[str] = frozenset()
    macro_names: frozenset[str] = frozenset()
    object_macro_names: frozenset[str] = frozenset()
    """The macros that take no arguments: they would also replace a parameter name."""

    tag_names: frozenset[str] = frozenset()
    """Struct, union and enum tags of a C translation unit, which only clash with
    the tags of the generated handle types (C++ class names are in
    ``global_names``)."""


@dataclass(frozen=True, slots=True)
class Header:
    path: Path
    include: str
    """Spelling used in ``#include "..."`` by the generated C++ source."""

    declarations: tuple[Declaration, ...]
    """Classes and free functions, in source order."""

    names: VisibleNames = VisibleNames()
    """Names of the header's translation unit, includes and builtins too."""

    @property
    def stem(self) -> str:
        return self.path.stem


@dataclass(frozen=True, slots=True)
class Module:
    """Everything that is generated together and linked into one library."""

    name: str
    headers: tuple[Header, ...]
    system_names: VisibleNames = VisibleNames()
    """Names of the platform's system headers (see :func:`bridgefex.parser.system_names`)."""
