# SPDX-License-Identifier: Apache-2.0
"""The Rust side of a callable of the plan: its parameters, its call and its result."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ...model import ClassForm, ClassRef, EnumRef, ScalarType, StringType, StructRef, Type
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

    generics: str = ""
    """The lifetimes of the function (``<'a>``): that of the objects that a borrowed result
    comes from."""

    unsafe: bool = False
    """The function is ``unsafe``: it gives a reference to an object that nobody in Rust owns
    (valid until C++ destroys it, and for a ``&mut`` the only one), or C++ keeps a pointer to what
    it is given."""


def public_type(type_: Type, names: Names, spec: ParamSpec | ResultSpec) -> str:
    """The type that the Rust API shows for a C++ type."""
    if isinstance(type_, ScalarType) and not type_.is_void:
        return scalar(type_.c_name)
    nullable = isinstance(spec, ParamSpec) and "nullable" in spec.words
    if isinstance(type_, StringType):
        if isinstance(spec, ParamSpec):
            return "::core::option::Option<&str>" if nullable else "&str"
        text = "::std::vec::Vec<u8>" if spec.as_bytes else "::std::string::String"
        return text if type_.sized else f"::core::option::Option<{text}>"
    if isinstance(type_, EnumRef) and spec.enum is not None:
        return names.enums[spec.enum.c_type]
    if isinstance(type_, StructRef) and spec.struct is not None:
        return names.structs[spec.struct.c_type]
    raise Unsupported(f"the type '{type_.cpp_spelling}' is not supported in Rust yet")


def is_borrowed(spec: ResultSpec, ref: ClassRef) -> bool:
    """A result that is an object that C++ keeps owning: a reference, or a pointer that the
    annotations say is borrowed."""
    return ref.form in (ClassForm.REFERENCE, ClassForm.CONST_REFERENCE) or (
        spec.ownership == "borrowed"
    )


def is_constant(ref: ClassRef) -> bool:
    return ref.form in (ClassForm.VALUE, ClassForm.CONST_REFERENCE, ClassForm.CONST_POINTER)


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
        self.lifetime = ""

    def run(self) -> Lowering:
        plan = self.plan
        lowering = self.lowering
        self.lifetime = self.borrowed_lifetime()
        if self.lifetime == "'a":
            lowering.generics = "<'a>"
        if plan.kind is CallableKind.METHOD:
            lowering.receiver = self.receiver()
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

    def exclusive(self) -> bool:
        """The result is a non-``const`` object that C++ keeps owning: a ``&mut`` that Rust may
        give only if what it borrows from is borrowed exclusively (two calls would give two
        ``&mut`` to the same object otherwise)."""
        spec = self.plan.result
        return (
            spec is not None
            and isinstance(spec.type, ClassRef)
            and is_borrowed(spec, spec.type)
            and not is_constant(spec.type)
        )

    def gives_object(self) -> bool:
        """The result is an object that the caller owns (and that may keep what the function was
        given)."""
        spec = self.plan.result
        return (
            spec is not None
            and isinstance(spec.type, ClassRef)
            and not is_borrowed(spec, spec.type)
        )

    def borrowing(self, spec: ParamSpec) -> bool:
        """An object parameter that the borrowed result may come from: one that the function
        only uses (not one it takes, and not a copy of the object)."""
        return (
            isinstance(spec.type, ClassRef)
            and "transfer" not in spec.words
            and spec.type.form not in (ClassForm.UNIQUE, ClassForm.VALUE)
        )

    def borrowed_lifetime(self) -> str:
        """The lifetime that the objects of a call share with the borrowed object that it gives
        back. C++ does not say which of them it comes from, so it is all of them: the result
        cannot outlive any (an object that nobody owns lives until the program ends)."""
        plan = self.plan
        spec = plan.result
        if spec is None or not isinstance(spec.type, ClassRef) or not is_borrowed(spec, spec.type):
            return ""
        owners = [param for param in plan.params if self.borrowing(param)]
        if plan.kind is CallableKind.METHOD:
            # Rust gives the lifetime of ``self`` by itself when there is nothing else.
            return "'a" if owners else ""
        return "'a" if owners else "'static"

    def receiver(self) -> str:
        """``&self`` or ``&mut self`` (with the lifetime that the result shares, if there is
        one). A ``const`` method that gives a ``&mut`` takes ``&mut self``."""
        constant = self.plan.is_const and not self.exclusive()
        if self.plan.is_const and not constant:
            self.lowering.remarks.append(
                "Takes `&mut self` although C++ declares it `const`: it gives a non-`const` "
                "reference, and two of them at once would be two `&mut` to the same object."
            )
        lifetime = f"{self.lifetime} " if self.lifetime == "'a" else ""
        return f"&{lifetime}self" if constant else f"&{lifetime}mut self"

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
        if isinstance(type_, ClassRef):
            self.lowering.parameters.append(f"{name}: {self.object_type(spec, type_)}")
        else:
            self.lowering.parameters.append(f"{name}: {public_type(type_, self.names, spec)}")
        if isinstance(type_, ScalarType) and not type_.is_void:
            self.arguments[spec.name] = name
        elif isinstance(type_, EnumRef):
            self.enum_param(spec, name)
        elif isinstance(type_, StructRef):
            self.arguments[spec.name] = f"&{name}"
        elif isinstance(type_, StringType):
            self.string_param(spec, name)
        elif isinstance(type_, ClassRef):
            self.object_param(spec, type_, name)
        else:
            raise Unsupported(
                f"the parameter '{spec.name}' of type '{type_.cpp_spelling}' is not supported in "
                "Rust yet"
            )

    def object_type(self, spec: ParamSpec, ref: ClassRef) -> str:
        """The Rust type of an object that a function takes."""
        assert spec.cls is not None
        path = self.names.classes[spec.cls.c_type]
        nullable = "nullable" in spec.words
        if "transfer" in spec.words or ref.form is ClassForm.UNIQUE:
            owned = f"crate::Owned<{path}>"
            return f"::core::option::Option<{owned}>" if nullable else owned
        lifetime = f"{self.lifetime} " if self.lifetime and self.borrowing(spec) else ""
        # What a non-``const`` result may come from is borrowed exclusively, also when C++ takes
        # it as ``const`` (it cannot be that object, but Rust does not know which one it is).
        constant = is_constant(ref) and not (self.exclusive() and self.borrowing(spec))
        if constant != is_constant(ref):
            self.lowering.remarks.append(
                f"`{spec.name}` is `&mut` although C++ takes it `const`: the result is not "
                "`const`, and may come from any of the objects that the call takes."
            )
        borrowed = f"&{lifetime}{path}" if constant else f"&{lifetime}mut {path}"
        if ref.form in (ClassForm.POINTER, ClassForm.CONST_POINTER) and nullable:
            return f"::core::option::Option<{borrowed}>"
        return borrowed

    def object_param(self, spec: ParamSpec, ref: ClassRef, name: str) -> None:
        nullable = "nullable" in spec.words
        if "retain" in spec.words and "transfer" not in spec.words:
            # C++ keeps a pointer to the object: Rust cannot see until when, so the caller says it.
            self.lowering.unsafe = True
            holder = "`self`" if self.plan.kind is CallableKind.METHOD else "the new object"
            if self.plan.kind in (CallableKind.STATIC, CallableKind.FUNCTION):
                holder = "the object that it returns" if self.gives_object() else "the program"
            self.lowering.remarks.append(
                f"Unsafe: C++ keeps a pointer to `{spec.name}`, which must stay valid for as long "
                f"as {holder} uses it, and nobody else may use it meanwhile."
            )
        if "transfer" in spec.words or ref.form is ClassForm.UNIQUE:
            # C++ takes the object, whatever the status: it is not Rust's any more.
            if nullable:
                self.arguments[spec.name] = (
                    f"{name}.map_or(::core::ptr::null_mut(), crate::Owned::into_raw)"
                )
            else:
                self.arguments[spec.name] = f"crate::Owned::into_raw({name})"
            return
        if ref.form in (ClassForm.POINTER, ClassForm.CONST_POINTER) and nullable:
            null = "null" if is_constant(ref) else "null_mut"
            cast = "*const _" if is_constant(ref) else "*mut _"
            self.arguments[spec.name] = (
                f"{name}.map_or(::core::ptr::{null}(), |object| object as {cast})"
            )
            return
        self.arguments[spec.name] = name

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
        # Not a double underscore in the middle (a lint), and one name for each parameter.
        local = unique_name(f"{L}c_{name.lstrip('_')}", frozenset(self.taken))
        self.taken.add(local)
        if nullable:
            self.before.append(
                f'let {local} = {name}.map(|text| {RUNTIME}::c_string(text, "{spec.name}"))'
                ".transpose()?;"
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
        if isinstance(type_, ClassRef):
            return self.object_result(type_, spec)
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

    def object_result(self, ref: ClassRef, spec: ResultSpec) -> tuple[str, list[str]]:
        assert spec.cls is not None
        path = self.names.classes[spec.cls.c_type]
        nullable = ref.form not in (
            ClassForm.VALUE,
            ClassForm.REFERENCE,
            ClassForm.CONST_REFERENCE,
        )
        self.before.append(f"let mut {L}object: *mut {path} = ::core::ptr::null_mut();")
        self.arguments["out_result"] = f"&mut {L}object"
        if is_borrowed(spec, ref):
            # The object belongs to C++, and to what it is part of: Rust borrows it from what the
            # call took it from (``self``, or the objects of a function).
            lifetime = f"{self.lifetime} " if self.lifetime else ""
            constant = is_constant(ref)
            public = f"&{lifetime}{path}" if constant else f"&{lifetime}mut {path}"
            view = "as_ref" if constant else "as_mut"
            if self.lifetime == "'static":
                # Nothing in Rust owns the object, and C++ may destroy it (any function can).
                self.lowering.unsafe = True
                self.lowering.remarks.append(
                    "Unsafe: the object belongs to nobody in particular, and C++ may destroy it: "
                    "the reference must not be used after that."
                    if constant
                    else "Unsafe: the object belongs to nobody in particular, C++ may destroy it, "
                    "and every call gives a `&mut` to it: do not keep two."
                )
            if nullable:
                return (
                    f"crate::Result<::core::option::Option<{public}>>",
                    [f"Ok(unsafe {{ {L}object.{view}() }})"],
                )
            return (
                f"crate::Result<{public}>",
                [f"unsafe {{ {L}object.{view}() }}.ok_or_else({RUNTIME}::null_object)"],
            )
        owned = f"crate::Owned<{path}>"
        if nullable:
            return (
                f"crate::Result<::core::option::Option<{owned}>>",
                [f"Ok(unsafe {{ crate::Owned::from_raw({L}object) }})"],
            )
        return f"crate::Result<{owned}>", [f"unsafe {{ {RUNTIME}::adopt({L}object) }}"]

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


__all__ = ["Lowering", "Names", "is_borrowed", "is_constant", "lower_callable", "public_type"]
