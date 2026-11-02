# SPDX-License-Identifier: Apache-2.0
"""The Python view of a module plan: the names that Python gives to its enums,
structs, classes and functions, and what the templates render."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from ... import naming
from ...errors import Diagnostic, GenerationError, Location
from ...model import (
    ContainerType,
    EnumRef,
    ScalarType,
    StringKind,
    StringType,
    StructRef,
    TypeCategory,
)
from ...plan import (
    CallableKind,
    CallablePlan,
    CallbackPart,
    CallbackSpec,
    CFunction,
    ClassPlan,
    ClassView,
    EnumPlan,
    FieldPlan,
    FunctionPlan,
    HeaderPlan,
    ModulePlan,
    StructPlan,
)
from .lower import (
    build_callable,
    callback_thunk_name,
    enum_ctypes,
    enum_type,
    handle,
    scalar_check,
)
from .model import PyCallable, python_call

# Enums and structs -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PyEnumerator:
    py_name: str
    value: int


@dataclass(frozen=True, slots=True)
class PyEnum:
    plan: EnumPlan
    py_name: str
    """Its name in the header module, or the class attribute of a nested enum."""

    py_qualname: str
    py_module: str
    enumerators: tuple[PyEnumerator, ...]

    @property
    def c_type(self) -> str:
        return self.plan.c_type

    @property
    def model(self) -> object:
        return self.plan.model

    @property
    def py_type(self) -> str:
        """The IntEnum, as the generated header modules name it."""
        return enum_type(self.plan)


@dataclass(frozen=True, slots=True)
class PyField:
    name: str
    ctypes_name: str
    """In the ctypes mirror, where a few names are taken by ctypes itself."""

    ctypes_type: str
    py_annotation: str
    """The type of the dataclass field, as _types.py names it."""

    fill: tuple[str, ...]
    """Statements of _<c_type>__fill that check the value and store it in ``raw``."""

    read: str
    """Expression of _<c_type>__from_c that reads it from ``raw``."""


@dataclass(frozen=True, slots=True)
class PyStruct:
    plan: StructPlan
    py_name: str
    py_qualname: str
    py_module: str
    fields: tuple[PyField, ...]
    enums: tuple[PyEnum, ...]

    @property
    def c_type(self) -> str:
        return self.plan.c_type

    @property
    def model(self) -> object:
        return self.plan.model

    @property
    def init(self) -> CFunction:
        return self.plan.init

    @property
    def mirror(self) -> str:
        return f"_{self.plan.c_type}"

    @property
    def py_type(self) -> str:
        """The dataclass, as the generated header modules name it."""
        return f"_types.{self.plan.c_type}"


@dataclass(frozen=True, slots=True)
class PyHandle:
    """The opaque C handle of a wrapped class, in ``_types.py``."""

    plan: ClassPlan

    @property
    def c_type(self) -> str:
        return self.plan.c_type

    @property
    def model(self) -> object:
        return self.plan.model

    @property
    def struct_name(self) -> str:
        return f"_{self.plan.c_type}"

    @property
    def pointer_name(self) -> str:
        # '__' separates the role from the C name: C names never contain it, so
        # the class X_p cannot take the pointer type of X.
        return f"_{self.plan.c_type}__p"


# Classes and functions ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PyView:
    """How a class object gives its handle as one of its ancestors."""

    ptr: str
    mptr: str
    upcast: CFunction


@dataclass(frozen=True, slots=True)
class PyClass:
    plan: ClassPlan
    py_name: str
    py_bases: tuple[str, ...]
    base_modules: tuple[str, ...]
    constructors: tuple[PyCallable, ...]
    members: tuple[PyCallable, ...]
    enums: tuple[PyEnum, ...]
    views: tuple[PyView, ...]

    @property
    def c_type(self) -> str:
        return self.plan.c_type

    @property
    def model(self) -> object:
        return self.plan.model

    @property
    def destroy(self) -> CFunction:
        return self.plan.destroy

    @property
    def is_base(self) -> bool:
        return self.plan.is_base

    @property
    def self_view(self) -> str:
        """The name of the method that gives its handle as its own class."""
        return f"_ptr_{self.plan.c_type}"

    @property
    def py_pointer(self) -> str:
        """The ctypes pointer type, as the generated header modules name it."""
        return f"_types._{self.plan.c_type}__p"

    @property
    def single_constructor(self) -> bool:
        return len(self.constructors) == 1


@dataclass(frozen=True, slots=True)
class PyFunction:
    plan: FunctionPlan
    py: PyCallable


PyItem = PyEnum | PyStruct | PyClass | PyFunction


@dataclass(frozen=True, slots=True)
class PyHeader:
    plan: HeaderPlan
    items: tuple[PyItem, ...]
    types: tuple[PyHandle | PyEnum, ...]
    """Handles and enums, in source order; nested enums after their class or struct."""

    @property
    def stem(self) -> str:
        return self.plan.stem

    @property
    def header(self) -> object:
        return self.plan.header

    @property
    def c_functions(self) -> tuple[CFunction, ...]:
        return self.plan.c_functions

    @property
    def struct_inits(self) -> tuple[CFunction, ...]:
        return self.plan.struct_inits

    @property
    def py_exports(self) -> tuple[str, ...]:
        return tuple(
            item.py.name if isinstance(item, PyFunction) else item.py_name for item in self.items
        )

    @property
    def base_modules(self) -> tuple[str, ...]:
        """The other header modules that the classes of this one derive from."""
        classes = [item for item in self.items if isinstance(item, PyClass)]
        return tuple(dict.fromkeys(stem for cls in classes for stem in cls.base_modules))

    @property
    def base_imports(self) -> tuple[str, ...]:
        """The imports of those, at module level (a base class is needed there)."""
        return tuple(
            f"from . import {stem} as {naming.module_alias(stem)}" for stem in self.base_modules
        )


# Callback thunks ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PyThunk:
    """The Python function that C++ calls for the callbacks of one C signature."""

    name: str
    ctypes_args: tuple[str, ...]
    """The ctypes types of the C parameters, user_data first."""

    params: tuple[str, ...]
    arguments: tuple[str, ...]
    """The Python values of the arguments of ``_function``."""

    check: tuple[str, ...] | None
    """The check of the result ``_value``, as the head and the arguments of a
    call, or None for ``void``."""

    @property
    def body(self) -> str:
        """The statements that call ``_function`` and write the checked result,
        indented for the thunk's ``try``."""
        if self.check is None:
            return python_call("_function", self.arguments, 8)
        call = python_call("_value = _function", self.arguments, 8)
        head, *arguments = self.check
        return f"{call}\n{python_call(f'_out_result[0] = {head}', arguments, 8)}"

    @property
    def type_name(self) -> str:
        return f"{self.name}__type"


def _thunk_argument(part: CallbackPart, local: str) -> tuple[list[str], list[str], str]:
    """(the ctypes types, the Python parameters, the Python value) of an argument."""
    type_ = part.type
    if isinstance(type_, StringType):
        if type_.kind is StringKind.C_STRING:
            return ["ctypes.c_void_p"], [local], f"_runtime.callback_c_string({local})"
        return (
            ["ctypes.c_void_p", "ctypes.c_size_t"],
            [local, f"{local}_size"],
            f"_runtime.callback_string({local}, {local}_size)",
        )
    if isinstance(type_, EnumRef):
        assert part.enum is not None
        return (
            [enum_ctypes(part.enum)],
            [local],
            f"_runtime.enum_result({part.enum.c_type}, {local})",
        )
    if isinstance(type_, StructRef):
        assert part.struct is not None
        mirror = f"_{part.struct.c_type}"
        return [f"ctypes.POINTER({mirror})"], [local], f"{mirror}__from_c({local}.contents)"
    assert isinstance(type_, ScalarType)
    return [f"ctypes.{type_.ctypes_name}"], [local], local


def _thunk_result(part: CallbackPart) -> tuple[str, tuple[str, ...]] | None:
    """(the ctypes type, the check of ``_value``) of the result, None for ``void``."""
    type_ = part.type
    what = 'what="the result of callback"'
    if isinstance(type_, EnumRef):
        assert part.enum is not None
        enum = part.enum
        valid = enum.model.valid_range
        check = (
            "_runtime.to_enum",
            "_value",
            enum.c_type,
            enum_ctypes(enum),
            "_name",
            "None" if valid is None else f"({valid[0]}, {valid[1]})",
            what,
        )
        return enum_ctypes(enum), check
    assert isinstance(type_, ScalarType)
    if type_.is_void:
        return None
    ctypes_type = f"ctypes.{type_.ctypes_name}"
    if type_.category is TypeCategory.BOOL:
        return ctypes_type, ("_runtime.to_bool", "_value", "_name", what)
    if type_.category is TypeCategory.FLOATING:
        return ctypes_type, ("_runtime.to_float", "_value", ctypes_type, "_name", what)
    return ctypes_type, ("_runtime.to_int", "_value", ctypes_type, "_name", what)


def thunk(callback: CallbackSpec) -> PyThunk:
    ctypes_args = ["ctypes.c_void_p"]
    params = ["_user_data"]
    arguments: list[str] = []
    for position, part in enumerate(callback.arguments, start=1):
        types, names, value = _thunk_argument(part, f"_arg{position}")
        ctypes_args += types
        params += names
        arguments.append(value)
    check: tuple[str, ...] | None = None
    result = _thunk_result(callback.result)
    if result is not None:
        ctypes_type, check = result
        ctypes_args.append(f"ctypes.POINTER({ctypes_type})")
        params.append("_out_result")
    return PyThunk(
        name=callback_thunk_name(callback),
        ctypes_args=tuple(ctypes_args),
        params=tuple(params),
        arguments=tuple(arguments),
        check=check,
    )


# The module --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PyModule:
    plan: ModulePlan
    headers: tuple[PyHeader, ...]
    structs: tuple[PyStruct, ...]
    """Every value struct, each after the structs it contains."""

    thunks: tuple[PyThunk, ...]
    """The callback thunks of every header, by name."""

    @property
    def name(self) -> str:
        return self.plan.name


def lower(plan: ModulePlan) -> PyModule:
    """Give a module plan its Python names and shapes.

    Raises:
        GenerationError: two declarations get the same Python name.
    """
    return _Lowering(plan).lower()


class _Lowering:
    def __init__(self, plan: ModulePlan) -> None:
        self.plan = plan
        self.errors: list[Diagnostic] = []
        self.enums = {enum.model.usr: enum for header in plan.headers for enum in header.enums}
        self.structs = {
            struct.model.usr: struct for header in plan.headers for struct in header.structs
        }

    def lower(self) -> PyModule:
        headers = tuple(self._header(header) for header in self.plan.headers)
        by_usr = {
            struct.plan.model.usr: struct
            for header in headers
            for struct in header.items
            if isinstance(struct, PyStruct)
        }
        structs = tuple(by_usr[plan.model.usr] for plan in self.plan.structs)
        thunks: dict[str, PyThunk] = {}
        for callable_plan in self._callables():
            for spec in callable_plan.params:
                if spec.callback is not None:
                    item = thunk(spec.callback)
                    known = thunks.setdefault(item.name, item)
                    assert known == item, (known, item)
        if self.errors:
            raise GenerationError(self.errors)
        return PyModule(self.plan, headers, structs, tuple(thunks[name] for name in sorted(thunks)))

    def _callables(self) -> Iterator[CallablePlan]:
        for header in self.plan.headers:
            for item in header.items:
                if isinstance(item, ClassPlan):
                    yield from item.constructors
                    yield from item.members
                elif isinstance(item, FunctionPlan):
                    yield item.callable

    # Headers ---------------------------------------------------------------------

    def _header(self, header: HeaderPlan) -> PyHeader:
        items: list[PyItem] = []
        seen: dict[str, Location] = {}
        for plan in header.items:
            lowered: PyItem
            if isinstance(plan, ClassPlan):
                lowered = self._class(plan, header)
                name = lowered.py_name
            elif isinstance(plan, StructPlan):
                lowered = self._struct(plan, header)
                name = lowered.py_name
            elif isinstance(plan, EnumPlan):
                name = naming.module_level_name(naming.python_name(plan.model.name))
                lowered = self._enum(plan, header, name, name)
            else:
                lowered = self._function(plan, header)
                name = lowered.py.name
            location = plan.model.location
            if name in seen:
                self.errors.append(
                    Diagnostic(
                        f"two declarations get the Python name '{name}' "
                        f"(the other one is declared at {seen[name]})",
                        location,
                    )
                )
            seen[name] = location
            items.append(lowered)
        types: list[PyHandle | PyEnum] = []
        for item in items:
            if isinstance(item, PyClass):
                types += [PyHandle(item.plan), *item.enums]
            elif isinstance(item, PyStruct):
                types += item.enums
            elif isinstance(item, PyEnum):
                types.append(item)
        return PyHeader(header, tuple(items), tuple(types))

    # Enums -----------------------------------------------------------------------

    def _enum(self, plan: EnumPlan, header: HeaderPlan, name: str, qualname: str) -> PyEnum:
        model = plan.model
        what = f"enum '{model.qualified_name}'"
        names: dict[str, str] = {}
        enumerators: list[PyEnumerator] = []
        for enumerator in plan.enumerators:
            py_name = naming.enum_member_name(enumerator.model_name)
            if py_name in names:
                self.errors.append(
                    Diagnostic(
                        f"enumerators '{names[py_name]}' and '{enumerator.model_name}' of {what} "
                        f"get the same Python name '{py_name}'",
                        model.location,
                    )
                )
            names[py_name] = enumerator.model_name
            enumerators.append(PyEnumerator(py_name, enumerator.value))
        return PyEnum(
            plan,
            name,
            qualname,
            f"{self.plan.name}.{header.stem}",
            tuple(enumerators),
        )

    # Structs ---------------------------------------------------------------------

    def _struct(self, plan: StructPlan, header: HeaderPlan) -> PyStruct:
        model = plan.model
        name = naming.module_level_name(naming.python_name(model.name))
        names = [field.name for field in plan.fields]
        ctypes_names = naming.ctypes_field_names(names)
        fields = tuple(
            self._field(field, ctypes_name)
            for field, ctypes_name in zip(plan.fields, ctypes_names, strict=True)
        )
        taken = {field.name: field.model.location for field in plan.fields}
        enums: list[PyEnum] = []
        for enum in plan.enums:
            py_name = naming.member_name(enum.model.name)
            if py_name in taken:
                self.errors.append(
                    Diagnostic(
                        f"enum '{enum.model.qualified_name}' and a member of "
                        f"'{model.qualified_name}' get the Python name '{py_name}'",
                        enum.model.location,
                    )
                )
            enums.append(self._enum(enum, header, py_name, f"{name}.{py_name}"))
        return PyStruct(plan, name, name, f"{self.plan.name}.{header.stem}", fields, tuple(enums))

    def _field(self, field: FieldPlan, ctypes_name: str) -> PyField:
        """How a member crosses in Python: ctypes type, checks and reading."""
        model = field.model
        type_ = model.type
        name = field.name
        length = model.length
        value = f"value.{name}"
        path = f'name + ".{name}"'
        if isinstance(type_, EnumRef):
            enum = self.enums[type_.usr]
            c_type, ctypes_element, annotation = enum.c_type, enum_ctypes(enum), enum.c_type
            valid = enum.model.valid_range
            extra = f", ({valid[0]}, {valid[1]})" if valid is not None else ""

            def check(item: str, where: str) -> str:
                return (
                    f"_runtime.to_enum({item}, {enum.c_type}, {enum_ctypes(enum)}, {where}{extra})"
                )

            def read(item: str) -> str:
                return f"_runtime.enum_result({enum.c_type}, {item})"

        elif isinstance(type_, StructRef):
            nested = self.structs[type_.usr].c_type
            c_type, ctypes_element, annotation = nested, f"_{nested}", nested

            def check(item: str, where: str) -> str:  # unused: structs are filled in place
                raise AssertionError

            def read(item: str) -> str:
                return f"_{nested}__from_c({item})"

        else:
            assert isinstance(type_, ScalarType)
            c_type, ctypes_element = type_.c_name, f"ctypes.{type_.ctypes_name}"
            annotation = {
                TypeCategory.BOOL: "bool",
                TypeCategory.FLOATING: "float",
            }.get(type_.category, "int")
            scalar = type_

            def check(item: str, where: str) -> str:
                return scalar_check(item, scalar, where)

            def read(item: str) -> str:
                return item

        if length is None:
            if isinstance(type_, StructRef):
                fill = (f"_{c_type}__fill(raw.{ctypes_name}, {value}, {path})",)
            else:
                fill = (f"raw.{ctypes_name} = {check(value, path)}",)
            return PyField(
                name=name,
                ctypes_name=ctypes_name,
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
        return PyField(
            name=name,
            ctypes_name=ctypes_name,
            ctypes_type=f"{ctypes_element} * {length}",
            py_annotation="list",
            fill=(f"for i, item in enumerate({items}):", f"    {body}"),
            read=reading,
        )

    # Functions -------------------------------------------------------------------

    def _function(self, plan: FunctionPlan, header: HeaderPlan) -> PyFunction:
        callable_plan = plan.callable
        name = naming.module_level_name(
            naming.python_name(callable_plan.name + callable_plan.suffix)
        )
        return PyFunction(plan, self._callable(name, callable_plan, header, leading=()))

    def _callable(
        self,
        name: str,
        plan: CallablePlan,
        header: HeaderPlan,
        *,
        leading: Sequence[str],
    ) -> PyCallable:
        kind = {
            CallableKind.FUNCTION: "static",
            CallableKind.STATIC: "static",
            CallableKind.METHOD: "method",
            CallableKind.CONSTRUCTOR: "constructor",
        }[plan.kind]
        result = build_callable(
            name,
            plan.function,
            plan.params,
            plan.result,
            leading=leading,
            kind=kind,
            module=self.plan.name,
            header_stem=header.stem,
        )
        if plan.kind is CallableKind.STATIC:
            result = dataclasses.replace(result, is_static=True)
        return result

    # Classes ---------------------------------------------------------------------

    def _class(self, plan: ClassPlan, header: HeaderPlan) -> PyClass:
        model = plan.model
        py_name = naming.module_level_name(naming.python_name(model.name))
        cpp_name = "::" + model.qualified_name
        constructors: list[PyCallable] = []
        for constructor in plan.constructors:
            name = "create" + constructor.suffix if len(plan.constructors) > 1 else "__init__"
            constructors.append(self._callable(name, constructor, header, leading=("_handle",)))
        factory_names = {item.name for item in constructors}
        taken: dict[str, Location] = {}
        enums: list[PyEnum] = []
        for enum in plan.enums:
            enum_name = naming.member_name(enum.model.name, reserved=factory_names)
            taken[enum_name] = enum.model.location
            enums.append(self._enum(enum, header, enum_name, f"{py_name}.{enum_name}"))
        members: list[PyCallable] = []
        for member in plan.members:
            name = naming.member_name(member.name + member.suffix, reserved=factory_names)
            if name in taken:
                self.errors.append(
                    Diagnostic(
                        f"two members of '{cpp_name}' get the Python name '{name}' "
                        f"(the other one is declared at {taken[name]})",
                        member.location,
                    )
                )
            taken[name] = member.location
            leading: tuple[str, ...]
            if member.kind is CallableKind.METHOD:
                leading = (handle(plan.c_type, plan.is_base, "self", writable=not member.is_const),)
            else:
                leading = ()
            members.append(self._callable(name, member, header, leading=leading))
        bases: list[str] = []
        modules: list[str] = []
        for base in plan.bases:
            base_name = naming.module_level_name(base.model.name)
            if base.header.stem == header.stem:
                bases.append(base_name)
            else:
                bases.append(f"{naming.module_alias(base.header.stem)}.{base_name}")
                modules.append(base.header.stem)
        return PyClass(
            plan,
            py_name,
            tuple(bases),
            tuple(dict.fromkeys(modules)),
            tuple(constructors),
            tuple(members),
            tuple(enums),
            tuple(_view(view) for view in plan.views),
        )


def _view(view: ClassView) -> PyView:
    return PyView(f"_ptr_{view.c_type}", f"_mptr_{view.c_type}", view.upcast)


__all__ = ["ContainerType", "PyModule", "lower"]
