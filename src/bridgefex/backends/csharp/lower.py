# SPDX-License-Identifier: Apache-2.0
"""The C# side of a callable of the plan: its parameters, its call and its result."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ...model import (
    ClassForm,
    ClassRef,
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
from .mapping import scalar
from .naming import LOCAL_PREFIX, identifier
from .source import Source

L = LOCAL_PREFIX


class Unsupported(Exception):
    """The plan has something that the C# bindings do not have yet."""


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
) -> Lowering:
    """The C# method (or constructor) for ``plan``. ``declaring`` is the class that
    declares a method."""
    return _Lowerer(plan, names, module, declaring).run()


class _Lowerer:
    def __init__(
        self, plan: CallablePlan, names: Names, module: ModulePlan, declaring: ClassPlan | None
    ) -> None:
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
        for position, spec in enumerate(plan.params, start=1):
            self.param(spec, position)
        out = self.result()
        call_args = ", ".join(self.argument(param.name) for param in plan.function.params)
        call = f"Native.{plan.function.name}({call_args})"
        for name, local, lease in self.transfers:
            body.add(f"{local} = {lease}.Give();")
            body.add(f"{name}?.Surrender();")
        if self.retained:
            target = (
                "Retain"
                if plan.kind in (CallableKind.METHOD, CallableKind.CONSTRUCTOR)
                else "BridgefexObject.RetainForever"
            )
            for name in self.retained:
                body.add(f"{target}({name});")
        if out.checks_after:
            body.add(f"int {L}status = {call};")
            for line in out.after_call:
                body.add(line)
            body.add(f"Runtime.Check({L}status);")
        else:
            body.add(f"Runtime.Check({call});")
        for line in out.after_check:
            body.add(line)
        self.lowering.body = body.finish()
        return self.lowering

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
        name = identifier(spec.name)
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
            self.body.add(f"Native.{spec.struct.c_type} {L}s{position} = {name}.ToNative();")
            self.arguments[spec.name] = f"&{L}s{position}"
        elif isinstance(type_, ClassRef):
            self.object_param(spec, type_, position, name)
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
        self.arguments[spec.name] = f"({underlying.native}){name}"

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
        hold = f"Hold_{info.c_type}({mutable})" if info.is_base else f"Hold({mutable})"
        if nullable:
            body.add(f"using var {lease} = {name} is null ? default : {name}.{hold};")
        else:
            body.add(f"using var {lease} = {name}.{hold};")
        body.add(f"IntPtr {view} = {lease}.Pointer;")
        self.arguments[spec.name] = view
        if "transfer" in spec.words:
            self.transfers.append((name, view, lease))

    # -- results ----------------------------------------------------------------------------

    def result(self) -> _Result:
        plan = self.plan
        body = self.body
        out = _Result()
        if plan.kind is CallableKind.CONSTRUCTOR:
            body.add(f"IntPtr {L}result;")
            self.arguments["out_self"] = f"&{L}result"
            out.checks_after = True
            out.after_call = [f"Handle.Assign({L}result);"]
            return out
        spec = plan.result
        if spec is None:
            return out
        type_ = spec.type
        self.lowering.returns = public_type(type_, self.names, spec)
        returns = self.lowering.returns
        if isinstance(type_, ScalarType):
            info = scalar(type_.c_name)
            body.add(f"{info.native} {L}result;")
            self.arguments["out_result"] = f"&{L}result"
            value = info.from_native.replace("{}", f"{L}result") if info.converts else f"{L}result"
            out.after_check = [f"return {value};"]
        elif isinstance(type_, EnumRef):
            assert spec.enum is not None
            underlying = scalar(spec.enum.model.underlying.c_name)
            body.add(f"{underlying.native} {L}result;")
            self.arguments["out_result"] = f"&{L}result"
            out.after_check = [f"return ({returns}){L}result;"]
        elif isinstance(type_, StringType):
            self.string_result(type_, out)
        elif isinstance(type_, StructRef):
            assert spec.struct is not None
            body.add(f"Native.{spec.struct.c_type} {L}result;")
            self.arguments["out_result"] = f"&{L}result"
            out.after_check = [f"return {returns}.FromNative({L}result);"]
        elif isinstance(type_, ClassRef):
            self.object_result(spec, type_, out)
        else:
            raise Unsupported(
                f"the result of type '{type_.cpp_spelling}' is not supported in C# yet"
            )
        return out

    def string_result(self, type_: StringType, out: _Result) -> None:
        self.body.add(f"IntPtr {L}text;")
        self.body.add(f"nuint {L}size;")
        self.arguments["out_result"] = f"&{L}text"
        self.arguments["out_result_size"] = f"&{L}size"
        free = f"Native.{self.module.free_function}({L}text);"
        decode = [
            "try",
            "{",
            f"    return Runtime.TakeText({L}text, {L}size);",
            "}",
            "finally",
            "{",
            f"    {free}",
            "}",
        ]
        if type_.sized:
            out.after_check = decode
            self.lowering.returns = "string"
        else:
            # A const char* result: NULL is a null string.
            out.after_check = [
                f"if ({L}text == IntPtr.Zero)",
                "{",
                "    return null;",
                "}",
                *decode,
            ]
            self.lowering.returns = "string?"

    def object_result(self, spec: ResultSpec, ref: ClassRef, out: _Result) -> None:
        info = spec.cls
        assert info is not None
        class_name = self.names.cls(info)
        handle_name = self.names.handles[info.c_type]
        borrowed = ref.form in (ClassForm.REFERENCE, ClassForm.CONST_REFERENCE) or (
            spec.ownership == "borrowed"
        )
        nullable = ref.form is not ClassForm.VALUE
        self.body.add(f"IntPtr {L}result;")
        self.arguments["out_result"] = f"&{L}result"
        given = ["this"] if self.plan.kind is CallableKind.METHOD else self.owners
        owner_list = f"new BridgefexObject?[] {{ {', '.join(given)} }}" if given else "null"
        self.lowering.returns = class_name + ("?" if nullable else "")
        if borrowed:
            constant = ref.form in (ClassForm.CONST_REFERENCE, ClassForm.CONST_POINTER)
            out.after_check = [
                f"return {class_name}.Borrow({L}result, {owner_list}, "
                f"{'true' if constant else 'false'});"
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


__all__ = ["Lowering", "Names", "Unsupported", "lower_callable", "public_type"]
