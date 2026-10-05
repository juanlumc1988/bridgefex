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


@dataclass(frozen=True, slots=True)
class Parameter:
    name: str
    """Name in the C++ declaration; empty when the parameter is unnamed."""

    type: ScalarType


@dataclass(frozen=True, slots=True)
class Function:
    """A free (non-member) function."""

    name: str
    namespace: tuple[str, ...]
    parameters: tuple[Parameter, ...]
    result: ScalarType
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
    result: ScalarType
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

    @property
    def qualified_name(self) -> str:
        return "::".join((*self.namespace, self.name))


Declaration = Class | Function


@dataclass(frozen=True, slots=True)
class VisibleNames:
    """Names that a translation unit declares at global scope, and its macros.

    Generated C names must not clash with them.
    """

    global_names: frozenset[str] = frozenset()
    macro_names: frozenset[str] = frozenset()
    object_macro_names: frozenset[str] = frozenset()
    """The macros that take no arguments: they would also replace a parameter name."""


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
