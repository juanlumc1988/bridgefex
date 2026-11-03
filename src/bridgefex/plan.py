# SPDX-License-Identifier: Apache-2.0
"""The neutral binding plan: the C layer and what a language binding needs of it.

The plan holds every name, signature and statement of the generated C layer, and
a semantic description of each callable (``CallablePlan``: what each parameter and
result means across the boundary, not how a language spells it). A backend lowers
that description to its own language (``bridgefex.backends``); nothing here knows
about any of them. Keeping the logic in code, instead of in the templates, makes
it testable and keeps the templates free of decisions.
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from . import naming
from .annotations import (
    Annotations,
    DirectorRequest,
    FunctionAnnotations,
    Kind,
    Param,
    Resolved,
    resolve,
    unknown_directors,
    unknown_functions,
)
from .errors import Diagnostic, GenerationError, Location
from .model import (
    Access,
    CallbackType,
    Class,
    ClassForm,
    ClassRef,
    Constructor,
    ContainerKind,
    ContainerType,
    ElementType,
    Enumeration,
    EnumRef,
    Field,
    Function,
    Header,
    Hook,
    Method,
    Module,
    Parameter,
    ScalarType,
    StringKind,
    StringType,
    Struct,
    StructRef,
    Type,
)


class Role(Enum):
    SELF = "self"
    OUT_SELF = "out_self"
    OUT_DIRECTOR = "out_director"
    INPUT = "input"
    SIZE = "size"
    """The size of the input before it (a string)."""

    OUT_RESULT = "out_result"
    OUT_RESULT_SIZE = "out_result_size"


@dataclass(frozen=True, slots=True)
class Reservations:
    """What the language backends need of the names of the C API: the generated C API
    is the same whichever languages are generated, so every backend says which names
    it uses, and the plan leaves them alone."""

    words: frozenset[str] = frozenset()
    """Names that no parameter, member or field takes: the keywords of the language."""

    parameter_words: frozenset[str] = frozenset()
    """Names that no parameter takes: those that the bindings use for the parameters
    and the locals of their functions."""

    names: Callable[[Module], frozenset[str]] = lambda module: frozenset()
    """For a module: the names a parameter or a member must not take, because the
    bindings use them unqualified (their classes, the aliases of their modules)."""

    locals: Callable[[int], frozenset[str]] = lambda count: frozenset()
    """For a function with this many parameters: the local variables of the bindings
    of it, which a parameter must not hide."""

    module_problem: Callable[[str], str | None] = lambda name: None
    """Why the bindings cannot be named like the module (``name``), or None."""

    header_problem: Callable[[str], str | None] = lambda stem: None
    """Why the bindings cannot have a module for a header called ``stem``, or None."""

    @staticmethod
    def merge(reservations: Iterable[Reservations]) -> Reservations:
        items = tuple(reservations)

        def first(problems: Iterable[str | None]) -> str | None:
            return next((problem for problem in problems if problem), None)

        return Reservations(
            words=frozenset().union(*(item.words for item in items)),
            parameter_words=frozenset().union(*(item.parameter_words for item in items)),
            names=lambda module: frozenset().union(*(item.names(module) for item in items)),
            locals=lambda count: frozenset().union(*(item.locals(count) for item in items)),
            module_problem=lambda name: first(item.module_problem(name) for item in items),
            header_problem=lambda stem: first(item.header_problem(stem) for item in items),
        )


NO_RESERVATIONS = Reservations()


class FfiKind(Enum):
    VOID = "void"
    STATUS = "status"
    """The status of a call."""

    SCALAR = "scalar"
    """``name``: the C name of the type (``int32_t``, ``double``, ``size_t``...)."""

    VOID_POINTER = "void*"
    CHAR_POINTER = "char*"
    """A string that is passed in."""

    HANDLE = "handle"
    """A pointer to an opaque wrapped class; ``name``: its C type."""

    STRUCT = "struct"
    """A value struct; ``name``: its C type."""

    STRING = "string"
    """A string of the runtime that is returned (data and size)."""

    STRING_IN = "string_in"
    """A string of the runtime that is passed in."""

    CALLBACK = "callback"
    """A function pointer; ``name``: the signature (see the backends)."""

    RELEASE = "release"
    """The release function of a callback: ``void (*)(void*)``."""

    POINTER = "pointer"
    """A pointer to ``inner``."""


@dataclass(frozen=True, slots=True)
class Ffi:
    """The C type of a parameter or a result, as a foreign function interface sees
    it: structured, so that a backend does not parse C declarations."""

    kind: FfiKind
    name: str = ""
    inner: Ffi | None = None

    @staticmethod
    def scalar(c_name: str) -> Ffi:
        return Ffi(FfiKind.SCALAR, c_name)

    @staticmethod
    def handle(c_type: str) -> Ffi:
        return Ffi(FfiKind.HANDLE, c_type)

    @staticmethod
    def struct(c_type: str) -> Ffi:
        return Ffi(FfiKind.STRUCT, c_type)

    def pointer(self) -> Ffi:
        return Ffi(FfiKind.POINTER, inner=self)


VOID_FFI = Ffi(FfiKind.VOID)
STATUS_FFI = Ffi(FfiKind.STATUS)
VOID_POINTER_FFI = Ffi(FfiKind.VOID_POINTER)
SIZE_FFI = Ffi.scalar("size_t")


@dataclass(frozen=True, slots=True)
class CParam:
    name: str
    c_type: str
    role: Role
    ffi: Ffi
    length: int | None = None
    """The bound of an array parameter (``const float m[4]``)."""

    function_params: str | None = None
    """The parameters of a function pointer parameter, whose ``c_type`` is then
    the result type (``void (*f)(void*, int32_t)``)."""

    @property
    def declaration(self) -> str:
        if self.function_params is not None:
            return f"{self.c_type} (*{self.name})({self.function_params})"
        if self.length is not None:
            return f"{self.c_type} {self.name}[{self.length}]"
        return f"{self.c_type} {self.name}"


@dataclass(frozen=True, slots=True)
class Precondition:
    """A check that runs before the guarded call: when ``condition`` holds, the
    function returns the status of the ``helper`` of the generated runtime."""

    condition: str
    """C++ expression, true when the argument is not valid."""

    helper: str
    """Function of the detail namespace that sets the message and gives the status."""

    message: str
    """What is wrong, without the name of the function (which the template adds)."""


def _null_check(name: str) -> Precondition:
    return Precondition(f"{name} == nullptr", "null_argument", f"'{name}' is NULL")


def _sized_null_check(name: str, size: str) -> Precondition:
    return Precondition(
        f"{name} == nullptr && {size} != 0",
        "null_argument",
        f"'{name}' is NULL and '{size}' is not 0",
    )


@dataclass(frozen=True, slots=True)
class CFunction:
    name: str
    params: tuple[CParam, ...]
    returns_status: bool
    statement: str
    """C++ statement that does the work, run inside the exception guard."""

    description: str
    """The wrapped C++ declaration, for comments and docstrings."""

    ffi_restype: Ffi

    owned_checks: tuple[Precondition, ...] = ()
    """NULL checks of the outputs that the function allocates (out_self...),
    which come first so that the outputs can be reset on every failure."""

    resets: tuple[str, ...] = ()
    """Statements that reset those outputs, one per check: each runs right
    after its check, so that a NULL output that comes after it cannot leave
    it unset."""

    checks: tuple[Precondition, ...] = ()
    """The other checks: NULL checks of self, of pointer inputs and of the
    caller's result storage, in parameter order, then value checks."""

    location: Location | None = None
    """Where the wrapped C++ declaration is."""

    compile_checks: tuple[str, ...] = ()
    """static_asserts at the start of the function body."""

    prologue: tuple[str, ...] = ()
    """Declarations before the checks: the owners of the callbacks, which
    release them on every return."""

    notes: tuple[str, ...] = ()
    """What the C header says besides the declaration: who owns the objects that
    cross (they are part of the API, so they are part of its fingerprint)."""

    c_return: str = ""
    """For a plain function (no status, no exception guard) that returns a value,
    the C type of it; ``statement`` is then the expression returned."""

    hint: str = ""
    """What to tell, after the error of the compiler, if the generated code of this
    function does not compile: where the request that caused it is, and the likely
    cause."""

    @property
    def owned_outputs(self) -> tuple[tuple[Precondition, str], ...]:
        """Each NULL check of an output that the function allocates, with its reset."""
        return tuple(zip(self.owned_checks, self.resets, strict=True))

    @property
    def signature(self) -> str:
        params = ", ".join(param.declaration for param in self.params) or "void"
        return f"{self.name}({params})"


class CallableKind(Enum):
    FUNCTION = "function"
    """A free function."""

    STATIC = "static"
    """A static method."""

    METHOD = "method"
    CONSTRUCTOR = "constructor"

    DIRECTOR_CONSTRUCTOR = "director constructor"
    """Creates the director of a class: a subclass whose virtual methods call back
    through a vtable."""

    DIRECTOR_BASE = "director base call"
    """Calls the implementation of a virtual method that the director overrides, without
    going through the vtable."""


@dataclass(frozen=True, slots=True)
class ElementSpec:
    """The element type of a container, with what it refers to."""

    type: ElementType
    enum: EnumPlan | None = None
    struct: StructPlan | None = None


@dataclass(frozen=True, slots=True)
class CallbackPart:
    """An argument or the result of a callback, with what it refers to."""

    type: Type
    enum: EnumPlan | None = None
    struct: StructPlan | None = None


@dataclass(frozen=True, slots=True)
class CallbackSpec:
    arguments: tuple[CallbackPart, ...]
    result: CallbackPart


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """A parameter of a wrapped function, as a language backend sees it: what it
    is (the C++ type and what it refers to), what the annotations say about it
    and the names of its C parameters. Everything a binding needs to lower it
    without reading the generated C."""

    name: str
    """The name of the C parameter (of the callback, for a ``std::function``)."""

    type: Type
    """A class passed by reference or pointer is a :class:`ClassRef`, never a
    :class:`StructRef`."""

    words: frozenset[str] = frozenset()
    """The annotations that apply, the implied ones included (``nullable``,
    ``transfer``, ``retain``, ``call``)."""

    size: str | None = None
    """The C parameter that holds the size of a string or a vector."""

    enum: EnumPlan | None = None
    struct: StructPlan | None = None
    cls: ClassInfo | None = None
    element: ElementSpec | None = None
    callback: CallbackSpec | None = None
    user_data: str | None = None
    release: str | None = None


@dataclass(frozen=True, slots=True)
class ResultSpec:
    """The result of a wrapped function, as a language backend sees it."""

    type: Type
    """A class returned by value, reference or pointer is a :class:`ClassRef`."""

    enum: EnumPlan | None = None
    struct: StructPlan | None = None
    cls: ClassInfo | None = None
    element: ElementSpec | None = None
    ownership: str | None = None
    """For a raw pointer to a class: ``owned`` or ``borrowed``, as the annotations say."""

    as_bytes: bool = False
    """A string result the annotations want as bytes, not decoded."""


@dataclass(frozen=True, slots=True)
class CallablePlan:
    """A C function with what a language backend needs to bind it: the semantic
    description of its parameters and its result."""

    kind: CallableKind
    name: str
    """The C++ name of the function or method (of the class, for a constructor)."""

    suffix: str
    """The overload suffix of its C name (empty if it is not overloaded)."""

    function: CFunction
    params: tuple[ParamSpec, ...]
    result: ResultSpec | None
    location: Location
    is_const: bool = False
    """A method that can be called on a const object."""


@dataclass(frozen=True, slots=True)
class LoweredParam:
    """A C++ parameter as the generated code passes it."""

    c_params: tuple[CParam, ...]
    argument: str
    """C++ expression passed to the wrapped function."""

    null_checks: tuple[Precondition, ...]
    value_checks: tuple[Precondition, ...]

    temporary: str | None = None
    """A temporary (prvalue) of the parameter's type, when ``argument`` passes
    an lvalue: see :func:`_temporaries_check`."""

    prologue: tuple[str, ...] = ()
    """Declarations at the start of the C function (CFunction.prologue)."""

    probe: str | None = None
    """An expression of the argument's type and value category, for unevaluated
    operands, when ``argument`` cannot appear there: a lambda before C++20."""

    compile_checks: tuple[str, ...] = ()
    """static_asserts for this parameter, at the start of the C function."""

    transfers: bool = False
    """The wrapped function takes the object this parameter points to."""

    words: frozenset[str] = frozenset()
    """The annotations that apply to this parameter, the implied ones included
    (``nullable``, ``transfer``, ``retain``, ``call``)."""

    spec: ParamSpec | None = None
    """What a language backend sees of the parameter."""


def _param_name(param: LoweredParam) -> str:
    # A parameter that could not be lowered was reported: the plan is not built, and
    # what is written about it does not matter.
    return param.spec.name if param.spec is not None else ""


def _parameter_notes(params: Sequence[LoweredParam], keeper: str) -> tuple[str, ...]:
    """What the C header says about the objects that the function is given: which
    it takes, which may be NULL and which it keeps a pointer to (``keeper`` is who
    keeps it: self, the new object, the library)."""
    notes: list[str] = []
    taken = [_param_name(param) for param in params if param.transfers]
    if taken:
        pronoun = "it" if len(taken) == 1 else "them"
        notes.append(
            f"{', '.join(taken)}: ownership is transferred, whatever the status: do not use or "
            f"destroy {pronoun} afterwards."
        )
    nullable = [_param_name(param) for param in params if "nullable" in param.words]
    if nullable:
        notes.append(f"{', '.join(nullable)}: may be NULL.")
    notes += [
        f"{_param_name(param)}: {keeper} a pointer to it; it must stay valid as long as it is used."
        for param in params
        if "retain" in param.words
    ]
    return tuple(notes)


def _temporaries_check(call: str, params: Sequence[LoweredParam], what: str) -> tuple[str, ...]:
    """A static_assert that fails to compile if the wrapped function cannot take
    its string arguments as temporaries, as with 'f(std::string&&) = delete'.

    The wrapper passes const references as lvalues, so that a 'f(std::string&&)'
    or a forwarding template of a header that is not wrapped does not take the
    call instead of the wrapped function. A deleted overload for temporaries is
    the sign of a function that keeps a reference to its argument, which would
    dangle after the call: this check keeps it an error.
    """
    if all(param.temporary is None for param in params):
        return ()
    arguments = ", ".join(param.temporary or param.probe or param.argument for param in params)
    probe = call.replace("{arguments}", arguments)
    return (
        f"static_assert(sizeof(decltype(static_cast<void>({probe}))*) != 0,\n"
        f'                  "{what} must accept temporaries: it would keep a dangling reference");',
    )


@dataclass(frozen=True, slots=True)
class LoweredResult:
    """A C++ result as the generated code returns it."""

    c_params: tuple[CParam, ...]
    """Output parameters, after the inputs."""

    statement: str
    """The guarded statement, with ``{call}`` where the wrapped call goes: the
    result is consumed in the same full-expression as the call, while the
    temporaries that it may point into are alive."""

    null_checks: tuple[Precondition, ...]
    """NULL checks of the caller's storage for the result."""

    owned_checks: tuple[Precondition, ...] = ()
    """NULL checks of the outputs that the function allocates."""

    resets: tuple[str, ...] = ()
    """Statements that reset those outputs, so they are NULL on every failure."""

    notes: tuple[str, ...] = ()
    """For the C header: who owns the result (see :attr:`CFunction.notes`)."""

    spec: ResultSpec | None = None
    """What a language backend sees of the result (None for ``void``)."""


# How each C type spells its constants: a suffix, or a macro of <stdint.h>.
# The spelling depends on the type, never on the value, so that every constant
# has exactly the (promoted) type of the enum: 2147483648 of a uint64_t enum is
# UINT64_C(2147483648), and ~ keeps its upper bits.
_LITERAL_FORMS = {
    "signed char": "",
    "unsigned char": "",
    "short": "",
    "unsigned short": "",
    "int": "",
    "unsigned int": "U",
    "long": "L",
    "unsigned long": "UL",
    "long long": "LL",
    "unsigned long long": "ULL",
    "int8_t": "",
    "uint8_t": "",
    "int16_t": "",
    "uint16_t": "",
    "int32_t": "INT32_C",
    "uint32_t": "UINT32_C",
    "int64_t": "INT64_C",
    "uint64_t": "UINT64_C",
}


def c_literal(value: int, c_type: str) -> str:
    """``value`` as a C constant of the type ``c_type`` (after the integer
    promotions), without casts, so that it also works in ``#if``."""
    form = _LITERAL_FORMS[c_type]

    def literal(number: int) -> str:
        return f"{form}({number})" if form.endswith("_C") else f"{number}{form}"

    if value >= 0:
        return literal(value)
    if -value in (2**31, 2**63):
        # The minimum of a 32 or 64-bit type: its magnitude is not a constant
        # of the type.
        return f"(-{literal(-value - 1)} - 1)"
    return f"(-{literal(-value)})"


@dataclass(frozen=True, slots=True)
class EnumeratorPlan:
    model_name: str
    macro: str
    literal: str
    value: int
    deprecated: bool


@dataclass(frozen=True, slots=True)
class EnumPlan:
    model: Enumeration
    c_type: str
    enumerators: tuple[EnumeratorPlan, ...]

    @property
    def cpp_name(self) -> str:
        return "::" + self.model.qualified_name

    @property
    def comment(self) -> str:
        valid = self.model.valid_range
        if valid is None:
            return self.model.description
        return f"{self.model.description}: only the values {valid[0]}..{valid[1]} are valid"


@dataclass(frozen=True, slots=True)
class FieldPlan:
    model: Field
    name: str
    """In the C struct (a backend may rename it for its own language)."""

    c_declaration: str


@dataclass(frozen=True, slots=True)
class StructPlan:
    model: Struct
    c_type: str
    fields: tuple[FieldPlan, ...]
    init: CFunction
    enums: tuple[EnumPlan, ...] = ()
    """Nested enums."""

    to_c: tuple[str, ...] = ()
    """Body of the C++ conversion to the C mirror (member by member)."""

    from_c: tuple[str, ...] = ()
    """Body of the C++ conversion from the C mirror (member by member)."""

    extents: tuple[tuple[str, int], ...] = ()
    """The arrays of this struct and of the structs it contains, with their length."""

    @property
    def cpp_name(self) -> str:
        return "::" + self.model.qualified_name


@dataclass(frozen=True, slots=True)
class ClassView:
    """A wrapped class seen as one of its ancestors. A pointer to a class is not
    always the pointer to its base (with several bases it is not), so the C API has
    a conversion for each ancestor."""

    c_type: str
    """The C type of the ancestor."""

    upcast: CFunction
    const_upcast: CFunction


@dataclass(frozen=True, slots=True)
class DirectorEntry:
    """A virtual method that the vtable of a director overrides: one function pointer,
    which is NULL when the method keeps the implementation of the class."""

    field: str
    """The member of the vtable, and the end of the name of its base call."""

    name: str
    """The C++ name of the method."""

    description: str
    """The C++ declaration, for the comments."""

    owner: str
    """The class that declares the method (the director's class or an ancestor)."""

    access: Access
    is_pure: bool
    """No implementation to fall back on: the vtable has to give one."""

    callback: CallbackSpec
    """What the function receives and gives back, as for a ``std::function``."""

    thunk_key: str
    """The signature of the function pointer, which the backends name their types by."""

    function_params: str
    """The C parameters of the function pointer, ``void*`` first."""

    base: CallablePlan | None
    """The base call: None for a pure virtual method."""

    @property
    def declaration(self) -> str:
        return f"void (*{self.field})({self.function_params})"


@dataclass(frozen=True, slots=True)
class DirectorPlan:
    """The director of a class: a generated subclass, final, whose virtual methods call
    the functions of a vtable that code of another language provides."""

    c_type: str
    """The C type of the class."""

    class_name: str
    """The C++ name of the class, for the comments."""

    director_type: str
    """The opaque C type of the director."""

    vtable_type: str
    entries: tuple[DirectorEntry, ...]
    constructors: tuple[CallablePlan, ...]
    """The functions that create a director, one per constructor of the class."""

    object: CFunction
    """The conversion from the director to the object (the class)."""

    glue: tuple[str, ...]
    """The C++ class, as lines."""

    @property
    def functions(self) -> tuple[CFunction, ...]:
        return (
            *(item.function for item in self.constructors),
            self.object,
            *(entry.base.function for entry in self.entries if entry.base is not None),
        )


@dataclass(frozen=True, slots=True)
class ClassPlan:
    model: Class
    c_type: str
    constructors: tuple[CallablePlan, ...]
    destroy: CFunction
    members: tuple[CallablePlan, ...]
    """Methods and static methods, in source order."""

    enums: tuple[EnumPlan, ...] = ()
    """Nested enums."""

    bases: tuple[ClassInfo, ...] = ()
    """Its public base classes (wrapped classes of the module), in declaration order."""

    views: tuple[ClassView, ...] = ()
    """How to see it as each of its ancestors (see :class:`ClassView`)."""

    is_base: bool = False
    """Another class of the module derives from it: it has a view of itself too."""

    director: DirectorPlan | None = None
    """Its director, if the annotations file asks for one."""

    @property
    def cpp_name(self) -> str:
        return "::" + self.model.qualified_name

    @property
    def c_functions(self) -> tuple[CFunction, ...]:
        return (
            *(item.function for item in self.constructors),
            self.destroy,
            *(function for view in self.views for function in (view.upcast, view.const_upcast)),
            *(item.function for item in self.members),
        )


@dataclass(frozen=True, slots=True)
class FunctionPlan:
    model: Function
    callable: CallablePlan

    @property
    def c_function(self) -> CFunction:
        return self.callable.function


@dataclass(frozen=True, slots=True)
class HeaderPlan:
    header: Header
    stem: str
    c_header: str
    c_source: str
    guard: str
    items: tuple[ClassPlan | FunctionPlan | EnumPlan | StructPlan, ...]
    """Classes, free functions, enums and value structs, in source order."""

    foreign_enums: tuple[EnumPlan, ...] = ()
    """Enums of other headers with a fixed underlying type that this header uses."""

    foreign_headers: tuple[HeaderPlan, ...] = ()
    """The other headers whose value structs this header's functions use: the
    generated source includes them and their conversions."""

    foreign_class_headers: tuple[HeaderPlan, ...] = ()
    """The other headers whose classes this header's functions use: the
    generated source includes them, so that the classes are complete there."""

    director_types: tuple[Type, ...] = ()
    """The types that the virtual methods of the directors of this header take and
    give: they cross the boundary in the generated source too."""

    created_classes: tuple[tuple[str, str], ...] = ()
    """The (C++ name, qualified name) of the classes that the generated source
    creates with ``new``: those of this header, and those that its functions
    return by value (which may belong to another header)."""

    value_results: tuple[tuple[str, str], ...] = ()
    """The same, for the classes that its functions return by value."""

    @property
    def included_headers(self) -> tuple[HeaderPlan, ...]:
        """The other wrapped headers that the generated source includes."""
        others = {plan.stem: plan for plan in (*self.foreign_headers, *self.foreign_class_headers)}
        return tuple(others.values())

    @property
    def structs(self) -> tuple[StructPlan, ...]:
        return tuple(item for item in self.items if isinstance(item, StructPlan))

    @property
    def struct_inits(self) -> tuple[CFunction, ...]:
        """The <c_type>_init functions of the value structs."""
        return tuple(struct.init for struct in self.structs)

    @property
    def c_internal_header(self) -> str:
        """The conversions of the value structs (generated only if there are any)."""
        return f"{self.stem}_c_internal.hpp"

    @property
    def internal_guard(self) -> str:
        return f"{self.guard.removesuffix('_C_H')}_C_INTERNAL_HPP"

    @property
    def internal_includes(self) -> tuple[str, ...]:
        """The internal headers that the generated source includes."""
        owners = (self, *self.foreign_headers) if self.structs else self.foreign_headers
        return tuple(header.c_internal_header for header in owners)

    @property
    def classes(self) -> tuple[ClassPlan, ...]:
        return tuple(item for item in self.items if isinstance(item, ClassPlan))

    @property
    def types(self) -> tuple[ClassPlan | EnumPlan, ...]:
        """Handles and enums, in source order; nested enums after their class or
        struct. (The value structs come later, in the order of their contents.)"""
        types: list[ClassPlan | EnumPlan] = []
        for item in self.items:
            if isinstance(item, ClassPlan):
                types += [item, *item.enums]
            elif isinstance(item, StructPlan):
                types += item.enums
            elif isinstance(item, EnumPlan):
                types.append(item)
        return tuple(types)

    @property
    def enums(self) -> tuple[EnumPlan, ...]:
        """The enums this header defines, nested ones included."""
        return tuple(item for item in self.types if isinstance(item, EnumPlan))

    @property
    def c_functions(self) -> tuple[CFunction, ...]:
        functions: list[CFunction] = []
        for item in self.items:
            if isinstance(item, ClassPlan):
                functions += item.c_functions
            elif isinstance(item, FunctionPlan):
                functions.append(item.c_function)
            elif isinstance(item, StructPlan):
                functions.append(item.init)
        return tuple(functions)

    @property
    def directors(self) -> tuple[DirectorPlan, ...]:
        return tuple(
            item.director
            for item in self.items
            if isinstance(item, ClassPlan) and item.director is not None
        )

    @property
    def director_functions(self) -> tuple[CFunction, ...]:
        """The functions of the directors: for the code that calls back, not for the
        bindings that call."""
        return tuple(function for director in self.directors for function in director.functions)

    @property
    def all_c_functions(self) -> tuple[CFunction, ...]:
        """Every function that the C header declares and the C source defines."""
        return (*self.c_functions, *self.director_functions)

    @property
    def std_includes(self) -> tuple[str, ...]:
        """Standard headers of the types that cross the boundary in the generated
        source: the wrapped header may only declare them (<iosfwd>)."""
        kinds: set[StringKind] = set()
        for element in _components((*_header_types(self.header), *self.director_types)):
            if isinstance(element, StringType):
                kinds.add(element.kind)
        includes = []
        if StringKind.STRING in kinds:
            includes.append("string")
        if StringKind.STRING_VIEW in kinds:
            includes.append("string_view")
        containers = {
            type_.kind for type_ in _header_types(self.header) if isinstance(type_, ContainerType)
        }
        if ContainerKind.SPAN in containers:
            # The elements of a span that must be converted are copied into a
            # std::vector.
            containers.add(ContainerKind.VECTOR)
        includes += [kind.value for kind in ContainerKind if kind in containers]
        if any(isinstance(type_, CallbackType) for type_ in _header_types(self.header)):
            includes.append("functional")
        if any(
            isinstance(type_, ClassRef) and type_.form is ClassForm.UNIQUE
            for type_ in _header_types(self.header)
        ):
            includes.append("memory")
        return tuple(includes)


def _components(types: Iterable[Type]) -> Iterable[Type]:
    """The types and the types they are made of: the element of a container,
    the result and arguments of a callback."""
    for type_ in types:
        yield type_
        if isinstance(type_, ContainerType):
            yield type_.element
        elif isinstance(type_, CallbackType):
            yield type_.result
            yield from type_.arguments


def _header_types(header: Header, *, fields: bool = True) -> Iterable[Type]:
    """Every parameter and result type of a header's declarations, and with
    ``fields`` the types of the members of its value structs."""
    for declaration in header.declarations:
        if fields and isinstance(declaration, Struct):
            yield from (field.type for field in declaration.fields)
        if isinstance(declaration, Function):
            yield from (parameter.type for parameter in declaration.parameters)
            yield declaration.result
        elif isinstance(declaration, Class):
            for constructor in declaration.constructors:
                yield from (parameter.type for parameter in constructor.parameters)
            for method in declaration.methods:
                yield from (parameter.type for parameter in method.parameters)
                yield method.result


@dataclass(frozen=True, slots=True)
class ModulePlan:
    name: str
    headers: tuple[HeaderPlan, ...]
    structs: tuple[StructPlan, ...] = ()
    """Every value struct, each after the structs it contains."""

    reservations: Reservations = NO_RESERVATIONS
    """What the plan left alone for the backends: a backend can only render a plan that
    was built with its own reservations."""

    @property
    def macro_prefix(self) -> str:
        return self.name.upper()

    @property
    def status_type(self) -> str:
        return f"{self.name}_status"

    @property
    def last_error(self) -> str:
        return f"{self.name}_last_error"

    @property
    def detail_namespace(self) -> str:
        return f"{self.name}_detail"

    @property
    def api_fingerprint_function(self) -> str:
        return f"{self.name}_api_fingerprint"

    @property
    def free_function(self) -> str:
        return f"{self.name}_free"

    @property
    def string_type(self) -> str:
        return f"{self.name}_string"

    @property
    def has_directors(self) -> bool:
        return any(header.directors for header in self.headers)

    @property
    def string_sink_type(self) -> str:
        """The string that a function of a vtable gives back."""
        return f"{self.name}_string_sink"

    @property
    def string_sink_set(self) -> str:
        return f"{self.name}_string_sink_set"

    @property
    def disable_callbacks(self) -> str:
        return f"{self.name}_disable_callbacks"

    @property
    def enable_callbacks(self) -> str:
        return f"{self.name}_enable_callbacks"

    @property
    def public_headers(self) -> tuple[str, ...]:
        """The generated C headers, in the order the API fingerprint hashes them."""
        return (
            self.runtime_header,
            self.types_header,
            *(header.c_header for header in self.headers),
        )

    @property
    def runtime_header(self) -> str:
        return f"{self.name}_runtime.h"

    @property
    def runtime_internal_header(self) -> str:
        return f"{self.name}_runtime_internal.hpp"

    @property
    def types_header(self) -> str:
        return f"{self.name}_types.h"

    @property
    def runtime_source(self) -> str:
        return f"{self.name}_runtime.cpp"

    @property
    def library_env_var(self) -> str:
        return f"{self.macro_prefix}_LIBRARY"


_INT_MIN = -(2**31)
_INT_MAX = 2**31 - 1


@dataclass(frozen=True, slots=True)
class ClassInfo:
    """A class of the module, as the uses of it in any header name it."""

    model: Class
    header: Header
    c_type: str
    is_base: bool = False
    """Another class of the module derives from it."""

    @property
    def cpp_name(self) -> str:
        return "::" + self.model.qualified_name


@dataclass(frozen=True, slots=True)
class _Slot:
    """A virtual method that a director overrides: the most derived declaration."""

    owner: ClassInfo
    hook: Hook
    method: Method


@dataclass(frozen=True, slots=True)
class _Element:
    """How the elements of a container cross the boundary."""

    c_type: str
    cpp: str
    """The C++ element type, in the C++ container type."""

    token: str
    ffi_in: Ffi
    ffi_out: Ffi
    to_cpp: str | None = None
    """C++ converter of a C element to C++, or None if they are the same type
    (``{name}``: the parameter, for messages)."""

    to_c: str | None = None
    """C++ converter of a C++ element to C, or None if they are the same type."""

    enum: EnumPlan | None = None
    struct: StructPlan | None = None


@dataclass(slots=True)
class _Copies:
    """The bodies of the C++ conversions of a struct, while they are built."""

    to_c: list[str] = dataclasses.field(default_factory=list)
    from_c: list[str] = dataclasses.field(default_factory=list)
    extents: dict[str, int] = dataclasses.field(default_factory=dict)


def _spelling(type_: Type) -> str:
    """How a type is spelled in comments and docstrings: never a libclang
    spelling, which differs between platforms and standard libraries."""
    return type_.cpp_spelling


_CLASS_TOKEN_SUFFIXES = {
    ClassForm.VALUE: "",
    ClassForm.CONST_REFERENCE: "",
    ClassForm.REFERENCE: "_ref",
    ClassForm.POINTER: "_ptr",
    ClassForm.CONST_POINTER: "_cptr",
    ClassForm.UNIQUE: "_unique",
}


def _token(
    type_: Type, enums: dict[str, EnumPlan], structs: dict[str, str], classes: dict[str, str]
) -> str:
    """Name of a type in overload suffixes: the C type name for an enum, a struct
    or a class (with a suffix for a reference, a pointer or a std::unique_ptr:
    ``f(T&)`` and ``f(const T&)`` are different overloads)."""
    if isinstance(type_, EnumRef):
        enum = enums.get(type_.usr)
        return enum.c_type if enum is not None else "enum"
    if isinstance(type_, StructRef):
        # A class that the header only declares is seen as a struct.
        return structs.get(type_.usr) or classes.get(type_.usr, "struct")
    if isinstance(type_, ClassRef):
        return classes.get(type_.usr, "class") + _CLASS_TOKEN_SUFFIXES[type_.form]
    if isinstance(type_, ContainerType):
        element = _token(type_.element, enums, structs, classes)
        if type_.kind is ContainerKind.ARRAY:
            return f"array{type_.length}_{element}"
        return f"{type_.kind.value}_{element}"
    if isinstance(type_, CallbackType):
        parts = [_token(part, enums, structs, classes) for part in (type_.result, *type_.arguments)]
        return "_".join(["function", *parts])
    return type_.token


_DIRECTOR_PARAMETERS = frozenset({"director", "vtable", "out_object", "out_director"})
"""The names of the parameters that the functions of a director add: the others avoid them."""


def _callback_locals(callbacks: Sequence[CallbackType]) -> frozenset[str]:
    """The names that the generated code of these callbacks declares in the C
    function (the owners) and in the lambdas that call them: no parameter may
    take them, it would be shadowed (-Wshadow)."""
    if not callbacks:
        return frozenset()
    count = max(len(callback.arguments) for callback in callbacks)
    return frozenset(
        {"_callback", "_user_data", "_result"}
        | {f"_arg{position}" for position in range(1, count + 1)}
        | {f"_owner{position}" for position in range(1, len(callbacks) + 1)}
    )


def _describe_params(parameters: Sequence[Parameter]) -> str:
    return ", ".join(
        f"{_spelling(parameter.type)} {parameter.name}"
        if parameter.name
        else _spelling(parameter.type)
        for parameter in parameters
    )


class _Builder:
    def __init__(
        self,
        module: Module,
        annotations: Mapping[str, FunctionAnnotations] | None = None,
        reservations: Reservations | None = None,
    ) -> None:
        self.module = module
        self._reservations = reservations or NO_RESERVATIONS
        self._annotations: Mapping[str, FunctionAnnotations] = annotations or {}
        # The generated C functions that an annotation can name.
        self.annotatable: list[str] = []
        self.errors: list[Diagnostic] = []
        # The classes that the annotations file asks a director for, and the types that
        # the virtual methods of those directors use, by header.
        self.director_requests: Mapping[str, DirectorRequest] = (
            annotations.directors if isinstance(annotations, Annotations) else {}
        )
        self._director_types: dict[str, list[Type]] = {}
        self._c_names: dict[str, tuple[str, Location | None]] = {}
        # Names that generated C identifiers must not take: declarations at
        # global scope and macros seen by the parsed headers, and those of the
        # system headers (C library, POSIX...), which C programs that use the
        # generated API also see.
        self._external: dict[str, str] = {}
        # Only handle types declare a tag ('typedef struct X X;').
        self._external_tags: dict[str, str] = {}
        sources = [(header.names, f"seen by {header.path}") for header in module.headers]
        sources.append((module.system_names, "of the system headers"))
        for names, where in sources:
            for name in names.global_names:
                self._external.setdefault(name, f"a global declaration {where}")
            for name in names.macro_names:
                self._external.setdefault(name, f"a macro {where}")
            for name in names.tag_names:
                self._external_tags.setdefault(name, f"a struct, union or enum tag {where}")
        self._macros = naming.generated_macros(module.name)
        for macro in sorted(self._macros):
            self._reserve_c_name(macro, "a macro generated by bridgefex", None)
        for name, what in (
            ("status", "the status type"),
            ("last_error", "the last-error function"),
            ("api_fingerprint", "the API fingerprint function"),
            ("free", "the function that releases returned memory"),
            ("disable_callbacks", "the function that disables the callbacks"),
            ("enable_callbacks", "the function that enables the callbacks"),
            ("detail", "the namespace of the generated C++ helpers"),
        ):
            self._reserve_c_name(f"{module.name}_{name}", what, None)
        self._reserve_c_name(
            f"{module.name}_string", "the string type of the runtime", None, tag=True
        )
        self._reserve_director_names()
        # A generated type with the name of an output parameter would be hidden
        # by it. (Only generated names: these do not clash with the headers.)
        for name in ("out_self", "out_result", "out_result_size"):
            self._c_names[name] = ("an output parameter of the generated functions", None)
        # A parameter named like a macro without arguments would be replaced
        # by it: the generated macros and include guards, and the macros of the
        # wrapped headers (the generated source includes them first).
        # The keywords of the languages of the bindings are not names either.
        self._parameter_macros = (
            self._macros
            | {naming.header_guard(module.name, header.stem) for header in module.headers}
            | self._reservations.words
        )
        for header in module.headers:
            self._parameter_macros |= header.names.object_macro_names
        self._overloads = self._count_overloads(module)
        # Every enum of the module, by USR: a header can use the enums of the others.
        self._enums: dict[str, EnumPlan] = {}
        self._enum_headers: dict[str, Header] = {}
        for header in module.headers:
            for declaration in header.declarations:
                nested = declaration.enums if isinstance(declaration, (Class, Struct)) else ()
                scope = (declaration,) if isinstance(declaration, (Class, Struct)) else ()
                enums = (declaration,) if isinstance(declaration, Enumeration) else nested
                for enum in enums:
                    plan = self._enum_plan(enum, header, scope)
                    self._enums.setdefault(enum.usr, plan)
                    self._enum_headers.setdefault(enum.usr, header)
        # A parameter named like an enumerator macro would be replaced by it,
        # and one named like a generated type would hide the type in the
        # prototypes and in the wrappers ('ns_Color ns_Color, ns_Color b').
        self._parameter_macros |= {
            enumerator.macro for enum in self._enums.values() for enumerator in enum.enumerators
        }
        self._parameter_macros |= {enum.c_type for enum in self._enums.values()}
        self._parameter_macros |= {f"{module.name}_status", f"{module.name}_string"}
        self._parameter_macros |= {
            f"{naming.c_prefix(declaration.namespace, module.name)}_{declaration.name}"
            for header in module.headers
            for declaration in header.declarations
            if isinstance(declaration, (Class, Struct))
        }
        self._handle_names = {
            declaration.qualified_name
            for header in module.headers
            for declaration in header.declarations
            if isinstance(declaration, Class)
        }
        self._structs = self._collect_structs(module)
        # Every class of the module, by USR: a header can use the classes of the others.
        self._classes: dict[str, ClassInfo] = {}
        for header in module.headers:
            for declaration in header.declarations:
                if isinstance(declaration, Class):
                    prefix = naming.c_prefix(declaration.namespace, module.name)
                    self._classes.setdefault(
                        declaration.usr,
                        ClassInfo(
                            declaration,
                            header,
                            f"{prefix}_{declaration.name}",
                        ),
                    )
        self._class_c_types = {usr: info.c_type for usr, info in self._classes.items()}
        # The classes that another class of the module derives from.
        self._base_usrs = {
            base.usr
            for info in self._classes.values()
            for base in info.model.bases
            if base.usr in self._classes
        }
        self._classes = {
            usr: dataclasses.replace(info, is_base=usr in self._base_usrs)
            for usr, info in self._classes.items()
        }
        # The header being built.
        self._header: Header | None = None
        # What the language backends use unqualified in their bindings.
        self._parameter_macros |= self._reservations.names(module)
        # Per header stem, the headers whose structs or classes its functions use.
        self.foreign_struct_headers: dict[str, tuple[Header, ...]] = {}
        self.foreign_class_headers: dict[str, tuple[Header, ...]] = {}

    # Bookkeeping -------------------------------------------------------------

    @staticmethod
    def _count_overloads(module: Module) -> Counter[tuple[str, ...]]:
        counts: Counter[tuple[str, ...]] = Counter()
        for header in module.headers:
            for declaration in header.declarations:
                if isinstance(declaration, Function):
                    counts[("function", *declaration.namespace, declaration.name)] += 1
                elif isinstance(declaration, Class):
                    key = (*declaration.namespace, declaration.name)
                    counts[("constructor", *key)] += len(declaration.constructors)
                    for method in declaration.methods:
                        counts[("method", *key, method.name)] += 1
        return counts

    def _reserve_c_name(
        self, name: str, what: str, location: Location | None, *, tag: bool = False
    ) -> None:
        problem = naming.c_identifier_problem(name)
        if problem is not None:
            self.errors.append(Diagnostic(f"the C name '{name}' of {what} {problem}", location))
            return
        external = self._external.get(name)
        if external is None and tag:
            external = self._external_tags.get(name)
        if external is not None:
            self.errors.append(
                Diagnostic(
                    f"the C name '{name}' of {what} clashes with {external}; rename one of "
                    "them (or choose another --module name)",
                    location,
                )
            )
            return
        previous = self._c_names.get(name)
        if previous is None:
            self._c_names[name] = (what, location)
            return
        previous_what, previous_location = previous
        where = f" (declared at {previous_location})" if previous_location else ""
        self.errors.append(
            Diagnostic(
                f"the C name '{name}' of {what} clashes with {previous_what}{where}; "
                "rename one of them",
                location,
            )
        )

    def _suffix(self, key: tuple[str, ...], parameters: Sequence[Parameter]) -> str:
        if self._overloads[key] > 1:
            return naming.overload_suffix(
                [
                    _token(parameter.type, self._enums, self._struct_c_types, self._class_c_types)
                    for parameter in parameters
                ]
            )
        return ""

    # Enums -------------------------------------------------------------------

    def _enum_plan(
        self, model: Enumeration, header: Header, scope: tuple[Class | Struct, ...]
    ) -> EnumPlan:
        prefix = naming.c_prefix(model.namespace, self.module.name)
        c_type = "_".join((prefix, *model.scope, model.name))
        what = f"enum '{model.qualified_name}'"
        self._reserve_c_name(c_type, f"the type of {what}", model.location)
        enumerators: list[EnumeratorPlan] = []
        for enumerator in model.enumerators:
            macro = f"{c_type}_{enumerator.name}"
            self._reserve_c_name(
                macro,
                f"the constant of '{model.qualified_name}::{enumerator.name}'",
                model.location,
            )
            enumerators.append(
                EnumeratorPlan(
                    model_name=enumerator.name,
                    macro=macro,
                    literal=c_literal(enumerator.value, model.underlying.c_name),
                    value=enumerator.value,
                    deprecated=enumerator.deprecated,
                )
            )
        return EnumPlan(model=model, c_type=c_type, enumerators=tuple(enumerators))

    # Value structs -----------------------------------------------------------------

    def _collect_structs(self, module: Module) -> dict[str, StructPlan]:
        """Every value struct of the module, by USR, with its conversions."""
        models: dict[str, tuple[Struct, Header]] = {}
        for header in module.headers:
            for declaration in header.declarations:
                if isinstance(declaration, Struct):
                    models.setdefault(declaration.usr, (declaration, header))
        self._struct_headers = {usr: header for usr, (_, header) in models.items()}
        self._struct_c_types = {
            usr: f"{naming.c_prefix(model.namespace, module.name)}_{model.name}"
            for usr, (model, _) in models.items()
        }
        plans = {usr: self._struct_plan(model, header) for usr, (model, header) in models.items()}
        for usr, plan in plans.items():
            copies = _Copies()
            self._struct_copies(plan, plans, copies)
            plans[usr] = dataclasses.replace(
                plan,
                to_c=tuple(copies.to_c),
                from_c=tuple(copies.from_c),
                extents=tuple(copies.extents.items()),
            )
        return plans

    def _struct_plan(self, model: Struct, header: Header) -> StructPlan:
        c_type = self._struct_c_types[model.usr]
        what = f"value struct '{model.qualified_name}'"
        self._reserve_c_name(c_type, f"the type of {what}", model.location, tag=True)
        init_name = f"{c_type}_init"
        self._reserve_c_name(init_name, f"the default value function of {what}", model.location)
        cpp_names = [field.name for field in model.fields]
        names = naming.field_names(cpp_names, self._parameter_macros)
        generated = self._macros | {
            enumerator.macro for enum in self._enums.values() for enumerator in enum.enumerators
        }
        fields: list[FieldPlan] = []
        for field, name in zip(model.fields, names, strict=True):
            if field.name in generated:
                # The generated sources would see the macro in place of the member.
                self.errors.append(
                    Diagnostic(
                        f"member '{model.qualified_name}::{field.name}' has the name of a macro "
                        "of the generated C headers; rename it",
                        field.location,
                    )
                )
                continue
            plan = self._field_plan(field, name)
            if plan is not None:
                fields.append(plan)
        detail = f"{self.module.name}_detail"
        cpp_name = "::" + model.qualified_name
        init = CFunction(
            name=init_name,
            params=(
                CParam("out_result", f"{c_type}*", Role.OUT_RESULT, Ffi.struct(c_type).pointer()),
            ),
            returns_status=True,
            # Value-initialization: zeros, then the default member initializers,
            # or the default constructor.
            statement=f"*out_result = {detail}::to_c({cpp_name}());",
            description=f"{model.qualified_name}(): the value of a default-constructed "
            f"{model.qualified_name}",
            ffi_restype=STATUS_FFI,
            checks=(_null_check("out_result"),),
            location=model.location,
        )
        return StructPlan(
            model=model,
            c_type=c_type,
            fields=tuple(fields),
            init=init,
            enums=tuple(self._enums[enum.usr] for enum in model.enums),
        )

    def _field_plan(self, field: Field, name: str) -> FieldPlan | None:
        """How a member crosses: its C declaration."""
        type_ = field.type
        if isinstance(type_, EnumRef):
            enum = self._enum(type_, field.location)
            if enum is None:
                return None
            c_type = enum.c_type
        elif isinstance(type_, StructRef):
            nested = self._struct_c_types.get(type_.usr)
            if nested is None:
                self._struct_missing(type_, field.location)
                return None
            c_type = nested
        else:
            c_type = type_.c_name
        if field.length is None:
            declaration = f"{c_type} {name}"
        else:
            declaration = f"{c_type} {name}[{field.length}]"
        return FieldPlan(model=field, name=name, c_declaration=declaration)

    def _struct_copies(
        self,
        plan: StructPlan,
        plans: dict[str, StructPlan],
        copies: _Copies,
        *,
        c_path: str = "",
        cpp_path: str = "",
        depth: int = 0,
    ) -> None:
        """The member-by-member copies of a struct, both ways, with full member
        paths (``c_path`` in the C mirror, ``cpp_path`` in the C++ struct): a
        nested struct is never bound to a reference, which would be misaligned
        in a packed struct. Loops use the variables _i<depth>."""
        for field in plan.fields:
            pad = "    " * (depth + 1)
            c_member, cpp_member = f"{c_path}.{field.name}", f"{cpp_path}.{field.model.name}"
            inner_depth = depth
            if field.model.length is not None:
                copies.extents[f"{plan.cpp_name}::{field.model.name}"] = field.model.length
                variable = f"_i{depth}"
                loop = (
                    f"{pad}for (size_t {variable} = 0; {variable} < {field.model.length}; "
                    f"++{variable}) {{"
                )
                copies.to_c.append(loop)
                copies.from_c.append(loop)
                c_member += f"[{variable}]"
                cpp_member += f"[{variable}]"
                inner_depth = depth + 1
            inner = "    " * (inner_depth + 1)
            type_ = field.model.type
            if isinstance(type_, StructRef):
                self._struct_copies(
                    plans[type_.usr],
                    plans,
                    copies,
                    c_path=c_member,
                    cpp_path=cpp_member,
                    depth=inner_depth,
                )
            elif isinstance(type_, EnumRef):
                enum = self._enums[type_.usr]
                copies.to_c.append(
                    f"{inner}_out{c_member} = static_cast<{enum.c_type}>(_in{cpp_member});"
                )
                valid = enum.model.valid_range
                if valid is not None:
                    low, high = valid
                    terms = [f"_in{c_member} < {low}"] if low > _INT_MIN else []
                    terms += [f"_in{c_member} > {high}"] if high < _INT_MAX else []
                    if terms:
                        message = (
                            f"'{plan.model.qualified_name}::{field.model.name}' is not a value of "
                            f"{enum.model.qualified_name} ({low}..{high})"
                        )
                        copies.from_c.append(f"{inner}if ({' || '.join(terms)}) {{")
                        copies.from_c.append(f'{inner}    throw argument_error{{"{message}"}};')
                        copies.from_c.append(f"{inner}}}")
                copies.from_c.append(
                    f"{inner}_out{cpp_member} = static_cast<{enum.cpp_name}>(_in{c_member});"
                )
            else:
                copies.to_c.append(f"{inner}_out{c_member} = _in{cpp_member};")
                copies.from_c.append(f"{inner}_out{cpp_member} = _in{c_member};")
            if field.model.length is not None:
                copies.to_c.append(f"{pad}}}")
                copies.from_c.append(f"{pad}}}")

    def _struct(self, ref: StructRef, location: Location) -> StructPlan | None:
        """The struct of a reference, or None (reported) if no wrapped header defines it."""
        struct = self._structs.get(ref.usr)
        if struct is None:
            self._struct_missing(ref, location)
        return struct

    def _struct_missing(self, ref: StructRef, location: Location) -> None:
        if ref.qualified_name in self._handle_names:
            message = (
                f"'{ref.qualified_name}': classes passed by value are not supported yet "
                "(only structs with public data members are)"
            )
        elif ref.system:
            message = (
                f"'{ref.qualified_name}' of a system header: classes passed by value are not "
                "supported yet (it is not one of the wrapped headers)"
            )
        elif ref.definition is None:
            message = f"struct '{ref.qualified_name}' has no definition in the wrapped headers"
        else:
            message = (
                f"struct '{ref.qualified_name}' is defined in {ref.definition.file}, which is "
                "not one of the wrapped headers"
            )
        self.errors.append(Diagnostic(message, location))

    def _enum(self, ref: EnumRef, location: Location) -> EnumPlan | None:
        """The enum of a reference, or None (reported) if no wrapped header defines it."""
        enum = self._enums.get(ref.usr)
        if enum is not None:
            return enum
        if ref.system:
            message = (
                f"enum '{ref.qualified_name}' of a system header is not supported (it is not "
                "one of the wrapped headers)"
            )
        elif ref.definition is None:
            message = (
                f"enum '{ref.qualified_name}' has no definition in the wrapped headers "
                "(opaque enums are not supported yet)"
            )
        else:
            message = (
                f"enum '{ref.qualified_name}' is defined in {ref.definition.file}, which is "
                "not one of the wrapped headers"
            )
        self.errors.append(Diagnostic(message, location))
        return None

    # Wrapped classes ---------------------------------------------------------------

    def _as_class(self, type_: Type) -> ClassRef | None:
        """The use of a class that a type is: a ClassRef, or a StructRef (by value
        or by const reference) of a class that the header only declares."""
        if isinstance(type_, ClassRef):
            return type_
        if isinstance(type_, StructRef) and type_.usr in self._classes:
            form = ClassForm.CONST_REFERENCE if type_.by_reference else ClassForm.VALUE
            return ClassRef(type_.usr, type_.qualified_name, type_.definition, form, type_.system)
        return None

    def _class(self, ref: ClassRef, location: Location) -> ClassInfo | None:
        """The class of a reference, or None (reported) if it is no class of the module."""
        info = self._classes.get(ref.usr)
        if info is not None and ref.form is ClassForm.VALUE and info.model.abstract:
            self.errors.append(
                Diagnostic(
                    f"abstract class '{ref.qualified_name}' cannot be passed or returned by "
                    "value (it cannot be copied): use a reference, a pointer or std::unique_ptr",
                    location,
                )
            )
            return None
        if info is not None:
            return info
        name = ref.qualified_name
        if ref.usr in self._structs:
            message = (
                f"'{name}' is a value struct: pointers, non-const references and "
                "std::unique_ptr of it are not supported yet (pass it by value or as a const "
                "reference)"
            )
        elif ref.system:
            message = (
                f"class '{name}' of a system header is not a class of this module "
                "(it is not one of the wrapped headers)"
            )
        elif ref.definition is None:
            message = (
                f"class '{name}' is not a class of this module: no wrapped header defines it "
                "(wrap the header that does)"
            )
        else:
            message = (
                f"class '{name}' is not a class of this module: it is defined in "
                f"{ref.definition.file}, which is not one of the wrapped headers"
            )
        self.errors.append(Diagnostic(message, location))
        return None

    def _is_base(self, info: ClassInfo) -> bool:
        return info.is_base

    # Lowering: how each type crosses the boundary --------------------------------

    def _lower_params(
        self,
        parameters: Sequence[Parameter],
        location: Location,
        policy: Resolved,
        reserved: Iterable[str] = (),
    ) -> list[LoweredParam]:
        """``policy`` says which of the parameters are nullable, taken or kept."""
        callbacks = [
            parameter.type for parameter in parameters if isinstance(parameter.type, CallbackType)
        ]
        taken = (
            self._parameter_macros
            | frozenset(reserved)
            | _callback_locals(callbacks)
            | self._reservations.locals(len(parameters))
            | self._reservations.parameter_words
        )
        cpp_names = [parameter.name for parameter in parameters]
        names = naming.parameter_names(cpp_names, taken)
        sizes = naming.derived_names(
            cpp_names,
            names,
            [
                isinstance(parameter.type, ContainerType | StringType) and parameter.type.sized
                for parameter in parameters
            ],
            "_size",
            taken,
        )
        is_callback = [isinstance(parameter.type, CallbackType) for parameter in parameters]
        derived = taken | {size for size in sizes if size is not None}
        user_data = naming.derived_names(cpp_names, names, is_callback, "_user_data", derived)
        derived |= {name for name in user_data if name is not None}
        releases = naming.derived_names(cpp_names, names, is_callback, "_release", derived)
        derived |= {name for name in releases if name is not None}
        owner_names = naming.derived_names(
            cpp_names,
            names,
            ["transfer" in words for words in policy.parameters],
            "_owner",
            derived,
        )
        lowered: list[LoweredParam] = []
        owners = 0
        for position, (name, parameter) in enumerate(zip(names, parameters, strict=True)):
            type_ = parameter.type
            if isinstance(type_, CallbackType):
                owners += 1
                data, release = user_data[position], releases[position]
                assert data is not None
                assert release is not None
                lowered.append(
                    self._lower_callback_param(
                        (name, data, release, f"_owner{owners}"), type_, location
                    )
                )
            else:
                words = policy.parameters[position]
                param = self._lower_param(
                    name, sizes[position], type_, location, owner=owner_names[position], words=words
                )
                spec = param.spec and dataclasses.replace(param.spec, words=words)
                lowered.append(dataclasses.replace(param, words=words, spec=spec))
        return lowered

    # Policies -------------------------------------------------------------------------

    def _kind(self, type_: Type) -> Kind:
        """What an annotation sees of a type."""
        ref = self._as_class(type_)
        if ref is not None:
            return {
                ClassForm.VALUE: Kind.OBJECT_VALUE,
                ClassForm.REFERENCE: Kind.OBJECT_REFERENCE,
                ClassForm.CONST_REFERENCE: Kind.OBJECT_REFERENCE,
                ClassForm.POINTER: Kind.OBJECT_POINTER,
                ClassForm.CONST_POINTER: Kind.OBJECT_CONST_POINTER,
                ClassForm.UNIQUE: Kind.UNIQUE_PTR,
            }[ref.form]
        if isinstance(type_, StringType):
            return Kind.C_STRING if type_.kind is StringKind.C_STRING else Kind.STRING
        if isinstance(type_, CallbackType):
            return Kind.CALLBACK
        return Kind.OTHER

    def _policy(
        self,
        c_name: str,
        what: str,
        parameters: Sequence[Parameter],
        result: Type | None,
        location: Location,
        *,
        constructor: bool = False,
    ) -> Resolved:
        """The policy of a generated C function: its annotations, and what the
        types imply. Reports what is wrong with them."""
        self.annotatable.append(c_name)
        resolved, problems = resolve(
            c_name,
            what,
            [Param(parameter.name, self._kind(parameter.type)) for parameter in parameters],
            None if result is None or result.is_void else self._kind(result),
            constructor=constructor,
            annotations=self._annotations,
            location=location,
        )
        self.errors.extend(problems)
        return resolved

    def _lower_param(
        self,
        name: str,
        size: str | None,
        type_: Type,
        location: Location,
        *,
        owner: str | None = None,
        words: frozenset[str] = frozenset(),
    ) -> LoweredParam:
        assert not isinstance(type_, CallbackType)  # see _lower_callback_param
        if isinstance(type_, StringType):
            lowered = self._lower_string_param(name, size, type_, "nullable" in words)
            return dataclasses.replace(lowered, spec=ParamSpec(name, type_, size=size))
        if isinstance(type_, EnumRef):
            enum = self._enum(type_, location)
            lowered = self._lower_enum_param(name, enum)
            if enum is None:  # reported
                return lowered
            return dataclasses.replace(lowered, spec=ParamSpec(name, type_, enum=enum))
        class_ref = self._as_class(type_)
        if class_ref is not None:
            return self._lower_class_param(name, class_ref, location, owner, words)
        assert not isinstance(type_, ClassRef)  # _as_class
        if isinstance(type_, StructRef):
            struct = self._struct(type_, location)
            lowered = self._lower_struct_param(name, type_, struct)
            if struct is None:  # reported
                return lowered
            return dataclasses.replace(lowered, spec=ParamSpec(name, type_, struct=struct))
        if isinstance(type_, ContainerType) and type_.kind is ContainerKind.ARRAY:
            return self._lower_array_param(name, type_, location)
        if isinstance(type_, ContainerType):
            assert size is not None
            return self._lower_vector_param(name, size, type_, location)
        return LoweredParam(
            c_params=(CParam(name, type_.c_name, Role.INPUT, Ffi.scalar(type_.c_name)),),
            argument=name,
            null_checks=(),
            value_checks=(),
            spec=ParamSpec(name, type_),
        )

    def _lower_callback_param(
        self, names: tuple[str, str, str, str], type_: CallbackType, location: Location
    ) -> LoweredParam:
        """A std::function: three C parameters (see the runtime header). ``names``:
        the callback, its user_data, its release function and its owner."""
        name, user_data, release, owner = names
        detail = f"{self.module.name}_detail"
        c_args = ["void*"]
        lambda_params = [f"decltype({name}) _callback", "void* _user_data"]
        passed = ["_user_data"]
        tokens: list[str] = []
        for position, part in enumerate(type_.arguments, start=1):
            local = f"_arg{position}"
            lowered = self._callback_argument(part, local, location)
            if lowered is None:  # reported
                return LoweredParam((), name, (), ())
            cpp, c_types, pass_c, token = lowered
            lambda_params.append(f"{cpp} {local}")
            c_args += c_types
            passed += pass_c
            tokens.append(token)
        result = self._callback_result(type_.result, location)
        if result is None:  # reported
            return LoweredParam((), name, (), ())
        result_cpp, result_c, result_token = result
        call = f"_callback({', '.join(passed)}"
        if result_c is None:
            body = f"{call});"
        else:
            c_args.append(f"{result_c}*")
            back = "_result" if result_cpp == result_c else f"static_cast<{result_cpp}>(_result)"
            valid = self._valid_range(type_.result, location)
            if valid is not None:
                # Another value would be undefined behaviour; 0 is always valid,
                # and it is what a callback that does not write gives. int
                # needs no check at its own limits.
                terms = [f"_result < {valid[0]}"] if valid[0] > _INT_MIN else []
                terms += [f"_result > {valid[1]}"] if valid[1] < _INT_MAX else []
                if terms:
                    back = f"{' || '.join(terms)} ? {result_cpp}() : {back}"
            body = f"{result_c} _result{{}}; {call}, &_result); return {back};"
        signature = self._callback_signature(type_, location)
        invoke = f"[]({', '.join(lambda_params)}) -> {result_cpp} {{ {body} }}"
        argument = (
            f"{detail}::make_callback<::std::function<{signature}>, {result_cpp}>("
            f"{name}, {owner}, {invoke})"
        )
        # The signature of the function pointer, which the backends name their types by.
        thunk_key = "__".join([result_token, *tokens])
        return LoweredParam(
            c_params=(
                CParam(
                    name,
                    "void",
                    Role.INPUT,
                    Ffi(FfiKind.CALLBACK, thunk_key),
                    function_params=", ".join(c_args),
                ),
                CParam(user_data, "void*", Role.INPUT, VOID_POINTER_FFI),
                CParam(release, "void", Role.INPUT, Ffi(FfiKind.RELEASE), function_params="void*"),
            ),
            argument=argument,
            null_checks=(),
            value_checks=(),
            prologue=(f"{detail}::callback_owner {owner}({release}, {user_data});",),
            probe=f"::std::function<{signature}>()",
            spec=ParamSpec(
                name,
                type_,
                callback=CallbackSpec(
                    tuple(self._callback_part(part, location) for part in type_.arguments),
                    self._callback_part(type_.result, location),
                ),
                user_data=user_data,
                release=release,
            ),
        )

    def _callback_part(self, type_: Type, location: Location) -> CallbackPart:
        """An argument or the result of a callback, with the enum or struct it names
        (already checked: a callback that names an unknown one was reported)."""
        if isinstance(type_, EnumRef):
            return CallbackPart(type_, enum=self._enum(type_, location))
        if isinstance(type_, StructRef):
            return CallbackPart(type_, struct=self._struct(type_, location))
        return CallbackPart(type_)

    def _callback_argument(
        self, type_: Type, local: str, location: Location
    ) -> tuple[str, list[str], list[str], str] | None:
        """How a callback argument crosses to C: (the C++ parameter of the invoke
        lambda, its C types, the C arguments, the token of the signature name)."""
        detail = f"{self.module.name}_detail"
        if isinstance(type_, StringType):
            if type_.kind is StringKind.C_STRING:
                return "const char*", ["const char*"], [local], "cstr"
            cpp = (
                "const ::std::string&" if type_.kind is StringKind.STRING else "::std::string_view"
            )
            return (
                cpp,
                ["const char*", "size_t"],
                [f"{local}.data()", f"{local}.size()"],
                "string",
            )
        if isinstance(type_, EnumRef):
            enum = self._enum(type_, location)
            if enum is None:
                return None
            return (
                enum.cpp_name,
                [enum.c_type],
                [f"static_cast<{enum.c_type}>({local})"],
                enum.c_type,
            )
        if isinstance(type_, StructRef):
            struct = self._struct(type_, location)
            if struct is None:
                return None
            return (
                f"const {struct.cpp_name}&",
                [f"const {struct.c_type}*"],
                [f"{detail}::address_of({detail}::to_c({local}))"],
                struct.c_type,
            )
        assert isinstance(type_, ScalarType)
        return type_.c_name, [type_.c_name], [local], type_.token

    def _callback_result(
        self, type_: ScalarType | EnumRef, location: Location
    ) -> tuple[str, str | None, str] | None:
        """How a callback result crosses from C: (its C++ type, its C type or None
        for void, the token of the signature name)."""
        if isinstance(type_, EnumRef):
            enum = self._enum(type_, location)
            if enum is None:
                return None
            return enum.cpp_name, enum.c_type, enum.c_type
        if type_.is_void:
            return "void", None, "void"
        return type_.c_name, type_.c_name, type_.token

    def _valid_range(self, type_: Type, location: Location) -> tuple[int, int] | None:
        """The values of an enum without a fixed underlying type, else None."""
        if not isinstance(type_, EnumRef):
            return None
        enum = self._enum(type_, location)
        return None if enum is None else enum.model.valid_range

    def _callback_signature(self, type_: CallbackType, location: Location) -> str:
        """The function type of a std::function, fully qualified, as the header
        declares it (a reference too): the generated std::function must have
        its exact type."""

        arguments = ", ".join(
            self._spell(argument, reference, location)
            for argument, reference in zip(type_.arguments, type_.references, strict=True)
        )
        return f"{self._spell(type_.result, False, location)} ({arguments})"

    def _spell(self, part: Type, reference: bool, location: Location) -> str:
        """A scalar, enum, struct or string as a signature declares it, fully qualified
        (the C++ spelling of the model is not a libclang one)."""
        if isinstance(part, EnumRef):
            enum = self._enum(part, location)
            name = enum.cpp_name if enum is not None else "::" + part.qualified_name
        elif isinstance(part, StructRef):
            name = "::" + part.qualified_name
            return f"const {name}&" if part.by_reference else name
        elif isinstance(part, StringType):
            if part.kind is StringKind.C_STRING:
                return "const char*"
            name = f"::std::{part.kind.value}"
            return f"const {name}&" if part.by_reference else name
        else:
            assert isinstance(part, ScalarType)
            name = part.c_name
        return f"const {name}&" if reference else name

    def _lower_string_param(
        self, name: str, size: str | None, type_: StringType, nullable: bool = False
    ) -> LoweredParam:
        data = CParam(name, "const char*", Role.INPUT, Ffi(FfiKind.CHAR_POINTER))
        if size is None:
            # NUL-terminated: the bindings must not pass NULs, which C++ would not see.
            return LoweredParam(
                c_params=(data,),
                argument=name,
                null_checks=() if nullable else (_null_check(name),),
                value_checks=(),
            )
        constructor = "std::string" if type_.kind is StringKind.STRING else "std::string_view"
        detail = f"{self.module.name}_detail"
        temporary = f"{constructor}({detail}::string_data({name}), {size})"
        # A const reference gets an lvalue, which only the overloads for const
        # references take (see _temporaries_check); a value gets the temporary.
        argument = (
            f"static_cast<const {constructor}&>({temporary})" if type_.by_reference else temporary
        )
        return LoweredParam(
            c_params=(data, CParam(size, "size_t", Role.SIZE, SIZE_FFI)),
            argument=argument,
            temporary=f"{constructor}()" if type_.by_reference else None,
            null_checks=(_sized_null_check(name, size),),
            value_checks=(),
        )

    def _lower_class_param(
        self,
        name: str,
        ref: ClassRef,
        location: Location,
        owner: str | None,
        words: frozenset[str] = frozenset(),
    ) -> LoweredParam:
        """A wrapped class: a handle, which is a copy for ``T``, else the object itself."""
        info = self._class(ref, location)
        if info is None:  # reported
            return LoweredParam((), name, (), ())
        nullable = "nullable" in words
        if "transfer" in words:
            assert owner is not None
            taken = self._lower_taken_param(name, info, owner, ref.form, nullable)
            return dataclasses.replace(taken, spec=ParamSpec(name, ref, cls=info))
        constant = ref.form in (ClassForm.VALUE, ClassForm.CONST_REFERENCE, ClassForm.CONST_POINTER)
        qualifier = "const " if constant else ""
        pointer = f"reinterpret_cast<{qualifier}{info.cpp_name}*>({name})"
        is_pointer = ref.form in (ClassForm.POINTER, ClassForm.CONST_POINTER)
        checks: list[str] = []
        if ref.form is ClassForm.VALUE:
            # The copy is made by the call; the compiler's error would be a template one.
            message = (
                f"parameter '{name}' takes {info.model.qualified_name} by value, which cannot "
                "be copied; take it by reference"
            )
            checks.append(
                f"static_assert(std::is_copy_constructible<{info.cpp_name}>::value,\n"
                f'                  "{message}");'
            )
        return LoweredParam(
            c_params=(
                CParam(
                    name,
                    f"{qualifier}{info.c_type}*",
                    Role.INPUT,
                    Ffi.handle(info.c_type),
                ),
            ),
            argument=pointer if is_pointer else f"*{pointer}",
            null_checks=() if nullable else (_null_check(name),),
            value_checks=(),
            compile_checks=tuple(checks),
            spec=ParamSpec(name, ref, cls=info),
        )

    def _lower_taken_param(
        self, name: str, info: ClassInfo, owner: str, form: ClassForm, nullable: bool
    ) -> LoweredParam:
        """A ``std::unique_ptr<T>`` or a ``T*`` that the function takes: the C
        function owns the object until the wrapped function takes it, whatever
        the status (``transferred`` deletes it on every return that comes first)."""
        detail = f"{self.module.name}_detail"
        taken = f"{info.cpp_name}*" if form is ClassForm.POINTER else None
        taken = taken or f"std::unique_ptr<{info.cpp_name}>"
        return LoweredParam(
            c_params=(CParam(name, f"{info.c_type}*", Role.INPUT, Ffi.handle(info.c_type)),),
            argument=f"{owner}.take<{taken}>()",
            null_checks=() if nullable else (_null_check(name),),
            value_checks=(),
            prologue=(
                f"{detail}::transferred<{info.cpp_name}> {owner}"
                f"(reinterpret_cast<{info.cpp_name}*>({name}));",
            ),
            transfers=True,
        )

    def _lower_enum_param(self, name: str, enum: EnumPlan | None) -> LoweredParam:
        if enum is None:  # reported
            return LoweredParam((), name, (), ())
        checks: list[Precondition] = []
        valid = enum.model.valid_range
        if valid is not None:
            # Other values are undefined behaviour for an enum without a fixed
            # underlying type. int needs no check at its own limits.
            low, high = valid
            terms = [f"{name} < {low}"] if low > _INT_MIN else []
            terms += [f"{name} > {high}"] if high < _INT_MAX else []
            if terms:
                checks.append(
                    Precondition(
                        " || ".join(terms),
                        "invalid_argument",
                        f"'{name}' is not a value of {enum.model.qualified_name} ({low}..{high})",
                    )
                )
        return LoweredParam(
            c_params=(
                CParam(name, enum.c_type, Role.INPUT, Ffi.scalar(enum.model.underlying.c_name)),
            ),
            argument=f"static_cast<{enum.cpp_name}>({name})",
            null_checks=(),
            value_checks=tuple(checks),
        )

    def _lower_struct_param(
        self, name: str, ref: StructRef, struct: StructPlan | None
    ) -> LoweredParam:
        if struct is None:  # reported
            return LoweredParam((), name, (), ())
        detail = f"{self.module.name}_detail"
        temporary = f"{detail}::from_c(*{name})"
        # As for strings: a const reference gets an lvalue (see _temporaries_check).
        argument = (
            f"static_cast<const {struct.cpp_name}&>({temporary})" if ref.by_reference else temporary
        )
        return LoweredParam(
            c_params=(
                CParam(
                    name,
                    f"const {struct.c_type}*",
                    Role.INPUT,
                    Ffi.struct(struct.c_type).pointer(),
                ),
            ),
            argument=argument,
            null_checks=(_null_check(name),),
            value_checks=(),
            temporary=f"{struct.cpp_name}()" if ref.by_reference else None,
        )

    def _element(self, type_: ElementType, location: Location) -> _Element | None:
        """How the elements of a container cross the boundary."""
        detail = f"{self.module.name}_detail"
        if isinstance(type_, StringType):
            return _Element(
                c_type=self.module.name + "_string",
                cpp="std::string",
                token="string",
                ffi_in=Ffi(FfiKind.STRING_IN),
                ffi_out=Ffi(FfiKind.STRING),
            )
        if isinstance(type_, EnumRef):
            enum = self._enum(type_, location)
            if enum is None:
                return None
            valid = enum.model.valid_range
            to_cpp = f"{detail}::cast_to<{enum.cpp_name}>()"
            if valid is not None:
                message = (
                    f"an element of '{{name}}' is not a value of {enum.model.qualified_name} "
                    f"({valid[0]}..{valid[1]})"
                )
                to_cpp = (
                    f'{detail}::enum_in<{enum.cpp_name}>{{{valid[0]}, {valid[1]}, "{message}"}}'
                )
            return _Element(
                c_type=enum.c_type,
                cpp=enum.cpp_name,
                token=enum.c_type,
                ffi_in=Ffi.scalar(enum.model.underlying.c_name),
                ffi_out=Ffi.scalar(enum.model.underlying.c_name),
                to_cpp=to_cpp,
                to_c=f"{detail}::cast_to<{enum.c_type}>()",
                enum=enum,
            )
        if isinstance(type_, StructRef):
            struct = self._struct(type_, location)
            if struct is None:
                return None
            return _Element(
                c_type=struct.c_type,
                cpp=struct.cpp_name,
                token=struct.c_type,
                ffi_in=Ffi.struct(struct.c_type),
                ffi_out=Ffi.struct(struct.c_type),
                # Not noexcept: building the C++ struct may throw.
                to_cpp=f"[](const {struct.c_type}& _item) {{ return {detail}::from_c(_item); }}",
                to_c=(
                    f"[](const {struct.cpp_name}& _item) noexcept "
                    f"{{ return {detail}::to_c(_item); }}"
                ),
                struct=struct,
            )
        return _Element(
            c_type=type_.c_name,
            cpp=type_.c_name,
            token=type_.token,
            ffi_in=Ffi.scalar(type_.c_name),
            ffi_out=Ffi.scalar(type_.c_name),
        )

    def _lower_vector_param(
        self, name: str, size: str, type_: ContainerType, location: Location
    ) -> LoweredParam:
        element = self._element(type_.element, location)
        if element is None:  # reported
            return LoweredParam((), name, (), ())
        detail = f"{self.module.name}_detail"
        vector = f"std::vector<{element.cpp}>"
        checks = [_sized_null_check(name, size)]
        span = type_.kind is ContainerKind.SPAN
        if isinstance(type_.element, StringType):
            temporary = f"{detail}::strings_in<{vector}>({name}, {size})"
            checks.append(
                Precondition(
                    f"{detail}::has_null_string({name}, {size})",
                    "null_argument",
                    f"an element of '{name}' is NULL with a size that is not 0",
                )
            )
        else:
            converter = (
                "" if element.to_cpp is None else ", " + element.to_cpp.replace("{name}", name)
            )
            temporary = f"{detail}::vector_in<{vector}>({name}, {size}{converter})"
        if span and element.to_cpp is None and not isinstance(type_.element, StringType):
            # The same type on both sides: a view of the caller's elements.
            temporary = f"std::span<const {element.cpp}>({name}, {size})"
        elif span:
            # A view of the converted copy, which lives until the end of the
            # call: as a std::vector, the argument would rather go to an
            # overload for vectors.
            temporary = f"std::span<const {element.cpp}>({temporary})"
        by_reference = type_.by_reference and not span  # a span is a view anyway
        argument = f"static_cast<const {vector}&>({temporary})" if by_reference else temporary
        pointer = element.ffi_in.pointer()
        return LoweredParam(
            c_params=(
                CParam(name, f"const {element.c_type}*", Role.INPUT, pointer),
                CParam(size, "size_t", Role.SIZE, SIZE_FFI),
            ),
            argument=argument,
            null_checks=tuple(checks),
            value_checks=(),
            temporary=f"{vector}()" if by_reference else None,
            spec=ParamSpec(
                name,
                type_,
                size=size,
                element=ElementSpec(type_.element, element.enum, element.struct),
            ),
        )

    def _lower_array_param(
        self, name: str, type_: ContainerType, location: Location
    ) -> LoweredParam:
        element = self._element(type_.element, location)
        if element is None:  # reported
            return LoweredParam((), name, (), ())
        detail = f"{self.module.name}_detail"
        array = f"std::array<{element.cpp}, {type_.length}>"
        converter = "" if element.to_cpp is None else ", " + element.to_cpp.replace("{name}", name)
        if type_.by_reference or type_.rvalue_reference:
            # On the heap, which the C++ API needs no more than the stack: a
            # large array would overflow the stack as a temporary.
            heap = f"*{detail}::array_on_heap<{array}>({name}{converter})"
            reference = "const " + array + "&" if type_.by_reference else array + "&&"
            argument = f"static_cast<{reference}>({heap})"
        else:
            argument = f"{detail}::array_in<{array}>({name}{converter})"
        return LoweredParam(
            c_params=(
                CParam(
                    name,
                    f"const {element.c_type}",
                    Role.INPUT,
                    element.ffi_in.pointer(),
                    type_.length,
                ),
            ),
            argument=argument,
            null_checks=(_null_check(name),),
            value_checks=(),
            temporary=f"{array}()" if type_.by_reference else None,
            spec=ParamSpec(
                name, type_, element=ElementSpec(type_.element, element.enum, element.struct)
            ),
        )

    def _lower_array_result(self, type_: ContainerType, location: Location) -> LoweredResult:
        element = self._element(type_.element, location)
        if element is None:  # reported
            return LoweredResult((), "{call};", ())
        detail = f"{self.module.name}_detail"
        converter = "" if element.to_c is None else f", {element.to_c}"
        array = f"std::array<{element.cpp}, {type_.length}>"
        return LoweredResult(
            c_params=(
                CParam(
                    "out_result",
                    element.c_type,
                    Role.OUT_RESULT,
                    element.ffi_out.pointer(),
                    type_.length,
                ),
            ),
            # The caller's array, which may be an input: written on success only.
            # N may differ on the target (std::array<T, sizeof(long)>): copy_to
            # checks the type, so that it cannot write past the caller's array.
            statement=f"{detail}::copy_to<{array}>({{call}}, out_result{converter});",
            null_checks=(_null_check("out_result"),),
            spec=ResultSpec(
                type_, element=ElementSpec(type_.element, element.enum, element.struct)
            ),
        )

    def _lower_vector_result(self, type_: ContainerType, location: Location) -> LoweredResult:
        element = self._element(type_.element, location)
        if element is None:  # reported
            return LoweredResult((), "{call};", ())
        detail = f"{self.module.name}_detail"
        if isinstance(type_.element, StringType):
            statement = f"{detail}::copy_out_strings({{call}}, out_result, out_result_size);"
        else:
            converter = "" if element.to_c is None else f", {element.to_c}"
            statement = f"{detail}::copy_out({{call}}, out_result, out_result_size{converter});"
        return LoweredResult(
            c_params=(
                CParam(
                    "out_result",
                    f"{element.c_type}**",
                    Role.OUT_RESULT,
                    element.ffi_out.pointer().pointer(),
                ),
                CParam(
                    "out_result_size",
                    "size_t*",
                    Role.OUT_RESULT_SIZE,
                    SIZE_FFI.pointer(),
                ),
            ),
            statement=statement,
            null_checks=(),
            owned_checks=(_null_check("out_result"), _null_check("out_result_size")),
            resets=("*out_result = nullptr;", "*out_result_size = 0;"),
            spec=ResultSpec(
                type_, element=ElementSpec(type_.element, element.enum, element.struct)
            ),
        )

    def _lower_struct_result(self, struct: StructPlan | None) -> LoweredResult:
        if struct is None:  # reported
            return LoweredResult((), "{call};", ())
        detail = f"{self.module.name}_detail"
        return LoweredResult(
            c_params=(
                CParam(
                    "out_result",
                    f"{struct.c_type}*",
                    Role.OUT_RESULT,
                    Ffi.struct(struct.c_type).pointer(),
                ),
            ),
            # Caller storage, which may alias an input: written on success only,
            # after the call.
            statement=f"*out_result = {detail}::to_c({{call}});",
            null_checks=(_null_check("out_result"),),
        )

    def _lower_class_result(
        self,
        ref: ClassRef,
        location: Location,
        is_method: bool,
        ownership: str | None,
    ) -> LoweredResult:
        """A wrapped class: a new object that the caller owns (by value, in a
        std::unique_ptr, or a raw pointer annotated ``owned``), or one that the
        C++ code keeps owning (a reference, or a pointer annotated ``borrowed``).

        ``ownership`` is the annotation of a raw pointer (None for the others,
        and when it is missing, which the policy reported).
        """
        info = self._class(ref, location)
        placeholder = LoweredResult((), "{call};", ())
        if info is None:  # reported
            return placeholder
        pointer_form = ref.form in (ClassForm.POINTER, ClassForm.CONST_POINTER)
        if pointer_form and ownership is None:  # reported by the policy
            return placeholder
        pointer = Ffi.handle(info.c_type).pointer()
        destroy = f"{info.c_type}_destroy"
        borrowed = ref.form in (ClassForm.REFERENCE, ClassForm.CONST_REFERENCE) or (
            ownership == "borrowed"
        )
        if borrowed:
            constant = ref.form in (ClassForm.CONST_REFERENCE, ClassForm.CONST_POINTER)
            qualifier = "const " if constant else ""
            origin = " from self" if is_method else ""
            if pointer_form:
                statement = f"*out_result = reinterpret_cast<{qualifier}{info.c_type}*>({{call}});"
                nullability = "may be NULL"
            else:
                statement = (
                    f"*out_result = reinterpret_cast<{qualifier}{info.c_type}*>("
                    f"{self.module.name}_detail::object_address({{call}}));"
                )
                nullability = "never NULL"
            return LoweredResult(
                c_params=(
                    CParam(
                        "out_result",
                        f"{qualifier}{info.c_type}**",
                        Role.OUT_RESULT,
                        pointer,
                    ),
                ),
                statement=statement,
                null_checks=(_null_check("out_result"),),
                notes=(f"*out_result: borrowed{origin}, {nullability}; do not destroy it.",),
                spec=ResultSpec(ref, cls=info, ownership=ownership),
            )
        # An object that the caller owns: the bindings make the object that will own it
        # before the call, so that from the moment the C function writes the handle,
        # its destruction is guaranteed.
        nullable = ref.form is not ClassForm.VALUE
        if ref.form is ClassForm.UNIQUE:
            statement = f"*out_result = reinterpret_cast<{info.c_type}*>({{call}}.release());"
        elif pointer_form:
            statement = f"*out_result = reinterpret_cast<{info.c_type}*>({{call}});"
        else:
            statement = (
                f"*out_result = reinterpret_cast<{info.c_type}*>(new {info.cpp_name}({{call}}));"
            )
        if nullable:
            note = f"*out_result: owned by the caller (may be NULL); destroy it with {destroy}."
        else:
            note = f"*out_result: owned by the caller; destroy it with {destroy}."
        return LoweredResult(
            c_params=(
                CParam(
                    "out_result",
                    f"{info.c_type}**",
                    Role.OUT_RESULT,
                    pointer,
                ),
            ),
            statement=statement,
            null_checks=(),
            owned_checks=(_null_check("out_result"),),
            resets=("*out_result = nullptr;",),
            notes=(note,),
            spec=ResultSpec(ref, cls=info, ownership=ownership),
        )

    def _lower_enum_result(self, enum: EnumPlan | None) -> LoweredResult:
        if enum is None:  # reported
            return LoweredResult((), "{call};", ())
        return LoweredResult(
            c_params=(
                CParam(
                    "out_result",
                    f"{enum.c_type}*",
                    Role.OUT_RESULT,
                    Ffi.scalar(enum.model.underlying.c_name).pointer(),
                ),
            ),
            statement=f"*out_result = static_cast<{enum.c_type}>({{call}});",
            null_checks=(_null_check("out_result"),),
        )

    def _lower_result(
        self,
        type_: Type,
        location: Location,
        policy: Resolved,
        is_method: bool = False,
    ) -> LoweredResult:
        assert not isinstance(type_, CallbackType)  # the parser rejects them
        if type_.is_void:
            return LoweredResult((), "{call};", ())
        if isinstance(type_, StringType):
            lowered = self._lower_string_result(type_)
            return dataclasses.replace(
                lowered, spec=ResultSpec(type_, as_bytes=policy.result_bytes)
            )
        if isinstance(type_, EnumRef):
            enum = self._enum(type_, location)
            lowered = self._lower_enum_result(enum)
            if enum is None:  # reported
                return lowered
            return dataclasses.replace(lowered, spec=ResultSpec(type_, enum=enum))
        class_ref = self._as_class(type_)
        if class_ref is not None:
            return self._lower_class_result(class_ref, location, is_method, policy.result)
        assert not isinstance(type_, ClassRef)  # _as_class
        if isinstance(type_, StructRef):
            struct = self._struct(type_, location)
            lowered = self._lower_struct_result(struct)
            if struct is None:  # reported
                return lowered
            return dataclasses.replace(lowered, spec=ResultSpec(type_, struct=struct))
        if isinstance(type_, ContainerType) and type_.kind is ContainerKind.ARRAY:
            return self._lower_array_result(type_, location)
        if isinstance(type_, ContainerType):
            return self._lower_vector_result(type_, location)
        return LoweredResult(
            c_params=(
                CParam(
                    "out_result",
                    f"{type_.c_name}*",
                    Role.OUT_RESULT,
                    Ffi.scalar(type_.c_name).pointer(),
                ),
            ),
            statement="*out_result = {call};",
            null_checks=(_null_check("out_result"),),
            spec=ResultSpec(type_),
        )

    def _lower_string_result(self, type_: StringType) -> LoweredResult:
        detail = f"{self.module.name}_detail"
        helper = "return_string" if type_.sized else "return_c_string"
        return LoweredResult(
            c_params=(
                CParam("out_result", "char**", Role.OUT_RESULT, VOID_POINTER_FFI.pointer()),
                CParam(
                    "out_result_size",
                    "size_t*",
                    Role.OUT_RESULT_SIZE,
                    SIZE_FFI.pointer(),
                ),
            ),
            statement=f"{detail}::{helper}({{call}}, out_result, out_result_size);",
            null_checks=(),
            owned_checks=(_null_check("out_result"), _null_check("out_result_size")),
            resets=("*out_result = nullptr;", "*out_result_size = 0;"),
        )

    def _status_function(
        self,
        name: str,
        params: Sequence[LoweredParam],
        call: str,
        result: Type,
        description: str,
        *,
        location: Location,
        policy: Resolved,
        self_param: CParam | None = None,
        keeper: str = "the library keeps",
    ) -> tuple[CFunction, LoweredResult]:
        """A function that returns a status and passes the result through out-parameters.

        ``call`` is the wrapped call, with ``{arguments}`` where the arguments go.
        """
        lowered = self._lower_result(result, location, policy, self_param is not None)
        leading = (self_param,) if self_param is not None else ()
        null_checks = [_null_check(param.name) for param in leading]
        for param in params:
            null_checks += param.null_checks
        null_checks += lowered.null_checks
        arguments = ", ".join(param.argument for param in params)
        function = CFunction(
            name=name,
            params=(
                *leading,
                *(c for param in params for c in param.c_params),
                *lowered.c_params,
            ),
            returns_status=True,
            statement=lowered.statement.replace("{call}", call.replace("{arguments}", arguments)),
            description=description,
            ffi_restype=STATUS_FFI,
            owned_checks=lowered.owned_checks,
            resets=lowered.resets,
            checks=(*null_checks, *(c for param in params for c in param.value_checks)),
            location=location,
            compile_checks=(
                *_temporaries_check(call, params, f"'{description}'"),
                *(check for param in params for check in param.compile_checks),
            ),
            prologue=tuple(line for param in params for line in param.prologue),
            notes=(*_parameter_notes(params, keeper), *lowered.notes),
        )
        return function, lowered

    @staticmethod
    def _callable(
        kind: CallableKind,
        name: str,
        suffix: str,
        function: CFunction,
        *,
        params: Sequence[LoweredParam],
        result: LoweredResult | None,
        location: Location,
        is_const: bool = False,
    ) -> CallablePlan:
        # A parameter that could not be lowered was reported: the plan is not built.
        return CallablePlan(
            kind=kind,
            name=name,
            suffix=suffix,
            function=function,
            params=tuple(param.spec for param in params if param.spec is not None),
            result=result.spec if result is not None else None,
            location=location,
            is_const=is_const,
        )

    # Declarations ------------------------------------------------------------

    def _constructor_plan(
        self, model: Class, constructor: Constructor, c_type: str
    ) -> CallablePlan:
        cpp_name = "::" + model.qualified_name
        key = (*model.namespace, model.name)
        suffix = self._suffix(("constructor", *key), constructor.parameters)
        name = f"{c_type}_create{suffix}"
        what = f"constructor '{cpp_name}({_describe_params(constructor.parameters)})'"
        self._reserve_c_name(name, what, constructor.location)
        out_self = CParam(
            "out_self",
            f"{c_type}**",
            Role.OUT_SELF,
            Ffi.handle(c_type).pointer(),
        )
        policy = self._policy(
            name, what, constructor.parameters, None, constructor.location, constructor=True
        )
        params = self._lower_params(constructor.parameters, constructor.location, policy)
        arguments = ", ".join(param.argument for param in params)
        description = (
            f"{model.qualified_name}::{model.name}({_describe_params(constructor.parameters)})"
        )
        function = CFunction(
            name=name,
            params=(out_self, *(c for param in params for c in param.c_params)),
            returns_status=True,
            statement=f"*out_self = reinterpret_cast<{c_type}*>(new {cpp_name}({arguments}));",
            description=description,
            ffi_restype=STATUS_FFI,
            owned_checks=(_null_check("out_self"),),
            resets=("*out_self = nullptr;",),
            checks=(
                *(c for param in params for c in param.null_checks),
                *(c for param in params for c in param.value_checks),
            ),
            location=constructor.location,
            compile_checks=(
                *_temporaries_check(f"{cpp_name}({{arguments}})", params, f"'{description}'"),
                *(check for param in params for check in param.compile_checks),
            ),
            prologue=tuple(line for param in params for line in param.prologue),
            notes=_parameter_notes(params, "the new object keeps"),
        )
        return self._callable(
            CallableKind.CONSTRUCTOR,
            model.name,
            suffix,
            function,
            params=params,
            result=None,
            location=constructor.location,
        )

    def build_class(self, model: Class) -> ClassPlan:
        prefix = naming.c_prefix(model.namespace, self.module.name)
        c_type = f"{prefix}_{model.name}"
        self._reserve_c_name(
            c_type, f"the handle type of '{model.qualified_name}'", model.location, tag=True
        )
        cpp_name = "::" + model.qualified_name
        key = (*model.namespace, model.name)

        def self_param(is_const: bool) -> CParam:
            qualifier = "const " if is_const else ""
            return CParam("self", f"{qualifier}{c_type}*", Role.SELF, Ffi.handle(c_type))

        def cast_self(is_const: bool) -> str:
            # The C handle is an opaque struct; in C++ it points to the class.
            qualifier = "const " if is_const else ""
            return f"reinterpret_cast<{qualifier}{cpp_name}*>(self)"

        constructors = [
            self._constructor_plan(model, constructor, c_type) for constructor in model.constructors
        ]

        destroy_name = f"{c_type}_destroy"
        self._reserve_c_name(destroy_name, f"the destructor of '{cpp_name}'", model.location)
        destroy = CFunction(
            name=destroy_name,
            params=(self_param(False),),
            returns_status=False,
            statement=f"delete {cast_self(False)};",
            description=f"{model.qualified_name}::~{model.name}()",
            ffi_restype=VOID_FFI,
            location=model.location,
        )

        info = self._classes[model.usr]
        bases = self._bases(info)
        views = self._views(info, self_param, cast_self)

        members: list[CallablePlan] = []
        for method in model.methods:
            suffix = self._suffix(("method", *key, method.name), method.parameters)
            name = f"{c_type}_{method.name}{suffix}"
            what = f"method '{cpp_name}::{method.name}({_describe_params(method.parameters)})'"
            self._reserve_c_name(name, what, method.location)
            policy = self._policy(name, what, method.parameters, method.result, method.location)
            params = self._lower_params(method.parameters, method.location, policy)
            qualifiers = " const" if method.is_const else ""
            static = "static " if method.is_static else ""
            description = (
                f"{static}{_spelling(method.result)} {model.qualified_name}::{method.name}"
                f"({_describe_params(method.parameters)}){qualifiers}"
            )
            if method.is_static:
                function, result = self._status_function(
                    name,
                    params,
                    f"{cpp_name}::{method.name}({{arguments}})",
                    method.result,
                    description,
                    location=method.location,
                    policy=policy,
                )
            else:
                function, result = self._status_function(
                    name,
                    params,
                    f"{cast_self(method.is_const)}->{method.name}({{arguments}})",
                    method.result,
                    description,
                    location=method.location,
                    policy=policy,
                    self_param=self_param(method.is_const),
                    keeper="self keeps",
                )
            members.append(
                self._callable(
                    CallableKind.STATIC if method.is_static else CallableKind.METHOD,
                    method.name,
                    suffix,
                    function,
                    params=params,
                    result=result,
                    location=method.location,
                    is_const=method.is_const,
                )
            )

        request = self.director_requests.get(c_type)
        return ClassPlan(
            model=model,
            c_type=c_type,
            constructors=tuple(constructors),
            destroy=destroy,
            members=tuple(members),
            enums=tuple(self._enums[enum.usr] for enum in model.enums),
            bases=tuple(bases),
            views=views,
            is_base=self._is_base(info),
            director=self._director(info, request) if request is not None else None,
        )

    # Directors ---------------------------------------------------------------

    def _reserve_director_names(self) -> None:
        """The names that the runtime of a module with directors adds."""
        if not self.director_requests:
            return
        name = self.module.name
        self._reserve_c_name(
            f"{name}_string_sink", "the string sink of the vtables", None, tag=True
        )
        self._reserve_c_name(
            f"{name}_string_sink_set", "the function that sets a string of the vtables", None
        )

    def unknown_directors(self) -> list[Diagnostic]:
        """The classes that the annotations file asks a director for, and are not wrapped."""
        return unknown_directors(
            self.director_requests, [info.c_type for info in self._classes.values()]
        )

    def _ancestor_list(self, info: ClassInfo) -> list[ClassInfo]:
        """The bases of a class, those of its bases too, each once, depth first."""
        found: dict[str, ClassInfo] = {}

        def visit(current: ClassInfo) -> None:
            for base in self._known_bases(current):
                if base.model.usr not in found:
                    found[base.model.usr] = base
                    visit(base)

        visit(info)
        return list(found.values())

    @staticmethod
    def _slot_key(hook: Hook) -> str:
        """What tells the virtual methods apart: see :attr:`Hook.key`. (A model that was
        not made by the parser has no key: the spelled types stand in for it.)"""
        if hook.key:
            return hook.key
        method = hook.method
        if method is None:
            return hook.name
        types = ", ".join(
            _spelling(p.type) + ("&" if p.by_reference else "") for p in method.parameters
        )
        return f"{method.name}({types}){' const' if method.is_const else ''}"

    def _director_slots(self, info: ClassInfo) -> tuple[list[_Slot], list[tuple[ClassInfo, Hook]]]:
        """The virtual methods that a director of the class overrides, the most derived
        declaration of each, and those that the model could not read.

        A method is left to the class (the director does not override it) when the most
        derived declaration is final, or private and has an implementation (a generated
        class could not run it). A private pure one is overridden: nothing else makes the
        director concrete. A method that two unrelated bases declare, and the class does
        not, is ambiguous for a base call: it is overridden only if it is pure."""
        slots: dict[str, _Slot] = {}
        unreadable: list[tuple[ClassInfo, Hook]] = []
        first: dict[str, ClassInfo] = {}
        ambiguous: set[str] = set()
        ancestors: dict[str, set[str]] = {}

        def derives(owner: ClassInfo, base: ClassInfo) -> bool:
            if owner.model.usr not in ancestors:
                ancestors[owner.model.usr] = {item.model.usr for item in self._ancestor_list(owner)}
            return base.model.usr in ancestors[owner.model.usr]

        for owner in (info, *self._ancestor_list(info)):
            for hook in owner.model.hooks:
                key = self._slot_key(hook)
                earlier = first.get(key)
                if earlier is not None:
                    if earlier is not info and not derives(earlier, owner):
                        ambiguous.add(key)
                    continue
                first[key] = owner
                if hook.is_final or (hook.access is Access.PRIVATE and not hook.is_pure):
                    continue
                if hook.method is None:
                    unreadable.append((owner, hook))
                else:
                    slots[key] = _Slot(owner, hook, hook.method)
        for key in ambiguous:
            slot = slots.get(key)
            if slot is not None and not slot.hook.is_pure:
                del slots[key]
        return list(slots.values()), unreadable

    def _director_problems(self, slot: _Slot) -> list[str]:
        """Why a virtual method cannot be overridden from another language (yet)."""
        method = slot.method
        problems: list[str] = []

        def supported(type_: Type, what: str) -> bool:
            if isinstance(type_, EnumRef) and type_.usr not in self._enums:
                problems.append(f"{what}: the enum '{type_.qualified_name}' is not wrapped")
            elif isinstance(type_, StructRef) and type_.usr not in self._structs:
                problems.append(f"{what}: the struct '{type_.qualified_name}' is not wrapped")
            elif not isinstance(type_, ScalarType | EnumRef | StructRef | StringType):
                problems.append(
                    f"{what}: '{_spelling(type_)}' is not a type that an override can take "
                    "or give yet (scalars, enums, strings and value structs are)"
                )
            else:
                return True
            return False

        for position, parameter in enumerate(method.parameters, start=1):
            supported(parameter.type, f"parameter {parameter.name or position}")
        result = method.result
        if method.result_by_reference or getattr(result, "by_reference", False):
            problems.append("the result is a reference: an override cannot return one")
        elif isinstance(result, StringType) and result.kind is not StringKind.STRING:
            problems.append(
                "the result is a view of characters that the override would not own: "
                "return a std::string"
            )
        elif isinstance(result, ScalarType) and result.is_void:
            pass
        else:
            supported(result, "the result")
        return problems

    def _director_eligible(
        self, info: ClassInfo, request: DirectorRequest
    ) -> tuple[list[_Slot], list[tuple[ClassInfo, Hook]]] | None:
        """The slots of the director of a class, or None (reported) if it cannot have one."""
        model = info.model
        problems_before = len(self.errors)

        def fail(reason: str) -> None:
            self.errors.append(
                Diagnostic(
                    f"class '{model.qualified_name}' cannot have a director: {reason} "
                    f"({request.where()})",
                    model.location,
                )
            )

        if model.final:
            fail("it is final")
        if not model.virtual_destructor:
            fail("it has no virtual destructor, so it is not meant to be derived from")
        if not model.director_constructors:
            reasons = "; ".join(problem.message for problem in model.director_problems)
            detail = f" ({reasons})" if reasons else ""
            fail("no constructor that a derived class can call crosses the C boundary" + detail)
        slots, unreadable = self._director_slots(info)
        if not slots and not unreadable:
            fail("it has no public or protected virtual method to override")
        for owner, hook in unreadable:
            if hook.is_pure:
                why = "; ".join(problem.message for problem in hook.problems)
                fail(
                    f"the pure virtual method '{owner.model.name}::{hook.name}' cannot be "
                    f"overridden from another language: {why}"
                )
        for slot in slots:
            problems = self._director_problems(slot)
            if problems and slot.hook.is_pure:
                fail(
                    f"the pure virtual method '{slot.owner.model.name}::{slot.method.name}' "
                    f"cannot be overridden from another language ({'; '.join(problems)})"
                )
        if len(self.errors) > problems_before:
            return None
        return slots, unreadable

    def _director(self, info: ClassInfo, request: DirectorRequest) -> DirectorPlan | None:
        """The director of a class that the annotations file asks for, or None
        (reported)."""
        model = info.model
        problems_before = len(self.errors)
        eligible = self._director_eligible(info, request)
        if eligible is None:
            return None
        slots, _ = eligible
        c_type = info.c_type
        director_type = f"{c_type}_director"
        vtable_type = f"{c_type}_vtable"
        impl = f"{c_type}_director_impl"
        cpp_base = info.cpp_name
        detail = f"{self.module.name}_detail"
        location = model.location
        for name, what in (
            (director_type, f"the director handle of '{model.qualified_name}'"),
            (vtable_type, f"the vtable of the director of '{model.qualified_name}'"),
        ):
            self._reserve_c_name(name, what, location, tag=True)

        # What can be overridden (a method whose types cannot cross yet keeps the C++
        # implementation), with the names of the vtable members.
        entries_slots = [slot for slot in slots if not self._director_problems(slot)]
        overloads: dict[str, int] = {}
        for slot in entries_slots:
            overloads[slot.method.name] = overloads.get(slot.method.name, 0) + 1
        raw = [
            slot.method.name
            + (
                naming.overload_suffix(
                    [
                        _token(p.type, self._enums, self._struct_c_types, self._class_c_types)
                        for p in slot.method.parameters
                    ]
                )
                if overloads[slot.method.name] > 1
                else ""
            )
            for slot in entries_slots
        ]
        fields = naming.field_names(raw, self._parameter_macros | {"user_data", "release"})

        entries: list[DirectorEntry] = []
        glue_overrides: list[str] = []
        glue_bases: list[str] = []
        types: list[Type] = []
        for slot, field in zip(entries_slots, fields, strict=True):
            built = self._director_entry(info, slot, field, impl)
            if built is None:  # reported
                continue
            entry, override, base_member = built
            entries.append(entry)
            glue_overrides += override
            glue_bases += base_member
            types += [p.type for p in slot.method.parameters] + [slot.method.result]
        if len(self.errors) > problems_before:
            return None

        constructors = self._director_constructors(
            info, director_type, vtable_type, impl, entries, request=request
        )
        object_name = f"{director_type}_object"
        self._reserve_c_name(object_name, f"the object of the director of '{cpp_base}'", location)
        object_function = CFunction(
            name=object_name,
            params=(CParam("director", f"{director_type}*", Role.SELF, Ffi.handle(director_type)),),
            returns_status=False,
            statement=(
                f"reinterpret_cast<{c_type}*>(static_cast<{cpp_base}*>"
                f"(reinterpret_cast<{detail}::{impl}*>(director)))"
            ),
            description=f"{model.qualified_name}: the object of its director",
            ffi_restype=Ffi.handle(c_type),
            location=location,
            c_return=f"{c_type}*",
        )
        glue = (
            f"class {impl} final : public {cpp_base} {{",
            "public:",
            "    template <typename... Args>",
            f"    {impl}({detail}::callback_owner& owner, const {vtable_type}& vtable,"
            " Args&&... args)",
            f"        : {cpp_base}(static_cast<Args&&>(args)...), vt_(vtable), ref_(owner) {{}}",
            *glue_overrides,
            *glue_bases,
            "",
            "private:",
            f"    {vtable_type} vt_;",
            f"    {detail}::callback_ref ref_;",
            "};",
        )
        types += [
            parameter.type
            for constructor in model.director_constructors
            for parameter in constructor.parameters
        ]
        self._director_types.setdefault(info.header.stem, []).extend(types)
        return DirectorPlan(
            c_type=c_type,
            class_name=model.qualified_name,
            director_type=director_type,
            vtable_type=vtable_type,
            entries=tuple(entries),
            constructors=tuple(constructors),
            object=object_function,
            glue=glue,
        )

    def _director_result(
        self, type_: Type, location: Location
    ) -> tuple[str, str | None, str, list[str], list[str]]:
        """How the override gets the result of the function pointer: (C++ type, C type
        of its output parameter or None, token of the signature, statements before the
        call, statements after it)."""
        detail = f"{self.module.name}_detail"
        if isinstance(type_, StringType):
            sink = f"{self.module.name}_string_sink"
            return (
                "::std::string",
                f"{sink}*",
                "string",
                [f"{sink} _result;"],
                ["return static_cast<::std::string&&>(_result.value);"],
            )
        if isinstance(type_, StructRef):
            struct = self._struct(type_, location)
            assert struct is not None  # checked
            return (
                struct.cpp_name,
                f"{struct.c_type}*",
                struct.c_type,
                [f"{struct.c_type} _result{{}};"],
                [
                    "try {",
                    f"    return {detail}::from_c(_result);",
                    f"}} catch (const {detail}::argument_error&) {{",
                    "    // A member that is not valid (an enum out of range) gives the zero",
                    "    // value, as it does when it is the result itself.",
                    f"    return {struct.cpp_name}();",
                    "}",
                ],
            )
        result = self._callback_result(type_, location)  # type: ignore[arg-type]
        assert result is not None  # checked
        cpp, c_type, token = result
        if c_type is None:
            return "void", None, "void", [], []
        back = "_result" if cpp == c_type else f"static_cast<{cpp}>(_result)"
        valid = self._valid_range(type_, location)
        if valid is not None:
            terms = [f"_result < {valid[0]}"] if valid[0] > _INT_MIN else []
            terms += [f"_result > {valid[1]}"] if valid[1] < _INT_MAX else []
            if terms:
                back = f"{' || '.join(terms)} ? {cpp}() : {back}"
        return cpp, f"{c_type}*", token, [f"{c_type} _result{{}};"], [f"return {back};"]

    def _director_entry(
        self, info: ClassInfo, slot: _Slot, field: str, impl: str
    ) -> tuple[DirectorEntry, list[str], list[str]] | None:
        """One member of the vtable: its description, its override in the director, the
        member that calls the implementation of the class, and the C function that
        calls it."""
        method = slot.method
        location = method.location
        detail = f"{self.module.name}_detail"
        cpp_base = info.cpp_name
        declarations: list[str] = []
        c_types = ["void*"]
        passed = ["ref_.user_data()"]
        tokens: list[str] = []
        parts: list[CallbackPart] = []
        for position, parameter in enumerate(method.parameters, start=1):
            local = f"_arg{position}"
            declarations.append(
                f"{self._spell(parameter.type, parameter.by_reference, location)} {local}"
            )
            lowered = self._callback_argument(parameter.type, local, location)
            assert lowered is not None  # checked
            _, c_args, pass_c, token = lowered
            c_types += c_args
            passed += pass_c
            tokens.append(token)
            parts.append(self._callback_part(parameter.type, location))
        result_cpp, result_c, result_token, before, after = self._director_result(
            method.result, location
        )
        if result_c is not None:
            c_types.append(result_c)
            passed.append("&_result")
        names = ", ".join(f"_arg{i}" for i in range(1, len(method.parameters) + 1))
        qualifiers = (" const" if method.is_const else "") + (
            " noexcept" if method.is_noexcept else ""
        )
        base_call = f"{cpp_base}::{method.name}({names})"
        if slot.hook.is_pure:
            fallback = "return;" if result_cpp == "void" else f"return {result_cpp}();"
            condition = "ref_.disabled()"
        else:
            fallback = f"return {base_call};"  # (a void result too: it is a void expression)
            condition = f"vt_.{field} == nullptr || ref_.disabled()"
        override = [
            "",
            f"    {result_cpp} {method.name}({', '.join(declarations)}){qualifiers} override",
            "    {",
            f"        if ({condition}) {{",
            f"            {fallback}",
            "        }",
            *(f"        {line}" for line in before),
            f"        vt_.{field}({', '.join(passed)});",
            *(f"        {line}" for line in after),
            "    }",
        ]
        const = " const" if method.is_const else ""
        base_member = (
            []
            if slot.hook.is_pure
            else [
                "",
                "    template <typename... Args>",
                f"    {result_cpp} bridgefex_base_{field}(Args&&... args){const}",
                "    {",
                f"        return this->{cpp_base}::{method.name}(static_cast<Args&&>(args)...);",
                "    }",
            ]
        )
        callback = CallbackSpec(
            tuple(parts),
            self._callback_part(method.result, location),
        )
        director_type = f"{info.c_type}_director"
        base: CallablePlan | None = None
        description = (
            f"{_spelling(method.result)} {slot.owner.model.qualified_name}::{method.name}"
            f"({_describe_params(method.parameters)}){' const' if method.is_const else ''}"
        )
        if not slot.hook.is_pure:
            name = f"{director_type}_base_{field}"
            what = f"the base call of '{description}'"
            self._reserve_c_name(name, what, location)
            policy = self._policy(name, what, method.parameters, method.result, location)
            params = self._lower_params(
                method.parameters, location, policy, reserved=_DIRECTOR_PARAMETERS
            )
            function, lowered_result = self._status_function(
                name,
                params,
                f"reinterpret_cast<{detail}::{impl}*>(director)->bridgefex_base_{field}({{arguments}})",
                method.result,
                description,
                location=location,
                policy=policy,
                self_param=CParam(
                    "director", f"{director_type}*", Role.SELF, Ffi.handle(director_type)
                ),
                keeper="the director keeps",
            )
            function = dataclasses.replace(
                function,
                notes=(
                    "calls the implementation of the class, without going through the vtable",
                    *function.notes,
                ),
            )
            base = self._callable(
                CallableKind.DIRECTOR_BASE,
                method.name,
                field.removeprefix(method.name),
                function,
                params=params,
                result=lowered_result,
                location=location,
                is_const=method.is_const,
            )
        entry = DirectorEntry(
            field=field,
            name=method.name,
            description=description,
            owner=slot.owner.model.qualified_name,
            access=slot.hook.access,
            is_pure=slot.hook.is_pure,
            callback=callback,
            thunk_key="__".join([result_token, *tokens]),
            function_params=", ".join(c_types),
            base=base,
        )
        return entry, override, base_member

    def _director_constructors(
        self,
        info: ClassInfo,
        director_type: str,
        vtable_type: str,
        impl: str,
        entries: Sequence[DirectorEntry],
        *,
        request: DirectorRequest,
    ) -> list[CallablePlan]:
        """The functions that create the director, one for each constructor that a derived
        class can call."""
        model = info.model
        c_type = info.c_type
        detail = f"{self.module.name}_detail"
        constructors = model.director_constructors
        plans: list[CallablePlan] = []
        for constructor in constructors:
            suffix = (
                naming.overload_suffix(
                    [
                        _token(p.type, self._enums, self._struct_c_types, self._class_c_types)
                        for p in constructor.parameters
                    ]
                )
                if len(constructors) > 1
                else ""
            )
            name = f"{director_type}_create{suffix}"
            description = (
                f"{model.qualified_name}::{model.name}({_describe_params(constructor.parameters)})"
                " of the director"
            )
            what = f"the constructor of the director '{description}'"
            self._reserve_c_name(name, what, constructor.location)
            policy = self._policy(
                name, what, constructor.parameters, None, constructor.location, constructor=True
            )
            params = self._lower_params(
                constructor.parameters, constructor.location, policy, reserved=_DIRECTOR_PARAMETERS
            )
            arguments = "".join(f", {param.argument}" for param in params)
            # The object first, as in every create function, so that whoever owns it can
            # destroy it from the moment it exists; and the director, for the base calls.
            out_object = CParam(
                "out_object", f"{c_type}**", Role.OUT_SELF, Ffi.handle(c_type).pointer()
            )
            out_director = CParam(
                "out_director",
                f"{director_type}**",
                Role.OUT_DIRECTOR,
                Ffi.handle(director_type).pointer(),
            )
            vtable = CParam(
                "vtable", f"const {vtable_type}*", Role.INPUT, Ffi.struct(vtable_type).pointer()
            )
            pure = tuple(
                Precondition(
                    f"vtable->{entry.field} == nullptr",
                    "invalid_argument",
                    f"'vtable->{entry.field}' is NULL: it has to implement the pure virtual "
                    f"method '{entry.description}'",
                )
                for entry in entries
                if entry.is_pure
            )
            function = CFunction(
                name=name,
                params=(
                    out_object,
                    out_director,
                    vtable,
                    *(c for param in params for c in param.c_params),
                ),
                returns_status=True,
                statement=(
                    f"{detail}::{impl}* _created = new {detail}::{impl}"
                    f"(_vtable_owner, *vtable{arguments}); "
                    f"*out_director = reinterpret_cast<{director_type}*>(_created); "
                    f"*out_object = reinterpret_cast<{c_type}*>"
                    f"(static_cast<{info.cpp_name}*>(_created));"
                ),
                description=description,
                ffi_restype=STATUS_FFI,
                owned_checks=(_null_check("out_object"), _null_check("out_director")),
                resets=("*out_object = nullptr;", "*out_director = nullptr;"),
                checks=(
                    _null_check("vtable"),
                    *pure,
                    *(c for param in params for c in param.null_checks),
                    *(c for param in params for c in param.value_checks),
                ),
                location=constructor.location,
                compile_checks=tuple(check for param in params for check in param.compile_checks),
                prologue=(
                    f"{detail}::callback_owner _vtable_owner(vtable != nullptr ? vtable->release"
                    " : nullptr, vtable != nullptr ? vtable->user_data : nullptr);",
                    *(line for param in params for line in param.prologue),
                ),
                notes=(
                    "the vtable is copied; its release function is called once, when the "
                    "director is destroyed (or now, if this function fails)",
                    *_parameter_notes(params, "the new object keeps"),
                ),
                hint=(
                    f"{request.where()}: the class asks for a director, which derives from it "
                    "and has to call this constructor: check that it is not deleted or "
                    "inaccessible, in the class or in its bases (a reference member or a base "
                    "without a default constructor deletes the implicit one)"
                ),
            )
            plans.append(
                self._callable(
                    CallableKind.DIRECTOR_CONSTRUCTOR,
                    model.name,
                    suffix,
                    function,
                    params=params,
                    result=None,
                    location=constructor.location,
                )
            )
        return plans

    def _bases(self, info: ClassInfo) -> list[ClassInfo]:
        """The public base classes of a class, which have to be classes of the module
        (reported otherwise)."""
        model = info.model
        bases: list[ClassInfo] = []
        for ref in model.bases:
            base = self._classes.get(ref.usr)
            if base is None:
                self.errors.append(
                    Diagnostic(
                        f"the base class '{ref.qualified_name}' of '{model.qualified_name}' is "
                        "not a class of this module: wrap the header that defines it (the "
                        "public base classes of a wrapped class must be wrapped too)",
                        ref.location,
                    )
                )
            else:
                bases.append(base)
        return bases

    def _ancestors(self, info: ClassInfo) -> list[ClassInfo]:
        """Every base class of a class, the bases of its bases too, each once."""
        found: dict[str, ClassInfo] = {}

        def visit(current: ClassInfo) -> None:
            for base in self._known_bases(current):
                if base.model.usr in found:
                    self.errors.append(
                        Diagnostic(
                            f"class '{info.model.qualified_name}' inherits "
                            f"'{base.model.qualified_name}' more than once: a pointer to it "
                            "cannot be converted to a pointer to that base",
                            info.model.location,
                        )
                    )
                    continue
                found[base.model.usr] = base
                visit(base)

        visit(info)
        return list(found.values())

    def _known_bases(self, info: ClassInfo) -> list[ClassInfo]:
        return [self._classes[ref.usr] for ref in info.model.bases if ref.usr in self._classes]

    def _views(
        self,
        info: ClassInfo,
        self_param: Callable[[bool], CParam],
        cast_self: Callable[[bool], str],
    ) -> tuple[ClassView, ...]:
        """The conversions of a class to each of its ancestors."""
        model = info.model
        views: list[ClassView] = []
        for ancestor in self._ancestors(info):
            functions: list[CFunction] = []
            for const in (False, True):
                qualifier = "const " if const else ""
                name = f"{info.c_type}_as_{ancestor.c_type}" + ("_const" if const else "")
                self._reserve_c_name(
                    name,
                    f"the conversion of '{model.qualified_name}' to its base class "
                    f"'{ancestor.model.qualified_name}'",
                    model.location,
                )
                functions.append(
                    CFunction(
                        name=name,
                        params=(self_param(const),),
                        returns_status=False,
                        # NULL stays NULL; the address changes when the base is not
                        # at the start of the object (several bases).
                        statement=(
                            f"reinterpret_cast<{qualifier}{ancestor.c_type}*>("
                            f"static_cast<{qualifier}{ancestor.cpp_name}*>({cast_self(const)}))"
                        ),
                        description=(
                            f"{qualifier}{ancestor.model.qualified_name}* "
                            f"{model.qualified_name}: the {'const ' if const else ''}object "
                            "as its base class"
                        ),
                        ffi_restype=Ffi.handle(ancestor.c_type),
                        location=model.location,
                        c_return=f"{qualifier}{ancestor.c_type}*",
                    )
                )
            views.append(ClassView(ancestor.c_type, functions[0], functions[1]))
        return tuple(views)

    def build_function(self, model: Function) -> FunctionPlan:
        prefix = naming.c_prefix(model.namespace, self.module.name)
        suffix = self._suffix(("function", *model.namespace, model.name), model.parameters)
        name = f"{prefix}_{model.name}{suffix}"
        what = f"function '{model.qualified_name}({_describe_params(model.parameters)})'"
        self._reserve_c_name(name, what, model.location)
        policy = self._policy(name, what, model.parameters, model.result, model.location)
        params = self._lower_params(model.parameters, model.location, policy)
        description = (
            f"{_spelling(model.result)} {model.qualified_name}"
            f"({_describe_params(model.parameters)})"
        )
        function, result = self._status_function(
            name,
            params,
            f"::{model.qualified_name}({{arguments}})",
            model.result,
            description,
            location=model.location,
            policy=policy,
        )
        return FunctionPlan(
            model,
            self._callable(
                CallableKind.FUNCTION,
                model.name,
                suffix,
                function,
                params=params,
                result=result,
                location=model.location,
            ),
        )

    def build_header(self, header: Header) -> HeaderPlan:
        stem = naming.check_header_stem(header.stem, self._reservations.header_problem)
        guard = naming.header_guard(self.module.name, stem)
        self._reserve_c_name(guard, f"the include guard of the C header of {header.path}", None)
        internal_guard = f"{guard.removesuffix('_C_H')}_C_INTERNAL_HPP"
        if any(isinstance(declaration, Struct) for declaration in header.declarations):
            self._reserve_c_name(
                internal_guard, f"the include guard of the conversions of {header.path}", None
            )
        self._header = header
        by_value = self._classes_returned_by_value(header)
        created = {info.model.usr: info for info in self._classes.values() if info.header is header}
        created.update({info.model.usr: info for info in by_value})
        items: list[ClassPlan | FunctionPlan | EnumPlan | StructPlan] = []
        for declaration in header.declarations:
            if isinstance(declaration, Class):
                items.append(self.build_class(declaration))
            elif isinstance(declaration, Function):
                items.append(self.build_function(declaration))
            elif isinstance(declaration, Struct):
                items.append(self._structs[declaration.usr])
            else:
                items.append(self._enums[declaration.usr])
        director_types = tuple(self._director_types.get(header.stem, ()))
        owners: dict[str, Header] = {}
        for type_ in _components((*_header_types(header, fields=False), *director_types)):
            if isinstance(type_, StructRef) and type_.usr in self._structs:
                owner = self._struct_headers[type_.usr]
                if owner is not header:
                    owners.setdefault(owner.stem, owner)
        self.foreign_struct_headers[header.stem] = tuple(owners.values())
        class_owners: dict[str, Header] = {}
        for type_ in _components((*_header_types(header, fields=False), *director_types)):
            ref = self._as_class(type_)
            if ref is not None and ref.usr in self._classes:
                owner = self._classes[ref.usr].header
                if owner is not header:
                    class_owners.setdefault(owner.stem, owner)
        self.foreign_class_headers[header.stem] = tuple(class_owners.values())
        foreign: dict[str, EnumPlan] = {}
        for type_ in _components((*_header_types(header), *director_types)):
            if isinstance(type_, EnumRef) and type_.usr in self._enums:
                owner = self._enum_headers[type_.usr]
                enum = self._enums[type_.usr]
                if owner is not header and enum.model.fixed:
                    foreign.setdefault(type_.usr, enum)
        return HeaderPlan(
            header=header,
            stem=stem,
            c_header=f"{stem}_c.h",
            c_source=f"{stem}_c.cpp",
            guard=guard,
            items=tuple(items),
            foreign_enums=tuple(foreign.values()),
            director_types=director_types,
            created_classes=tuple((i.cpp_name, i.model.qualified_name) for i in created.values()),
            value_results=tuple((i.cpp_name, i.model.qualified_name) for i in by_value),
        )

    def _classes_returned_by_value(self, header: Header) -> list[ClassInfo]:
        """The classes of the module that the functions of a header return by value."""
        found: dict[str, ClassInfo] = {}
        for declaration in header.declarations:
            results: list[Type] = []
            if isinstance(declaration, Function):
                results.append(declaration.result)
            elif isinstance(declaration, Class):
                results += [method.result for method in declaration.methods]
            for result in results:
                ref = self._as_class(result)
                if ref is not None and ref.form is ClassForm.VALUE and ref.usr in self._classes:
                    found.setdefault(ref.usr, self._classes[ref.usr])
        return list(found.values())


# Conventions of the generated C API that its headers do not show (the
# contract of the out-parameters, how memory is returned and released...).
# Bump it with every change to them, and never reuse a value:
#   1  status codes, opaque handles, last_error and api_fingerprint.
#   2  <module>_free, strings (results in memory that <module>_free releases),
#      ERROR_INVALID_ARGUMENT, outputs that the function allocates are NULL on
#      every failure.
#   3  value structs: by pointer, in pack(8) mirrors, results written on
#      success only; ERROR_INVALID_ARGUMENT for invalid members.
#   4  std::vector: elements in one block that <module>_free releases, never
#      NULL on success (1 byte when empty); <module>_string elements.
#   5  std::function: a callback, its user_data and a release function, which
#      C++ calls once for each callback, whatever the status;
#      <module>_disable_callbacks.
#   6  wrapped classes as parameters and results: references and pointers are
#      handles of the caller's objects; a result by value or in a
#      std::unique_ptr is a new object that the caller destroys (NULL on every
#      failure, and for an empty std::unique_ptr); a reference result is
#      borrowed (never NULL, never destroyed by the caller); a
#      std::unique_ptr parameter, or a pointer annotated "transfer", takes the
#      object whatever the status, also when the call fails before it reaches
#      the wrapped function; a pointer result is owned or borrowed as the
#      annotations say (it may be NULL); the header says which parameters may
#      be NULL and which pointers the function keeps.
#   7  <module>_disable_callbacks disables every release function that was
#      given, not only the last one (several runtimes share a library), and
#      <module>_enable_callbacks undoes it for one function.
#   8  inheritance: a class converts to each of its ancestors with
#      <class>_as_<ancestor> (and _const), the only way to get the pointer of
#      a base class (it is not the same address with several bases); the
#      methods of a base are not repeated in the derived classes; an abstract
#      class has no create function; virtual methods are called through any
#      pointer to a class that has them.
C_API_CONVENTIONS = 8


def api_fingerprint(public_headers: Iterable[str]) -> str:
    """Hash of a module's C API: the conventions, then the text of every public
    generated C header, in :attr:`ModulePlan.public_headers` order.

    Those headers are byte-identical on every platform, so the fingerprint is
    too, and it changes with every handle, function and constant they declare.
    """
    digest = hashlib.sha256(f"bridgefex-c-api-{C_API_CONVENTIONS}\n".encode())
    for text in public_headers:
        data = text.encode("utf-8")
        # Length-prefixed, so that moving text between headers changes it too.
        digest.update(f"{len(data)}\n".encode())
        digest.update(data)
    return digest.hexdigest()[:32]


def build_plan(
    module: Module,
    annotations: Mapping[str, FunctionAnnotations] | None = None,
    reservations: Reservations | None = None,
) -> ModulePlan:
    """Decide every generated name and check that none of them clash.

    ``annotations`` are the annotation files, loaded (see :mod:`bridgefex.annotations`).
    ``reservations`` are the names that the language backends need left alone (see
    :func:`bridgefex.backends.reservations`). Without them the plan serves the C layer
    only: the names of the C parameters may then be keywords or locals of a language,
    and :func:`bridgefex.generator.render` refuses to give such a plan to a backend.

    Raises:
        ConfigurationError: invalid module or header names.
        GenerationError: generated names clash.
    """
    naming.check_module_name(module.name, (reservations or NO_RESERVATIONS).module_problem)
    builder = _Builder(module, annotations, reservations)
    # Compared without case: Windows and macOS file systems ignore it.
    generated_files = {
        name.casefold()
        for name in (
            f"{module.name}_runtime.h",
            f"{module.name}_runtime.cpp",
            f"{module.name}_runtime_internal.hpp",
            f"{module.name}_types.h",
        )
    }
    for header in module.headers:
        generated_files |= {
            f"{header.stem}{suffix}".casefold() for suffix in ("_c.h", "_c.cpp", "_c_internal.hpp")
        }
    stems: dict[str, Header] = {}
    headers: list[HeaderPlan] = []
    for header in module.headers:
        if header.include.casefold() in generated_files:
            # A quoted #include looks next to the including file first, so the
            # generated sources would include the generated file instead.
            builder.errors.append(
                Diagnostic(
                    f"header '{header.path}' has the name of a generated file; rename it, "
                    "choose another --module name or use --include-root"
                )
            )
            continue
        key = header.stem.casefold()
        if key in stems:
            builder.errors.append(
                Diagnostic(
                    f"headers '{stems[key].path}' and '{header.path}' have the same name "
                    "(ignoring case); their generated files would overwrite each other"
                )
            )
            continue
        stems[key] = header
        headers.append(builder.build_header(header))
    builder.errors.extend(unknown_functions(annotations or {}, builder.annotatable))
    builder.errors.extend(builder.unknown_directors())
    if builder.errors:
        raise GenerationError(builder.errors)
    # The sources include the headers (in module order) whose structs they use.
    plans = {plan.stem: plan for plan in headers}
    headers = [
        dataclasses.replace(
            plan,
            foreign_headers=tuple(
                plans[other.stem]
                for other in module.headers
                if other in builder.foreign_struct_headers.get(plan.stem, ())
            ),
            foreign_class_headers=tuple(
                plans[other.stem]
                for other in module.headers
                if other in builder.foreign_class_headers.get(plan.stem, ())
            ),
        )
        for plan in headers
    ]
    return ModulePlan(
        name=module.name,
        headers=tuple(headers),
        structs=_structs_in_order(headers),
        reservations=reservations or NO_RESERVATIONS,
    )


def _structs_in_order(headers: Sequence[HeaderPlan]) -> tuple[StructPlan, ...]:
    """Every struct after those it contains, otherwise in header and source order."""
    by_usr = {struct.model.usr: struct for header in headers for struct in header.structs}
    ordered: dict[str, StructPlan] = {}

    def visit(struct: StructPlan) -> None:
        if struct.model.usr in ordered:
            return
        for field in struct.model.fields:
            if isinstance(field.type, StructRef) and field.type.usr in by_usr:
                visit(by_usr[field.type.usr])
        ordered[struct.model.usr] = struct

    for struct in by_usr.values():
        visit(struct)
    return tuple(ordered.values())
