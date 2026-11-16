# SPDX-License-Identifier: Apache-2.0
"""The Rust side of a callable of the plan: its parameters, its call and its result."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ...model import EnumRef, ScalarType, StringType, StructRef, Type
from ...naming import unique_name
from ...plan import (
    CallableKind,
    CallablePlan,
    ClassPlan,
    EnumPlan,
    ModulePlan,
    ParamSpec,
    ResultSpec,
    StructPlan,
)
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

    enums: Mapping[str, str] = field(default_factory=dict)
    structs: Mapping[str, str] = field(default_factory=dict)
    enum_plans: Mapping[str, EnumPlan] = field(default_factory=dict)
    """The enums and the structs by USR: a member of a struct names its type that way."""

    struct_plans: Mapping[str, StructPlan] = field(default_factory=dict)

    @property
    def types(self) -> dict[str, str]:
        """The path of every class and struct, by the C type (what a pointer of the C API points
        to)."""
        return {**self.classes, **self.structs}


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


def public_type(type_: Type, names: Names, spec: ParamSpec | ResultSpec) -> str:
    """The type that the Rust API shows for a C++ type."""
    if isinstance(type_, ScalarType) and not type_.is_void:
        return scalar(type_.c_name)
    nullable = isinstance(spec, ParamSpec) and "nullable" in spec.words
    if isinstance(type_, StringType):
        if isinstance(spec, ParamSpec):
            return "::core::option::Option<&str>" if nullable else "&str"
        if spec.as_bytes:
            return "::std::vec::Vec<u8>"
        text = "::std::string::String"
        return text if type_.sized else f"::core::option::Option<{text}>"
    if isinstance(type_, EnumRef) and spec.enum is not None:
        return names.enums[spec.enum.c_type]
    if isinstance(type_, StructRef) and spec.struct is not None:
        return names.structs[spec.struct.c_type]
    raise Unsupported(f"the type '{type_.cpp_spelling}' is not supported in Rust yet")


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
        self.lowering.parameters.append(f"{name}: {public_type(type_, self.names, spec)}")
        if isinstance(type_, ScalarType) and not type_.is_void:
            self.arguments[spec.name] = name
        elif isinstance(type_, EnumRef):
            self.enum_param(spec, name)
        elif isinstance(type_, StructRef):
            self.arguments[spec.name] = f"&{name}"
        elif isinstance(type_, StringType):
            self.string_param(spec, name)
        else:
            raise Unsupported(
                f"the parameter '{spec.name}' of type '{type_.cpp_spelling}' is not supported in "
                "Rust yet"
            )

    def enum_param(self, spec: ParamSpec, name: str) -> None:
        assert spec.enum is not None
        valid = spec.enum.model.valid_range
        if valid is not None:
            # An enum without a fixed type: C++ does not define a value that none of its
            # enumerators' bits can make.
            self.before.append(
                f'{RUNTIME}::check_enum({name}.0 as i128, {valid[0]}, {valid[1]}, "{spec.name}")?;'
            )
        self.arguments[spec.name] = f"{name}.0"

    def string_param(self, spec: ParamSpec, name: str) -> None:
        nullable = "nullable" in spec.words
        if spec.size is not None:
            # A std::string or a std::string_view: the bytes and their number; NUL is text.
            self.arguments[spec.name] = f"{name}.as_ptr().cast()"
            self.arguments[spec.size] = f"{name}.len()"
            return
        # A const char*: C++ reads up to the first NUL, so the text must not have one.
        local = f"{L}c_{name}"
        if nullable:
            self.before.append(
                f"let {local} = match {name} {{ Some(text) => "
                f'Some({RUNTIME}::c_string(text, "{spec.name}")?), None => None }};'
            )
            self.arguments[spec.name] = (
                f"{local}.as_ref().map_or(::core::ptr::null(), |text| text.as_ptr())"
            )
        else:
            self.before.append(f'let {local} = {RUNTIME}::c_string({name}, "{spec.name}")?;')
            self.arguments[spec.name] = f"{local}.as_ptr()"

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
        public = public_type(type_, self.names, spec)
        if isinstance(type_, ScalarType) and not type_.is_void:
            self.before.append(
                f"let mut {L}result: {public} = ::core::default::Default::default();"
            )
            self.arguments["out_result"] = f"&mut {L}result"
            return f"crate::Result<{public}>", [f"Ok({L}result)"]
        if isinstance(type_, EnumRef):
            assert spec.enum is not None
            raw = scalar(spec.enum.model.underlying.c_name)
            self.before.append(f"let mut {L}result: {raw} = ::core::default::Default::default();")
            self.arguments["out_result"] = f"&mut {L}result"
            return f"crate::Result<{public}>", [f"Ok({public}({L}result))"]
        if isinstance(type_, StructRef):
            self.before.append(f"let mut {L}result = {public}::zeroed();")
            self.arguments["out_result"] = f"&mut {L}result"
            return f"crate::Result<{public}>", [f"Ok({L}result)"]
        if isinstance(type_, StringType):
            return self.string_result(type_, spec, public)
        raise Unsupported(f"the result of type '{type_.cpp_spelling}' is not supported in Rust yet")

    def string_result(
        self, type_: StringType, spec: ResultSpec, public: str
    ) -> tuple[str, list[str]]:
        self.before.append(f"let mut {L}text = {RUNTIME}::Text::new();")
        self.arguments["out_result"] = f"{L}text.data()"
        self.arguments["out_result_size"] = f"{L}text.size()"
        take = f"{L}text.into_bytes()" if spec.as_bytes else f"{L}text.into_string()"
        if type_.sized:
            return f"crate::Result<{public}>", [f"Ok({take})"]
        # A const char* result: NULL is no string.
        return f"crate::Result<{public}>", [
            f"if {L}text.is_null() {{",
            "    return Ok(None);",
            "}",
            f"Ok(Some({take}))",
        ]


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


__all__ = ["Lowering", "Names", "lower_callable", "public_type"]
