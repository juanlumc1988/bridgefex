# SPDX-License-Identifier: Apache-2.0
"""The Rust side of a callable of the plan: its parameters, its call and its result."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ...model import ScalarType
from ...naming import unique_name
from ...plan import CallableKind, CallablePlan, ClassPlan, ModulePlan, ParamSpec
from .mapping import Unsupported, scalar
from .naming import LOCAL_PREFIX, parameter

L = LOCAL_PREFIX

NATIVE = "crate::native"
RUNTIME = "crate::runtime"


@dataclass(frozen=True, slots=True)
class Names:
    """The Rust names of the types of the module, by the C type."""

    classes: Mapping[str, str] = field(default_factory=dict)
    """The path of the type of each class (``crate::counter::Counter``)."""


@dataclass(slots=True)
class Lowering:
    """What a callable becomes: the parameters of the Rust function, and its body."""

    receiver: str = ""
    """``&self`` or ``&mut self`` for a method; empty for an associated or free function."""

    parameters: list[str] = field(default_factory=list)
    returns: str = "()"
    body: list[str] = field(default_factory=list)
    remarks: list[str] = field(default_factory=list)
    """What the signature does not say (for the documentation comment)."""


def lower_callable(
    plan: CallablePlan, names: Names, module: ModulePlan, *, declaring: ClassPlan | None = None
) -> Lowering:
    """The Rust function for ``plan``. ``declaring`` is the class that a method or a
    constructor belongs to."""
    try:
        return _Lowerer(plan, names, module, declaring).run()
    except Unsupported as error:
        if error.location is not None:
            raise
        raise Unsupported(f"'{plan.function.description}': {error}", plan.location) from error


class _Lowerer:
    def __init__(
        self, plan: CallablePlan, names: Names, module: ModulePlan, declaring: ClassPlan | None
    ) -> None:
        self.declaring = declaring
        self.plan = plan
        self.names = names
        self.module = module
        self.lowering = Lowering()
        self.arguments: dict[str, str] = {}
        """The expression passed for each C parameter."""

        self.before: list[str] = []
        self.taken: set[str] = set()

    def run(self) -> Lowering:
        plan = self.plan
        lowering = self.lowering
        if plan.kind is CallableKind.METHOD:
            lowering.receiver = "&self" if plan.is_const else "&mut self"
            self.arguments["self"] = "self"
        elif plan.kind in (CallableKind.DIRECTOR_CONSTRUCTOR, CallableKind.DIRECTOR_BASE):
            raise Unsupported("directors are not supported in Rust yet")
        for spec in plan.params:
            self.param(spec)
        returns, after = self.result()
        arguments = [self.argument(param.name) for param in plan.function.params]
        lowering.returns = returns
        lowering.body = [
            f"{RUNTIME}::ensure()?;",
            *self.before,
            *_call(f"let {L}status = ", f"{NATIVE}::{plan.function.name}", arguments),
            f"{RUNTIME}::check({L}status)?;",
            *after,
        ]
        return lowering

    def argument(self, name: str) -> str:
        try:
            return self.arguments[name]
        except KeyError:
            raise Unsupported(
                f"the C parameter '{name}' of a function is not supported in Rust yet"
            ) from None

    # -- parameters -------------------------------------------------------------------------

    def param(self, spec: ParamSpec) -> None:
        type_ = spec.type
        name = unique_name(parameter(spec.name), frozenset(self.taken))
        self.taken.add(name)
        if isinstance(type_, ScalarType) and not type_.is_void:
            rust = scalar(type_.c_name)
            self.lowering.parameters.append(f"{name}: {rust}")
            self.arguments[spec.name] = name
            return
        raise Unsupported(
            f"the parameter '{spec.name}' of type '{type_.cpp_spelling}' is not supported in "
            "Rust yet"
        )

    # -- results ----------------------------------------------------------------------------

    def result(self) -> tuple[str, list[str]]:
        """(the Rust result type, the statements that return it)."""
        plan = self.plan
        if plan.kind is CallableKind.CONSTRUCTOR:
            assert self.declaring is not None
            self.before.append(f"let mut {L}object: *mut Self = ::core::ptr::null_mut();")
            self.arguments["out_self"] = f"&mut {L}object"
            return (
                "crate::Result<crate::Owned<Self>>",
                [f"unsafe {{ {RUNTIME}::adopt({L}object) }}"],
            )
        spec = plan.result
        if spec is None:
            return "crate::Result<()>", ["Ok(())"]
        type_ = spec.type
        if isinstance(type_, ScalarType) and not type_.is_void:
            rust = scalar(type_.c_name)
            self.before.append(f"let mut {L}result: {rust} = ::core::default::Default::default();")
            self.arguments["out_result"] = f"&mut {L}result"
            return f"crate::Result<{rust}>", [f"Ok({L}result)"]
        raise Unsupported(f"the result of type '{type_.cpp_spelling}' is not supported in Rust yet")


def _call(prefix: str, function: str, arguments: list[str]) -> list[str]:
    """``prefix`` and the unsafe call of the C function, on as many lines as the line limit
    needs."""
    one = f"{prefix}unsafe {{ {function}({', '.join(arguments)}) }};"
    if len(one) + 8 <= 100:
        return [one]
    return [
        f"{prefix}unsafe {{",
        f"    {function}(",
        *(f"        {argument}," for argument in arguments),
        "    )",
        "};",
    ]


__all__ = ["Lowering", "Names", "lower_callable"]
