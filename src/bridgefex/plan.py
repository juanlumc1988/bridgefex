# SPDX-License-Identifier: Apache-2.0
"""Binding plan: every name, signature and statement that the templates render.

Keeping this logic in Python, instead of in the templates, makes it testable
and keeps the templates free of decisions.
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum

from . import naming
from .errors import Diagnostic, GenerationError, Location
from .model import (
    Class,
    Enumeration,
    EnumRef,
    Field,
    Function,
    Header,
    Module,
    Parameter,
    ScalarType,
    StringKind,
    StringType,
    Struct,
    StructRef,
    Type,
    TypeCategory,
)


class Role(Enum):
    SELF = "self"
    OUT_SELF = "out_self"
    INPUT = "input"
    SIZE = "size"
    """The size of the input before it (a string)."""

    OUT_RESULT = "out_result"
    OUT_RESULT_SIZE = "out_result_size"


@dataclass(frozen=True, slots=True)
class CParam:
    name: str
    c_type: str
    role: Role
    ctypes_type: str

    @property
    def declaration(self) -> str:
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

    ctypes_restype: str

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

    @property
    def owned_outputs(self) -> tuple[tuple[Precondition, str], ...]:
        """Each NULL check of an output that the function allocates, with its reset."""
        return tuple(zip(self.owned_checks, self.resets, strict=True))

    @property
    def signature(self) -> str:
        params = ", ".join(param.declaration for param in self.params) or "void"
        return f"{self.name}({params})"

    @property
    def ctypes_argtypes(self) -> str:
        return "[" + ", ".join(param.ctypes_type for param in self.params) + "]"


@dataclass(frozen=True, slots=True)
class PyArg:
    name: str
    conversions: tuple[str, ...]
    """Statements that check and convert the argument, rebinding its name (and
    computing derived locals), before the call."""


@dataclass(frozen=True, slots=True)
class PyResult:
    """How a Python function gets the result of its C function."""

    setup: tuple[str, ...] = ()
    """Statements that create the output locals, before the conversions."""

    value: str | None = None
    """Expression returned after the call, or None for ``void``."""

    read: str | None = None
    """For outputs that the C function allocates: statement that copies them
    into Python objects (``_out_data``) right after the call. The call and the
    copy then run in a ``try`` whose ``finally`` runs ``cleanup``."""

    cleanup: tuple[str, ...] = ()
    """Statements that release the allocated outputs, inline: a Python function
    call before the release would let a second pending signal (Ctrl-C) raise
    first and leak them."""


_NO_RESULT = PyResult()


@dataclass(frozen=True, slots=True)
class PyCallable:
    name: str
    function: CFunction
    args: tuple[PyArg, ...]
    call_args: tuple[str, ...]
    """Arguments of the C call, in order."""

    result: PyResult = _NO_RESULT
    is_static: bool = False

    @property
    def parameter_list(self) -> str:
        return ", ".join(arg.name for arg in self.args)

    def check_call(self, indent: int) -> str:
        """The argument conversions, then the C call wrapped in ``_runtime.check``,
        indented by ``indent`` spaces.

        The conversions run Python code (``__index__``, ``__float__``), which
        could close the object: they come before ``self._ptr()``, after which
        only local variables are read before the call (ctypes passes ``_out``
        by reference itself, without a call to ``ctypes.byref``).
        """
        pad = " " * indent
        lines = [f"{pad}{line}" for arg in self.args for line in arg.conversions]
        lines.append(self._call(indent))
        return "\n".join(lines)

    def _call(self, indent: int) -> str:
        pad = " " * indent
        head = f"_runtime.check(_bind().{self.function.name}("
        one_line = f"{pad}{head}{', '.join(self.call_args)}))"
        if len(one_line) <= _MAX_LINE_LENGTH:
            return one_line
        lines = [pad + head]
        lines += [f"{pad}    {argument}," for argument in self.call_args]
        lines.append(f"{pad}))")
        return "\n".join(lines)

    def body(self, indent: int) -> str:
        """Statements of a method or function that calls the C function."""
        pad = " " * indent
        result = self.result
        lines = [f"{pad}{line}" for line in result.setup]
        if result.read is None:
            lines.append(self.check_call(indent))
        else:
            # The conversions stay out of the 'try': nothing is allocated yet.
            inner = " " * (indent + 4)
            lines += [f"{pad}{line}" for arg in self.args for line in arg.conversions]
            lines.append(f"{pad}try:")
            lines.append(self._call(indent + 4))
            lines.append(f"{inner}{result.read}")
            lines.append(f"{pad}finally:")
            lines += [f"{inner}{line}" for line in result.cleanup]
        if result.value is not None:
            lines.append(f"{pad}return {result.value}")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class LoweredParam:
    """A C++ parameter as the generated code passes it."""

    c_params: tuple[CParam, ...]
    argument: str
    """C++ expression passed to the wrapped function."""

    null_checks: tuple[Precondition, ...]
    value_checks: tuple[Precondition, ...]
    py: PyArg
    call_args: tuple[str, ...]
    """Arguments of the C call, in Python."""

    temporary: str | None = None
    """The argument as a temporary (prvalue), when ``argument`` passes it as an
    lvalue: see :func:`_temporaries_check`."""


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
    arguments = ", ".join(param.temporary or param.argument for param in params)
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

    py: PyResult
    call_args: tuple[str, ...]
    owned_checks: tuple[Precondition, ...] = ()
    """NULL checks of the outputs that the function allocates."""

    resets: tuple[str, ...] = ()
    """Statements that reset those outputs, so they are NULL on every failure."""


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
    py_name: str
    value: int
    deprecated: bool


@dataclass(frozen=True, slots=True)
class EnumPlan:
    model: Enumeration
    c_type: str
    enumerators: tuple[EnumeratorPlan, ...]
    py_name: str
    """Its name in the header module, or the class attribute of a nested enum."""

    py_qualname: str
    py_module: str

    @property
    def cpp_name(self) -> str:
        return "::" + self.model.qualified_name

    @property
    def ctypes_type(self) -> str:
        return f"ctypes.{self.model.underlying.ctypes_name}"

    @property
    def py_type(self) -> str:
        """The IntEnum, as the generated header modules name it."""
        return f"_types.{self.c_type}"

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
    """In the C mirror and the Python dataclass."""

    ctypes_name: str
    """In the ctypes mirror, where a few names are taken by ctypes itself."""

    c_declaration: str
    ctypes_type: str
    py_annotation: str
    """The type of the dataclass field, as _types.py names it."""

    fill: tuple[str, ...]
    """Statements of _<c_type>__fill that check the value and store it in ``raw``."""

    read: str
    """Expression of _<c_type>__from_c that reads it from ``raw``."""


@dataclass(frozen=True, slots=True)
class StructPlan:
    model: Struct
    c_type: str
    fields: tuple[FieldPlan, ...]
    init: CFunction
    py_name: str
    py_qualname: str
    py_module: str
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

    @property
    def mirror(self) -> str:
        return f"_{self.c_type}"

    @property
    def py_type(self) -> str:
        """The dataclass, as the generated header modules name it."""
        return f"_types.{self.c_type}"


@dataclass(frozen=True, slots=True)
class ClassPlan:
    model: Class
    c_type: str
    py_name: str
    struct_name: str
    pointer_name: str
    constructors: tuple[PyCallable, ...]
    destroy: CFunction
    members: tuple[PyCallable, ...]
    """Methods and static methods, in source order."""

    enums: tuple[EnumPlan, ...] = ()
    """Nested enums."""

    @property
    def cpp_name(self) -> str:
        return "::" + self.model.qualified_name

    @property
    def py_pointer(self) -> str:
        """The ctypes pointer type, as the generated header modules name it."""
        return f"_types.{self.pointer_name}"

    @property
    def single_constructor(self) -> bool:
        return len(self.constructors) == 1

    @property
    def c_functions(self) -> tuple[CFunction, ...]:
        return (
            *(item.function for item in self.constructors),
            self.destroy,
            *(item.function for item in self.members),
        )


@dataclass(frozen=True, slots=True)
class FunctionPlan:
    model: Function
    py: PyCallable

    @property
    def c_function(self) -> CFunction:
        return self.py.function


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

    @property
    def structs(self) -> tuple[StructPlan, ...]:
        return tuple(item for item in self.items if isinstance(item, StructPlan))

    @property
    def struct_inits(self) -> tuple[CFunction, ...]:
        """The <c_type>_init functions, which _types.py binds."""
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
    def py_exports(self) -> tuple[str, ...]:
        return tuple(
            item.py.name if isinstance(item, FunctionPlan) else item.py_name for item in self.items
        )

    @property
    def std_includes(self) -> tuple[str, ...]:
        """Standard headers of the types that cross the boundary in the generated
        source: the wrapped header may only declare them (<iosfwd>)."""
        kinds: set[StringKind] = set()
        for type_ in _header_types(self.header):
            if isinstance(type_, StringType):
                kinds.add(type_.kind)
        includes = []
        if StringKind.STRING in kinds:
            includes.append("string")
        if StringKind.STRING_VIEW in kinds:
            includes.append("string_view")
        return tuple(includes)


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


@dataclass(slots=True)
class _Copies:
    """The bodies of the C++ conversions of a struct, while they are built."""

    to_c: list[str] = dataclasses.field(default_factory=list)
    from_c: list[str] = dataclasses.field(default_factory=list)
    extents: dict[str, int] = dataclasses.field(default_factory=dict)


# Longest line written in one piece in the generated Python code.
_MAX_LINE_LENGTH = 99


def _spelling(type_: Type) -> str:
    """How a type is spelled in comments and docstrings: never a libclang
    spelling, which differs between platforms and standard libraries."""
    return type_.cpp_spelling


def _token(type_: Type, enums: dict[str, EnumPlan], structs: dict[str, str]) -> str:
    """Name of a type in overload suffixes: the C type name for an enum or a struct."""
    if isinstance(type_, EnumRef):
        enum = enums.get(type_.usr)
        return enum.c_type if enum is not None else "enum"
    if isinstance(type_, StructRef):
        return structs.get(type_.usr, "struct")
    return type_.token


def _describe_params(parameters: Sequence[Parameter]) -> str:
    return ", ".join(
        f"{_spelling(parameter.type)} {parameter.name}"
        if parameter.name
        else _spelling(parameter.type)
        for parameter in parameters
    )


def _checked(value: str, scalar: ScalarType, name: str) -> str:
    """Python expression that checks ``value`` for a scalar of C; ``name`` is the
    expression of its name in error messages."""
    if scalar.category is TypeCategory.BOOL:
        return f"_runtime.to_bool({value}, {name})"
    if scalar.category is TypeCategory.FLOATING:
        return f"_runtime.to_float({value}, ctypes.{scalar.ctypes_name}, {name})"
    return f"_runtime.to_int({value}, ctypes.{scalar.ctypes_name}, {name})"


def _conversion(name: str, scalar: ScalarType) -> str:
    return _checked(name, scalar, f'"{name}"')


class _Builder:
    def __init__(self, module: Module) -> None:
        self.module = module
        self.errors: list[Diagnostic] = []
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
            ("detail", "the namespace of the generated C++ helpers"),
        ):
            self._reserve_c_name(f"{module.name}_{name}", what, None)
        # A generated type with the name of an output parameter would be hidden
        # by it. (Only generated names: these do not clash with the headers.)
        for name in ("out_self", "out_result", "out_result_size"):
            self._c_names[name] = ("an output parameter of the generated functions", None)
        # A parameter named like a macro without arguments would be replaced
        # by it: the generated macros and include guards, and the macros of the
        # wrapped headers (the generated source includes them first).
        self._parameter_macros = self._macros | {
            naming.header_guard(module.name, header.stem) for header in module.headers
        }
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
        self._parameter_macros |= {f"{module.name}_status"}
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
        # Per header stem, the headers whose structs its functions use.
        self.foreign_struct_headers: dict[str, tuple[Header, ...]] = {}

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
                    _token(parameter.type, self._enums, self._struct_c_types)
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
        py_names: dict[str, str] = {}
        for enumerator in model.enumerators:
            macro = f"{c_type}_{enumerator.name}"
            self._reserve_c_name(
                macro,
                f"the constant of '{model.qualified_name}::{enumerator.name}'",
                model.location,
            )
            py_name = naming.enum_member_name(enumerator.name)
            if py_name in py_names:
                self.errors.append(
                    Diagnostic(
                        f"enumerators '{py_names[py_name]}' and '{enumerator.name}' of {what} get "
                        f"the same Python name '{py_name}'",
                        model.location,
                    )
                )
            py_names[py_name] = enumerator.name
            enumerators.append(
                EnumeratorPlan(
                    model_name=enumerator.name,
                    macro=macro,
                    literal=c_literal(enumerator.value, model.underlying.c_name),
                    py_name=py_name,
                    value=enumerator.value,
                    deprecated=enumerator.deprecated,
                )
            )
        # Nested: the class attribute; the class name is final once the header
        # module's names are assigned (see build_header).
        py_name = naming.member_name(model.name) if scope else naming.python_name(model.name)
        return EnumPlan(
            model=model,
            c_type=c_type,
            enumerators=tuple(enumerators),
            py_name=py_name,
            py_qualname=py_name,
            py_module=f"{self.module.name}.{header.stem}",
        )

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
        ctypes_names = naming.ctypes_field_names(names)
        generated = self._macros | {
            enumerator.macro for enum in self._enums.values() for enumerator in enum.enumerators
        }
        fields: list[FieldPlan] = []
        for field, name, ctypes_name in zip(model.fields, names, ctypes_names, strict=True):
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
            plan = self._field_plan(field, name, ctypes_name)
            if plan is not None:
                fields.append(plan)
        detail = f"{self.module.name}_detail"
        cpp_name = "::" + model.qualified_name
        init = CFunction(
            name=init_name,
            params=(
                CParam("out_result", f"{c_type}*", Role.OUT_RESULT, f"ctypes.POINTER(_{c_type})"),
            ),
            returns_status=True,
            # Value-initialization: zeros, then the default member initializers,
            # or the default constructor.
            statement=f"*out_result = {detail}::to_c({cpp_name}());",
            description=f"{model.qualified_name}(): the value of a default-constructed "
            f"{model.qualified_name}",
            ctypes_restype="_runtime.Status",
            checks=(_null_check("out_result"),),
            location=model.location,
        )
        py_name = naming.python_name(model.name)
        return StructPlan(
            model=model,
            c_type=c_type,
            fields=tuple(fields),
            init=init,
            py_name=py_name,
            py_qualname=py_name,
            py_module=f"{self.module.name}.{header.stem}",
            enums=tuple(self._enums[enum.usr] for enum in model.enums),
        )

    def _field_plan(self, field: Field, name: str, ctypes_name: str) -> FieldPlan | None:
        """How a member crosses: C declaration, ctypes type, Python checks."""
        type_ = field.type
        length = field.length
        value = f"value.{name}"
        path = f'name + ".{name}"'
        if isinstance(type_, EnumRef):
            enum = self._enum(type_, field.location)
            if enum is None:
                return None
            c_type, ctypes_element, annotation = enum.c_type, enum.ctypes_type, enum.c_type
            valid = enum.model.valid_range
            extra = f", ({valid[0]}, {valid[1]})" if valid is not None else ""

            def check(item: str, where: str) -> str:
                return (
                    f"_runtime.to_enum({item}, {enum.c_type}, {enum.ctypes_type}, {where}{extra})"
                )

            def read(item: str) -> str:
                return f"_runtime.enum_result({enum.c_type}, {item})"

        elif isinstance(type_, StructRef):
            nested = self._struct_c_types.get(type_.usr)
            if nested is None:
                self._struct_missing(type_, field.location)
                return None
            c_type, ctypes_element, annotation = nested, f"_{nested}", nested

            def check(item: str, where: str) -> str:  # unused: structs are filled in place
                raise AssertionError

            def read(item: str) -> str:
                return f"_{nested}__from_c({item})"

        else:
            c_type, ctypes_element = type_.c_name, f"ctypes.{type_.ctypes_name}"
            annotation = {
                TypeCategory.BOOL: "bool",
                TypeCategory.FLOATING: "float",
            }.get(type_.category, "int")
            scalar = type_

            def check(item: str, where: str) -> str:
                return _checked(item, scalar, where)

            def read(item: str) -> str:
                return item

        if length is None:
            if isinstance(type_, StructRef):
                fill = (f"_{c_type}__fill(raw.{ctypes_name}, {value}, {path})",)
            else:
                fill = (f"raw.{ctypes_name} = {check(value, path)}",)
            return FieldPlan(
                model=field,
                name=name,
                ctypes_name=ctypes_name,
                c_declaration=f"{c_type} {name}",
                ctypes_type=ctypes_element,
                py_annotation=annotation,
                fill=fill,
                read=read(f"raw.{ctypes_name}"),
            )
        # Bytes for the arrays of bytes only: b"ab" is no list of int16.
        is_bytes = isinstance(type_, ScalarType) and type_.c_name in ("uint8_t", "unsigned char")
        where = f'f"{{name}}.{name}[{{i}}]"'
        items = f"_runtime.to_items({value}, {length}, {path}{', True' if is_bytes else ''})"
        if isinstance(type_, StructRef):
            body = f"_{c_type}__fill(raw.{ctypes_name}[i], item, {where})"
        else:
            body = f"raw.{ctypes_name}[i] = {check('item', where)}"
        if isinstance(type_, ScalarType):
            reading = f"raw.{ctypes_name}[:]"
        else:
            reading = f"[{read('item')} for item in raw.{ctypes_name}]"
        return FieldPlan(
            model=field,
            name=name,
            ctypes_name=ctypes_name,
            c_declaration=f"{c_type} {name}[{length}]",
            ctypes_type=f"{ctypes_element} * {length}",
            py_annotation="list",
            fill=(f"for i, item in enumerate({items}):", f"    {body}"),
            read=reading,
        )

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

    # Lowering: how each type crosses the boundary --------------------------------

    def _lower_params(
        self, parameters: Sequence[Parameter], location: Location, reserved: Iterable[str] = ()
    ) -> list[LoweredParam]:
        taken = self._parameter_macros | frozenset(reserved)
        cpp_names = [parameter.name for parameter in parameters]
        names = naming.parameter_names(cpp_names, taken)
        sizes = naming.derived_names(
            cpp_names,
            names,
            [
                isinstance(parameter.type, StringType) and parameter.type.sized
                for parameter in parameters
            ],
            "_size",
            taken,
        )
        return [
            self._lower_param(name, size, parameter.type, location)
            for name, size, parameter in zip(names, sizes, parameters, strict=True)
        ]

    def _lower_param(
        self, name: str, size: str | None, type_: Type, location: Location
    ) -> LoweredParam:
        if isinstance(type_, StringType):
            return self._lower_string_param(name, size, type_)
        if isinstance(type_, EnumRef):
            return self._lower_enum_param(name, self._enum(type_, location))
        if isinstance(type_, StructRef):
            return self._lower_struct_param(name, type_, self._struct(type_, location))
        return LoweredParam(
            c_params=(CParam(name, type_.c_name, Role.INPUT, f"ctypes.{type_.ctypes_name}"),),
            argument=name,
            null_checks=(),
            value_checks=(),
            py=PyArg(name, (f"{name} = {_conversion(name, type_)}",)),
            call_args=(name,),
        )

    def _lower_string_param(self, name: str, size: str | None, type_: StringType) -> LoweredParam:
        data = CParam(name, "const char*", Role.INPUT, "ctypes.c_char_p")
        if size is None:
            # NUL-terminated: Python refuses NULs, which C++ would not see.
            return LoweredParam(
                c_params=(data,),
                argument=name,
                null_checks=(_null_check(name),),
                value_checks=(),
                py=PyArg(name, (f'{name} = _runtime.to_c_string({name}, "{name}")',)),
                call_args=(name,),
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
            c_params=(data, CParam(size, "size_t", Role.SIZE, "ctypes.c_size_t")),
            argument=argument,
            temporary=temporary if type_.by_reference else None,
            null_checks=(_sized_null_check(name, size),),
            value_checks=(),
            # The size of the exact bytes object: before self._ptr(), so that
            # nothing runs between it and the call.
            py=PyArg(
                name,
                (
                    f'{name} = _runtime.to_bytes({name}, "{name}")',
                    f"{size} = _builtins.len({name})",
                ),
            ),
            call_args=(name, size),
        )

    def _lower_enum_param(self, name: str, enum: EnumPlan | None) -> LoweredParam:
        if enum is None:  # reported
            return LoweredParam((), name, (), (), PyArg(name, ()), ())
        checks: list[Precondition] = []
        valid = enum.model.valid_range
        conversion = (
            f'{name} = _runtime.to_enum({name}, {enum.py_type}, {enum.ctypes_type}, "{name}"'
        )
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
            conversion += f", ({low}, {high})"
        return LoweredParam(
            c_params=(CParam(name, enum.c_type, Role.INPUT, enum.ctypes_type),),
            argument=f"static_cast<{enum.cpp_name}>({name})",
            null_checks=(),
            value_checks=tuple(checks),
            py=PyArg(name, (conversion + ")",)),
            call_args=(name,),
        )

    def _lower_struct_param(
        self, name: str, ref: StructRef, struct: StructPlan | None
    ) -> LoweredParam:
        if struct is None:  # reported
            return LoweredParam((), name, (), (), PyArg(name, ()), ())
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
                    f"ctypes.POINTER(_types.{struct.mirror})",
                ),
            ),
            argument=argument,
            null_checks=(_null_check(name),),
            value_checks=(),
            py=PyArg(name, (f'{name} = _types.{struct.mirror}__to_c({name}, "{name}")',)),
            call_args=(name,),
            temporary=temporary if ref.by_reference else None,
        )

    def _lower_struct_result(self, struct: StructPlan | None) -> LoweredResult:
        if struct is None:  # reported
            return LoweredResult((), "{call};", (), _NO_RESULT, ())
        detail = f"{self.module.name}_detail"
        return LoweredResult(
            c_params=(
                CParam(
                    "out_result",
                    f"{struct.c_type}*",
                    Role.OUT_RESULT,
                    f"ctypes.POINTER(_types.{struct.mirror})",
                ),
            ),
            # Caller storage, which may alias an input: written on success only,
            # after the call.
            statement=f"*out_result = {detail}::to_c({{call}});",
            null_checks=(_null_check("out_result"),),
            py=PyResult(
                setup=(f"_out = _types.{struct.mirror}()",),
                value=f"_types.{struct.mirror}__from_c(_out)",
            ),
            call_args=("_out",),
        )

    def _lower_enum_result(self, enum: EnumPlan | None) -> LoweredResult:
        if enum is None:  # reported
            return LoweredResult((), "{call};", (), _NO_RESULT, ())
        return LoweredResult(
            c_params=(
                CParam(
                    "out_result",
                    f"{enum.c_type}*",
                    Role.OUT_RESULT,
                    f"ctypes.POINTER({enum.ctypes_type})",
                ),
            ),
            statement=f"*out_result = static_cast<{enum.c_type}>({{call}});",
            null_checks=(_null_check("out_result"),),
            py=PyResult(
                setup=(f"_out = {enum.ctypes_type}()",),
                value=f"_runtime.enum_result({enum.py_type}, _out.value)",
            ),
            call_args=("_out",),
        )

    def _lower_result(self, type_: Type, location: Location) -> LoweredResult:
        if type_.is_void:
            return LoweredResult((), "{call};", (), _NO_RESULT, ())
        if isinstance(type_, StringType):
            return self._lower_string_result(type_)
        if isinstance(type_, EnumRef):
            return self._lower_enum_result(self._enum(type_, location))
        if isinstance(type_, StructRef):
            return self._lower_struct_result(self._struct(type_, location))
        return LoweredResult(
            c_params=(
                CParam(
                    "out_result",
                    f"{type_.c_name}*",
                    Role.OUT_RESULT,
                    f"ctypes.POINTER(ctypes.{type_.ctypes_name})",
                ),
            ),
            statement="*out_result = {call};",
            null_checks=(_null_check("out_result"),),
            py=PyResult(setup=(f"_out = ctypes.{type_.ctypes_name}()",), value="_out.value"),
            call_args=("_out",),
        )

    def _lower_string_result(self, type_: StringType) -> LoweredResult:
        detail = f"{self.module.name}_detail"
        helper = "return_string" if type_.sized else "return_c_string"
        if type_.sized:
            read = "_out_data = _runtime.string_at(_out, _out_size.value)"
            value = "_runtime.decode(_out_data)"
        else:
            # A NULL const char* is None.
            read = "_out_data = _runtime.string_at(_out, _out_size.value) if _out else None"
            value = "None if _out_data is None else _runtime.decode(_out_data)"
        return LoweredResult(
            c_params=(
                CParam("out_result", "char**", Role.OUT_RESULT, "ctypes.POINTER(ctypes.c_void_p)"),
                CParam(
                    "out_result_size",
                    "size_t*",
                    Role.OUT_RESULT_SIZE,
                    "ctypes.POINTER(ctypes.c_size_t)",
                ),
            ),
            statement=f"{detail}::{helper}({{call}}, out_result, out_result_size);",
            null_checks=(),
            py=PyResult(
                setup=("_out = ctypes.c_void_p()", "_out_size = ctypes.c_size_t()"),
                value=value,
                read=read,
                cleanup=("if _out:", f"    _lib.{self.module.name}_free(_out)"),
            ),
            call_args=("_out", "_out_size"),
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
        self_param: CParam | None = None,
    ) -> tuple[CFunction, LoweredResult]:
        """A function that returns a status and passes the result through out-parameters.

        ``call`` is the wrapped call, with ``{arguments}`` where the arguments go.
        """
        lowered = self._lower_result(result, location)
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
            ctypes_restype="_runtime.Status",
            owned_checks=lowered.owned_checks,
            resets=lowered.resets,
            checks=(*null_checks, *(c for param in params for c in param.value_checks)),
            location=location,
            compile_checks=_temporaries_check(call, params, f"'{description}'"),
        )
        return function, lowered

    @staticmethod
    def _python_callable(
        name: str,
        function: CFunction,
        params: Sequence[LoweredParam],
        result: LoweredResult | None,
        *,
        leading: Sequence[str] = (),
    ) -> PyCallable:
        """``leading`` are the call arguments before the parameters (self._ptr()...)."""
        call_args = [*leading, *(arg for param in params for arg in param.call_args)]
        if result is not None:
            call_args += result.call_args
        return PyCallable(
            name,
            function,
            tuple(param.py for param in params),
            tuple(call_args),
            result.py if result is not None else _NO_RESULT,
        )

    # Declarations ------------------------------------------------------------

    def build_class(self, model: Class) -> ClassPlan:
        prefix = naming.c_prefix(model.namespace, self.module.name)
        c_type = f"{prefix}_{model.name}"
        self._reserve_c_name(
            c_type, f"the handle type of '{model.qualified_name}'", model.location, tag=True
        )
        cpp_name = "::" + model.qualified_name
        # '__' separates the role from the C name: C names never contain it, so
        # the class X_p cannot take the pointer type of X.
        pointer_name = f"_{c_type}__p"
        key = (*model.namespace, model.name)

        def self_param(is_const: bool) -> CParam:
            qualifier = "const " if is_const else ""
            return CParam("self", f"{qualifier}{c_type}*", Role.SELF, f"_types.{pointer_name}")

        def cast_self(is_const: bool) -> str:
            # The C handle is an opaque struct; in C++ it points to the class.
            qualifier = "const " if is_const else ""
            return f"reinterpret_cast<{qualifier}{cpp_name}*>(self)"

        constructors: list[PyCallable] = []
        for constructor in model.constructors:
            suffix = self._suffix(("constructor", *key), constructor.parameters)
            name = f"{c_type}_create{suffix}"
            what = f"constructor '{cpp_name}({_describe_params(constructor.parameters)})'"
            self._reserve_c_name(name, what, constructor.location)
            out_self = CParam(
                "out_self",
                f"{c_type}**",
                Role.OUT_SELF,
                f"ctypes.POINTER(_types.{pointer_name})",
            )
            params = self._lower_params(constructor.parameters, constructor.location)
            arguments = ", ".join(param.argument for param in params)
            description = (
                f"{model.qualified_name}::{model.name}({_describe_params(constructor.parameters)})"
            )
            function = CFunction(
                name=name,
                params=(out_self, *(c for param in params for c in param.c_params)),
                returns_status=True,
                statement=(
                    f"*out_self = reinterpret_cast<{c_type}*>(new {cpp_name}({arguments}));"
                ),
                description=description,
                ctypes_restype="_runtime.Status",
                owned_checks=(_null_check("out_self"),),
                resets=("*out_self = nullptr;",),
                checks=(
                    *(c for param in params for c in param.null_checks),
                    *(c for param in params for c in param.value_checks),
                ),
                location=constructor.location,
                compile_checks=_temporaries_check(
                    f"{cpp_name}({{arguments}})", params, f"'{description}'"
                ),
            )
            py_name = "create" + suffix if len(model.constructors) > 1 else "__init__"
            constructors.append(
                self._python_callable(py_name, function, params, None, leading=("_handle",))
            )

        destroy_name = f"{c_type}_destroy"
        self._reserve_c_name(destroy_name, f"the destructor of '{cpp_name}'", model.location)
        destroy = CFunction(
            name=destroy_name,
            params=(self_param(False),),
            returns_status=False,
            statement=f"delete {cast_self(False)};",
            description=f"{model.qualified_name}::~{model.name}()",
            ctypes_restype="None",
            location=model.location,
        )

        factory_names = {item.name for item in constructors}
        members: list[PyCallable] = []
        py_names: dict[str, Location] = {}
        enums = self._nested_enums(model, factory_names, py_names)
        for method in model.methods:
            suffix = self._suffix(("method", *key, method.name), method.parameters)
            name = f"{c_type}_{method.name}{suffix}"
            what = f"method '{cpp_name}::{method.name}({_describe_params(method.parameters)})'"
            self._reserve_c_name(name, what, method.location)
            params = self._lower_params(method.parameters, method.location)
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
                )
                leading: tuple[str, ...] = ()
            else:
                function, result = self._status_function(
                    name,
                    params,
                    f"{cast_self(method.is_const)}->{method.name}({{arguments}})",
                    method.result,
                    description,
                    location=method.location,
                    self_param=self_param(method.is_const),
                )
                leading = ("self._ptr()",)
            py_name = naming.member_name(method.name + suffix, reserved=factory_names)
            if py_name in py_names:
                self.errors.append(
                    Diagnostic(
                        f"two members of '{cpp_name}' get the Python name '{py_name}' "
                        f"(the other one is declared at {py_names[py_name]})",
                        method.location,
                    )
                )
            py_names[py_name] = method.location
            member = self._python_callable(py_name, function, params, result, leading=leading)
            members.append(dataclasses.replace(member, is_static=method.is_static))

        return ClassPlan(
            model=model,
            c_type=c_type,
            py_name=naming.python_name(model.name),
            struct_name=f"_{c_type}",
            pointer_name=pointer_name,
            constructors=tuple(constructors),
            destroy=destroy,
            members=tuple(members),
            enums=enums,
        )

    def _nested_enums(
        self, model: Class, factory_names: set[str], py_names: dict[str, Location]
    ) -> tuple[EnumPlan, ...]:
        """The plans of the enums nested in a class, with their attribute names
        (added to ``py_names``, which the methods must not take)."""
        enums: list[EnumPlan] = []
        for enum in model.enums:
            py_name = naming.member_name(enum.name, reserved=factory_names)
            py_names[py_name] = enum.location
            enums.append(dataclasses.replace(self._enums[enum.usr], py_name=py_name))
        return tuple(enums)

    def build_function(self, model: Function) -> FunctionPlan:
        prefix = naming.c_prefix(model.namespace, self.module.name)
        suffix = self._suffix(("function", *model.namespace, model.name), model.parameters)
        name = f"{prefix}_{model.name}{suffix}"
        what = f"function '{model.qualified_name}({_describe_params(model.parameters)})'"
        self._reserve_c_name(name, what, model.location)
        params = self._lower_params(model.parameters, model.location)
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
        )
        py_name = naming.python_name(model.name + suffix)
        return FunctionPlan(model, self._python_callable(py_name, function, params, result))

    def build_header(self, header: Header) -> HeaderPlan:
        stem = naming.check_header_stem(header.stem)
        guard = naming.header_guard(self.module.name, stem)
        self._reserve_c_name(guard, f"the include guard of the C header of {header.path}", None)
        internal_guard = f"{guard.removesuffix('_C_H')}_C_INTERNAL_HPP"
        if any(isinstance(declaration, Struct) for declaration in header.declarations):
            self._reserve_c_name(
                internal_guard, f"the include guard of the conversions of {header.path}", None
            )
        items: list[ClassPlan | FunctionPlan | EnumPlan | StructPlan] = []
        for declaration in header.declarations:
            if isinstance(declaration, Class):
                items.append(self.build_class(declaration))
            elif isinstance(declaration, Function):
                items.append(self.build_function(declaration))
            elif isinstance(declaration, Struct):
                items.append(self._struct_with_names(self._structs[declaration.usr]))
            else:
                items.append(self._enums[declaration.usr])
        owners: dict[str, Header] = {}
        for type_ in _header_types(header, fields=False):
            if isinstance(type_, StructRef) and type_.usr in self._structs:
                owner = self._struct_headers[type_.usr]
                if owner is not header:
                    owners.setdefault(owner.stem, owner)
        self.foreign_struct_headers[header.stem] = tuple(owners.values())
        foreign: dict[str, EnumPlan] = {}
        for type_ in _header_types(header):
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
            items=self._assign_module_names(items),
            foreign_enums=tuple(foreign.values()),
        )

    def _struct_with_names(self, struct: StructPlan) -> StructPlan:
        """The Python names of a struct's nested enums, which must not take the
        name of a field."""
        fields = {field.name: field.model.location for field in struct.fields}
        enums: list[EnumPlan] = []
        for enum in struct.enums:
            py_name = naming.member_name(enum.model.name)
            if py_name in fields:
                self.errors.append(
                    Diagnostic(
                        f"enum '{enum.model.qualified_name}' and a member of "
                        f"'{struct.model.qualified_name}' get the Python name '{py_name}'",
                        enum.model.location,
                    )
                )
            enums.append(dataclasses.replace(enum, py_name=py_name))
        return dataclasses.replace(struct, enums=tuple(enums))

    def _assign_module_names(
        self, items: Sequence[ClassPlan | FunctionPlan | EnumPlan | StructPlan]
    ) -> tuple[ClassPlan | FunctionPlan | EnumPlan | StructPlan, ...]:
        """Give classes, functions, enums and structs Python names that do not hide
        generated globals, and nested enums their final qualified name."""
        renamed: list[ClassPlan | FunctionPlan | EnumPlan | StructPlan] = []
        seen: dict[str, Location] = {}
        for item in items:
            if isinstance(item, (ClassPlan, StructPlan)):
                name = naming.module_level_name(item.py_name)
                location = item.model.location
                enums = tuple(
                    dataclasses.replace(enum, py_qualname=f"{name}.{enum.py_name}")
                    for enum in item.enums
                )
                if isinstance(item, StructPlan):
                    renamed.append(
                        dataclasses.replace(item, py_name=name, py_qualname=name, enums=enums)
                    )
                else:
                    renamed.append(dataclasses.replace(item, py_name=name, enums=enums))
            elif isinstance(item, EnumPlan):
                name = naming.module_level_name(item.py_name)
                location = item.model.location
                renamed.append(dataclasses.replace(item, py_name=name, py_qualname=name))
            else:
                name = naming.module_level_name(item.py.name)
                location = item.model.location
                renamed.append(
                    dataclasses.replace(item, py=dataclasses.replace(item.py, name=name))
                )
            if name in seen:
                self.errors.append(
                    Diagnostic(
                        f"two declarations get the Python name '{name}' "
                        f"(the other one is declared at {seen[name]})",
                        location,
                    )
                )
            seen[name] = location
        return tuple(renamed)


# Conventions of the generated C API that its headers do not show (the
# contract of the out-parameters, how memory is returned and released...).
# Bump it with every change to them, and never reuse a value:
#   1  status codes, opaque handles, last_error and api_fingerprint.
#   2  <module>_free, strings (results in memory that <module>_free releases),
#      ERROR_INVALID_ARGUMENT, outputs that the function allocates are NULL on
#      every failure.
#   3  value structs: by pointer, in pack(8) mirrors, results written on
#      success only; ERROR_INVALID_ARGUMENT for invalid members.
C_API_CONVENTIONS = 3


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


def build_plan(module: Module) -> ModulePlan:
    """Decide every generated name and check that none of them clash.

    Raises:
        ConfigurationError: invalid module or header names.
        GenerationError: generated names clash.
    """
    naming.check_module_name(module.name)
    builder = _Builder(module)
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
        )
        for plan in headers
    ]
    return ModulePlan(name=module.name, headers=tuple(headers), structs=_structs_in_order(headers))


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
