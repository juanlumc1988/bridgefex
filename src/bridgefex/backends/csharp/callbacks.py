# SPDX-License-Identifier: Apache-2.0
"""The callbacks (``std::function`` parameters) in the wrappers: a parameter is a ``Func`` or an
``Action``, and C++ calls it through a thunk, a static method with the signature of the C callback.

A thunk is shared by the callbacks that have the same C signature. It finds the delegate by the
``user_data`` (a key of a table, never an address), converts the arguments, calls it, converts the
result, and keeps its exception for the call that runs it: an exception must not cross C++."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ...model import ContainerKind, ContainerType, EnumRef, ScalarType, StringType, StructRef
from ...plan import (
    CallbackPart,
    CallbackSpec,
    DirectorEntry,
    ElementSpec,
    FfiKind,
    ModulePlan,
)
from .elements import Element, element_of
from .mapping import CDECL_CALLERS, Unsupported
from .naming import LOCAL_PREFIX

if TYPE_CHECKING:
    from .lower import Names

L = LOCAL_PREFIX

MAX_ARGUMENTS = 16
"""``Func`` and ``Action`` have no more parameters than this: a callback with more has delegates
of the bindings (``BridgefexCallbacks.Func17``...)."""

WIDEST = 64
"""The most arguments of a callback that the bindings have delegates for."""

_CDECL = CDECL_CALLERS


def has_callbacks(plan: ModulePlan) -> bool:
    """True if a function of the C layer takes a callback."""
    return any(
        param.ffi.kind is FfiKind.CALLBACK
        for header in plan.headers
        for function in header.c_functions
        for param in function.params
    )


@dataclass(frozen=True, slots=True)
class _Argument:
    """An argument of a callback: the type the delegate shows, the C parameters of the thunk, and
    the expression that converts them (``{0}``, ``{1}``: the parameters)."""

    public: str
    natives: tuple[str, ...]
    read: str
    prelude: tuple[str, ...] = ()
    """Statements that run before the call (a container that is converted is built there):
    ``{v}`` is a local of the argument, ``{i}`` its index."""


def _element(part: CallbackPart, names: Names) -> Element:
    return element_of(ElementSpec(part.type, part.enum, part.struct), names)  # type: ignore[arg-type]


def _argument(part: CallbackPart, names: Names) -> _Argument:
    type_ = part.type
    if isinstance(type_, StringType):
        if type_.sized:
            return _Argument("string", ("byte*", "nuint"), "Runtime.TakeText((nint){0}, {1})")
        # A const char*: NULL is a null string.
        return _Argument("string?", ("byte*",), "Runtime.CString({0})")
    if isinstance(type_, StructRef):
        element = _element(part, names)
        return _Argument(
            element.public, (f"{element.native}*",), f"{element.public}.FromNative({{0}})"
        )
    if isinstance(type_, EnumRef | ScalarType):
        element = _element(part, names)
        return _Argument(
            element.public, (element.native,), element.from_native.replace("{}", "{0}")
        )
    if isinstance(type_, ContainerType):
        return _container_argument(part, type_, names)
    raise Unsupported(f"the type '{type_.cpp_spelling}' of an argument of a callback")


def _container_argument(part: CallbackPart, type_: ContainerType, names: Names) -> _Argument:
    """A container that C++ gives to an override: a span over what C++ holds (it is only valid
    while the override runs, and a span cannot be kept), or an array if its elements have to be
    converted."""
    element = element_of(part.element, names)
    span = f"global::System.ReadOnlySpan<{element.public}>"
    pointer = f"{element.native}*"
    natives: tuple[str, ...]
    if type_.kind is ContainerKind.ARRAY:
        natives = (pointer,)
        count = f"Runtime.Count({{0}}, {type_.length})"
    else:
        natives = (pointer, "nuint")
        count = "Runtime.Count({0}, {1})"
    if element.pinnable:
        return _Argument(span, natives, f"new {span}(({element.public}*){{0}}, {count})")
    if element.kind == "struct":
        value = f"{element.public}.FromNative(&{{0}}[{{i}}])"
    elif element.kind == "string":
        value = "Runtime.TakeText((nint){0}[{i}].Data, {0}[{i}].Size)"
    else:
        value = element.from_native.replace("{}", "{0}[{i}]")
    prelude = (
        f"var {{v}} = new {element.public}[{count}];",
        "for (int {i} = 0; {i} < {v}.Length; {i}++)",
        "{",
        f"    {{v}}[{{i}}] = {value};",
        "}",
    )
    return _Argument(span, natives, "{v}", prelude)


def public_of(part: CallbackPart, names: Names) -> str:
    """The C# type of an argument of a callback or of a virtual method."""
    return _argument(part, names).public


def _to_native(element: Element, value: str) -> str:
    if element.kind == "enum":
        return f"({element.native}){value}"
    return element.to_native.replace("{}", value)


def delegate_type(callback: CallbackSpec, names: Names) -> str:
    """``Func<A, R>`` or ``Action<A>`` for a ``std::function<R(A)>``, or a delegate of the
    bindings if it has more arguments than those have."""
    count = len(callback.arguments)
    if count > WIDEST:
        raise Unsupported(
            f"a callback with {count} arguments (the bindings have delegates for up to {WIDEST})"
        )
    types = [_argument(part, names).public for part in callback.arguments]
    result = callback.result.type
    returns = not (isinstance(result, ScalarType) and result.is_void)
    if returns:
        types.append(_element(callback.result, names).public)
    if count > MAX_ARGUMENTS:
        names.thunks.wide(returns, count)
        return f"BridgefexCallbacks.{'Func' if returns else 'Action'}{count}<{', '.join(types)}>"
    if not returns:
        if not types:
            return "global::System.Action"
        return f"global::System.Action<{', '.join(types)}>"
    return f"global::System.Func<{', '.join(types)}>"


@dataclass(frozen=True, slots=True)
class Thunk:
    name: str
    parameters: tuple[str, ...]
    pointer: str
    """The function pointer type, to take the address of the method."""

    body: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Sinks:
    """The functions of the C layer that set what an override gives back."""

    string: str
    vector: str
    strings: str


def sinks_of(plan: ModulePlan) -> Sinks:
    return Sinks(plan.string_sink_set, plan.vector_sink_set, plan.vector_sink_set_strings)


def _fill(text: str, locals_: list[str], position: int) -> str:
    """The expression or statement of an argument, with its C parameters and its locals."""
    for index, local in enumerate(locals_):
        text = text.replace(f"{{{index}}}", local)
    return text.replace("{v}", f"c{position}").replace("{i}", f"i{position}")


def _thunk(name: str, callback: CallbackSpec, delegate: str, names: Names) -> Thunk:
    arguments = [_argument(part, names) for part in callback.arguments]
    parameters = ["void* userData"]
    pointer = ["void*"]
    values: list[str] = []
    for position, argument in enumerate(arguments, start=1):
        locals_ = []
        for index, native in enumerate(argument.natives):
            local = f"a{position}" if len(argument.natives) == 1 else f"a{position}{'ds'[index]}"
            parameters.append(f"{native} {local}")
            pointer.append(native)
            locals_.append(local)
        expression = argument.read
        for index, local in enumerate(locals_):
            expression = expression.replace(f"{{{index}}}", local)
        values.append(expression)
    result = callback.result.type
    returns = not (isinstance(result, ScalarType) and result.is_void)
    out: Element | None = None
    if returns:
        out = _element(callback.result, names)
        parameters.append(f"{out.native}* result")
        pointer.append(f"{out.native}*")
    pointer.append("void")
    call = f"function({', '.join(values)})"
    body = [
        "CallbackBox? box = null;",
        "try",
        "{",
        "    box = CallbackBox.Of(userData);",
        "    if (box.Skip)",
        "    {",
        "        return;",
        "    }",
        f"    var function = ({delegate})box.Function!;",
    ]
    if out is None:
        body.append(f"    {call};")
    else:
        body.append(f"    var value = {call};")
        if out.check is not None:
            low, high = out.check
            body.append(f"    box.CheckResult((long)value, {low}, {high});")
        body.append(f"    *result = {_to_native(out, 'value')};")
    body += [
        "}",
        "catch (global::System.Exception error)",
        "{",
        "    CallbackBox.Fail(box, error);",
        "}",
    ]
    return Thunk(name, tuple(parameters), ", ".join(pointer), tuple(body))


def _director_thunk(
    name: str, root: str, method: str, entry: DirectorEntry, *, names: Names, sinks: Sinks
) -> Thunk:
    """The thunk of a virtual method: it finds the C# object of the director and calls
    ``method``."""
    callback = entry.callback
    arguments = [_argument(part, names) for part in callback.arguments]
    parameters = ["void* userData"]
    pointer = ["void*"]
    values: list[str] = []
    before: list[str] = []
    for position, argument in enumerate(arguments, start=1):
        locals_ = []
        for index, native in enumerate(argument.natives):
            local = f"a{position}" if len(argument.natives) == 1 else f"a{position}{'ds'[index]}"
            parameters.append(f"{native} {local}")
            pointer.append(native)
            locals_.append(local)
        before += [_fill(line, locals_, position) for line in argument.prelude]
        values.append(_fill(argument.read, locals_, position))
    result = callback.result
    kind = result.type
    what = f"{root.rsplit('.', 1)[-1]}.{entry.name}"
    call = f"target.{method}({', '.join(values)})"
    give: list[str] = []
    if isinstance(kind, ScalarType) and kind.is_void:
        statement = [f"{call};"]
    else:
        statement = [f"var value = {call};"]
        if isinstance(kind, StringType):
            parameters.append("nint sink")
            pointer.append("nint")
            nullable = "" if kind.sized else None
            give = (
                [
                    "if (value is null)",
                    "{",
                    "    throw new global::System.InvalidOperationException("
                    f'"the override {what} returned null for a std::string");',
                    "}",
                ]
                if nullable is not None
                else []
            ) + [
                "if (value is not null)",
                "{",
                '    byte[] bytes = Runtime.Utf8(value, "result");',
                "    fixed (byte* text = bytes)",
                "    {",
                f"        Native.{sinks.string}(sink, text, (nuint)bytes.Length);",
                "    }",
                "}",
            ]
        elif isinstance(kind, StructRef):
            out = _element(result, names)
            parameters.append(f"{out.native}* result")
            pointer.append(f"{out.native}*")
            give = ["value.ToNative(result);"]
        elif isinstance(kind, ContainerType):
            parameters.append("nint sink")
            pointer.append("nint")
            give = _container_result(kind, result, names, what, sinks)
        else:
            out = _element(result, names)
            parameters.append(f"{out.native}* result")
            pointer.append(f"{out.native}*")
            if out.check is not None:
                low, high = out.check
                give.append(f'box.CheckResult((long)value, {low}, {high}, "{what}");')
            give.append(f"*result = {_to_native(out, 'value')};")
    pointer.append("void")
    body = [
        "DirectorBox? box = null;",
        "try",
        "{",
        "    box = (DirectorBox)CallbackBox.Of(userData);",
        "    if (box.Skip)",
        "    {",
        "        return;",
        "    }",
        "    if (box.Target is not " + root + " target)",
        "    {",
        "        throw new global::System.InvalidOperationException(",
        '            "C++ called an override of an object that was collected: '
        'keep a reference to it while C++ uses it");',
        "    }",
        "    using var running = box.Enter();",
        *(f"    {line}" for line in before),
        *(f"    {line}" for line in statement),
        *(f"    {line}" for line in give),
        "}",
        "catch (global::System.Exception error)",
        "{",
        "    CallbackBox.Fail(box, error);",
        "}",
    ]
    return Thunk(name, tuple(parameters), ", ".join(pointer), tuple(body))


def _container_result(
    type_: ContainerType, part: CallbackPart, names: Names, what: str, sinks: Sinks
) -> list[str]:
    """The statements that give C++ the array that an override returned: C++ copies it into the
    container before the thunk returns."""
    element = element_of(part.element, names)
    shape = "std::array" if type_.kind is ContainerKind.ARRAY else "std::vector"
    lines = [
        "if (value is null)",
        "{",
        "    throw new global::System.InvalidOperationException("
        f'"the override {what} returned null for a {shape}");',
        "}",
    ]
    if type_.kind is ContainerKind.ARRAY:
        lines += [
            f"if (value.Length != {type_.length})",
            "{",
            "    throw new global::System.InvalidOperationException("
            f'"the override {what} returned " + value.Length + " elements for a std::array of '
            f'{type_.length}");',
            "}",
        ]
    if element.kind == "string":
        return [
            *lines,
            'using var block = StringBlock.Make(value, "result");',
            f"Native.{sinks.strings}(sink, block.Pointer, (nuint)value.Length);",
        ]
    check: list[str] = []
    if element.check is not None:
        low, high = element.check
        check = [
            "for (int i = 0; i < value.Length; i++)",
            "{",
            f'    box.CheckResult((long)value[i], {low}, {high}, "{what}[" + i + "]");',
            "}",
        ]
    size = f"(nuint)sizeof({element.native})"
    if element.pinnable:
        return (
            lines
            + check
            + [
                f"fixed ({element.public}* data = value)",
                "{",
                f"    Native.{sinks.vector}(sink, data, (nuint)value.Length, {size});",
                "}",
            ]
        )
    if element.kind == "struct":
        convert = "value[i].ToNative(&block.Pointer[i]);"
    else:
        convert = f"block.Pointer[i] = {element.to_native.replace('{}', 'value[i]')};"
    return (
        lines
        + check
        + [
            f"using var block = Block<{element.native}>.Alloc(value.Length);",
            "for (int i = 0; i < value.Length; i++)",
            "{",
            f"    {convert}",
            "}",
            f"Native.{sinks.vector}(sink, block.Pointer, (nuint)value.Length, {size});",
        ]
    )


@dataclass(slots=True)
class ThunkTable:
    """The thunks that the wrappers of a module use, by C signature and delegate type."""

    _items: dict[tuple[str, str], Thunk] = field(default_factory=dict)
    _names: set[str] = field(default_factory=set)
    _directors: dict[str, Thunk] = field(default_factory=dict)
    _wide: set[tuple[bool, int]] = field(default_factory=set)
    """The delegates with more arguments than ``Func`` and ``Action`` have: (returns, arguments)."""

    def wide(self, returns: bool, count: int) -> None:
        self._wide.add((returns, count))

    def delegates(self) -> list[str]:
        """The declarations of the delegates that are used, inside ``BridgefexCallbacks``."""
        lines: list[str] = []
        for returns, count in sorted(self._wide):
            kind = "Func" if returns else "Action"
            generics = [f"in T{i}" for i in range(1, count + 1)]
            parameters = [f"T{i} arg{i}" for i in range(1, count + 1)]
            if returns:
                generics.append("out TResult")
            lines += [
                "",
                f"    /// <summary>A callback with {count} arguments (<c>{kind}</c> has no more "
                f"than {MAX_ARGUMENTS}).</summary>",
                f"    public delegate {'TResult' if returns else 'void'} {kind}{count}"
                f"<{', '.join(generics)}>({', '.join(parameters)});",
            ]
        return lines

    def director(
        self,
        name: str,
        root: str,
        method: str,
        entry: DirectorEntry,
        *,
        names: Names,
        sinks: Sinks,
    ) -> None:
        """The thunk ``name`` of a virtual method of the C# class ``root``."""
        if name not in self._directors:
            self._directors[name] = _director_thunk(
                name, root, method, entry, names=names, sinks=sinks
            )

    def address(self, key: str, callback: CallbackSpec, names: Names) -> str:
        """The expression of the address of the thunk for a callback of C signature ``key``."""
        delegate = delegate_type(callback, names)
        found = self._items.get((key, delegate))
        if found is None:
            base = "Thunk_" + re.sub(r"\W", "_", key)
            name = base
            number = 1
            while name in self._names:
                number += 1
                name = f"{base}_{number}"
            self._names.add(name)
            found = self._items[(key, delegate)] = _thunk(name, callback, delegate, names)
        return f"Thunks.Address_{found.name}"

    def source(self) -> list[str]:
        """The lines of the class with the thunks, in the order they were first used."""
        lines = ["internal static unsafe class Thunks", "{"]
        for thunk in [*self._items.values(), *self._directors.values()]:
            lines += [
                "    " + _CDECL,
                f"    internal static void {thunk.name}({', '.join(thunk.parameters)})",
                "    {",
                *(f"        {line}" if line else "" for line in thunk.body),
                "    }",
                "",
                f"    internal static nint Address_{thunk.name} =>",
                f"        (nint)(delegate* unmanaged[Cdecl]<{thunk.pointer}>)&{thunk.name};",
                "",
            ]
        if lines[-1] == "":
            lines.pop()
        lines.append("}")
        return lines


__all__ = ["ThunkTable", "delegate_type", "has_callbacks", "sinks_of"]
