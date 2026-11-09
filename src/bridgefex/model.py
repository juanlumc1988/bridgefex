# SPDX-License-Identifier: Apache-2.0
"""Language-neutral description of the API found in the input headers.

The parser builds these objects from the libclang AST; the generators only
read them. Nothing in this module depends on libclang.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .errors import Diagnostic, Location


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

    system: bool = False
    """Declared in a system header (which is wrapped only if given as one)."""

    @property
    def is_void(self) -> bool:
        return False

    @property
    def cpp_spelling(self) -> str:
        return self.qualified_name


@dataclass(frozen=True, slots=True)
class StructRef:
    """A use of a value struct; the plan finds its definition (:class:`Struct`) by
    USR, in any wrapped header."""

    usr: str
    qualified_name: str
    definition: Location | None
    """Where the definition seen by the using header is, if it sees one."""

    by_reference: bool = False
    """True for ``const T&``."""

    system: bool = False
    """Declared in a system header (which is wrapped only if given as one)."""

    @property
    def is_void(self) -> bool:
        return False

    @property
    def cpp_spelling(self) -> str:
        return f"const {self.qualified_name}&" if self.by_reference else self.qualified_name


class ClassForm(Enum):
    """How a wrapped class (a handle) is passed or returned."""

    VALUE = "value"
    """``T`` or ``const T``: a copy."""

    REFERENCE = "reference"
    """``T&``."""

    CONST_REFERENCE = "const_reference"
    """``const T&``."""

    POINTER = "pointer"
    """``T*`` and ``T* const``."""

    CONST_POINTER = "const_pointer"
    """``const T*``."""

    UNIQUE = "unique"
    """``std::unique_ptr<T>`` (``&&`` too, as a parameter)."""


@dataclass(frozen=True, slots=True)
class ClassRef:
    """A use of a wrapped class (a handle); the plan finds its definition
    (:class:`Class`) by USR, in any wrapped header."""

    usr: str
    qualified_name: str
    definition: Location | None
    """Where the definition seen by the using header is, if it sees one."""

    form: ClassForm

    system: bool = False
    """Declared in a system header (which is wrapped only if given as one)."""

    rvalue_reference: bool = False
    """``std::unique_ptr<T>&&``, which is not the same parameter (an override declares it)."""

    @property
    def is_void(self) -> bool:
        return False

    @property
    def token(self) -> str:
        """Overload token: ``f(T&)``, ``f(const T&)``, ``f(T*)``... are different overloads."""
        return self.form.value

    @property
    def cpp_spelling(self) -> str:
        name = self.qualified_name
        spellings = {
            ClassForm.VALUE: name,
            ClassForm.REFERENCE: f"{name}&",
            ClassForm.CONST_REFERENCE: f"const {name}&",
            ClassForm.POINTER: f"{name}*",
            ClassForm.CONST_POINTER: f"const {name}*",
            ClassForm.UNIQUE: f"std::unique_ptr<{name}>",
        }
        return spellings[self.form]


ElementType = ScalarType | EnumRef | StructRef | StringType
"""Every type that a container can hold."""


class ContainerKind(Enum):
    VECTOR = "vector"
    """``std::vector<T>``, with the default allocator."""

    ARRAY = "array"
    """``std::array<T, N>``."""

    SPAN = "span"
    """``std::span<const T>``, with a dynamic extent."""


@dataclass(frozen=True, slots=True)
class ContainerType:
    kind: ContainerKind
    element: ElementType
    length: int | None = None
    """N of std::array."""

    by_reference: bool = False
    """True for ``const C&``."""

    rvalue_reference: bool = False
    """True for ``C&&``, which is passed like a value."""

    @property
    def is_void(self) -> bool:
        return False

    @property
    def sized(self) -> bool:
        """True if it crosses as a pointer and a size (not std::array)."""
        return self.kind is not ContainerKind.ARRAY

    @property
    def cpp_spelling(self) -> str:
        element = self.element.cpp_spelling
        if self.kind is ContainerKind.ARRAY:
            name = f"std::array<{element}, {self.length}>"
        elif self.kind is ContainerKind.SPAN:
            name = f"std::span<const {element}>"
        else:
            name = f"std::vector<{element}>"
        if self.rvalue_reference:
            return f"{name}&&"
        return f"const {name}&" if self.by_reference else name


CallbackArgument = ScalarType | EnumRef | StructRef | StringType
"""Every type that a callback can take."""


@dataclass(frozen=True, slots=True)
class CallbackType:
    """``std::function<R(A...)>``: a C callback with its user_data and release
    function."""

    result: ScalarType | EnumRef
    """A scalar (``void`` too) or an enum."""

    arguments: tuple[CallbackArgument, ...]
    references: tuple[bool, ...]
    """For each argument: True for ``const T&`` of a scalar or an enum, which the
    model of the type does not keep (the C++ spelling of the signature does)."""

    by_reference: bool = False
    """True for ``const std::function<...>&``."""

    rvalue_reference: bool = False
    """True for ``std::function<...>&&``, which is passed like a value."""

    @property
    def is_void(self) -> bool:
        return False

    @property
    def signature(self) -> str:
        """The function type, ``R (A...)``."""
        arguments = [
            f"const {argument.cpp_spelling}&" if reference else argument.cpp_spelling
            for argument, reference in zip(self.arguments, self.references, strict=True)
        ]
        return f"{self.result.cpp_spelling} ({', '.join(arguments)})"

    @property
    def cpp_spelling(self) -> str:
        name = f"std::function<{self.signature}>"
        if self.rvalue_reference:
            return f"{name}&&"
        return f"const {name}&" if self.by_reference else name


Type = ScalarType | StringType | EnumRef | StructRef | ClassRef | ContainerType | CallbackType
"""Every type that can cross the C boundary."""

FieldType = ScalarType | EnumRef | StructRef
"""Every type that a field of a value struct can have."""


@dataclass(frozen=True, slots=True)
class Field:
    name: str
    type: FieldType
    length: int | None
    """The number of elements of an array, or None."""

    location: Location


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
    unavailable: tuple[int, ...] = ()
    """The values of the enumerators that cannot be used, which still count for
    the valid values."""

    @property
    def qualified_name(self) -> str:
        return "::".join((*self.namespace, *self.scope, self.name))

    @property
    def valid_range(self) -> tuple[int, int] | None:
        """The values of an enum without a fixed underlying type ([dcl.enum]/8),
        or None when the underlying type is fixed."""
        if self.fixed:
            return None
        values = [enumerator.value for enumerator in self.enumerators] + list(self.unavailable)
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

    by_reference: bool = False
    """``const T&`` of a scalar or an enum, which the type does not say (it is copied
    across the boundary). The other types keep it themselves. An override has to
    declare the same parameter."""


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
    is_virtual: bool = False
    """Declared ``virtual``, or overrides a virtual method: a call through a base
    runs the override of the object's class."""

    is_pure: bool = False
    """Declared ``= 0``."""

    is_final: bool = False
    """Declared ``final``: no class can override it."""

    is_noexcept: bool = False
    """``noexcept`` or ``throw()``: an override must not throw either."""

    result_by_reference: bool = False
    """It returns a reference (``const T&``): an override cannot give one back from a
    value that another language computed."""


class Access(Enum):
    """Where a class declares a member that code of another language may use or
    override. Private virtual methods are recorded too, only to tell what a derived
    class cannot call: it cannot override one that has an implementation, because it
    could not run that implementation, but it has to override a pure one."""

    PUBLIC = "public"
    PROTECTED = "protected"
    PRIVATE = "private"


@dataclass(frozen=True, slots=True)
class Hook:
    """A virtual method that a class declares, public or protected: something that a
    subclass written in another language may override (a director does it). It is not
    part of the API of the class unless it is public. The parser does not know
    whether the class asks for a director, so a signature that cannot cross the C
    boundary is not an error here: ``method`` is None and ``problems`` says why."""

    name: str
    access: Access
    is_pure: bool
    is_final: bool
    location: Location
    method: Method | None
    problems: tuple[Diagnostic, ...] = ()
    key: str = ""
    """What tells a virtual method from the others of its class and its bases: the name,
    the types of the parameters as the compiler sees them (``int`` and ``std::int32_t``
    are one) and whether it is ``const``. An override has the key of what it overrides.
    Only used to compare, never written to the generated code."""


@dataclass(frozen=True, slots=True)
class BaseRef:
    """A public base class of a wrapped class. The plan finds its definition
    (:class:`Class`) by USR, in any wrapped header."""

    usr: str
    qualified_name: str
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

    usr: str = ""
    """What the uses of the class (:class:`ClassRef`) name it by."""

    bases: tuple[BaseRef, ...] = ()
    """Its public base classes, in declaration order. Private and protected
    ones are an implementation detail: nothing is exposed through them."""

    abstract: bool = False
    """It has a pure virtual method that it does not override: it has no
    constructor in the C API, and cannot be passed or returned by value."""

    virtual_destructor: bool = False
    """Its destructor is virtual (declared so, or inherited)."""

    final: bool = False
    """Declared ``final``: no class derives from it."""

    hooks: tuple[Hook, ...] = ()
    """The virtual methods it declares (overrides too), public and protected, in
    source order."""

    director_constructors: tuple[Constructor, ...] = ()
    """The constructors that a derived class can call (public and protected, not the
    copy and move ones), the implicit default one included, whether or not the
    class is abstract."""

    director_problems: tuple[Diagnostic, ...] = ()
    """Why some of those constructors cannot be used from C."""

    @property
    def qualified_name(self) -> str:
        return "::".join((*self.namespace, self.name))


@dataclass(frozen=True, slots=True)
class Struct:
    """A value struct: public data members only, copied across the boundary."""

    name: str
    namespace: tuple[str, ...]
    usr: str
    fields: tuple[Field, ...]
    location: Location
    enums: tuple[Enumeration, ...] = ()
    """Public enums nested in the struct."""

    @property
    def qualified_name(self) -> str:
        return "::".join((*self.namespace, self.name))


Declaration = Class | Function | Enumeration | Struct


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
