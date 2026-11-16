# SPDX-License-Identifier: Apache-2.0
"""Lowering a callable of the plan to a Java method: what the wrapper converts before the call,
the call of the native function, and what it converts after it."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ...model import (
    CallbackType,
    ClassForm,
    ClassRef,
    ContainerType,
    EnumRef,
    ScalarType,
    StringType,
    StructRef,
)
from ...plan import (
    CallableKind,
    CallablePlan,
    ClassInfo,
    ClassPlan,
    EnumPlan,
    ModulePlan,
    ParamSpec,
    ResultSpec,
    StructPlan,
)
from . import containers
from .flavor import FFM, Flavor
from .mapping import STRING, Unsupported, scalar
from .naming import LOCAL_PREFIX, camel, identifier, parameter
from .source import Source

if TYPE_CHECKING:
    from .callbacks import Thunks

L = LOCAL_PREFIX
R = "BridgefexRuntime"


@dataclass(frozen=True, slots=True)
class Names:
    """The Java names of the types of the module, by the C type: qualified, so that the code
    of a header does not need an import (which a class of the header could hide)."""

    root: str
    """The package of the module."""

    enums: Mapping[str, str] = field(default_factory=dict)
    structs: Mapping[str, str] = field(default_factory=dict)
    classes: Mapping[str, str] = field(default_factory=dict)
    enum_plans: Mapping[str, EnumPlan] = field(default_factory=dict)
    """The enums by the USR of the C++ type (the type of a member of a struct)."""

    struct_plans: Mapping[str, StructPlan] = field(default_factory=dict)
    destroyers: Mapping[str, str] = field(default_factory=dict)
    """The C function that destroys an object of each class, by the C type."""

    flavor: Flavor = FFM
    """How the bindings call the C layer."""

    thunks: Thunks | None = None
    """The functions that C++ calls for the callbacks of the module (None: it has none)."""

    directors: Mapping[str, Any] = field(default_factory=dict)
    """The classes that can be derived from, by C type (see ``directors.py``)."""

    guarded: frozenset[str] = frozenset()
    """The classes below one that can be derived from, that cannot be derived from themselves."""

    @property
    def has_callbacks(self) -> bool:
        """The module needs the runtime of the callbacks: C++ calls into Java (a director of a
        class none of whose virtual methods can be overridden still has its object and its
        release)."""
        return self.has_directors or (self.thunks is not None and bool(self.thunks.items))

    @property
    def has_directors(self) -> bool:
        return bool(self.directors)

    @property
    def imports(self) -> tuple[str, ...]:
        """The classes of the runtime that the generated code of a header imports besides the
        common ones."""
        extra = ("BridgefexCallbacks",) if self.has_callbacks else ()
        if self.has_directors:
            extra = (*extra, "BridgefexDirectors")
        return (*self.flavor.root_types, *extra)

    def cls(self, info: ClassInfo) -> str:
        return self.classes[info.c_type]

    def destroyer(self, c_type: str) -> str:
        """The handle of an object of the class: it owns it, and destroys it with the C API."""
        return f"new BridgefexHandle(BridgefexNative::{self.destroyers[c_type]}, true)"

    def enum(self, enum: EnumPlan) -> str:
        return self.enums[enum.c_type]

    def struct(self, struct: StructPlan) -> str:
        return self.structs[struct.c_type]


@dataclass(slots=True)
class Lowering:
    """What a callable becomes: the parameters of the Java method, and its body."""

    parameters: list[str] = field(default_factory=list)
    returns: str = "void"
    body: list[str] = field(default_factory=list)
    remarks: list[str] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)
    """The annotations of the method besides ``@BridgefexFunction``."""


def method_name(name: str, reserved: frozenset[str]) -> str:
    """The Java name of a method: its C++ name and overload suffix in camel case."""
    word = camel(name)
    while identifier(word) != word or word in reserved:
        word = identifier(word) if identifier(word) != word else f"{word}_"
    return word


def lower_callable(
    plan: CallablePlan,
    names: Names,
    module: ModulePlan,
    *,
    declaring: ClassPlan | None = None,
    factory: str | None = None,
    prelude: Sequence[str] = (),
) -> Lowering:
    """The Java method (or constructor) for ``plan``. ``declaring`` is the class that declares a
    method. ``factory`` (the Java class) lowers a constructor to a static method that returns the
    object. ``prelude``: statements that follow the scope of the call (the vtable of a director)."""
    try:
        return _Lowerer(
            plan, names, module, declaring, factory=factory, prelude=tuple(prelude)
        ).run()
    except Unsupported as error:
        if error.location is not None:
            raise
        raise Unsupported(f"'{plan.function.description}': {error}", plan.location) from error


class _Lowerer:
    def __init__(
        self,
        plan: CallablePlan,
        names: Names,
        module: ModulePlan,
        declaring: ClassPlan | None,
        *,
        factory: str | None,
        prelude: tuple[str, ...] = (),
    ) -> None:
        self.plan = plan
        self.names = names
        self.module = module
        self.declaring = declaring
        self.factory = factory
        self.prelude = prelude
        self.lowering = Lowering()
        self.pre: list[str] = []
        """What is checked before anything is held."""

        self.resources: list[str] = []
        """The ``try`` resources: the memory of the call, the holds on the objects."""

        self.statements: list[str] = []
        self.arguments: dict[str, str] = {}
        self.arena = False
        self.callbacks = False
        """True if the call gives callbacks."""

        self.cleanup: list[str] = []
        """What frees what C++ allocated for the result."""

        self.after_call: list[str] = []
        """What follows the call, with the status still unchecked."""

        self.transfers: list[tuple[str, str, bool]] = []
        """(the parameter, the hold, may be null) of each object given to C++."""

        self.retained: list[str] = []
        self.kept_texts: list[str] = []
        """The locals with the memory of the texts that C++ keeps pointers to (``retain``)."""

        self.owners: list[str] = []
        self.created: str | None = None
        """The object that a factory returns."""

        self.parameter_names = self._parameter_names()
        """The Java name of each parameter, by position: all different."""

    def _parameter_names(self) -> list[str]:
        """The names of the parameters: two C++ names that give the same Java one (``x`` and
        ``x_``, ``fooBar`` and ``foo_bar``) are told apart by a trailing ``_``."""
        taken = {self.names.root}
        result: list[str] = []
        for spec in self.plan.params:
            name = parameter(spec.name)
            while name in taken:
                name += "_"
            taken.add(name)
            result.append(name)
        return result

    def declare(self, type_: str) -> str:
        """The start of the declaration of a local variable of ``type_``."""
        return self.names.flavor.declare(type_)

    @property
    def segment(self) -> str:
        return self.names.flavor.segment

    def arena_resource(self) -> str:
        """The memory of the call, made when something needs it."""
        if not self.arena:
            self.arena = True
            self.resources.insert(
                0, f"{self.declare(self.names.flavor.arena)} {L}arena = BridgefexRuntime.arena()"
            )
        return f"{L}arena"

    def scope_resource(self) -> str:
        """The call that C++ runs the callbacks of: it raises their exceptions, and drops the
        callbacks if the C function is never called."""
        if not self.callbacks:
            self.callbacks = True
            self.resources.insert(
                0, f"BridgefexCallbacks.Scope {L}scope = BridgefexCallbacks.begin()"
            )
        return f"{L}scope"

    def director_setup(self) -> None:
        """What a module with directors adds to a call: its scope (an override may run), the vtable
        of a director that is made, and the director that a base call runs."""
        plan = self.plan
        if self.names.has_directors:
            # Every call of a module with directors can run an override, whose exception it
            # raises.
            self.scope_resource()
        if plan.kind is CallableKind.DIRECTOR_CONSTRUCTOR:
            self.arena_resource()
        self.statements.extend(self.prelude)
        if plan.kind is CallableKind.DIRECTOR_BASE:
            # The base call of an override: the director, not the object, is what C++ runs.
            mutable = "false" if plan.is_const else "true"
            self.resources.append(
                f"{self.declare('BridgefexLease')} {L}self = holdDirector({mutable})"
            )
            self.arguments["director"] = f"{L}self.pointer()"

    def run(self) -> Lowering:
        plan = self.plan
        self.director_setup()
        if plan.kind is CallableKind.METHOD:
            mutable = "false" if plan.is_const else "true"
            # A class that others derive from is reached through the address of its own part of
            # the object, which is not the address of the object with several bases.
            declaring = self.declaring
            hold = (
                f"hold_{declaring.c_type}"
                if declaring is not None and declaring.is_base
                else "hold"
            )
            self.resources.append(
                f"{self.declare('BridgefexLease')} {L}self = {hold}({mutable}, false)"
            )
            self.arguments["self"] = f"{L}self.pointer()"
        self.same_object_checks()
        for position, spec in enumerate(plan.params, start=1):
            self.param(spec, position)
        after = self.result()
        call_args = ", ".join(self.argument(param.name) for param in plan.function.params)
        call = f"BridgefexNative.{plan.function.name}({call_args})"
        for name, lease, nullable in self.transfers:
            # From here on C++ owns the object, whatever the status.
            self.statements.append(f"{lease}.give();")
            surrender = f"{name}.surrender();"
            self.statements.append(f"if ({name} != null) {surrender}" if nullable else surrender)
        # What C++ may keep is retained before the call: a call that stores a pointer and then
        # fails has kept it all the same. A constructor that fails has kept nothing.
        constructor = plan.kind is CallableKind.CONSTRUCTOR
        if not constructor:
            keeper = "" if plan.kind in (CallableKind.METHOD, CallableKind.DIRECTOR_BASE) else None
            self.statements.extend(self.retains(keeper))
        if not self.callbacks and self.names.has_callbacks:
            # A callback that C++ stored and runs now is not one of the callbacks of this call.
            self.resources.insert(
                0, f"BridgefexCallbacks.Call {L}call = BridgefexCallbacks.enter()"
            )
            # The resource only counts the call: nothing in the body refers to it.
            self.lowering.annotations.append('@SuppressWarnings("try")')
        if self.callbacks:
            self.checked_call(call, after)
        elif self.cleanup:
            # What C++ allocated for the result is freed whatever happens after the call.
            self.statements.append("try")
            self.statements.append("{")
            self.statements.append(f"    BridgefexRuntime.check({call});")
            self.statements.extend(f"    {line}" if line else line for line in after)
            self.statements.append("}")
            self.statements.append("finally")
            self.statements.append("{")
            self.statements.extend(f"    {line}" for line in self.cleanup)
            self.statements.append("}")
        elif self.after_call:
            self.statements.append(f"int {L}status = {call};")
            self.statements.extend(self.after_call)
            self.statements.append(f"BridgefexRuntime.check({L}status);")
            self.statements.extend(after)
        else:
            self.statements.append(f"BridgefexRuntime.check({call});")
            self.statements.extend(after)
        self.lowering.body = self.finish()
        return self.lowering

    def checked_call(self, call: str, after: list[str]) -> None:
        """The call of a function that gives callbacks: from the moment that C++ has it, C++ owns
        and releases them, and an exception of one of them is the one that the call raises."""
        body = [
            f"int {L}status = {call};",
            f"{L}scope.returned();",
            *self.after_call,
            f"{L}scope.check({L}status);",
            *after,
        ]
        if not self.cleanup:
            self.statements.extend(body)
            return
        self.statements.append("try")
        self.statements.append("{")
        self.statements.extend(f"    {line}" if line else line for line in body)
        self.statements.append("}")
        self.statements.append("finally")
        self.statements.append("{")
        self.statements.extend(f"    {line}" for line in self.cleanup)
        self.statements.append("}")

    def finish(self) -> list[str]:
        src = Source()
        src.lines_of(self.pre)
        if self.resources:
            src.open(f"try ({'; '.join(self.resources)})")
            src.lines_of(self.statements)
            src.close()
        else:
            src.lines_of(self.statements)
        return src.lines

    def same_object_checks(self) -> None:
        """An object that is given to C++ (which destroys it) cannot also be used by the call:
        not as ``this`` nor as another argument."""
        plan = self.plan
        objects = [
            (self.parameter_names[position], "nullable" in spec.words, "transfer" in spec.words)
            for position, spec in enumerate(plan.params)
            if isinstance(spec.type, ClassRef)
        ]
        for index, (name, nullable, given) in enumerate(objects):
            if not given:
                continue
            others = ["this"] if plan.kind is CallableKind.METHOD else []
            for other_index, (other, _, other_given) in enumerate(objects):
                # Two given objects are compared once.
                if other_index != index and not (other_given and other_index < index):
                    others.append(other)
            for other in others:
                # Objects of classes that are unrelated cannot be compared without the cast.
                condition = f"(java.lang.Object) {name} == {other}"
                if nullable:
                    condition = f"{name} != null && {condition}"
                self.pre.append(f"if ({condition})")
                self.pre.append("{")
                self.pre.append(
                    "    throw new java.lang.IllegalArgumentException("
                    f'"{name}: the object is given to C++, which destroys it, '
                    'and the call uses it too");'
                )
                self.pre.append("}")

    def retains(self, keeper: str | None) -> list[str]:
        """The statements that keep what C++ keeps pointers to: the objects and the texts that
        are given, by the object that keeps them (``keeper``: the prefix of the call, empty for
        this one), or for ever (None: a function or a static method); and the object itself, if
        the library keeps it."""
        plan = self.plan
        retain = "BridgefexObject.retainForever" if keeper is None else f"{keeper}retain"
        retain_text = (
            "BridgefexObject.retainTextForever" if keeper is None else f"{keeper}retainText"
        )
        lines = [f"{retain}({name});" for name in self.retained]
        lines += [f"{retain_text}({name});" for name in self.kept_texts]
        if plan.retains_this and not self.gives_object():
            # Nothing that C++ gives keeps the object: the library does, for ever.
            lines.append("BridgefexObject.retainForever(this);")
        return lines

    def gives_object(self) -> bool:
        """The method returns an object that the caller owns (a wrapper that is new)."""
        spec = self.plan.result
        return (
            spec is not None
            and isinstance(spec.type, ClassRef)
            and spec.type.form not in (ClassForm.REFERENCE, ClassForm.CONST_REFERENCE)
            and spec.ownership != "borrowed"
        )

    def argument(self, name: str) -> str:
        try:
            return self.arguments[name]
        except KeyError:
            raise Unsupported(
                f"the C parameter '{name}' of a function is not supported in Java yet"
            ) from None

    # -- parameters ---------------------------------------------------------------------

    def param(self, spec: ParamSpec, position: int) -> None:
        type_ = spec.type
        name = self.parameter_names[position - 1]
        if "retain" in spec.words and not isinstance(type_, StringType):
            self.retained.append(name)
        if (
            isinstance(type_, ClassRef)
            and type_.form is not ClassForm.VALUE
            and "transfer" not in spec.words
        ):
            self.owners.append(name)
        if isinstance(type_, ScalarType):
            info = scalar(type_.c_name)
            self.lowering.parameters.append(f"{info.annotation}{info.public} {name}")
            self.arguments[spec.name] = info.to_native.replace("{name}", f'"{name}"').replace(
                "{}", name
            )
        elif isinstance(type_, EnumRef):
            self.enum_param(spec, name)
        elif isinstance(type_, StringType):
            self.string_param(spec, position, name)
        elif isinstance(type_, StructRef):
            self.struct_param(spec, position, name)
        elif isinstance(type_, ClassRef):
            self.object_param(spec, type_, position, name)
        elif isinstance(type_, ContainerType):
            containers.param(self, spec, type_, position, name)
        elif isinstance(type_, CallbackType):
            self.callback_param(spec, position, name)
        else:
            raise Unsupported(
                f"the parameter '{spec.name}' of type '{type_.cpp_spelling}' is not supported "
                "in Java yet"
            )

    def callback_param(self, spec: ParamSpec, position: int, name: str) -> None:
        """A functional object (or null) goes to C as a thunk, a key and the release function."""
        assert spec.callback is not None
        assert spec.user_data
        assert spec.release
        assert self.names.thunks is not None
        key = next(param.ffi.name for param in self.plan.function.params if param.name == spec.name)
        thunk = self.names.thunks.of(key, spec.callback, self.names)
        self.lowering.parameters.append(f"{thunk.interface} {name}")
        self.lowering.remarks.append(
            f"{name}: null is an empty std::function, and C++ that calls it fails with "
            "BridgefexException. An exception of the callback is raised by this call; one that no "
            "call can raise (a callback that C++ stored and a later call runs) goes to "
            "BridgefexCallbacks.setUnobserved."
        )
        scope = self.scope_resource()
        registration = f"{L}o{position}"
        address = f"{self.names.root}.BridgefexCallbacks.Thunk{thunk.index}.address()"
        self.statements.append(
            f"{self.declare('BridgefexCallbacks.Registration')} {registration} = "
            f'{scope}.register({name}, "{spec.name}", {address});'
        )
        self.arguments[spec.name] = f"{registration}.callback()"
        self.arguments[spec.user_data] = f"{registration}.userData()"
        self.arguments[spec.release] = f"{registration}.release()"

    def enum_param(self, spec: ParamSpec, name: str) -> None:
        assert spec.enum is not None
        enum = spec.enum
        info = scalar(enum.model.underlying.c_name)
        self.lowering.parameters.append(f"{self.names.enum(enum)} {name}")
        self.statements.append(f'{R}.nonNull({name}, "{name}");')
        valid = enum.model.valid_range
        if valid is not None:
            self.statements.append(
                f'{R}.checkEnum({name}.value(), {valid[0]}, {valid[1]}, "{name}");'
            )
        value = f"{name}.value()" if info.public == "long" else f"({info.public}) {name}.value()"
        self.arguments[spec.name] = info.to_native.replace("{name}", f'"{name}"').replace(
            "{}", value
        )

    def kept_text(self, spec: ParamSpec, position: int, name: str) -> None:
        """A text that C++ keeps a pointer to: memory of its own that the object that keeps it
        holds (the memory of the call is gone when the call returns)."""
        self.lowering.parameters.append(f"{STRING} {name}")
        statements = self.statements
        local = f"{L}k{position}"
        pointer = f"{L}p{position}"
        segment = self.declare(self.segment)
        if spec.size is None:
            make = f'{R}.keptBytes({R}.utf8z({name}, "{name}"))'
            if "nullable" in spec.words:
                self.lowering.remarks.append(f"{name} may be null (a NULL const char*).")
                statements.append(f"{segment} {local} = {name} == null ? null : {make};")
                statements.append(
                    f"{segment} {pointer} = {local} == null ? {self.segment}.NULL : {local};"
                )
            else:
                statements.append(f"{segment} {local} = {make};")
                statements.append(f"{segment} {pointer} = {local};")
        else:
            statements.append(f'byte[] {L}b{position} = {R}.utf8({name}, "{name}");')
            statements.append(f"{segment} {local} = {R}.keptBytes({L}b{position});")
            statements.append(f"{segment} {pointer} = {local};")
            self.arguments[spec.size] = f"(long) {L}b{position}.length"
        self.arguments[spec.name] = pointer
        self.kept_texts.append(local)

    def string_param(self, spec: ParamSpec, position: int, name: str) -> None:
        if "retain" in spec.words:
            self.kept_text(spec, position, name)
            return
        arena = self.arena_resource()
        self.lowering.parameters.append(f"{STRING} {name}")
        statements = self.statements
        if spec.size is None:
            # NUL-terminated: C++ would stop at a NUL of the text.
            if "nullable" in spec.words:
                self.lowering.remarks.append(f"{name} may be null (a NULL const char*).")
                statements.append(
                    f"{self.declare(self.segment)} {L}p{position} = "
                    f"{name} == null ? {self.segment}.NULL"
                    f' : {R}.bytes({arena}, {R}.utf8z({name}, "{name}"));'
                )
            else:
                statements.append(
                    f"{self.declare(self.segment)} {L}p{position} = "
                    f'{R}.bytes({arena}, {R}.utf8z({name}, "{name}"));'
                )
            self.arguments[spec.name] = f"{L}p{position}"
            return
        statements.append(f'byte[] {L}b{position} = {R}.utf8({name}, "{name}");')
        statements.append(
            f"{self.declare(self.segment)} {L}p{position} = {R}.bytes({arena}, {L}b{position});"
        )
        self.arguments[spec.name] = f"{L}p{position}"
        self.arguments[spec.size] = f"(long) {L}b{position}.length"

    def struct_param(self, spec: ParamSpec, position: int, name: str) -> None:
        assert spec.struct is not None
        struct = self.names.struct(spec.struct)
        arena = self.arena_resource()
        self.lowering.parameters.append(f"{struct} {name}")
        local = f"{L}s{position}"
        self.statements.append(
            f"{self.declare(self.segment)} {local} = {arena}.allocate({struct}.LAYOUT);"
        )
        self.statements.append(f'{R}.nonNull({name}, "{name}").write({local});')
        self.arguments[spec.name] = local

    def object_param(self, spec: ParamSpec, ref: ClassRef, position: int, name: str) -> None:
        info = spec.cls
        assert info is not None
        nullable = "nullable" in spec.words
        given = "transfer" in spec.words
        constant = ref.form in (
            ClassForm.VALUE,
            ClassForm.CONST_REFERENCE,
            ClassForm.CONST_POINTER,
        )
        mutable = "false" if constant else "true"
        lease = f"{L}t{position}"
        # A call that gives the object also needs it for itself alone (see BridgefexObject.hold).
        arguments = f"{mutable}, {'true' if given else 'false'}"
        hold = f"hold_{info.c_type}({arguments})" if info.is_base else f"hold({arguments})"
        self.lowering.parameters.append(f"{self.names.cls(info)} {name}")
        if nullable:
            self.lowering.remarks.append(f"{name} may be null.")
        else:
            self.pre.append(f'{R}.nonNull({name}, "{name}");')
        if given and info.is_base and not info.model.virtual_destructor:
            # C++ destroys the object through this class: only an object of exactly it.
            check = f'BridgefexObject.checkExact({name}, {self.names.cls(info)}.class, "{name}");'
            self.pre.append(f"if ({name} != null) {check}" if nullable else check)
        if nullable:
            self.resources.append(
                f"{self.declare('BridgefexLease')} {lease} = {name} == null ? null : {name}.{hold}"
            )
            self.statements.append(
                f"{self.declare(self.segment)} {L}h{position} = "
                f"{lease} == null ? {self.segment}.NULL : {lease}.pointer();"
            )
        else:
            self.resources.append(f"{self.declare('BridgefexLease')} {lease} = {name}.{hold}")
            self.statements.append(
                f"{self.declare(self.segment)} {L}h{position} = {lease}.pointer();"
            )
        self.arguments[spec.name] = f"{L}h{position}"
        if given:
            self.transfers.append((name, lease, nullable))

    # -- results ------------------------------------------------------------------------

    def result(self) -> list[str]:
        if self.plan.kind is CallableKind.CONSTRUCTOR:
            return self.constructor_result()
        if self.plan.kind is CallableKind.DIRECTOR_CONSTRUCTOR:
            return self.director_constructor_result()
        spec = self.plan.result
        if spec is None:
            return []
        type_ = spec.type
        if isinstance(type_, ScalarType):
            if type_.is_void:
                return []
            return self.scalar_result(type_)
        if isinstance(type_, EnumRef):
            return self.enum_result(spec)
        if isinstance(type_, StringType):
            return self.string_result(type_, spec.as_bytes)
        if isinstance(type_, StructRef):
            return self.struct_result(spec)
        if isinstance(type_, ClassRef):
            return self.object_result(spec, type_)
        if isinstance(type_, ContainerType):
            return containers.result(self, spec, type_)
        raise Unsupported(f"the result of type '{type_.cpp_spelling}' is not supported in Java yet")

    def scalar_result(self, type_: ScalarType) -> list[str]:
        info = scalar(type_.c_name)
        arena = self.arena_resource()
        self.statements.append(
            f"{self.declare(self.segment)} {L}result = {arena}.allocate({info.layout});"
        )
        self.arguments["out_result"] = f"{L}result"
        self.lowering.returns = info.public
        if info.unsigned:
            self.lowering.annotations.append("@BridgefexUnsigned")
        return [f"return {info.read.replace('{seg}', f'{L}result').replace('{off}', '0')};"]

    def enum_result(self, spec: ResultSpec) -> list[str]:
        assert spec.enum is not None
        info = scalar(spec.enum.model.underlying.c_name)
        arena = self.arena_resource()
        self.statements.append(
            f"{self.declare(self.segment)} {L}result = {arena}.allocate({info.layout});"
        )
        self.arguments["out_result"] = f"{L}result"
        enum = self.names.enum(spec.enum)
        self.lowering.returns = enum
        read = info.read.replace("{seg}", f"{L}result").replace("{off}", "0")
        return [f"return {enum}.of({read});"]

    def struct_result(self, spec: ResultSpec) -> list[str]:
        assert spec.struct is not None
        struct = self.names.struct(spec.struct)
        arena = self.arena_resource()
        self.statements.append(
            f"{self.declare(self.segment)} {L}result = {arena}.allocate({struct}.LAYOUT);"
        )
        self.arguments["out_result"] = f"{L}result"
        self.lowering.returns = struct
        return [f"return {struct}.read({L}result);"]

    def string_result(self, type_: StringType, as_bytes: bool) -> list[str]:
        arena = self.arena_resource()
        self.statements.append(
            f"{self.declare(self.segment)} {L}text = {arena}.allocate({R}.ADDRESS);"
        )
        self.statements.append(
            f"{self.declare(self.segment)} {L}size = {arena}.allocate({R}.C_SIZE);"
        )
        self.arguments["out_result"] = f"{L}text"
        self.arguments["out_result_size"] = f"{L}size"
        self.cleanup = [
            f"BridgefexNative.{self.module.free_function}({L}text.get({R}.ADDRESS, 0));"
        ]
        take = "takeBytes" if as_bytes else "takeText"
        pointer = f"{L}text.get({R}.ADDRESS, 0)"
        decode = f"{R}.{take}({pointer}, {R}.getSize({L}size, 0))"
        public = "byte[]" if as_bytes else STRING
        self.lowering.returns = public
        if type_.sized:
            return [f"return {decode};"]
        # A const char* result: NULL is a null string.
        self.lowering.remarks.append("The result is null if C++ gives a NULL const char*.")
        return [
            f"if ({pointer}.address() == 0L)",
            "{",
            "    return null;",
            "}",
            f"return {decode};",
        ]

    def constructor_result(self) -> list[str]:
        arena = self.arena_resource()
        self.statements.append(
            f"{self.declare(self.segment)} {L}result = {arena}.allocate({R}.ADDRESS);"
        )
        self.arguments["out_self"] = f"{L}result"
        assign = f"{L}result.get({R}.ADDRESS, 0)"
        if self.factory is None:
            # The handle of the object exists already (the constructor of the base made it).
            self.after_call = [f"this.handle.assign({assign});"]
            return self.retains("")
        # A static method: the object exists before the call, so that its destruction is
        # guaranteed from the moment the C function writes the pointer.
        assert self.declaring is not None
        handle = self.declare("BridgefexHandle")
        self.statements.append(f"{handle} {L}h = {self.names.destroyer(self.declaring.c_type)};")
        self.statements.append(f"{self.declare(self.factory)} {L}new = {self.factory}.wrap({L}h);")
        self.after_call = [f"{L}h.assign({assign});"]
        self.lowering.returns = self.factory
        return [*self.retains(f"{L}new."), f"return {L}new;"]

    def director_constructor_result(self) -> list[str]:
        """A constructor that makes the director: the vtable and the box were made by the
        prelude, and the object (this one) is the C++ object that the C function writes."""
        arena = self.arena_resource()
        self.statements.append(
            f"{self.declare(self.segment)} {L}result = {arena}.allocate({R}.ADDRESS);"
        )
        self.statements.append(
            f"{self.declare(self.segment)} {L}director = {arena}.allocate({R}.ADDRESS);"
        )
        self.arguments["out_object"] = f"{L}result"
        self.arguments["out_director"] = f"{L}director"
        self.arguments["vtable"] = f"{L}vt"
        self.after_call = [
            f"this.handle.assign({L}result.get({R}.ADDRESS, 0));",
            f"{L}box.attach({L}director.get({R}.ADDRESS, 0), {L}result.get({R}.ADDRESS, 0));",
            f"setDirector({L}box);",
        ]
        return self.retains("")

    def object_result(self, spec: ResultSpec, ref: ClassRef) -> list[str]:
        info = spec.cls
        assert info is not None
        class_name = self.names.cls(info)
        arena = self.arena_resource()
        borrowed = ref.form in (ClassForm.REFERENCE, ClassForm.CONST_REFERENCE) or (
            spec.ownership == "borrowed"
        )
        # A reference and a copy are never NULL; a pointer and a std::unique_ptr may be.
        nullable = ref.form not in (
            ClassForm.VALUE,
            ClassForm.REFERENCE,
            ClassForm.CONST_REFERENCE,
        )
        self.statements.append(
            f"{self.declare(self.segment)} {L}result = {arena}.allocate({R}.ADDRESS);"
        )
        self.arguments["out_result"] = f"{L}result"
        given = ["this"] if self.plan.kind is CallableKind.METHOD else self.owners
        owner_list = f"new BridgefexObject[] {{{', '.join(given)}}}" if given else "null"
        self.lowering.returns = class_name
        pointer = f"{L}result.get({R}.ADDRESS, 0)"
        if nullable:
            self.lowering.remarks.append("The result is null if C++ gives a NULL pointer.")
        if borrowed:
            constant = ref.form in (ClassForm.CONST_REFERENCE, ClassForm.CONST_POINTER)
            return [
                f"return {class_name}.borrow({pointer}, {owner_list}, {str(constant).lower()});"
            ]
        # The object exists before the call, so that its destruction is guaranteed from the
        # moment the C function writes the pointer.
        handle = self.declare("BridgefexHandle")
        self.statements.append(f"{handle} {L}h = {self.names.destroyer(info.c_type)};")
        self.statements.append(f"{self.declare(class_name)} {L}new = {class_name}.wrap({L}h);")
        self.after_call = [f"{L}h.assign({pointer});"]
        if self.plan.retains_this:
            # The object that is given keeps a pointer to this one: it keeps it alive.
            if nullable:
                return [
                    f"if ({L}h.isInvalid()) return null;",
                    f"{L}new.retain(this);",
                    f"return {L}new;",
                ]
            return [f"{L}new.retain(this);", f"return {L}new;"]
        if nullable:
            return [f"return {L}h.isInvalid() ? null : {L}new;"]
        return [f"return {L}new;"]
