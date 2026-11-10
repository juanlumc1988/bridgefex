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

from ...model import EnumRef, ScalarType, StringType, StructRef
from ...plan import CallbackPart, CallbackSpec, ElementSpec, FfiKind, ModulePlan
from .elements import Element, element_of
from .mapping import Unsupported
from .naming import LOCAL_PREFIX

if TYPE_CHECKING:
    from .lower import Names

L = LOCAL_PREFIX

MAX_ARGUMENTS = 16
"""``Func`` and ``Action`` have no more parameters than this."""

_CDECL = "[UnmanagedCallersOnly(CallConvs = new[] { typeof(CallConvCdecl) })]"


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
    raise Unsupported(f"the type '{type_.cpp_spelling}' of an argument of a callback")


def _to_native(element: Element, value: str) -> str:
    if element.kind == "enum":
        return f"({element.native}){value}"
    return element.to_native.replace("{}", value)


def delegate_type(callback: CallbackSpec, names: Names) -> str:
    """``Func<A, R>`` or ``Action<A>`` for a ``std::function<R(A)>``."""
    if len(callback.arguments) > MAX_ARGUMENTS:
        raise Unsupported(
            f"a callback with {len(callback.arguments)} arguments (C# has no delegate with more "
            f"than {MAX_ARGUMENTS})"
        )
    types = [_argument(part, names).public for part in callback.arguments]
    result = callback.result.type
    if isinstance(result, ScalarType) and result.is_void:
        if not types:
            return "global::System.Action"
        return f"global::System.Action<{', '.join(types)}>"
    types.append(_element(callback.result, names).public)
    return f"global::System.Func<{', '.join(types)}>"


@dataclass(frozen=True, slots=True)
class Thunk:
    name: str
    parameters: tuple[str, ...]
    pointer: str
    """The function pointer type, to take the address of the method."""

    body: tuple[str, ...]


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
        f"    var function = ({delegate})box.Function;",
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


@dataclass(slots=True)
class ThunkTable:
    """The thunks that the wrappers of a module use, by C signature and delegate type."""

    _items: dict[tuple[str, str], Thunk] = field(default_factory=dict)
    _names: set[str] = field(default_factory=set)

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
        for thunk in self._items.values():
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


__all__ = ["ThunkTable", "delegate_type", "has_callbacks"]
