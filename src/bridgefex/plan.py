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
from .model import Class, Function, Header, Module, Parameter, ScalarType, TypeCategory
from .typemap import VOID


class Role(Enum):
    SELF = "self"
    OUT_SELF = "out_self"
    INPUT = "input"
    OUT_RESULT = "out_result"


@dataclass(frozen=True, slots=True)
class CParam:
    name: str
    c_type: str
    role: Role
    ctypes_type: str
    scalar: ScalarType | None = None

    @property
    def declaration(self) -> str:
        return f"{self.c_type} {self.name}"


@dataclass(frozen=True, slots=True)
class CFunction:
    name: str
    params: tuple[CParam, ...]
    returns_status: bool
    statement: str
    """C++ statement that does the work, run inside the exception guard."""

    description: str
    """The wrapped C++ declaration, for comments and docstrings."""

    null_checked: tuple[CParam, ...]
    """Pointer parameters that must not be NULL."""

    ctypes_restype: str

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
    conversion: str


@dataclass(frozen=True, slots=True)
class PyCallable:
    name: str
    function: CFunction
    args: tuple[PyArg, ...]
    call_args: tuple[str, ...]
    """Arguments of the C call, in order."""

    result_ctype: str | None
    """ctypes type of the result (``ctypes.c_int32``), or None for ``void``."""

    is_static: bool = False

    @property
    def parameter_list(self) -> str:
        return ", ".join(arg.name for arg in self.args)

    def check_call(self, indent: int) -> str:
        """The C call wrapped in ``_runtime.check``, indented by ``indent`` spaces."""
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
        if self.result_ctype is None:
            return self.check_call(indent)
        return "\n".join(
            (
                f"{pad}_out = {self.result_ctype}()",
                self.check_call(indent),
                f"{pad}return _out.value",
            )
        )


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

    @property
    def cpp_name(self) -> str:
        return "::" + self.model.qualified_name

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
    items: tuple[ClassPlan | FunctionPlan, ...]
    """Classes and free functions, in source order."""

    @property
    def classes(self) -> tuple[ClassPlan, ...]:
        return tuple(item for item in self.items if isinstance(item, ClassPlan))

    @property
    def c_functions(self) -> tuple[CFunction, ...]:
        functions: list[CFunction] = []
        for item in self.items:
            if isinstance(item, ClassPlan):
                functions += item.c_functions
            else:
                functions.append(item.c_function)
        return tuple(functions)

    @property
    def py_exports(self) -> tuple[str, ...]:
        return tuple(
            item.py_name if isinstance(item, ClassPlan) else item.py.name for item in self.items
        )


@dataclass(frozen=True, slots=True)
class ModulePlan:
    name: str
    headers: tuple[HeaderPlan, ...]
    api_fingerprint: str
    """Hash of every generated C declaration; see :func:`api_fingerprint`."""

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
    def runtime_header(self) -> str:
        return f"{self.name}_runtime.h"

    @property
    def runtime_internal_header(self) -> str:
        return f"{self.name}_runtime_internal.hpp"

    @property
    def runtime_source(self) -> str:
        return f"{self.name}_runtime.cpp"

    @property
    def library_env_var(self) -> str:
        return f"{self.macro_prefix}_LIBRARY"


# Longest line written in one piece in the generated Python code.
_MAX_LINE_LENGTH = 99

# Python names that the generated per-header module defines or imports.
_MODULE_LEVEL_RESERVED = frozenset({"ctypes", "weakref"})


def _describe_params(parameters: Sequence[Parameter]) -> str:
    return ", ".join(
        f"{parameter.type.c_name} {parameter.name}" if parameter.name else parameter.type.c_name
        for parameter in parameters
    )


def _conversion(name: str, scalar: ScalarType) -> str:
    if scalar.category is TypeCategory.BOOL:
        return f'_runtime.to_bool({name}, "{name}")'
    if scalar.category is TypeCategory.FLOATING:
        return f'_runtime.to_float({name}, ctypes.{scalar.ctypes_name}, "{name}")'
    return f'_runtime.to_int({name}, ctypes.{scalar.ctypes_name}, "{name}")'


class _Builder:
    def __init__(self, module: Module) -> None:
        self.module = module
        self.errors: list[Diagnostic] = []
        self._c_names: dict[str, tuple[str, Location | None]] = {}
        self._reserve_c_name(f"{module.name}_status", "the status type", None)
        self._reserve_c_name(f"{module.name}_last_error", "the last-error function", None)
        self._reserve_c_name(f"{module.name}_api_fingerprint", "the API fingerprint function", None)
        self._overloads = self._count_overloads(module)

    # Bookkeeping -------------------------------------------------------------

    @staticmethod
    def _count_overloads(module: Module) -> Counter[tuple[str, ...]]:
        counts: Counter[tuple[str, ...]] = Counter()
        for header in module.headers:
            for declaration in header.declarations:
                if isinstance(declaration, Function):
                    counts[("function", *declaration.namespace, declaration.name)] += 1
                else:
                    key = (*declaration.namespace, declaration.name)
                    counts[("constructor", *key)] += len(declaration.constructors)
                    for method in declaration.methods:
                        counts[("method", *key, method.name)] += 1
        return counts

    def _reserve_c_name(self, name: str, what: str, location: Location | None) -> None:
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
            return naming.overload_suffix([parameter.type for parameter in parameters])
        return ""

    def _params(
        self, parameters: Sequence[Parameter], reserved: Iterable[str] = ()
    ) -> list[CParam]:
        names = naming.parameter_names([parameter.name for parameter in parameters], reserved)
        return [
            CParam(
                name=name,
                c_type=parameter.type.c_name,
                role=Role.INPUT,
                ctypes_type=f"ctypes.{parameter.type.ctypes_name}",
                scalar=parameter.type,
            )
            for name, parameter in zip(names, parameters, strict=True)
        ]

    def _status_function(
        self,
        name: str,
        params: Sequence[CParam],
        call: str,
        result: ScalarType,
        description: str,
    ) -> CFunction:
        """A function that returns a status and passes the result through an out-parameter."""
        all_params = list(params)
        if result.is_void:
            statement = f"{call};"
        else:
            all_params.append(
                CParam(
                    name="out_result",
                    c_type=f"{result.c_name}*",
                    role=Role.OUT_RESULT,
                    ctypes_type=f"ctypes.POINTER(ctypes.{result.ctypes_name})",
                    scalar=result,
                )
            )
            statement = f"*out_result = {call};"
        return CFunction(
            name=name,
            params=tuple(all_params),
            returns_status=True,
            statement=statement,
            description=description,
            null_checked=tuple(param for param in all_params if param.role is not Role.INPUT),
            ctypes_restype="_runtime.Status",
        )

    @staticmethod
    def _python_callable(name: str, function: CFunction, result: ScalarType) -> PyCallable:
        args: list[PyArg] = []
        call_args: list[str] = []
        for param in function.params:
            if param.role is Role.SELF:
                call_args.append("self._ptr()")
            elif param.role is Role.OUT_SELF:
                call_args.append("ctypes.byref(_handle)")
            elif param.role is Role.OUT_RESULT:
                call_args.append("ctypes.byref(_out)")
            else:
                assert param.scalar is not None
                conversion = _conversion(param.name, param.scalar)
                args.append(PyArg(param.name, conversion))
                call_args.append(conversion)
        result_ctype = None if result.is_void else f"ctypes.{result.ctypes_name}"
        return PyCallable(name, function, tuple(args), tuple(call_args), result_ctype)

    # Declarations ------------------------------------------------------------

    def build_class(self, model: Class) -> ClassPlan:
        prefix = naming.c_prefix(model.namespace, self.module.name)
        c_type = f"{prefix}_{model.name}"
        self._reserve_c_name(c_type, f"the handle type of '{model.qualified_name}'", model.location)
        cpp_name = "::" + model.qualified_name
        pointer_name = f"_{c_type}_p"
        key = (*model.namespace, model.name)

        def self_param(is_const: bool) -> CParam:
            qualifier = "const " if is_const else ""
            return CParam("self", f"{qualifier}{c_type}*", Role.SELF, pointer_name)

        constructors: list[PyCallable] = []
        for constructor in model.constructors:
            suffix = self._suffix(("constructor", *key), constructor.parameters)
            name = f"{c_type}_create{suffix}"
            what = f"constructor '{cpp_name}({_describe_params(constructor.parameters)})'"
            self._reserve_c_name(name, what, constructor.location)
            out_self = CParam(
                "out_self", f"{c_type}**", Role.OUT_SELF, f"ctypes.POINTER({pointer_name})"
            )
            params = self._params(constructor.parameters, reserved=(pointer_name,))
            arguments = ", ".join(param.name for param in params)
            description = (
                f"{model.qualified_name}::{model.name}({_describe_params(constructor.parameters)})"
            )
            function = CFunction(
                name=name,
                params=(out_self, *params),
                returns_status=True,
                statement=f"*out_self = new {cpp_name}({arguments});",
                description=description,
                null_checked=(out_self,),
                ctypes_restype="_runtime.Status",
            )
            py_name = "create" + suffix if len(model.constructors) > 1 else "__init__"
            constructors.append(self._python_callable(py_name, function, VOID))

        destroy_name = f"{c_type}_destroy"
        self._reserve_c_name(destroy_name, f"the destructor of '{cpp_name}'", model.location)
        destroy = CFunction(
            name=destroy_name,
            params=(self_param(False),),
            returns_status=False,
            statement="delete self;",
            description=f"{model.qualified_name}::~{model.name}()",
            null_checked=(),
            ctypes_restype="None",
        )

        factory_names = {item.name for item in constructors}
        members: list[PyCallable] = []
        py_names: dict[str, Location] = {}
        for method in model.methods:
            suffix = self._suffix(("method", *key, method.name), method.parameters)
            name = f"{c_type}_{method.name}{suffix}"
            what = f"method '{cpp_name}::{method.name}({_describe_params(method.parameters)})'"
            self._reserve_c_name(name, what, method.location)
            params = self._params(method.parameters)
            arguments = ", ".join(param.name for param in params)
            qualifiers = " const" if method.is_const else ""
            static = "static " if method.is_static else ""
            description = (
                f"{static}{method.result.c_name} {model.qualified_name}::{method.name}"
                f"({_describe_params(method.parameters)}){qualifiers}"
            )
            if method.is_static:
                call = f"{cpp_name}::{method.name}({arguments})"
                function = self._status_function(name, params, call, method.result, description)
            else:
                call = f"self->{method.name}({arguments})"
                function = self._status_function(
                    name, [self_param(method.is_const), *params], call, method.result, description
                )
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
            member = self._python_callable(py_name, function, method.result)
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
        )

    def build_function(self, model: Function) -> FunctionPlan:
        prefix = naming.c_prefix(model.namespace, self.module.name)
        suffix = self._suffix(("function", *model.namespace, model.name), model.parameters)
        name = f"{prefix}_{model.name}{suffix}"
        what = f"function '{model.qualified_name}({_describe_params(model.parameters)})'"
        self._reserve_c_name(name, what, model.location)
        params = self._params(model.parameters)
        arguments = ", ".join(param.name for param in params)
        call = f"::{model.qualified_name}({arguments})"
        description = (
            f"{model.result.c_name} {model.qualified_name}({_describe_params(model.parameters)})"
        )
        function = self._status_function(name, params, call, model.result, description)
        py_name = naming.python_name(model.name + suffix)
        return FunctionPlan(model, self._python_callable(py_name, function, model.result))

    def build_header(self, header: Header) -> HeaderPlan:
        stem = naming.check_header_stem(header.stem)
        items: list[ClassPlan | FunctionPlan] = []
        for declaration in header.declarations:
            if isinstance(declaration, Class):
                items.append(self.build_class(declaration))
            else:
                items.append(self.build_function(declaration))
        self._check_module_names(items)
        return HeaderPlan(
            header=header,
            stem=stem,
            c_header=f"{stem}_c.h",
            c_source=f"{stem}_c.cpp",
            guard=f"{self.module.name}_{stem}_c_h".upper(),
            items=tuple(items),
        )

    def _check_module_names(self, items: Iterable[ClassPlan | FunctionPlan]) -> None:
        seen: dict[str, Location] = {}
        for item in items:
            if isinstance(item, ClassPlan):
                name, location = item.py_name, item.model.location
            else:
                name, location = item.py.name, item.model.location
            if name in _MODULE_LEVEL_RESERVED:
                message = f"'{name}' cannot be used as a Python name in the bindings"
                self.errors.append(Diagnostic(message, location))
            elif name in seen:
                self.errors.append(
                    Diagnostic(
                        f"two declarations get the Python name '{name}' "
                        f"(the other one is declared at {seen[name]})",
                        location,
                    )
                )
            seen[name] = location


# Bump when the conventions of the generated C API change (status codes,
# runtime functions, calling conventions), so that the fingerprint changes too.
_C_API_CONVENTIONS = "bridgefex-c-api-1"


def api_fingerprint(module_name: str, headers: Sequence[HeaderPlan]) -> str:
    """Hash of the module's C API: every handle type and function declaration.

    It only depends on the generated declarations, so it is stable across
    runs and platforms, and changes whenever a declaration changes.
    """
    lines = [_C_API_CONVENTIONS, module_name]
    for header in headers:
        lines += [f"type {item.c_type}" for item in header.classes]
        for function in header.c_functions:
            result = f"{module_name}_status" if function.returns_status else "void"
            lines.append(f"{result} {function.signature}")
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:32]


def build_plan(module: Module) -> ModulePlan:
    """Decide every generated name and check that none of them clash.

    Raises:
        ConfigurationError: invalid module or header names.
        GenerationError: generated names clash.
    """
    naming.check_module_name(module.name)
    builder = _Builder(module)
    stems: dict[str, Header] = {}
    headers: list[HeaderPlan] = []
    for header in module.headers:
        if header.stem in stems:
            builder.errors.append(
                Diagnostic(
                    f"headers '{stems[header.stem].path}' and '{header.path}' have the same "
                    "name; their generated files would overwrite each other"
                )
            )
            continue
        stems[header.stem] = header
        headers.append(builder.build_header(header))
    if builder.errors:
        raise GenerationError(builder.errors)
    return ModulePlan(
        name=module.name,
        headers=tuple(headers),
        api_fingerprint=api_fingerprint(module.name, headers),
    )
