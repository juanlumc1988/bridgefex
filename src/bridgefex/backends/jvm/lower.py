# SPDX-License-Identifier: Apache-2.0
"""Lowering a callable of the plan to a Java method: what the wrapper converts before the call,
the call of the native function, and what it converts after it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ...model import ClassForm, ClassRef, EnumRef, ScalarType, StringType, StructRef
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
from .mapping import SEGMENT, STRING, Unsupported, scalar
from .naming import LOCAL_PREFIX, camel, identifier, parameter
from .source import Source

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
) -> Lowering:
    """The Java method (or constructor) for ``plan``. ``declaring`` is the class that declares a
    method. ``factory`` (the Java class) lowers a constructor to a static method that returns the
    object."""
    try:
        return _Lowerer(plan, names, module, declaring, factory).run()
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
        factory: str | None,
    ) -> None:
        self.plan = plan
        self.names = names
        self.module = module
        self.declaring = declaring
        self.factory = factory
        self.lowering = Lowering()
        self.pre: list[str] = []
        """What is checked before anything is held."""

        self.resources: list[str] = []
        """The ``try`` resources: the memory of the call, the holds on the objects."""

        self.statements: list[str] = []
        self.arguments: dict[str, str] = {}
        self.arena = False
        self.cleanup: list[str] = []
        """What frees what C++ allocated for the result."""

        self.after_call: list[str] = []
        """What follows the call, with the status still unchecked."""

        self.transfers: list[tuple[str, str, bool]] = []
        """(the parameter, the hold, may be null) of each object given to C++."""

        self.retained: list[str] = []
        self.owners: list[str] = []
        self.created: str | None = None
        """The object that a factory returns."""

    def arena_resource(self) -> str:
        """The memory of the call, made when something needs it."""
        if not self.arena:
            self.arena = True
            self.resources.insert(0, f"var {L}arena = BridgefexRuntime.arena()")
        return f"{L}arena"

    def run(self) -> Lowering:
        plan = self.plan
        if plan.kind in (CallableKind.DIRECTOR_CONSTRUCTOR, CallableKind.DIRECTOR_BASE):
            raise Unsupported("directors are not supported in Java yet")
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
            self.resources.append(f"var {L}self = {hold}({mutable}, false)")
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
            target = (
                "retain" if plan.kind is CallableKind.METHOD else "BridgefexObject.retainForever"
            )
            self.statements.extend(f"{target}({name});" for name in self.retained)
        if self.cleanup:
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
            (parameter(spec.name), "nullable" in spec.words, "transfer" in spec.words)
            for spec in plan.params
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
        name = parameter(spec.name)
        if name == self.names.root:
            name += "_"
        if "retain" in spec.words:
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
        else:
            raise Unsupported(
                f"the parameter '{spec.name}' of type '{type_.cpp_spelling}' is not supported "
                "in Java yet"
            )

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

    def string_param(self, spec: ParamSpec, position: int, name: str) -> None:
        arena = self.arena_resource()
        self.lowering.parameters.append(f"{STRING} {name}")
        statements = self.statements
        if spec.size is None:
            # NUL-terminated: C++ would stop at a NUL of the text.
            if "nullable" in spec.words:
                self.lowering.remarks.append(f"{name} may be null (a NULL const char*).")
                statements.append(
                    f"var {L}p{position} = {name} == null ? {SEGMENT}.NULL"
                    f' : {R}.bytes({arena}, {R}.utf8z({name}, "{name}"));'
                )
            else:
                statements.append(
                    f'var {L}p{position} = {R}.bytes({arena}, {R}.utf8z({name}, "{name}"));'
                )
            self.arguments[spec.name] = f"{L}p{position}"
            return
        statements.append(f'byte[] {L}b{position} = {R}.utf8({name}, "{name}");')
        statements.append(f"var {L}p{position} = {R}.bytes({arena}, {L}b{position});")
        self.arguments[spec.name] = f"{L}p{position}"
        self.arguments[spec.size] = f"(long) {L}b{position}.length"

    def struct_param(self, spec: ParamSpec, position: int, name: str) -> None:
        assert spec.struct is not None
        struct = self.names.struct(spec.struct)
        arena = self.arena_resource()
        self.lowering.parameters.append(f"{struct} {name}")
        local = f"{L}s{position}"
        self.statements.append(f"var {local} = {arena}.allocate({struct}.LAYOUT);")
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
            self.resources.append(f"var {lease} = {name} == null ? null : {name}.{hold}")
            self.statements.append(
                f"var {L}h{position} = {lease} == null ? {SEGMENT}.NULL : {lease}.pointer();"
            )
        else:
            self.resources.append(f"var {lease} = {name}.{hold}")
            self.statements.append(f"var {L}h{position} = {lease}.pointer();")
        self.arguments[spec.name] = f"{L}h{position}"
        if given:
            self.transfers.append((name, lease, nullable))

    # -- results ------------------------------------------------------------------------

    def result(self) -> list[str]:
        if self.plan.kind is CallableKind.CONSTRUCTOR:
            return self.constructor_result()
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
        raise Unsupported(f"the result of type '{type_.cpp_spelling}' is not supported in Java yet")

    def scalar_result(self, type_: ScalarType) -> list[str]:
        info = scalar(type_.c_name)
        arena = self.arena_resource()
        self.statements.append(f"var {L}result = {arena}.allocate({info.layout});")
        self.arguments["out_result"] = f"{L}result"
        self.lowering.returns = info.public
        if info.unsigned:
            self.lowering.annotations.append("@BridgefexUnsigned")
        return [f"return {info.read.replace('{seg}', f'{L}result').replace('{off}', '0')};"]

    def enum_result(self, spec: ResultSpec) -> list[str]:
        assert spec.enum is not None
        info = scalar(spec.enum.model.underlying.c_name)
        arena = self.arena_resource()
        self.statements.append(f"var {L}result = {arena}.allocate({info.layout});")
        self.arguments["out_result"] = f"{L}result"
        enum = self.names.enum(spec.enum)
        self.lowering.returns = enum
        read = info.read.replace("{seg}", f"{L}result").replace("{off}", "0")
        return [f"return {enum}.of({read});"]

    def struct_result(self, spec: ResultSpec) -> list[str]:
        assert spec.struct is not None
        struct = self.names.struct(spec.struct)
        arena = self.arena_resource()
        self.statements.append(f"var {L}result = {arena}.allocate({struct}.LAYOUT);")
        self.arguments["out_result"] = f"{L}result"
        self.lowering.returns = struct
        return [f"return {struct}.read({L}result);"]

    def string_result(self, type_: StringType, as_bytes: bool) -> list[str]:
        arena = self.arena_resource()
        self.statements.append(f"var {L}text = {arena}.allocate({R}.ADDRESS);")
        self.statements.append(f"var {L}size = {arena}.allocate({R}.C_SIZE);")
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
        self.statements.append(f"var {L}result = {arena}.allocate({R}.ADDRESS);")
        self.arguments["out_self"] = f"{L}result"
        assign = f"{L}result.get({R}.ADDRESS, 0)"
        if self.factory is None:
            # The handle of the object exists already (the constructor of the base made it).
            self.after_call = [f"handle.assign({assign});"]
            return [f"retain({name});" for name in self.retained]
        # A static method: the object exists before the call, so that its destruction is
        # guaranteed from the moment the C function writes the pointer.
        assert self.declaring is not None
        self.statements.append(f"var {L}h = {self.names.destroyer(self.declaring.c_type)};")
        self.statements.append(f"var {L}new = new {self.factory}({L}h);")
        self.after_call = [f"{L}h.assign({assign});"]
        self.lowering.returns = self.factory
        return [*(f"{L}new.retain({name});" for name in self.retained), f"return {L}new;"]

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
        self.statements.append(f"var {L}result = {arena}.allocate({R}.ADDRESS);")
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
        self.statements.append(f"var {L}h = {self.names.destroyer(info.c_type)};")
        self.statements.append(f"var {L}new = new {class_name}({L}h);")
        self.after_call = [f"{L}h.assign({pointer});"]
        if nullable:
            return [f"return {L}h.isInvalid() ? null : {L}new;"]
        return [f"return {L}new;"]
