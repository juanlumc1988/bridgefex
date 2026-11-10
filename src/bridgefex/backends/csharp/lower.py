# SPDX-License-Identifier: Apache-2.0
"""The C# side of a callable of the plan: its parameters, its call and its result."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ...model import (
    ClassForm,
    ClassRef,
    ContainerType,
    EnumRef,
    ScalarType,
    StringType,
    StructRef,
    Type,
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
from .mapping import Unsupported, scalar
from .naming import LOCAL_PREFIX, parameter
from .source import Source

L = LOCAL_PREFIX


@dataclass(frozen=True, slots=True)
class Names:
    """The C# names of the types of the module, by the C type."""

    root: str
    classes: Mapping[str, str]
    enums: Mapping[str, str]
    structs: Mapping[str, str]
    handles: Mapping[str, str]
    enum_plans: Mapping[str, EnumPlan] = field(default_factory=dict)
    """The enums and the structs by USR: a member of a struct names its type that way."""

    struct_plans: Mapping[str, StructPlan] = field(default_factory=dict)

    large_structs: frozenset[str] = frozenset()
    """The structs (by C type) whose mirror is too big for the stack."""

    def cls(self, info: ClassInfo) -> str:
        return self.classes[info.c_type]

    def enum(self, enum: EnumPlan) -> str:
        return self.enums[enum.c_type]

    def struct(self, struct: StructPlan) -> str:
        return self.structs[struct.c_type]


@dataclass(slots=True)
class Lowering:
    """What a callable becomes: the parameters of the C# method, and its body."""

    parameters: list[str] = field(default_factory=list)
    returns: str = "void"
    body: list[str] = field(default_factory=list)


def public_type(type_: Type, names: Names, spec: ParamSpec | ResultSpec | None = None) -> str:
    """The type that the C# API shows for a C++ type."""
    if isinstance(type_, ScalarType):
        return "void" if type_.is_void else scalar(type_.c_name).public
    nullable = isinstance(spec, ParamSpec) and "nullable" in spec.words
    if isinstance(type_, ContainerType) and spec is not None:
        if isinstance(spec, ParamSpec):
            return containers.public_param(spec, names)
        return containers.public_result(spec, names)
    if isinstance(type_, StringType):
        return "string?" if nullable else "string"
    if spec is None:
        raise Unsupported(f"the type '{type_.cpp_spelling}' is not supported in C# yet")
    if isinstance(type_, EnumRef) and spec.enum is not None:
        return names.enum(spec.enum)
    if isinstance(type_, StructRef) and spec.struct is not None:
        return names.struct(spec.struct)
    if isinstance(type_, ClassRef) and spec.cls is not None:
        return names.cls(spec.cls) + ("?" if nullable else "")
    raise Unsupported(f"the type '{type_.cpp_spelling}' is not supported in C# yet")


class _Body:
    """The statements of a body, with the blocks (``fixed``) that are open."""

    def __init__(self) -> None:
        self.source = Source()
        self.blocks = 0

    def add(self, line: str) -> None:
        self.source.line(line)

    def open(self, header: str) -> None:
        self.source.open(header)
        self.blocks += 1

    def finish(self) -> list[str]:
        while self.blocks:
            self.source.close()
            self.blocks -= 1
        return self.source.lines


@dataclass(slots=True)
class _Result:
    """What follows the call: statements before it, after it (with the status still
    unchecked) and after the check."""

    before_call: list[str] = field(default_factory=list)
    after_call: list[str] = field(default_factory=list)
    after_check: list[str] = field(default_factory=list)
    checks_after: bool = False


def lower_callable(
    plan: CallablePlan,
    names: Names,
    module: ModulePlan,
    *,
    declaring: ClassPlan | None = None,
    factory: tuple[str, str] | None = None,
) -> Lowering:
    """The C# method (or constructor) for ``plan``. ``declaring`` is the class that
    declares a method. ``factory`` (the class and its handle) lowers a constructor to a static
    method that returns the object."""
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
        factory: tuple[str, str] | None = None,
    ) -> None:
        self.factory = factory
        self.plan = plan
        self.names = names
        self.module = module
        self.declaring = declaring
        self.lowering = Lowering()
        self.body = _Body()
        self.arguments: dict[str, str] = {}
        self.transfers: list[tuple[str, str, str]] = []
        """(the parameter, the local with its pointer, the hold) of each object given to C++."""

        self.retained: list[str] = []
        self.owners: list[str] = []

    def run(self) -> Lowering:
        plan = self.plan
        body = self.body
        if plan.kind is CallableKind.METHOD:
            mutable = "false" if plan.is_const else "true"
            # A class that others derive from is reached through the address of its own part
            # of the object, which is not the address of the object with several bases.
            declaring = self.declaring
            hold = (
                f"Hold_{declaring.c_type}"
                if declaring is not None and declaring.is_base
                else "Hold"
            )
            body.add(f"using var {L}self = {hold}({mutable});")
            self.arguments["self"] = f"{L}self.Pointer"
        self.same_object_checks()
        for position, spec in enumerate(plan.params, start=1):
            self.param(spec, position)
        out = self.result()
        call_args = ", ".join(self.argument(param.name) for param in plan.function.params)
        call = f"Native.{plan.function.name}({call_args})"
        for name, local, lease in self.transfers:
            body.add(f"{local} = {lease}.Give();")
            body.add(f"{name}?.Surrender();")
        # What C++ may keep is retained before the call: a call that stores a pointer and then
        # throws has kept it all the same. A constructor that throws has kept nothing.
        target = (
            "Retain"
            if plan.kind in (CallableKind.METHOD, CallableKind.CONSTRUCTOR)
            else "BridgefexObject.RetainForever"
        )
        if plan.kind is not CallableKind.CONSTRUCTOR:
            for name in self.retained:
                body.add(f"{target}({name});")
        if out.checks_after:
            body.add(f"int {L}status = {call};")
            for line in out.after_call:
                body.add(line)
            body.add(f"Runtime.Check({L}status);")
        else:
            body.add(f"Runtime.Check({call});")
        if plan.kind is CallableKind.CONSTRUCTOR and self.factory is None:
            for name in self.retained:
                body.add(f"{target}({name});")
        if plan.kind is CallableKind.CONSTRUCTOR and self.factory is not None:
            # The object that is returned keeps what it was given.
            out.after_check = [
                f"var {L}new = new {self.factory[0]}({L}o);",
                *(f"{L}new.Retain({name});" for name in self.retained),
                f"return {L}new;",
            ]
        for line in out.after_check:
            body.add(line)
        self.lowering.body = body.finish()
        return self.lowering

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
                condition = f"object.ReferenceEquals({name}, {other})"
                if nullable:
                    condition = f"{name} is not null && {condition}"
                self.body.add(f"if ({condition})")
                self.body.add("{")
                self.body.add(
                    "    throw new global::System.ArgumentException("
                    '"the object is given to C++, which destroys it, and the call uses it too", '
                    f"nameof({name}));"
                )
                self.body.add("}")

    def argument(self, name: str) -> str:
        try:
            return self.arguments[name]
        except KeyError:
            raise Unsupported(
                f"the C parameter '{name}' of a function is not supported in C# yet"
            ) from None

    # -- parameters -------------------------------------------------------------------------

    def param(self, spec: ParamSpec, position: int) -> None:
        type_ = spec.type
        name = parameter(spec.name)
        self.lowering.parameters.append(f"{public_type(type_, self.names, spec)} {name}")
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
            self.arguments[spec.name] = (
                info.to_native.replace("{}", name) if info.converts else name
            )
        elif isinstance(type_, EnumRef):
            self.enum_param(spec, name)
        elif isinstance(type_, StringType):
            self.string_param(spec, position, name)
        elif isinstance(type_, StructRef):
            assert spec.struct is not None
            mirror = f"Native.{spec.struct.c_type}"
            local = f"{L}s{position}"
            if spec.struct.c_type in self.names.large_structs:
                self.body.add(f"using var {local} = Block<{mirror}>.Alloc();")
                self.body.add(f"{name}.ToNative({local}.Pointer);")
                self.arguments[spec.name] = f"{local}.Pointer"
            else:
                self.body.add(f"{mirror} {local} = default;")
                self.body.add(f"{name}.ToNative(&{local});")
                self.arguments[spec.name] = f"&{local}"
        elif isinstance(type_, ClassRef):
            self.object_param(spec, type_, position, name)
        elif isinstance(type_, ContainerType):
            site = containers.Site(self.body, self.arguments, self.names, self.module.free_function)
            containers.param(spec, type_, position, name, site)
        else:
            raise Unsupported(
                f"the parameter '{spec.name}' of type '{type_.cpp_spelling}' is not supported "
                "in C# yet"
            )

    def enum_param(self, spec: ParamSpec, name: str) -> None:
        assert spec.enum is not None
        enum = spec.enum
        valid = enum.model.valid_range
        underlying = scalar(enum.model.underlying.c_name)
        if valid is not None:
            self.body.add(
                f'Runtime.CheckEnum((long){name}, {valid[0]}, {valid[1]}, "{spec.name}");'
            )
        self.arguments[spec.name] = underlying.to_native.replace(
            "{}", f"({underlying.public}){name}"
        )

    def string_param(self, spec: ParamSpec, position: int, name: str) -> None:
        body = self.body
        if spec.size is None:
            # NUL-terminated: C++ would stop at a NUL of the text.
            if "nullable" in spec.words:
                body.add(
                    f"byte[]? {L}b{position} = {name} is null ? null "
                    f': Runtime.Utf8Z({name}, "{spec.name}");'
                )
            else:
                body.add(f'byte[] {L}b{position} = Runtime.Utf8Z({name}, "{spec.name}");')
            body.open(f"fixed (byte* {L}p{position} = {L}b{position})")
            self.arguments[spec.name] = f"{L}p{position}"
            return
        body.add(f'byte[] {L}b{position} = Runtime.Utf8({name}, "{spec.name}");')
        body.open(f"fixed (byte* {L}p{position} = {L}b{position})")
        self.arguments[spec.name] = f"{L}p{position}"
        self.arguments[spec.size] = f"(nuint){L}b{position}.Length"

    def object_param(self, spec: ParamSpec, ref: ClassRef, position: int, name: str) -> None:
        info = spec.cls
        assert info is not None
        body = self.body
        nullable = "nullable" in spec.words
        constant = ref.form in (
            ClassForm.VALUE,
            ClassForm.CONST_REFERENCE,
            ClassForm.CONST_POINTER,
        )
        mutable = "false" if constant else "true"
        lease, view = f"{L}t{position}", f"{L}h{position}"
        # A call that gives the object also needs it for itself alone (see Hold).
        arguments = f"{mutable}, true" if "transfer" in spec.words else mutable
        hold = f"Hold_{info.c_type}({arguments})" if info.is_base else f"Hold({arguments})"
        if not nullable:
            body.add(f"global::System.ArgumentNullException.ThrowIfNull({name});")
        if "transfer" in spec.words and info.is_base and not info.model.virtual_destructor:
            # C++ destroys the object through this class: only an object of exactly it.
            check = f'Runtime.CheckExact({name}, typeof({self.names.cls(info)}), "{spec.name}");'
            body.add(f"if ({name} is not null) {check}" if nullable else check)
        if nullable:
            body.add(f"using var {lease} = {name} is null ? default : {name}.{hold};")
        else:
            body.add(f"using var {lease} = {name}.{hold};")
        body.add(f"nint {view} = {lease}.Pointer;")
        self.arguments[spec.name] = view
        if "transfer" in spec.words:
            self.transfers.append((name, view, lease))

    # -- results ----------------------------------------------------------------------------

    def result(self) -> _Result:
        plan = self.plan
        body = self.body
        out = _Result()
        if plan.kind is CallableKind.CONSTRUCTOR:
            body.add(f"nint {L}result;")
            self.arguments["out_self"] = f"&{L}result"
            out.checks_after = True
            if self.factory is None:
                out.after_call = [f"Handle.Assign({L}result);"]
            else:
                # A static method: the object is made after the call, and owns the handle.
                class_name, handle_name = self.factory
                body.add(f"var {L}o = new {handle_name}();")
                out.after_call = [f"{L}o.Assign({L}result);"]
                out.after_check = [f"return new {class_name}({L}o);"]
                self.lowering.returns = class_name
            return out
        spec = plan.result
        if spec is None:
            return out
        type_ = spec.type
        self.lowering.returns = public_type(type_, self.names, spec)
        if isinstance(type_, ScalarType):
            self.scalar_result(type_, out)
        elif isinstance(type_, EnumRef):
            self.enum_result(spec, out)
        elif isinstance(type_, StringType):
            self.string_result(type_, spec.as_bytes, out)
        elif isinstance(type_, StructRef):
            self.struct_result(spec, out)
        elif isinstance(type_, ClassRef):
            self.object_result(spec, type_, out)
        elif isinstance(type_, ContainerType):
            site = containers.Site(self.body, self.arguments, self.names, self.module.free_function)
            out.after_check = containers.result(spec, type_, site)
        else:
            raise Unsupported(
                f"the result of type '{type_.cpp_spelling}' is not supported in C# yet"
            )
        return out

    def scalar_result(self, type_: ScalarType, out: _Result) -> None:
        info = scalar(type_.c_name)
        self.body.add(f"{info.native} {L}result;")
        self.arguments["out_result"] = f"&{L}result"
        value = info.from_native.replace("{}", f"{L}result") if info.converts else f"{L}result"
        out.after_check = [f"return {value};"]

    def enum_result(self, spec: ResultSpec, out: _Result) -> None:
        assert spec.enum is not None
        underlying = scalar(spec.enum.model.underlying.c_name)
        self.body.add(f"{underlying.native} {L}result;")
        self.arguments["out_result"] = f"&{L}result"
        raw = underlying.from_native.replace("{}", f"{L}result")
        out.after_check = [f"return ({self.lowering.returns}){raw};"]

    def struct_result(self, spec: ResultSpec, out: _Result) -> None:
        assert spec.struct is not None
        returns = self.lowering.returns
        mirror = f"Native.{spec.struct.c_type}"
        if spec.struct.c_type in self.names.large_structs:
            self.body.add(f"using var {L}result = Block<{mirror}>.Alloc();")
            self.arguments["out_result"] = f"{L}result.Pointer"
            out.after_check = [f"return {returns}.FromNative({L}result.Pointer);"]
        else:
            self.body.add(f"{mirror} {L}result;")
            self.arguments["out_result"] = f"&{L}result"
            out.after_check = [f"return {returns}.FromNative(&{L}result);"]

    def string_result(self, type_: StringType, as_bytes: bool, out: _Result) -> None:
        self.body.add(f"nint {L}text;")
        self.body.add(f"nuint {L}size;")
        self.arguments["out_result"] = f"&{L}text"
        self.arguments["out_result_size"] = f"&{L}size"
        free = f"Native.{self.module.free_function}({L}text);"
        take = "TakeBytes" if as_bytes else "TakeText"
        decode = [
            "try",
            "{",
            f"    return Runtime.{take}({L}text, {L}size);",
            "}",
            "finally",
            "{",
            f"    {free}",
            "}",
        ]
        public = "byte[]" if as_bytes else "string"
        if type_.sized:
            out.after_check = decode
            self.lowering.returns = public
        else:
            # A const char* result: NULL is a null string.
            out.after_check = [
                f"if ({L}text == 0)",
                "{",
                "    return null;",
                "}",
                *decode,
            ]
            self.lowering.returns = public + "?"

    def object_result(self, spec: ResultSpec, ref: ClassRef, out: _Result) -> None:
        info = spec.cls
        assert info is not None
        class_name = self.names.cls(info)
        handle_name = self.names.handles[info.c_type]
        borrowed = ref.form in (ClassForm.REFERENCE, ClassForm.CONST_REFERENCE) or (
            spec.ownership == "borrowed"
        )
        # A reference and a copy are never NULL; a pointer and a std::unique_ptr may be.
        nullable = ref.form not in (
            ClassForm.VALUE,
            ClassForm.REFERENCE,
            ClassForm.CONST_REFERENCE,
        )
        self.body.add(f"nint {L}result;")
        self.arguments["out_result"] = f"&{L}result"
        given = ["this"] if self.plan.kind is CallableKind.METHOD else self.owners
        owner_list = f"new BridgefexObject?[] {{ {', '.join(given)} }}" if given else "null"
        self.lowering.returns = class_name + ("?" if nullable else "")
        if borrowed:
            constant = ref.form in (ClassForm.CONST_REFERENCE, ClassForm.CONST_POINTER)
            bang = "" if nullable else "!"
            out.after_check = [
                f"return {class_name}.Borrow({L}result, {owner_list}, "
                f"{'true' if constant else 'false'}){bang};"
            ]
            return
        # The handle that will own the object exists before the call: from the moment the
        # C function writes the pointer, its destruction is guaranteed.
        self.body.add(f"var {L}o = new {handle_name}();")
        out.checks_after = True
        out.after_call = [f"{L}o.Assign({L}result);"]
        if nullable:
            out.after_check = [f"return {L}o.IsInvalid ? null : new {class_name}({L}o);"]
        else:
            out.after_check = [f"return new {class_name}({L}o);"]


__all__ = ["Lowering", "Names", "lower_callable", "public_type"]
