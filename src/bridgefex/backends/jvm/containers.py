# SPDX-License-Identifier: Apache-2.0
"""The containers (``std::vector``, ``std::array``, ``std::span``) in the wrappers: a parameter and
a result are Java arrays.

The elements go through the memory of the call (an array of the C type of the element, and a
pair of pointer and size for each string), written and read one by one with the same
operations as the members of a struct: they are the ones that both flavors have."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...model import ContainerKind, ContainerType, EnumRef, ScalarType, StringType, StructRef
from ...plan import ElementSpec, ParamSpec, ResultSpec
from .mapping import STRING, Unsupported, scalar
from .naming import LOCAL_PREFIX

if TYPE_CHECKING:
    from .lower import Names, _Lowerer

L = LOCAL_PREFIX
R = "BridgefexRuntime"


@dataclass(frozen=True, slots=True)
class Element:
    """An element type of a container."""

    public: str
    """The Java type of an element."""

    annotation: str
    """``@BridgefexUnsigned `` for a 64-bit unsigned element (the array carries it)."""

    size: str
    """The expression of the size of an element in memory, in bytes."""

    write: str
    """The statement that writes the element ``{value}`` to ``{seg}`` at ``{off}``; ``{name}`` is
    the text of the message of a check (an expression)."""

    read: str
    """The expression that reads an element at ``{seg}``, ``{off}``."""

    kind: str
    """``scalar``, ``enum``, ``struct`` or ``string``."""


def element_of(spec: ElementSpec | None, names: Names) -> Element:
    """The element type of a container of the plan."""
    if spec is None:
        raise Unsupported("a container without its element type")
    type_ = spec.type
    if isinstance(type_, ScalarType):
        info = scalar(type_.c_name)
        return Element(
            info.public,
            info.annotation,
            f"{info.layout}.byteSize()",
            info.write.replace("{}", "{value}"),
            info.read,
            "scalar",
        )
    if isinstance(type_, EnumRef):
        if spec.enum is None:
            raise Unsupported(f"the enum '{type_.cpp_spelling}' of a container")
        enum = spec.enum
        info = scalar(enum.model.underlying.c_name)
        public = names.enum(enum)
        # The first use of an element checks that it is not null, and names it if it is.
        first = f"{R}.nonNull({{value}}, {{name}}).value()"
        checks = ""
        valid = enum.model.valid_range
        if valid is not None:
            checks = f"{R}.checkEnum({first}, {valid[0]}, {valid[1]}, {{name}}); "
            first = "{value}.value()"
        value = first if info.public == "long" else f"({info.public}) {first}"
        write = checks + info.write.replace("{}", value)
        return Element(
            public, "", f"{info.layout}.byteSize()", write, f"{public}.of({info.read})", "enum"
        )
    if isinstance(type_, StructRef):
        if spec.struct is None:
            raise Unsupported(f"the struct '{type_.cpp_spelling}' of a container")
        struct = names.struct(spec.struct)
        size = f"{struct}.LAYOUT.byteSize()"
        return Element(
            struct,
            "",
            size,
            f"{R}.nonNull({{value}}, {{name}}).write({{seg}}.asSlice({{off}}, {size}));",
            f"{struct}.read({{seg}}.asSlice({{off}}, {size}))",
            "struct",
        )
    if isinstance(type_, StringType):
        return Element(STRING, "", f"{R}.STRING_SIZE", "", "", "string")
    raise Unsupported(f"the type '{type_.cpp_spelling}' of a container's element")


def public_param(spec: ParamSpec, names: Names) -> str:
    """The Java type of a container parameter."""
    element = element_of(spec.element, names)
    return f"{element.annotation}{element.public}[]"


def _write(
    lw: _Lowerer, element: Element, where: tuple[str, str], value: str, name: str
) -> list[str]:
    """The statements that write ``value`` as the element at ``where`` (a segment and an offset)."""
    seg, offset = where
    if element.kind != "string":
        text = element.write
        for key, new in (("{seg}", seg), ("{off}", offset), ("{value}", value), ("{name}", name)):
            text = text.replace(key, new)
        return [text]
    declare = lw.declare
    text = f"{L}e"
    return [
        f"byte[] {L}bytes = {R}.utf8({R}.nonNull({value}, {name}), {name});",
        f"{declare(lw.segment)} {text} = {R}.bytes({lw.arena_resource()}, {L}bytes);",
        f"{seg}.set({R}.ADDRESS, {offset}, {text});",
        f"{R}.putSize({seg}, {offset} + {R}.ADDRESS.byteSize(), (long) {L}bytes.length);",
    ]


def param(lw: _Lowerer, spec: ParamSpec, type_: ContainerType, position: int, name: str) -> None:
    """The statements that make the memory (and the size) that a container goes to C++ as."""
    element = element_of(spec.element, lw.names)
    arena = lw.arena_resource()
    lw.lowering.parameters.append(f"{element.annotation}{element.public}[] {name}")
    lw.statements.append(f'{R}.nonNull({name}, "{name}");')
    if type_.kind is ContainerKind.ARRAY:
        lw.statements.extend(
            [
                f"if ({name}.length != {type_.length})",
                "{",
                "    throw new java.lang.IllegalArgumentException("
                f"\"argument '{spec.name}' must have {type_.length} elements, got \" "
                f"+ {name}.length);",
                "}",
            ]
        )
    elif spec.size is None:
        raise AssertionError(f"the container '{spec.name}' has no size parameter")
    else:
        lw.arguments[spec.size] = f"(long) {name}.length"
    block = f"{L}c{position}"
    room = f"{element.size} * (long) {name}.length"
    lw.statements.append(f"{lw.declare(lw.segment)} {block} = {arena}.allocate({room});")
    index = f"{L}i"
    lw.statements.append(f"for (int {index} = 0; {index} < {name}.length; {index}++)")
    lw.statements.append("{")
    where = f'"{spec.name}[" + {index} + "]"'
    at = (block, f"{index} * {element.size}")
    for line in _write(lw, element, at, f"{name}[{index}]", where):
        lw.statements.append(f"    {line}")
    lw.statements.append("}")
    lw.arguments[spec.name] = block


def result(lw: _Lowerer, spec: ResultSpec, type_: ContainerType) -> list[str]:
    """The statements that declare what C++ writes; returns the ones that run after the call has
    succeeded (and sets the ones that run whatever happens)."""
    element = element_of(spec.element, lw.names)
    arena = lw.arena_resource()
    lw.lowering.returns = f"{element.public}[]"
    if element.annotation:
        lw.lowering.annotations.append("@BridgefexUnsigned")
    segment = lw.declare(lw.segment)
    index = f"{L}i"
    if type_.kind is ContainerKind.ARRAY:
        # std::array: the caller gives the room.
        lw.statements.append(
            f"{segment} {L}block = {arena}.allocate({element.size} * {type_.length}L);"
        )
        lw.arguments["out_result"] = f"{L}block"
        return _items(element, f"{L}block", str(type_.length), index, lw)
    lw.statements.append(f"{segment} {L}result = {arena}.allocate({R}.ADDRESS);")
    lw.statements.append(f"{segment} {L}size = {arena}.allocate({R}.C_SIZE);")
    lw.arguments["out_result"] = f"{L}result"
    lw.arguments["out_result_size"] = f"{L}size"
    pointer = f"{L}result.get({R}.ADDRESS, 0)"
    lw.cleanup = [f"BridgefexNative.{lw.module.free_function}({pointer});"]
    return [
        f"int {L}count = {R}.count({R}.getSize({L}size, 0));",
        f"{segment} {L}data = {R}.view({pointer}, (long) {L}count * {element.size});",
        *_items(element, f"{L}data", f"{L}count", index, lw),
    ]


def _items(element: Element, source: str, count: str, index: str, lw: _Lowerer) -> list[str]:
    """``return`` the Java array of ``count`` elements read at ``source``."""
    if element.kind == "string":
        read = (
            f"{R}.takeText({source}.get({R}.ADDRESS, {index} * {element.size}), "
            f"{R}.getSize({source}, {index} * {element.size} + {R}.ADDRESS.byteSize()))"
        )
    else:
        read = element.read.replace("{seg}", source).replace("{off}", f"{index} * {element.size}")
    return [
        f"{element.public}[] {L}items = new {element.public}[{count}];",
        f"for (int {index} = 0; {index} < {count}; {index}++)",
        "{",
        f"    {L}items[{index}] = {read};",
        "}",
        f"return {L}items;",
    ]
