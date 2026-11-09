# SPDX-License-Identifier: Apache-2.0
"""The C# side of a callable of the plan: its parameters, its call and its result."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ...errors import Diagnostic, GenerationError
from ...model import (
    ClassForm,
    ClassRef,
    EnumRef,
    ScalarType,
    StringKind,
    StringType,
    StructRef,
    Type,
)
from ...plan import (
    CallableKind,
    CallablePlan,
    ClassInfo,
    CParam,
    EnumPlan,
    FfiKind,
    ModulePlan,
    ParamSpec,
    ResultSpec,
    Role,
    StructPlan,
)
from .naming import LOCAL_PREFIX, identifier, pascal
from .source import Source
from .mapping import native_type, scalar

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


class _Body:
    """The statements of a body, with the blocks (``fixed``) that are open."""

    def __init__(self) -> None:
        self.source = Source()
        self.source._depth = 0
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


def _ffi_of(function_params: tuple[CParam, ...], name: str) -> CParam:
    for param in function_params:
        if param.name == name:
            return param
    raise KeyError(name)


def scalar_public(type_: ScalarType) -> str:
    return scalar(type_.c_name).public


def public_type(type_: Type, names: Names, spec: ParamSpec | ResultSpec | None = None) -> str:
    """The type that the C# API shows for a C++ type."""
    if isinstance(type_, ScalarType):
        return "void" if type_.is_void else scalar_public(type_)
    if isinstance(type_, StringType):
        return "string"
    if isinstance(type_, EnumRef):
        assert spec is not None and spec.enum is not None
        return names.enum(spec.enum)
    if isinstance(type_, StructRef):
        assert spec is not None and spec.struct is not None
        return names.struct(spec.struct)
    if isinstance(type_, ClassRef):
        assert spec is not None and spec.cls is not None
        return names.cls(spec.cls)
    raise Unsupported(f"the type '{type_.cpp_spelling}' is not supported in C# yet")


def lower_callable(
    plan: CallablePlan, names: Names, module: ModulePlan, *, owner: str | None = None
) -> Lowering:
    """The C# method (or constructor, with ``owner`` the class it constructs) for ``plan``."""
    function = plan.function
    result = Lowering()
    body = _Body()
    arguments: dict[str, str] = {}
    transfers: list[tuple[str, str, str]] = []
    retained: list[str] = []
    owners: list[str] = []

    kind = plan.kind
    if kind is CallableKind.METHOD:
        mutable = "false" if plan.is_const else "true"
        body.add(f"using var {L}self = Hold({mutable});")
        arguments["self"] = f"{L}self.Pointer"

    for position, spec in enumerate(plan.params, start=1):
        result.parameters.append(f"{public_type(spec.type, names, spec)} {identifier(spec.name)}")
        _param(spec, position, names, body, arguments, transfers, function.params)
        if "retain" in spec.words:
            retained.append(identifier(spec.name))
        if (
            isinstance(spec.type, ClassRef)
            and spec.type.form is not ClassForm.VALUE
            and "transfer" not in spec.words
        ):
            owners.append(identifier(spec.name))

    ret_lines = _result(plan, names, module, body, arguments, result, owners)

    call_args = ", ".join(_argument(param, arguments) for param in function.params)
    call = f"Native.{function.name}({call_args})"
    for line in ret_lines.before_call:
        body.add(line)
    for name, local, lease in transfers:
        body.add(f"{local} = {lease}.Give();")
        body.add(f"{name}?.Surrender();")
    if retained:
        target = {
            CallableKind.METHOD: "Retain",
            CallableKind.CONSTRUCTOR: "Retain",
        }.get(kind, "RetainForever")
        for name in retained:
            body.add(f"{target}({name});")
    if ret_lines.checks_after:
        body.add(f"int {L}status = {call};")
        for line in ret_lines.after_call:
            body.add(line)
        body.add(f"Runtime.Check({L}status);")
    else:
        body.add(f"Runtime.Check({call});")
    for line in ret_lines.after_check:
        body.add(line)
    result.body = body.finish()
    return result


@dataclass(slots=True)
class _Result:
    before_call: list[str] = field(default_factory=list)
    after_call: list[str] = field(default_factory=list)
    after_check: list[str] = field(default_factory=list)
    checks_after: bool = False


def _argument(param: CParam, arguments: Mapping[str, str]) -> str:
    try:
        return arguments[param.name]
    except KeyError:
        raise Unsupported(
            f"the C parameter '{param.name}' of a function is not supported yet"
        ) from None


def _param(
    spec: ParamSpec,
    position: int,
    names: Names,
    body: _Body,
    arguments: dict[str, str],
    transfers: list[tuple[str, str, str]],
    c_params: tuple[CParam, ...],
) -> None:
    type_ = spec.type
    name = identifier(spec.name)
    if isinstance(type_, ScalarType):
        info = scalar(type_.c_name)
        arguments[spec.name] = info.to_native.replace("{}", name) if info.converts else name
        return
    if isinstance(type_, EnumRef):
        assert spec.enum is not None
        enum = spec.enum
        valid = enum.model.valid_range
        underlying = scalar(enum.model.underlying.c_name)
        if valid is not None:
            body.add(f'Runtime.CheckEnum((long){name}, {valid[0]}, {valid[1]}, "{spec.name}");')
        arguments[spec.name] = f"({underlying.native}){name}"
        return
    if isinstance(type_, StringType):
        _string_param(spec, position, body, arguments)
        return
    if isinstance(type_, ClassRef):
        _object_param(spec, type_, position, names, body, arguments, transfers)
        return
    raise Unsupported(
        f"the parameter '{spec.name}' of type '{type_.cpp_spelling}' is not supported in C# yet"
    )


def _string_param(spec: ParamSpec, position: int, body: _Body, arguments: dict[str, str]) -> None:
    name = identifier(spec.name)
    nullable = "nullable" in spec.words
    assert isinstance(spec.type, StringType)
    if spec.size is None:
        # NUL-terminated: C++ would stop at a NUL of the text.
        body.add(f'byte[]? {L}b{position} = Runtime.Utf8Z({name}, "{spec.name}");')
        body.open(f"fixed (byte* {L}p{position} = {L}b{position})")
        arguments[spec.name] = f"{L}p{position}"
        return
    body.add(f'byte[] {L}b{position} = Runtime.Utf8({name}, "{spec.name}");')
    body.open(f"fixed (byte* {L}p{position} = {L}b{position})")
    arguments[spec.name] = f"{L}p{position}"
    arguments[spec.size] = f"(nuint){L}b{position}.Length"
    del nullable


def _object_param(
    spec: ParamSpec,
    ref: ClassRef,
    position: int,
    names: Names,
    body: _Body,
    arguments: dict[str, str],
    transfers: list[tuple[str, str, str]],
) -> None:
    name = identifier(spec.name)
    info = spec.cls
    assert info is not None
    nullable = "nullable" in spec.words
    constant = ref.form in (ClassForm.VALUE, ClassForm.CONST_REFERENCE, ClassForm.CONST_POINTER)
    mutable = "false" if constant else "true"
    taken = "transfer" in spec.words
    view = f"{L}h{position}"
    hold = f"Hold_{info.c_type}({mutable})" if info.is_base else f"Hold({mutable})"
    if nullable:
        body.add(f"IntPtr {view} = IntPtr.Zero;")
        body.add(f"using var {L}t{position} = {name} is null ? default : {name}.{hold};")
        body.add(f"{view} = {L}t{position}.Pointer;")
        arguments[spec.name] = view
        if taken:
            transfers.append((name, view, f"{L}t{position}"))
        return
    body.add(f"using var {L}t{position} = {name}.{hold};")
    body.add(f"IntPtr {view} = {L}t{position}.Pointer;")
    arguments[spec.name] = view
    if taken:
        transfers.append((name, view, f"{L}t{position}"))


def _result(
    plan: CallablePlan,
    names: Names,
    module: ModulePlan,
    body: _Body,
    arguments: dict[str, str],
    lowering: Lowering,
    owners: list[str],
) -> _Result:
    out = _Result()
    spec = plan.result
    function = plan.function
    kind = plan.kind
    if kind is CallableKind.CONSTRUCTOR:
        body.add(f"IntPtr {L}result;")
        arguments["out_self"] = f"&{L}result"
        out.checks_after = True
        out.after_call = [f"Handle.Assign({L}result);"]
        return out
    if spec is None:
        return out
    type_ = spec.type
    lowering.returns = public_type(type_, names, spec)
    if isinstance(type_, ScalarType):
        info = scalar(type_.c_name)
        body.add(f"{info.native} {L}result;")
        arguments["out_result"] = f"&{L}result"
        value = info.from_native.replace("{}", f"{L}result") if info.converts else f"{L}result"
        out.after_check = [f"return {value};"]
        return out
    if isinstance(type_, EnumRef):
        assert spec.enum is not None
        underlying = scalar(spec.enum.model.underlying.c_name)
        body.add(f"{underlying.native} {L}result;")
        arguments["out_result"] = f"&{L}result"
        out.after_check = [f"return ({lowering.returns}){L}result;"]
        return out
    if isinstance(type_, StringType):
        body.add(f"IntPtr {L}text;")
        body.add(f"nuint {L}size;")
        arguments["out_result"] = f"&{L}text"
        arguments["out_result_size"] = f"&{L}size"
        free = f"Native.{module.free_function}({L}text);"
        if type_.sized:
            out.after_check = [
                "try",
                "{",
                f"    return Runtime.TakeText({L}text, {L}size);",
                "}",
                "finally",
                "{",
                f"    {free}",
                "}",
            ]
            lowering.returns = "string"
        else:
            # A const char* result: NULL is a null string.
            out.after_check = [
                f"if ({L}text == IntPtr.Zero)",
                "{",
                "    return null;",
                "}",
                "try",
                "{",
                f"    return Runtime.TakeText({L}text, {L}size);",
                "}",
                "finally",
                "{",
                f"    {free}",
                "}",
            ]
            lowering.returns = "string?"
        return out
    if isinstance(type_, ClassRef):
        _object_result(plan, spec, type_, names, body, arguments, lowering, out, owners)
        return out
    raise Unsupported(f"the result of type '{type_.cpp_spelling}' is not supported in C# yet")


def _object_result(
    plan: CallablePlan,
    spec: ResultSpec,
    ref: ClassRef,
    names: Names,
    body: _Body,
    arguments: dict[str, str],
    lowering: Lowering,
    out: _Result,
    owners: list[str],
) -> None:
    info = spec.cls
    assert info is not None
    class_name = names.cls(info)
    handle_name = names.handles[info.c_type]
    borrowed = ref.form in (ClassForm.REFERENCE, ClassForm.CONST_REFERENCE) or (
        spec.ownership == "borrowed"
    )
    nullable = ref.form is not ClassForm.VALUE
    body.add(f"IntPtr {L}result;")
    arguments["out_result"] = f"&{L}result"
    given = ["this"] if plan.kind is CallableKind.METHOD else owners
    owner_list = f"new BridgefexObject?[] {{ {', '.join(given)} }}" if given else "null"
    if borrowed:
        constant = ref.form in (ClassForm.CONST_REFERENCE, ClassForm.CONST_POINTER)
        lowering.returns = class_name + ("?" if nullable else "")
        out.after_check = [
            f"return {class_name}.Borrow({L}result, {owner_list}, {'true' if constant else 'false'});"
        ]
        return
    # The handle that will own the object exists before the call: from the moment the
    # C function writes the pointer, its destruction is guaranteed.
    body.add(f"var {L}o = new {handle_name}();")
    out.checks_after = True
    out.after_call = [f"{L}o.Assign({L}result);"]
    lowering.returns = class_name + ("?" if nullable else "")
    out.after_check = [
        f"return {L}o.IsInvalid ? null : new {class_name}({L}o);"
        if nullable
        else f"return new {class_name}({L}o);"
    ]
    if nullable and lowering.returns.endswith("?"):
        pass


__all__ = ["Lowering", "Names", "Unsupported", "lower_callable", "public_type"]

_ = (Diagnostic, GenerationError, FfiKind, Role, native_type, pascal)
