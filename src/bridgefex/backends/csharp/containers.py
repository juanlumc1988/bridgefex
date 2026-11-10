# SPDX-License-Identifier: Apache-2.0
"""The containers (``std::vector``, ``std::array``, ``std::span``) in the wrappers: a parameter is a
``ReadOnlySpan<T>`` and a result a ``T[]``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from ...model import ContainerKind, ContainerType
from ...plan import ParamSpec, ResultSpec
from .elements import Element, element_of
from .naming import LOCAL_PREFIX

if TYPE_CHECKING:
    from .lower import Names

L = LOCAL_PREFIX

SPAN = "global::System.ReadOnlySpan"


class Emitter(Protocol):
    """Where the statements of a body go."""

    def add(self, line: str) -> None: ...

    def open(self, header: str) -> None: ...


@dataclass(frozen=True, slots=True)
class Site:
    """What a container is lowered into: the body, the arguments of the C call, and the names."""

    body: Emitter
    arguments: dict[str, str]
    names: Names
    free_function: str


def public_param(spec: ParamSpec, names: Names) -> str:
    """The C# type of a container parameter."""
    return f"{SPAN}<{element_of(spec.element, names).public}>"


def public_result(spec: ResultSpec, names: Names) -> str:
    """The C# type of a container result."""
    return f"{element_of(spec.element, names).public}[]"


# -- Parameters --------------------------------------------------------------------------------


def param(spec: ParamSpec, type_: ContainerType, position: int, name: str, site: Site) -> None:
    """The statements that make the pointer (and the size) that a container goes to C++ as."""
    body, arguments = site.body, site.arguments
    element = element_of(spec.element, site.names)
    if type_.kind is ContainerKind.ARRAY:
        body.add(f"if ({name}.Length != {type_.length})")
        body.add("{")
        body.add(
            "    throw new global::System.ArgumentException("
            f"\"argument '{spec.name}' must have {type_.length} elements, got \" + {name}.Length, "
            f"nameof({name}));"
        )
        body.add("}")
    else:
        if spec.size is None:
            raise AssertionError(f"the container '{spec.name}' has no size parameter")
        arguments[spec.size] = f"(nuint){name}.Length"
    arguments[spec.name] = _pointer(spec, element, position, name, body)


def _pointer(spec: ParamSpec, element: Element, position: int, name: str, body: Emitter) -> str:
    pinned, block = f"{L}p{position}", f"{L}b{position}"
    if element.kind == "string":
        body.add(f'using var {block} = StringBlock.Make({name}, "{spec.name}");')
        return f"{block}.Pointer"
    if element.pinnable:
        _check_range(element, spec.name, name, body)
        # A span of numbers is given as it is, where it is (an empty one is NULL).
        body.open(f"fixed ({element.public}* {pinned} = {name})")
        return f"({element.native}*){pinned}"
    body.add(f"using var {block} = Block<{element.native}>.Alloc({name}.Length);")
    body.add(f"for (int {L}i = 0; {L}i < {name}.Length; {L}i++)")
    body.add("{")
    if element.kind == "struct":
        body.add(f"    {name}[{L}i].ToNative(&{block}.Pointer[{L}i]);")
    else:
        if element.check is not None:
            low, high = element.check
            body.add(
                f"    Runtime.CheckEnum((long){name}[{L}i], {low}, {high}, "
                f'"{spec.name}[" + {L}i + "]");'
            )
        value = element.to_native.replace("{}", f"{name}[{L}i]")
        body.add(f"    {block}.Pointer[{L}i] = {value};")
    body.add("}")
    return f"{block}.Pointer"


def _check_range(element: Element, c_name: str, name: str, body: Emitter) -> None:
    """The elements of an enum without a fixed type are all values that its C++ type holds."""
    if element.check is None:
        return
    low, high = element.check
    body.add(f"for (int {L}i = 0; {L}i < {name}.Length; {L}i++)")
    body.add("{")
    body.add(f'    Runtime.CheckEnum((long){name}[{L}i], {low}, {high}, "{c_name}[" + {L}i + "]");')
    body.add("}")


# -- Results -----------------------------------------------------------------------------------


def result(spec: ResultSpec, type_: ContainerType, site: Site) -> list[str]:
    """The statements that declare what C++ writes and, after the call has succeeded, build the
    array and release what C++ allocated."""
    body, arguments = site.body, site.arguments
    element = element_of(spec.element, site.names)
    if type_.kind is ContainerKind.ARRAY:
        # std::array: the caller gives the room.
        body.add(f"using var {L}b = Block<{element.native}>.Alloc({type_.length});")
        arguments["out_result"] = f"{L}b.Pointer"
        return _items(element, f"{L}b.Pointer", str(type_.length))
    body.add(f"{element.result_native}* {L}result;")
    body.add(f"nuint {L}size;")
    arguments["out_result"] = f"&{L}result"
    arguments["out_result_size"] = f"&{L}size"
    inner = _items(element, f"{L}result", f"{L}count")
    return [
        "try",
        "{",
        f"    int {L}count = Runtime.Count({L}result, {L}size);",
        *(f"    {line}" for line in inner),
        "}",
        "finally",
        "{",
        f"    Native.{site.free_function}((nint){L}result);",
        "}",
    ]


def _items(element: Element, source: str, count: str) -> list[str]:
    """``return`` the C# array of ``count`` elements read at ``source``."""
    if element.kind == "plain":
        return [f"return new {SPAN}<{element.public}>({source}, {count}).ToArray();"]
    item = f"{source}[{L}i]"
    if element.kind == "struct":
        value = f"{element.public}.FromNative(&{item})"
    elif element.kind == "string":
        value = f"Runtime.TakeText((nint){item}.Data, {item}.Size)"
    else:
        value = element.from_native.replace("{}", item)
    return [
        f"var {L}items = new {element.public}[{count}];",
        f"for (int {L}i = 0; {L}i < {count}; {L}i++)",
        "{",
        f"    {L}items[{L}i] = {value};",
        "}",
        f"return {L}items;",
    ]
