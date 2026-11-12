# SPDX-License-Identifier: Apache-2.0
"""A Java class for a value struct of C++: public fields, and the conversion to and from the
memory of its C mirror."""

from __future__ import annotations

from dataclasses import dataclass

from ...model import EnumRef, ScalarType, StructRef
from ...plan import StructPlan
from .enums import enum_class
from .lower import Names
from .mapping import Unsupported, scalar
from .naming import PARAMETER_RESERVED, RESERVED_MEMBERS, camel, identifier, pascal
from .source import Source


@dataclass(frozen=True, slots=True)
class _Member:
    """A member of a value struct."""

    name: str
    """The Java field."""

    native_name: str
    java: str
    """The type of the field (an array if the member is)."""

    element: str
    """The type of an element."""

    length: int | None
    layout: str
    """The layout of an element."""

    write: str
    """The statement that writes the element ``{value}`` to ``{seg}`` at ``{off}``."""

    read: str
    """The expression that reads an element at ``{seg}``, ``{off}``."""

    size: str
    """The expression of the size of an element, in bytes."""

    fresh: str
    """The value of a field that is not set (a struct, an enum: it must not be null)."""

    annotation: str = ""


def _members(struct: StructPlan, names: Names) -> list[_Member]:
    # A field hides the package that a qualified name of the generated code starts with.
    taken = {
        pascal(struct.model.name),
        *RESERVED_MEMBERS,
        "LAYOUT",
        names.root,
        *PARAMETER_RESERVED,
    }
    taken |= {pascal(nested.model.name) for nested in struct.enums}
    result: list[_Member] = []
    for field_plan in struct.fields:
        model = field_plan.model
        field = identifier(camel(model.name))
        while field in taken:
            field += "_"
        taken.add(field)
        type_ = model.type
        quoted = f'"{field}"'
        annotation = ""
        if isinstance(type_, ScalarType):
            info = scalar(type_.c_name)
            element = info.public
            layout = info.layout
            write = info.write.replace("{name}", quoted).replace("{seg}", "{seg}")
            write = write.replace("{}", "{value}")
            read = info.read
            size = f"{info.layout}.byteSize()"
            fresh = "false" if element == "boolean" else "0"
            annotation = info.annotation
        elif isinstance(type_, EnumRef):
            enum = names.enum_plans[type_.usr]
            info = scalar(enum.model.underlying.c_name)
            element = names.enum(enum)
            layout = info.layout
            checks = ""
            valid = enum.model.valid_range
            if valid is not None:
                checks = (
                    f"BridgefexRuntime.checkEnum({{value}}.value(), {valid[0]}, {valid[1]}, "
                    f"{quoted}); "
                )
            value = (
                "{value}.value()" if info.public == "long" else f"({info.public}) {{value}}.value()"
            )
            write = checks + info.write.replace("{name}", quoted).replace("{}", value)
            read = f"{element}.of({info.read})"
            size = f"{info.layout}.byteSize()"
            fresh = f"{element}.of(0L)"
        elif isinstance(type_, StructRef):
            other = names.struct_plans[type_.usr]
            element = names.struct(other)
            layout = f"{element}.LAYOUT"
            write = "{value}.write({seg}.asSlice({off}, " + layout + ".byteSize()));"
            read = f"{element}.read({{seg}}.asSlice({{off}}, {layout}.byteSize()))"
            size = f"{layout}.byteSize()"
            fresh = f"new {element}()"
        else:
            raise Unsupported(f"the member '{model.name}' of a struct is not supported in Java yet")
        length = model.length
        result.append(
            _Member(
                name=field,
                native_name=field_plan.name,
                java=f"{element}[]" if length is not None else element,
                element=element,
                length=length,
                layout=layout,
                write=write,
                read=read,
                size=size,
                fresh=fresh,
                annotation=annotation,
            )
        )
    return result


def struct_class(src: Source, struct: StructPlan, names: Names, *, nested: bool = False) -> None:
    name = pascal(struct.model.name)
    members = _members(struct, names)
    src.doc(
        struct.model.qualified_name,
        "A value: its members are public and a copy is made when it crosses to C++.",
    )
    src.open(f"public {'static ' if nested else ''}final class {name}")
    for member in members:
        src.line(f"public {member.annotation}{member.java} {member.name};")
    for enum in struct.enums:
        src.line()
        enum_class(src, enum, nested=True)
    src.line()
    src.line("/** The layout of the C mirror of the struct. */")
    layouts = [_layout_member(member) for member in members]
    if layouts:
        src.line(
            "public static final java.lang.foreign.StructLayout LAYOUT = BridgefexRuntime.struct("
        )
        for position, layout in enumerate(layouts):
            comma = "," if position < len(layouts) - 1 else ");"
            src.line(f"    {layout}{comma}")
    else:
        src.line(
            "public static final java.lang.foreign.StructLayout LAYOUT = BridgefexRuntime.struct();"
        )
    for member in members:
        src.line(
            f"private static final long OFFSET_{member.name.upper()} = "
            f'BridgefexRuntime.offset(LAYOUT, "{member.native_name}");'
        )
    src.line()
    src.doc("A value with every member zero (see defaults() for the values that C++ gives).")
    src.open(f"public {name}()")
    for member in members:
        if member.length is not None:
            src.line(f"{member.name} = {_new_array(member)};")
        elif member.fresh not in ("0", "false"):
            src.line(f"{member.name} = {member.fresh};")
    src.close()
    if members:
        src.line()
        parameters = ", ".join(f"{m.annotation}{m.java} {m.name}" for m in members)
        src.open(f"public {name}({parameters})")
        for member in members:
            src.line(f"this.{member.name} = {member.name};")
        src.close()
    src.line()
    src.doc("A value with the members that C++ gives a default-constructed object.")
    src.open(f"public static {name} defaults()")
    src.open("try (var __arena = BridgefexRuntime.arena())")
    src.line("var __raw = __arena.allocate(LAYOUT);")
    src.line(f"BridgefexRuntime.check(BridgefexNative.{struct.init.name}(__raw));")
    src.line("return read(__raw);")
    src.close()
    src.close()
    src.line()
    _write(src, members)
    src.line()
    _read(src, name, members)
    _object_methods(src, name, members)
    src.close()


def _new_array(member: _Member) -> str:
    if member.fresh in ("0", "false"):
        return f"new {member.element}[{member.length}]"
    items = ", ".join([member.fresh] * (member.length or 0))
    return f"new {member.element}[] {{{items}}}"


def _layout_member(member: _Member) -> str:
    layout = member.layout
    if member.length is not None:
        layout = f"BridgefexRuntime.array({member.length}, {layout})"
    return f'{layout}.withName("{member.native_name}")'


def _write(src: Source, members: list[_Member]) -> None:
    src.doc("Writes the value to the C mirror (for the generated code).")
    src.open("public void write(java.lang.foreign.MemorySegment __seg)")
    src.line("__seg.fill((byte) 0);")
    for member in members:
        offset = f"OFFSET_{member.name.upper()}"
        if member.length is None:
            src.line(
                member.write.replace("{value}", member.name)
                .replace("{seg}", "__seg")
                .replace("{off}", offset)
            )
            continue
        src.line(f'BridgefexRuntime.length({member.name}, {member.length}, "{member.name}");')
        src.open(f"for (int __i = 0; __i < {member.length}; __i++)")
        src.line(
            member.write.replace("{value}", f"{member.name}[__i]")
            .replace("{seg}", "__seg")
            .replace("{off}", f"{offset} + __i * {member.size}")
        )
        src.close()
    src.close()


def _read(src: Source, name: str, members: list[_Member]) -> None:
    src.doc("The value in a C mirror (for the generated code).")
    src.open(f"public static {name} read(java.lang.foreign.MemorySegment __seg)")
    src.line(f"{name} __result = new {name}();")
    for member in members:
        offset = f"OFFSET_{member.name.upper()}"
        if member.length is None:
            src.line(
                f"__result.{member.name} = "
                + member.read.replace("{seg}", "__seg").replace("{off}", offset)
                + ";"
            )
            continue
        src.open(f"for (int __i = 0; __i < {member.length}; __i++)")
        src.line(
            f"__result.{member.name}[__i] = "
            + member.read.replace("{seg}", "__seg").replace(
                "{off}", f"{offset} + __i * {member.size}"
            )
            + ";"
        )
        src.close()
    src.line("return __result;")
    src.close()


def _object_methods(src: Source, name: str, members: list[_Member]) -> None:
    src.line()
    src.line("@java.lang.Override")
    src.open("public boolean equals(java.lang.Object other)")
    src.open(f"if (!(other instanceof {name} that))")
    src.line("return false;")
    src.close()
    terms = []
    for member in members:
        field = member.name
        if member.length is not None:
            terms.append(f"java.util.Arrays.equals({field}, that.{field})")
        elif member.java in ("float", "double"):
            wrapper = "java.lang.Float" if member.java == "float" else "java.lang.Double"
            terms.append(f"{wrapper}.compare({field}, that.{field}) == 0")
        elif member.java in ("boolean", "byte", "short", "int", "long"):
            terms.append(f"{field} == that.{field}")
        else:
            terms.append(f"{field}.equals(that.{field})")
    src.line(f"return {' && '.join(terms) if terms else 'true'};")
    src.close()
    src.line()
    src.line("@java.lang.Override")
    src.open("public int hashCode()")
    items = ", ".join(
        f"java.util.Arrays.hashCode({m.name})" if m.length is not None else m.name for m in members
    )
    src.line(f"return java.util.Objects.hash({items});")
    src.close()
    src.line()
    src.line("@java.lang.Override")
    src.open("public java.lang.String toString()")
    parts = ' + ", " + '.join(
        f'"{m.name}=" + '
        + (f"java.util.Arrays.toString({m.name})" if m.length is not None else m.name)
        for m in members
    )
    src.line(f'return "{name}{{" + {parts if parts else chr(34) + chr(34)} + "}}";')
    src.close()
