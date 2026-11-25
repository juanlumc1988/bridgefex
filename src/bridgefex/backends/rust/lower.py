# SPDX-License-Identifier: Apache-2.0
"""The Rust side of a callable of the plan: its parameters, its call and its result."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ...model import (
    CallbackType,
    ClassForm,
    ClassRef,
    ContainerKind,
    ContainerType,
    EnumRef,
    ScalarType,
    StringType,
    StructRef,
    Type,
)
from ...naming import unique_name
from ...plan import (
    CallableKind,
    CallablePlan,
    CallbackPart,
    CallbackSpec,
    ClassPlan,
    ElementSpec,
    EnumPlan,
    ModulePlan,
    ParamSpec,
    ResultSpec,
    StructPlan,
)
from .mapping import TEXT, Unsupported, function_pointer, function_types, scalar
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

    @property
    def raw_enums(self) -> dict[str, str]:
        """The Rust type of the integer of every enum, by the C type (which is that integer)."""
        return {
            item.c_type: scalar(item.model.underlying.c_name) for item in self.enum_plans.values()
        }


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
            # A const char* that C++ keeps is the caller's C string: a text that is converted
            # for the call would be gone when it returns.
            text = "&::core::ffi::CStr" if kept_text(spec) else "&str"
            return f"::core::option::Option<{text}>" if nullable else text
        text = "::std::vec::Vec<u8>" if spec.as_bytes else "::std::string::String"
        return text if type_.sized else f"::core::option::Option<{text}>"
    if isinstance(type_, EnumRef) and spec.enum is not None:
        return names.enums[spec.enum.c_type]
    if isinstance(type_, StructRef) and spec.struct is not None:
        return names.structs[spec.struct.c_type]
    if isinstance(type_, ContainerType):
        return container_type(type_, names, spec)
    if isinstance(type_, CallbackType) and isinstance(spec, ParamSpec) and spec.callback:
        closure = f"impl {closure_type(spec.callback, names)}"
        return f"::core::option::Option<{closure}>" if nullable else closure
    raise Unsupported(f"the type '{type_.cpp_spelling}' is not supported in Rust yet")


def closure_type(callback: CallbackSpec, names: Names) -> str:
    """The closure that a ``std::function`` is: ``Fn(A) -> R`` that may be called by C++ on any
    thread, for as long as it holds it (and it may be for ever)."""
    arguments = ", ".join(_callback_argument(part, names) for part in callback.arguments)
    result = callback.result.type
    if isinstance(result, ScalarType) and result.is_void:
        returns = ""
    elif isinstance(result, ScalarType):
        returns = f" -> {scalar(result.c_name)}"
    elif isinstance(result, EnumRef) and callback.result.enum is not None:
        returns = f" -> {names.enums[callback.result.enum.c_type]}"
    else:
        raise Unsupported(f"a callback that gives '{result.cpp_spelling}'")
    return (
        f"::core::ops::Fn({arguments}){returns} + ::core::marker::Send + ::core::marker::Sync "
        "+ 'static"
    )


def _callback_argument(part: CallbackPart, names: Names) -> str:
    """The type of an argument of a closure: what C++ gives, as Rust shows it (a struct and a
    string are borrowed for the call)."""
    type_ = part.type
    if isinstance(type_, ScalarType) and not type_.is_void:
        return scalar(type_.c_name)
    if isinstance(type_, EnumRef) and part.enum is not None:
        return names.enums[part.enum.c_type]
    if isinstance(type_, StructRef) and part.struct is not None:
        return f"&{names.structs[part.struct.c_type]}"
    if isinstance(type_, StringType):
        return "&str" if type_.sized else "::core::option::Option<&str>"
    raise Unsupported(f"a callback that takes '{type_.cpp_spelling}'")


def element_type(element: ElementSpec | None, names: Names, text: str) -> str:
    """The Rust type of an element of a container (``text``: the type that a string has)."""
    if element is None:
        raise Unsupported("a container without its element type")
    type_ = element.type
    if isinstance(type_, ScalarType):
        return scalar(type_.c_name)
    if isinstance(type_, StringType):
        return text
    if isinstance(type_, EnumRef) and element.enum is not None:
        return names.enums[element.enum.c_type]
    if isinstance(type_, StructRef) and element.struct is not None:
        return names.structs[element.struct.c_type]
    raise Unsupported(
        f"the type '{type_.cpp_spelling}' as the element of a container is not supported in Rust"
    )


def container_type(type_: ContainerType, names: Names, spec: ParamSpec | ResultSpec) -> str:
    """The type that the Rust API shows for a ``std::vector``, a ``std::array`` or a
    ``std::span``: a slice (or a reference to an array, whose length is the one of the C++ type)
    that a function takes, and a ``Vec`` (or an array) that it gives."""
    if isinstance(spec, ParamSpec):
        item = element_type(spec.element, names, "&str")
        return f"&[{item}; {type_.length}]" if type_.kind is ContainerKind.ARRAY else f"&[{item}]"
    item = element_type(spec.element, names, "::std::string::String")
    if type_.kind is ContainerKind.ARRAY:
        return f"[{item}; {type_.length}]"
    return f"::std::vec::Vec<{item}>"


def kept_text(spec: ParamSpec) -> bool:
    """A ``const char*`` parameter that C++ keeps a pointer to (the annotation ``retain``)."""
    return isinstance(spec.type, StringType) and spec.size is None and "retain" in spec.words


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
        self.items: list[str] = []
        """What the body declares before it starts: the functions that C++ calls for a closure."""

        self.adopted: list[str] = []
        """What takes the object that a constructor or a function made, right after the call: a
        panic of a closure, which the call raises next, drops it (and C++ destroys it) instead of
        losing it."""

        self.late: list[str] = []
        """What runs right before the call, once nothing can fail any more: the closures are given
        away there (the C layer releases them, also when the call fails, and not before)."""

        self.scope = ""
        """The local that holds the call, if it gives closures: it gets their panics."""

        self.callbacks = 0
        """The closures that the call gives (to name the types that it declares for them)."""

    def run(self) -> Lowering:
        plan = self.plan
        lowering = self.lowering
        self.lifetime = self.borrowed_lifetime()
        if self.lifetime == "'a":
            lowering.generics = "<'a>"
        if plan.kind is CallableKind.METHOD:
            lowering.receiver = self.receiver()
            self.arguments["self"] = "self"
            if plan.retains_this:
                self.keeps_this()
        elif plan.kind in (CallableKind.DIRECTOR_CONSTRUCTOR, CallableKind.DIRECTOR_BASE):
            raise Unsupported("directors are not supported in Rust yet")
        for spec in plan.params:
            self.param(spec)
        returns, after = self.result()
        arguments = [self.argument(param.name) for param in plan.function.params]
        lowering.returns = returns
        settle = []
        if self.scope:
            # A panic of a closure is raised by the call that gave it, when C++ has returned.
            settle = [
                f"if let Some({L}panic) = {self.scope}.finish() {{",
                f"    ::std::panic::resume_unwind({L}panic);",
                "}",
            ]
        lowering.body = [
            *self.items,
            f"{RUNTIME}::ensure()?;",
            *self.before,
            *self.late,
            *_call(f"let {L}status = ", f"{NATIVE}::{plan.function.name}", arguments),
            *self.adopted,
            *settle,
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
        elif isinstance(type_, ContainerType):
            self.container_param(spec, type_, name)
        elif isinstance(type_, CallbackType):
            self.callback_param(spec, name)
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

    def keeps_this(self) -> None:
        """What the method gives, or the library, keeps a pointer to ``self``."""
        self.lowering.unsafe = True
        if self.gives_object():
            self.lowering.remarks.append(
                "Unsafe: the object that it returns keeps a pointer to `self`, which must stay "
                "valid (not dropped or moved) for as long as that object is used."
            )
        else:
            self.lowering.remarks.append(
                "Unsafe: C++ keeps a pointer to `self` for as long as the program runs: it must "
                "not be dropped or moved, so give it up for good with `Owned::leak`."
            )

    def holder(self) -> str:
        """Who keeps what C++ keeps a pointer to, as the documentation says it."""
        if self.plan.kind is CallableKind.CONSTRUCTOR:
            return "the new object"
        if self.plan.kind is CallableKind.METHOD:
            return "`self`"
        return "the object that it returns" if self.gives_object() else "the program"

    def keeps(self, name: str, *, text: bool = False) -> None:
        """C++ keeps a pointer to a parameter: Rust cannot see until when, so the caller says it
        (the function is ``unsafe``)."""
        self.lowering.unsafe = True
        rest = "and must not change" if text else "and nobody else may use it meanwhile"
        self.lowering.remarks.append(
            f"Unsafe: C++ keeps a pointer to `{name}`, which must stay valid for as long as "
            f"{self.holder()} uses it, {rest}."
        )

    def object_param(self, spec: ParamSpec, ref: ClassRef, name: str) -> None:
        nullable = "nullable" in spec.words
        if "retain" in spec.words and "transfer" not in spec.words:
            self.keeps(spec.name)
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

    def callback_param(self, spec: ParamSpec, name: str) -> None:
        """A closure goes to C++ as a function, a ``user_data`` that is the closure and the
        function that releases it. C++ may keep it and call it from any thread, which is why a
        closure is ``Send + Sync + 'static``."""
        callback = spec.callback
        assert callback is not None
        assert spec.user_data is not None
        assert spec.release is not None
        param = next(item for item in self.plan.function.params if item.name == spec.name)
        types = function_types(param, self.names.types, self.names.raw_enums)
        pointer = function_pointer(param, self.names.types, self.names.raw_enums)
        base = name.lstrip("_")
        register = unique_name(f"{L}register_{base}", frozenset(self.taken))
        self.taken.add(register)
        local = unique_name(f"{L}cb_{base}", frozenset(self.taken))
        self.taken.add(local)
        bound = f"{closure_type(callback, self.names)}"
        self.items.extend(self.trampoline(register, callback, types, pointer, bound))
        self.callbacks += 1
        if not self.scope:
            self.scope = f"{L}scope"
            self.late.append(f"let {self.scope} = {RUNTIME}::Scope::new();")
        if "nullable" in spec.words:
            self.late.append(
                f"let {local} = match {name} {{ ::core::option::Option::Some({name}) => "
                f"{register}({name}, &{self.scope}), ::core::option::Option::None => "
                f"{RUNTIME}::Registered::none() }};"
            )
        else:
            self.late.append(f"let {local} = {register}({name}, &{self.scope});")
        self.arguments[spec.name] = f"{local}.call"
        self.arguments[spec.user_data] = f"{local}.data"
        self.arguments[spec.release] = f"{local}.release"
        self.lowering.remarks.append(
            f"`{spec.name}`: C++ may call it on any thread, for as long as it holds it (also after "
            "this function returns). A panic of the closure is caught, and this call raises it "
            "again when C++ returns (one that no call is waiting for is dropped)."
            + (
                " `None` is an empty `std::function`: C++ that calls it fails with "
                "`ErrorKind::Exception`."
                if "nullable" in spec.words
                else ""
            )
        )

    def trampoline(
        self,
        register: str,
        callback: CallbackSpec,
        types: list[str],
        pointer: str,
        bound: str,
    ) -> list[str]:
        """The functions that give a closure to C++: the one that C++ calls, which converts what
        C++ gives and runs the closure, and the registration of both."""
        parameters = [f"data: {types[0]}"]
        arguments: list[str] = []
        locals_: list[str] = []
        position = 1
        for index, part in enumerate(callback.arguments):
            type_ = part.type
            name = f"{L}a{index}"
            if isinstance(type_, StringType):
                if type_.sized:
                    parameters += [
                        f"{name}: {types[position]}",
                        f"{name}_size: {types[position + 1]}",
                    ]
                    locals_.append(
                        f"let {name}_text = unsafe {{ {RUNTIME}::text({name}, {name}_size) }};"
                    )
                    arguments.append(f"&*{name}_text")
                    position += 2
                else:
                    parameters.append(f"{name}: {types[position]}")
                    locals_.append(f"let {name}_text = unsafe {{ {RUNTIME}::c_text({name}) }};")
                    arguments.append(f"{name}_text.as_deref()")
                    position += 1
                continue
            parameters.append(f"{name}: {types[position]}")
            position += 1
            if isinstance(type_, EnumRef):
                assert part.enum is not None
                arguments.append(f"{self.names.enums[part.enum.c_type]}({name})")
            elif isinstance(type_, StructRef):
                arguments.append(f"unsafe {{ &*{name} }}")
            else:
                arguments.append(name)
        result = callback.result.type
        gives = not (isinstance(result, ScalarType) and result.is_void)
        if gives:
            parameters.append(f"out: {types[position]}")
        call = f"function({', '.join(arguments)})"
        valid = callback.result.enum.model.valid_range if callback.result.enum else None
        if valid is None:
            inner = [*(f"    {line}" for line in locals_), f"    {call}"]
        else:
            # An enum without a fixed type: a value that C++ cannot hold is a failure of the
            # closure (as a panic is, and raised as one), not a value that C++ gets.
            inner = [
                *(f"    {line}" for line in locals_),
                f"    let value = {call};",
                f"    if !({valid[0]}..={valid[1]}).contains(&(value.0 as i128)) {{",
                "        ::core::panic!(",
                f'            "a callback returned {{}}: the enum has {valid[0]}..{valid[1]}",',
                "            value.0,",
                "        );",
                "    }",
                "    value",
            ]
        if gives:
            value = "value.0" if isinstance(result, EnumRef) else "value"
            body = [
                "if let ::core::option::Option::Some(value) = registration.run(|function| {",
                *inner,
                "}) {",
                f"    unsafe {{ *out = {value} }};",
                "}",
            ]
        else:
            body = ["registration.run(|function| {", *inner, "});"]
        alias = f"{L}Callback{self.callbacks}"
        where = [f"where F: {bound},"]
        if len(where[0]) + 8 > 100:
            first, *rest = bound.split(" + ")
            where = ["where", f"    F: {first}", *(f"        + {item}" for item in rest)]
            where[-1] += ","
        return [
            f'type {alias} = unsafe extern "C" fn(',
            *(f"    {type_}," for type_ in types),
            ");",
            f"fn {register}<F>(",
            "    function: F,",
            f"    scope: &::std::sync::Arc<{RUNTIME}::Scope>,",
            f") -> {RUNTIME}::Registered<{alias}>",
            *where,
            "{",
            f"    use {RUNTIME}::Registration;",
            '    unsafe extern "C" fn call<F>(',
            *(f"        {parameter}," for parameter in parameters),
            "    )",
            *(f"    {line}" for line in where),
            "    {",
            "        let registration = unsafe { Registration::<F>::from_raw(data) };",
            *(f"        {line}" for line in body),
            "    }",
            f"    {RUNTIME}::Registered {{",
            "        call: ::core::option::Option::Some(call::<F>),",
            f"        data: {RUNTIME}::Registration::into_raw(function, scope),",
            f"        release: ::core::option::Option::Some({RUNTIME}::release::<F>),",
            "    }",
            "}",
        ]

    def container_param(self, spec: ParamSpec, type_: ContainerType, name: str) -> None:
        """A slice (or an array) goes to C++ as it is, where it is: a pointer, and the number of
        its elements for a vector or a span. The elements that are not C types themselves get
        what C needs first: an enum is its integer (``#[repr(transparent)]``), a string the
        pair of its bytes and their number."""
        element = spec.element
        assert element is not None
        if "retain" in spec.words:
            # C++ keeps the pointer of the slice: Rust cannot see until when.
            self.keeps(spec.name, text=True)
        if type_.kind is not ContainerKind.ARRAY:
            if spec.size is None:
                raise AssertionError(f"the container '{spec.name}' has no size parameter")
            self.arguments[spec.size] = f"{name}.len()"
        if isinstance(element.type, StringType):
            # No NUL check: the size of each string is passed, and a NUL is text.
            local = unique_name(f"{L}t_{name.lstrip('_')}", frozenset(self.taken))
            self.taken.add(local)
            self.before.append(f"let {local} = {RUNTIME}::NativeText::all({name});")
            self.arguments[spec.name] = f"{local}.as_ptr()"
        elif isinstance(element.type, EnumRef):
            assert element.enum is not None
            valid = element.enum.model.valid_range
            if valid is not None:
                # An enum without a fixed type: see enum_param.
                self.before.append(
                    f"{RUNTIME}::check_enums({name}.iter().map(|item| item.0 as i128), "
                    f'{valid[0]}, {valid[1]}, "{spec.name}")?;'
                )
            self.arguments[spec.name] = f"{name}.as_ptr().cast()"
        else:
            self.arguments[spec.name] = f"{name}.as_ptr()"

    def string_param(self, spec: ParamSpec, name: str) -> None:
        nullable = "nullable" in spec.words
        if "retain" in spec.words:
            self.keeps(spec.name, text=True)
        if spec.size is not None:
            # A std::string or a std::string_view: the bytes and their number; NUL is text.
            self.arguments[spec.name] = f"{name}.as_ptr().cast()"
            self.arguments[spec.size] = f"{name}.len()"
            return
        if kept_text(spec):
            # The caller's C string itself, which it keeps valid.
            if nullable:
                self.arguments[spec.name] = (
                    f"{name}.map_or(::core::ptr::null(), |text| text.as_ptr())"
                )
            else:
                self.arguments[spec.name] = f"{name}.as_ptr()"
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
            self.adopted.append(f"let {L}owned = unsafe {{ crate::Owned::from_raw({L}object) }};")
            return (
                "crate::Result<crate::Owned<Self>>",
                [f"{L}owned.ok_or_else({RUNTIME}::null_object)"],
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
        if isinstance(type_, ContainerType):
            return self.container_result(type_, spec, public)
        raise Unsupported(f"the result of type '{type_.cpp_spelling}' is not supported in Rust yet")

    def container_result(
        self, type_: ContainerType, spec: ResultSpec, public: str
    ) -> tuple[str, list[str]]:
        """A ``Vec`` (or an array) of copies: the elements that C++ wrote are read, and what C++
        allocated for them is released."""
        element = spec.element
        assert element is not None
        raw = self.raw_element(element)
        convert = ""
        if isinstance(element.type, EnumRef):
            assert element.enum is not None
            convert = self.names.enums[element.enum.c_type]
        if type_.kind is ContainerKind.ARRAY:
            # The room is Rust's: C++ writes every element when it succeeds.
            zero = (
                f"{raw}::zeroed()"
                if isinstance(element.type, StructRef)
                else ("::core::default::Default::default()")
            )
            length = type_.length
            self.before.append(f"let mut {L}result: [{raw}; {length}] = [{zero}; {length}];")
            self.arguments["out_result"] = f"{L}result.as_mut_ptr()"
            value = f"{L}result.map({convert})" if convert else f"{L}result"
            return f"crate::Result<{public}>", [f"Ok({value})"]
        self.before.append(f"let mut {L}result = {RUNTIME}::Array::<{raw}>::new();")
        self.arguments["out_result"] = f"{L}result.data()"
        self.arguments["out_result_size"] = f"{L}result.size()"
        items = f"{L}result.items()"
        if isinstance(element.type, StringType):
            value = f"{items}.iter().map(|text| unsafe {{ text.lossy() }}).collect()"
        elif convert:
            value = f"{items}.iter().map(|&raw| {convert}(raw)).collect()"
        else:
            value = f"{items}.to_vec()"
        return f"crate::Result<{public}>", [f"Ok({value})"]

    def raw_element(self, element: ElementSpec) -> str:
        """The type that an element has in the C API: what C++ writes in a result."""
        type_ = element.type
        if isinstance(type_, EnumRef):
            assert element.enum is not None
            return scalar(element.enum.model.underlying.c_name)
        if isinstance(type_, StringType):
            return TEXT
        return element_type(element, self.names, TEXT)

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
        self.adopted.append(f"let {L}owned = unsafe {{ crate::Owned::from_raw({L}object) }};")
        if nullable:
            return (
                f"crate::Result<::core::option::Option<{owned}>>",
                [f"Ok({L}owned)"],
            )
        return f"crate::Result<{owned}>", [f"{L}owned.ok_or_else({RUNTIME}::null_object)"]

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
