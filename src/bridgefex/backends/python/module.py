# SPDX-License-Identifier: Apache-2.0
"""The Python view of a module plan: the names that Python gives to its enums,
structs, classes and functions, and what the templates render."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from ...errors import Diagnostic, GenerationError, Location
from ...model import (
    Access,
    ContainerType,
    EnumRef,
    Method,
    ScalarType,
    StructRef,
    TypeCategory,
)
from ...plan import (
    CallableKind,
    CallablePlan,
    CFunction,
    ClassPlan,
    ClassView,
    EnumeratorPlan,
    EnumPlan,
    FieldPlan,
    FunctionPlan,
    HeaderPlan,
    ModulePlan,
    StructPlan,
)
from .directors import PyAlias, PyDirector, PyDirectorConstructor, PySlot
from .ffi import PyBinding, binding, ctypes_scalar
from .lower import (
    build_callable,
    class_name,
    enum_ctypes,
    enum_type,
    handle,
    scalar_check,
)
from .model import PyCallable
from .naming import (
    ctypes_field_names,
    enum_member_name,
    member_name,
    module_alias,
    module_level_name,
    python_name,
)
from .thunks import PyThunk, director_thunk, thunk


class PlanView:
    """A Python view of an object of the plan: what the view does not say is the plan's,
    so the C layer and the Python layer of an item are read from the same place."""

    plan: Any

    def __getattr__(self, name: str) -> Any:
        if name == "plan":  # not set yet
            raise AttributeError(name)
        return getattr(self.plan, name)


# Enums and structs -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PyEnumerator(PlanView):
    plan: EnumeratorPlan
    py_name: str
    value: int


@dataclass(frozen=True, slots=True)
class PyEnum(PlanView):
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
class PyStruct(PlanView):
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
    def init_binding(self) -> PyBinding:
        """The signature of its init function, declared in ``_types.py``."""
        return binding(self.plan.init, local=True)

    @property
    def mirror(self) -> str:
        return f"_{self.plan.c_type}"

    @property
    def py_type(self) -> str:
        """The dataclass, as the generated header modules name it."""
        return f"_types.{self.plan.c_type}"


@dataclass(frozen=True, slots=True)
class PyHandle(PlanView):
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

    c_type: str
    ptr: str
    mptr: str
    upcast: CFunction


@dataclass(frozen=True, slots=True)
class PyClass(PlanView):
    plan: ClassPlan
    py_name: str
    py_bases: tuple[str, ...]
    base_modules: tuple[str, ...]
    constructors: tuple[PyCallable, ...]
    members: tuple[PyCallable, ...]
    enums: tuple[PyEnum, ...]
    views: tuple[PyView, ...]
    director: PyDirector | None = None
    virtual_names: tuple[str, ...] = ()
    """In a module that has directors: the names of the virtual methods the class declares
    (those of their wrappers and the C++ ones), so that a subclass cannot define one that
    C++ would never call."""

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

    @property
    def c_functions(self) -> tuple[CFunction, ...]:
        return self.plan.c_functions


@dataclass(frozen=True, slots=True)
class PyFunction(PlanView):
    plan: FunctionPlan
    py: PyCallable

    @property
    def model(self) -> object:
        return self.plan.model

    @property
    def c_function(self) -> CFunction:
        return self.plan.c_function


PyItem = PyEnum | PyStruct | PyClass | PyFunction


@dataclass(frozen=True, slots=True)
class PyHeader(PlanView):
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
    def bindings(self) -> tuple[PyBinding, ...]:
        """The signatures of the C functions of this header (the init functions of
        the value structs are declared in ``_types.py``)."""
        inits = set(self.plan.struct_inits)
        return tuple(binding(f) for f in self.plan.all_c_functions if f not in inits)

    @property
    def classes(self) -> tuple[PyClass, ...]:
        return tuple(item for item in self.items if isinstance(item, PyClass))

    @property
    def enums(self) -> tuple[PyEnum, ...]:
        """The enums this header defines, nested ones included."""
        return tuple(item for item in self.types if isinstance(item, PyEnum))

    @property
    def foreign_enums(self) -> tuple[EnumPlan, ...]:
        return self.plan.foreign_enums

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
        return tuple(f"from . import {stem} as {module_alias(stem)}" for stem in self.base_modules)


# The module --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PyModule(PlanView):
    plan: ModulePlan
    headers: tuple[PyHeader, ...]
    structs: tuple[PyStruct, ...]
    """Every value struct, each after the structs it contains."""

    thunks: tuple[PyThunk, ...]
    """The callback thunks of every header, by name."""

    directors: tuple[PyDirector, ...] = ()
    """The directors of every header."""

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
        self.class_plans: dict[str, ClassPlan] = {
            cls.model.usr: cls
            for header in plan.headers
            for cls in header.items
            if isinstance(cls, ClassPlan)
        }
        # What a Python class calls the public method that a director overrides.
        self.method_names: dict[object, str] = {
            member.source: member_name(
                member.name + member.suffix, reserved=_constructor_names(cls)
            )
            for header in plan.headers
            for cls in header.items
            if isinstance(cls, ClassPlan)
            for member in cls.members
            if member.kind is CallableKind.METHOD and member.source is not None
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
        directors = tuple(
            cls.director for header in headers for cls in header.classes if cls.director is not None
        )
        return PyModule(
            self.plan, headers, structs, tuple(thunks[name] for name in sorted(thunks)), directors
        )

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
                name = module_level_name(python_name(plan.model.name))
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
            py_name = enum_member_name(enumerator.model_name)
            if py_name in names:
                self.errors.append(
                    Diagnostic(
                        f"enumerators '{names[py_name]}' and '{enumerator.model_name}' of {what} "
                        f"get the same Python name '{py_name}'",
                        model.location,
                    )
                )
            names[py_name] = enumerator.model_name
            enumerators.append(PyEnumerator(enumerator, py_name, enumerator.value))
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
        name = module_level_name(python_name(model.name))
        names = [field.name for field in plan.fields]
        ctypes_names = ctypes_field_names(names)
        fields = tuple(
            self._field(field, ctypes_name)
            for field, ctypes_name in zip(plan.fields, ctypes_names, strict=True)
        )
        taken = {field.name: field.model.location for field in plan.fields}
        enums: list[PyEnum] = []
        for enum in plan.enums:
            py_name = member_name(enum.model.name)
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
            c_type, ctypes_element = type_.c_name, f"ctypes.{ctypes_scalar(type_.c_name)}"
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
        name = module_level_name(python_name(callable_plan.name + callable_plan.suffix))
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
            CallableKind.DIRECTOR_CONSTRUCTOR: "constructor",
            CallableKind.DIRECTOR_BASE: "method",
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
        py_name = class_name(model.name)
        cpp_name = "::" + model.qualified_name
        constructors: list[PyCallable] = []
        for constructor in plan.constructors:
            name = "create" + constructor.suffix if len(plan.constructors) > 1 else "__init__"
            constructors.append(self._callable(name, constructor, header, leading=("_handle",)))
        factory_names = _constructor_names(plan)
        taken: dict[str, Location] = {}
        enums: list[PyEnum] = []
        for enum in plan.enums:
            enum_name = member_name(enum.model.name, reserved=factory_names)
            taken[enum_name] = enum.model.location
            enums.append(self._enum(enum, header, enum_name, f"{py_name}.{enum_name}"))
        names = [
            member_name(member.name + member.suffix, reserved=factory_names)
            for member in plan.members
        ]
        methods = frozenset(
            member.source
            for member in plan.members
            if member.kind is CallableKind.METHOD and member.source is not None
        )
        director = self._director(plan, header, factory_names, methods)
        own_slots: dict[object, PySlot] = (
            {slot.entry.method: slot for slot in director.slots if slot.wrapper == "own"}
            if director is not None
            else {}
        )
        members: list[PyCallable] = []
        for member, name in zip(plan.members, names, strict=True):
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
            lowered = self._callable(name, member, header, leading=leading)
            slot = own_slots.get(member.source)
            if slot is not None:
                lowered = dataclasses.replace(
                    lowered, prelude=_director_prelude(slot, py_name, model.qualified_name)
                )
            members.append(lowered)
        if director is not None:
            self._check_director_names(plan, director, taken)
        bases: list[str] = []
        modules: list[str] = []
        for base in plan.bases:
            base_name = class_name(base.model.name)
            if base.header.stem == header.stem:
                bases.append(base_name)
            else:
                bases.append(f"{module_alias(base.header.stem)}.{base_name}")
                modules.append(base.header.stem)
        virtual: dict[str, None] = {}
        if self.plan.has_directors:
            for member, name in zip(plan.members, names, strict=True):
                source = member.source
                if isinstance(source, Method) and source.is_virtual:
                    virtual[name] = None
                    virtual[member_name(source.name, reserved=factory_names)] = None
            for hook in model.hooks:
                virtual[member_name(hook.name, reserved=factory_names)] = None
            if director is not None:
                virtual.update(dict.fromkeys(slot.name for slot in director.slots))
                virtual.update(dict.fromkeys(alias.name for alias in director.aliases))
        return PyClass(
            plan,
            py_name,
            tuple(bases),
            tuple(dict.fromkeys(modules)),
            tuple(constructors),
            tuple(members),
            tuple(enums),
            tuple(_view(view) for view in plan.views),
            director,
            tuple(sorted(virtual)),
        )

    # Directors -------------------------------------------------------------------

    def _director(
        self,
        plan: ClassPlan,
        header: HeaderPlan,
        factory_names: frozenset[str],
        methods: frozenset[object],
    ) -> PyDirector | None:
        """The methods that a Python subclass of the class overrides, and what creates its
        objects as directors."""
        director = plan.director
        if director is None:
            return None
        slots: list[PySlot] = []
        seen: dict[str, Location] = {}
        ctypes_names = ctypes_field_names(
            ["user_data", "release", *(entry.field for entry in director.entries)]
        )[2:]
        for entry, ctypes_name in zip(director.entries, ctypes_names, strict=True):
            public = entry.access is Access.PUBLIC
            # The name of the method that a user calls is the one that overrides it.
            name = self.method_names.get(entry.method) if public else None
            if name is None:
                name = member_name(entry.field, reserved=factory_names)
            if name in seen:
                self.errors.append(
                    Diagnostic(
                        f"two virtual methods of '{director.class_name}' that a subclass may "
                        f"override get the Python name '{name}' (the other one is declared "
                        f"at {seen[name]})",
                        entry.location,
                    )
                )
            seen[name] = entry.location
            base = (
                self._callable(f"_base_{name}", entry.base, header, leading=("self._dptr()",))
                if entry.base is not None
                else None
            )
            own = public and entry.method in methods
            wrapper: str | None
            if own:
                wrapper = "own"
            elif entry.is_pure:
                wrapper = None
            else:
                wrapper = "inherited" if public else "protected"
            slots.append(
                PySlot(
                    entry,
                    name,
                    ctypes_name,
                    director_thunk(director.c_type, entry.field, name, entry.callback),
                    base,
                    wrapper,
                )
            )
        # What creates a director: the ordinary constructors, else the director's own.
        plain = {
            constructor.source: (
                "create" + constructor.suffix if len(plan.constructors) > 1 else "__init__"
            )
            for constructor in plan.constructors
            if constructor.source is not None
        }
        creations: list[PyDirectorConstructor] = []
        for constructor in director.constructors:
            own_name = (
                "create" + constructor.suffix if len(director.constructors) > 1 else "__init__"
            )
            lowered = self._callable(
                own_name,
                constructor,
                header,
                leading=("_handle", "_director", "_record.register(_vtable)"),
            )
            if plan.constructors and constructor.source in plain:
                name = plain[constructor.source]
                creations.append(PyDirectorConstructor(name, lowered, name))
            else:
                # A constructor that only a derived class can call (a protected one): of a
                # class that has ordinary ones too, a class method of the subclasses.
                creations.append(PyDirectorConstructor(own_name, lowered, None))
        return PyDirector(
            director,
            tuple(slots),
            tuple(creations),
            bool(plan.constructors),
            self._aliases(plan, slots),
        )

    def _aliases(self, plan: ClassPlan, slots: Sequence[PySlot]) -> tuple[PyAlias, ...]:
        """The names that the bases of the class give to its virtual methods when they are
        not the class's own: a call of the base's name has to reach the director of this class,
        not the base function of the director of the base."""
        by_key = {slot.entry.key: slot for slot in slots if slot.base is not None}
        own = {slot.name for slot in slots}
        aliases: dict[str, PyAlias] = {}

        def visit(cls: ClassPlan) -> None:
            for info in cls.bases:
                base = self.class_plans[info.model.usr]
                visit(base)
                other = base.director
                if other is None:
                    continue
                factory = _constructor_names(base)
                for entry in other.entries:
                    target = by_key.get(entry.key)
                    if target is None or entry.base is None:
                        continue
                    public = entry.access is Access.PUBLIC
                    name = self.method_names.get(entry.method) if public else None
                    if name is None:
                        name = member_name(entry.field, reserved=factory)
                    if name not in own and name not in aliases:
                        aliases[name] = PyAlias(name, target)

        visit(plan)
        return tuple(aliases.values())

    def _check_director_names(
        self, plan: ClassPlan, director: PyDirector, taken: dict[str, Location]
    ) -> None:
        """The methods that a director adds to the class must not take the name of another."""
        cpp_name = "::" + plan.model.qualified_name
        for slot in director.slots:
            added = [slot.private_name] if slot.base is not None else []
            if slot.wrapper in ("inherited", "protected"):
                added.append(slot.name)
            for name in added:
                if name in taken:
                    self.errors.append(
                        Diagnostic(
                            f"a member of '{cpp_name}' and the virtual method "
                            f"'{slot.entry.description}', which a subclass may override, get the "
                            f"Python name '{name}' (the other one is declared at {taken[name]})",
                            slot.entry.location,
                        )
                    )
        for constructor in director.constructors:
            if constructor.name != "__init__" and constructor.private_name in taken:
                self.errors.append(
                    Diagnostic(
                        f"a member of '{cpp_name}' gets the Python name "
                        f"'{constructor.private_name}', which the class uses for its director",
                        plan.model.location,
                    )
                )


def _constructor_names(plan: ClassPlan) -> frozenset[str]:
    """What the Python class calls its constructors: ``__init__`` or ``create<suffix>``,
    those of its director if it has no ordinary ones."""
    constructors = plan.constructors or (plan.director.constructors if plan.director else ())
    names = {
        "create" + constructor.suffix if len(constructors) > 1 else "__init__"
        for constructor in constructors
    }
    if plan.constructors and plan.director is not None:
        # The constructors that only a subclass can call are class methods too.
        ordinary = {constructor.source for constructor in plan.constructors}
        names |= {
            "create" + constructor.suffix
            for constructor in plan.director.constructors
            if constructor.source not in ordinary
        }
    return frozenset(names)


def _director_prelude(slot: PySlot, py_name: str, cpp_name: str) -> tuple[str, ...]:
    """What a method that a director overrides does first, on an object that is a director:
    its call goes to the implementation of the class, never through the vtable (which would
    call the override that called it)."""
    if slot.base is None:
        return (
            "if self._director is not None:",
            f'    raise _builtins.NotImplementedError("{py_name}.{slot.name}() is a pure virtual '
            f'method of {cpp_name}: there is no implementation to call")',
        )
    return (
        "if self._director is not None:",
        f"    return self.{slot.private_name}({slot.parameters})",
    )


def _view(view: ClassView) -> PyView:
    return PyView(view.c_type, f"_ptr_{view.c_type}", f"_mptr_{view.c_type}", view.upcast)


__all__ = ["ContainerType", "PyModule", "lower"]
