# SPDX-License-Identifier: Apache-2.0
"""Lowering a callable of the plan to a Java method: what the wrapper converts before the call,
the call of the native function, and what it converts after it."""

from __future__ import annotations

from dataclasses import dataclass, field

from ...model import ScalarType
from ...plan import CallablePlan, ModulePlan, ParamSpec
from .mapping import Unsupported, scalar
from .naming import LOCAL_PREFIX, camel, identifier, parameter
from .source import Source

L = LOCAL_PREFIX


@dataclass(frozen=True, slots=True)
class Names:
    """The Java names of the types of the module."""

    root: str
    """The package of the module."""


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
            return
        raise Unsupported(
            f"the parameter '{spec.name}' of type '{type_.cpp_spelling}' is not supported "
            "in Java yet"
        )

    # -- results ------------------------------------------------------------------------

    def result(self) -> list[str]:
        spec = self.plan.result
        if spec is None:
            return []
        type_ = spec.type
        if isinstance(type_, ScalarType):
            if type_.is_void:
                return []
            info = scalar(type_.c_name)
            arena = self.arena_resource()
            self.statements.append(f"var {L}result = {arena}.allocate({info.layout});")
            self.arguments["out_result"] = f"{L}result"
            self.lowering.returns = info.public
            if info.unsigned:
                self.lowering.annotations.append("@BridgefexUnsigned")
            return [f"return {info.read.replace('{seg}', f'{L}result').replace('{off}', '0')};"]
        raise Unsupported(f"the result of type '{type_.cpp_spelling}' is not supported in Java yet")
