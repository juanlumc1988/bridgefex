# SPDX-License-Identifier: Apache-2.0
"""Lowering a callable of the plan to a Java method: what the wrapper converts before the call,
the call of the native function, and what it converts after it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ...model import EnumRef, ScalarType, StringType, StructRef
from ...plan import CallablePlan, EnumPlan, ModulePlan, ParamSpec, ResultSpec, StructPlan
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


def lower_callable(plan: CallablePlan, names: Names, module: ModulePlan) -> Lowering:
    """The Java method for ``plan``."""
    try:
        return _Lowerer(plan, names, module).run()
    except Unsupported as error:
        if error.location is not None:
            raise
        raise Unsupported(f"'{plan.function.description}': {error}", plan.location) from error


class _Lowerer:
    def __init__(self, plan: CallablePlan, names: Names, module: ModulePlan) -> None:
        self.plan = plan
        self.names = names
        self.module = module
        self.lowering = Lowering()
        self.resources: list[str] = []
        """The ``try`` resources: the memory of the call, the holds on the objects."""

        self.statements: list[str] = []
        self.arguments: dict[str, str] = {}
        self.arena = False
        self.cleanup: list[str] = []
        """What frees what C++ allocated for the result."""

    def arena_resource(self) -> str:
        """The memory of the call, made when something needs it."""
        if not self.arena:
            self.arena = True
            self.resources.insert(0, f"var {L}arena = BridgefexRuntime.arena()")
        return f"{L}arena"

    def run(self) -> Lowering:
        plan = self.plan
        for position, spec in enumerate(plan.params, start=1):
            self.param(spec, position)
        after = self.result()
        call_args = ", ".join(self.argument(param.name) for param in plan.function.params)
        call = f"BridgefexNative.{plan.function.name}({call_args})"
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
        else:
            self.statements.append(f"BridgefexRuntime.check({call});")
            self.statements.extend(after)
        self.lowering.body = self.finish()
        return self.lowering

    def finish(self) -> list[str]:
        if not self.resources:
            return self.statements
        src = Source()
        src.open(f"try ({'; '.join(self.resources)})")
        src.lines_of(self.statements)
        src.close()
        return src.lines

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

    # -- results ------------------------------------------------------------------------

    def result(self) -> list[str]:
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
            f"if ({pointer}.equals({SEGMENT}.NULL))",
            "{",
            "    return null;",
            "}",
            f"return {decode};",
        ]
