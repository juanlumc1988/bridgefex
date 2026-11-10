# SPDX-License-Identifier: Apache-2.0
"""The directors in the C# bindings: a C# class that derives from a wrapped class and overrides some
of its virtual methods makes objects whose C++ object is the director of the class (see
:class:`bridgefex.plan.DirectorPlan`).

A virtual method of the C++ class is a ``virtual`` method of the C# class. C++ calls an override
through a thunk (a static method with the C signature of the vtable member) that finds the C#
object by the ``user_data`` of the vtable; ``base.Method()`` calls the implementation of the C++
class through the director. The classes that can be derived from are those whose chain of bases
is single and has a director each, and whose virtual methods that are pure have types that the
C# side supports: scalars, enums, strings and value structs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...model import Access, EnumRef, ScalarType, StringType, StructRef
from ...plan import CallablePlan, CallbackPart, ClassPlan, DirectorEntry, ModulePlan
from .callbacks import public_of
from .lower import lower_callable
from .mapping import scalar
from .naming import LOCAL_PREFIX, RESERVED_MEMBERS, identifier, pascal
from .source import Source

if TYPE_CHECKING:
    from .lower import Names

L = LOCAL_PREFIX


def supported_part(part: CallbackPart) -> bool:
    return isinstance(part.type, ScalarType | EnumRef | StructRef | StringType)


def supported(entry: DirectorEntry) -> bool:
    """True if C# can override the virtual method (the other ones keep the implementation of the
    class)."""
    return supported_part(entry.callback.result) and all(
        supported_part(part) for part in entry.callback.arguments
    )


@dataclass(frozen=True, slots=True)
class Slot:
    """A virtual method in the vtable of a director of the class."""

    entry: DirectorEntry
    ok: bool
    """C# overrides it (else the member of the vtable stays NULL)."""

    root: ClassPlan
    """The first class of the chain that has it: the C# method is declared there."""

    root_entry: DirectorEntry
    name: str
    """The name of the C# method."""

    thunk: str
    """The name of the thunk (``Thunks.Address_<thunk>`` is its address)."""


@dataclass(frozen=True, slots=True)
class DirectorClass:
    cls: ClassPlan
    kind: int
    chain: tuple[ClassPlan, ...]
    """The classes it derives from, the first base first, and itself last."""

    slots: tuple[Slot, ...]

    @property
    def pure(self) -> tuple[Slot, ...]:
        return tuple(slot for slot in self.slots if slot.entry.is_pure)


def reserved_names(cls: ClassPlan) -> frozenset[str]:
    """The names that a member of the C# class cannot have."""
    return RESERVED_MEMBERS | {pascal(cls.model.name)} | {pascal(e.model.name) for e in cls.enums}


def _member(cls: ClassPlan, entry: DirectorEntry):  # type: ignore[no-untyped-def]
    """The wrapper that ``cls`` has for the virtual method, if it declares it."""
    return next((item for item in cls.members if item.source == entry.method), None)


def _method_name(cls: ClassPlan, entry: DirectorEntry) -> str:
    member = _member(cls, entry)
    name = pascal(member.name + member.suffix) if member is not None else pascal(entry.field)
    reserved = reserved_names(cls)
    while name in reserved:
        name += "_"
    return name


def _sanitize(text: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in text)


def build(plan: ModulePlan) -> dict[str, DirectorClass]:
    """The classes that can be derived from in C#, by C type."""
    by_type = {
        item.c_type: item
        for header in plan.headers
        for item in header.items
        if isinstance(item, ClassPlan)
    }

    def chain_of(cls: ClassPlan) -> tuple[ClassPlan, ...] | None:
        director = cls.director
        if director is None or len(cls.bases) > 1:
            return None
        if any(entry.is_pure and not supported(entry) for entry in director.entries):
            return None
        if not cls.bases:
            return (cls,)
        parent = by_type.get(cls.bases[0].c_type)
        above = chain_of(parent) if parent is not None else None
        return None if above is None else (*above, cls)

    result: dict[str, DirectorClass] = {}
    for kind, c_type in enumerate(sorted(by_type)):
        cls = by_type[c_type]
        chain = chain_of(cls)
        if chain is None or cls.director is None:
            continue
        slots = []
        for entry in cls.director.entries:
            root = next(
                item
                for item in chain
                if item.director is not None
                and any(other.key == entry.key for other in item.director.entries)
            )
            assert root.director is not None
            root_entry = next(other for other in root.director.entries if other.key == entry.key)
            slots.append(
                Slot(
                    entry=entry,
                    ok=supported(entry),
                    root=root,
                    root_entry=root_entry,
                    name=_method_name(root, root_entry),
                    thunk=f"D_{_sanitize(root.c_type)}_{_sanitize(root_entry.field)}",
                )
            )
        result[c_type] = DirectorClass(cls, kind, chain, tuple(slots))
    return result


def emits(names: Names, cls: ClassPlan, entry: DirectorEntry) -> bool:
    """True if the C# class ``cls`` declares the method for ``entry`` (a wrapper it has, or a
    method that is not part of the API and no class above declares)."""
    d = names.directors.get(cls.c_type)
    if d is None or not supported(entry):
        return False
    if _member(cls, entry) is not None:
        return True
    if entry.access is Access.PUBLIC:
        return False
    # Not part of the API: declared by the first class that has it.
    return next(s for s in d.slots if s.entry.key == entry.key).root is cls


def modifier(names: Names, cls: ClassPlan, entry: DirectorEntry) -> str:
    """``virtual`` for the first declaration of the method, else ``override``."""
    d = names.directors[cls.c_type]
    above = d.chain[:-1]
    for ancestor in above:
        parent = names.directors.get(ancestor.c_type)
        if parent is None or ancestor.director is None:
            continue
        for other in ancestor.director.entries:
            if other.key == entry.key and emits(names, ancestor, other):
                return "override"
    return "virtual"


def kinds_with(names: Names, cls: ClassPlan, key: str) -> list[tuple[DirectorClass, DirectorEntry]]:
    """The directors that ``cls`` is the class of, or a class above, and that have the method: a
    base call has to use the one of the object."""
    found = []
    for d in sorted(names.directors.values(), key=lambda item: item.kind):
        if cls.c_type not in {item.c_type for item in d.chain}:
            continue
        for entry in d.slots:
            if entry.entry.key == key:
                found.append((d, entry.entry))
                break
    return found


def types_of(entry: DirectorEntry, names: Names) -> list[tuple[str, str]]:
    """The (type, name) of the parameters of the method, as the thunk gives them."""
    result = []
    for position, part in enumerate(entry.callback.arguments, start=1):
        declared = entry.method.parameters[position - 1].name if entry.method.parameters else ""
        result.append((public_of(part, names), declared or f"arg{position}"))
    return result


def result_type(entry: DirectorEntry, names: Names) -> str:
    """The C# type that an override of the method gives back."""
    kind = entry.callback.result.type
    part = entry.callback.result
    if isinstance(kind, ScalarType):
        return "void" if kind.is_void else scalar(kind.c_name).public
    if isinstance(kind, StringType):
        return "string" if kind.sized else "string?"
    if isinstance(kind, EnumRef) and part.enum is not None:
        return names.enum(part.enum)
    if isinstance(kind, StructRef) and part.struct is not None:
        return names.struct(part.struct)
    raise AssertionError(kind)


def _indent(lines: list[str], width: int) -> list[str]:
    return [f"{' ' * width}{line}" if line else line for line in lines]


def virtual_body(
    plan: ModulePlan,
    names: Names,
    cls: ClassPlan,
    entry: DirectorEntry,
    normal: list[str] | None,
    *,
    returns: str,
) -> list[str]:
    """The body of the C# method of a virtual method: on an object that is a director, the
    implementation of the C++ class (through the director that the object has); else the call
    of the wrapper."""
    lines: list[str] = []
    if entry.is_pure:
        lines += [
            "if (Director is not null)",
            "{",
            "    throw new global::System.InvalidOperationException("
            f'"{cls.model.name}.{entry.name} is pure virtual: it has no implementation to call");',
            "}",
        ]
    else:
        lines += [f"if (Director is {{ }} {L}d)", "{", f"    switch ({L}d.Kind)", "    {"]
        for d, other in kinds_with(names, cls, entry.key):
            lines += [f"        case {d.kind}:", "        {"]
            if other.base is None:
                lines += [
                    "            throw new global::System.InvalidOperationException("
                    f'"{d.cls.model.name}.{entry.name} is pure virtual: it has no implementation '
                    'to call");'
                ]
            else:
                body = lower_callable(other.base, names, plan).body
                lines += _indent(body, 12)
                if returns == "void":
                    lines.append("            return;")
            lines.append("        }")
        lines += ["    }", "}"]
    if normal is None:
        lines.append(
            "throw new global::System.InvalidOperationException("
            f'"{cls.model.name}.{entry.name} is not part of the API: only an override of it '
            'can call it");'
        )
    else:
        lines += ["{", *_indent(normal, 4), "}"]
    return lines


def overrides_helper(src: Source, names: Names, d: DirectorClass) -> None:
    """``DirectorOverrides``: which virtual methods does the C# class of an object override."""
    cls = d.cls
    name = pascal(cls.model.name)
    generated = ", ".join(f"typeof({names.classes[item.c_type]})" for item in d.chain)
    src.line()
    src.line(
        "private static readonly global::System.Collections.Concurrent.ConcurrentDictionary<"
        "global::System.Type, bool[]> s_overrides = new();"
    )
    src.line()
    src.open("private static bool[]? DirectorOverrides(global::System.Type type, bool always)")
    src.open("bool[] found = s_overrides.GetOrAdd(type, static t => new[]")
    for slot in d.slots:
        if not slot.ok:
            src.line("false,")
            continue
        parameters = ", ".join(
            f"typeof({type_.removesuffix('?')})" for type_, _ in types_of(slot.entry, names)
        )
        root = names.classes[slot.root.c_type]
        src.line(
            f'DirectorBox.Overrides(t, typeof({root}), "{slot.name}", '
            f"new global::System.Type[] {{ {parameters} }}, "
            f"new global::System.Type[] {{ {generated} }}),"
        )
    src.close(");")
    src.line(
        f"if (!always && (type == typeof({name}) || !global::System.Array.Exists(found, x => x)))"
    )
    src.open()
    src.line("return null;")
    src.close()
    for position, slot in enumerate(d.slots):
        if slot.entry.is_pure and slot.ok:
            src.line(f"if (!found[{position}])")
            src.open()
            src.line(
                "throw new global::System.InvalidOperationException("
                f'type.Name + " must override the pure virtual method {slot.root.model.name}.'
                f'{slot.root_entry.name}");'
            )
            src.close()
    src.line("return found;")
    src.close()


def vtable_prelude(names: Names, d: DirectorClass) -> tuple[str, ...]:
    """The statements that make the box and the vtable of the director of an object."""
    director = d.cls.director
    assert director is not None
    lines = [
        f"var {L}box = {L}scope.BeginDirector(this, {d.kind});",
        f"Native.{director.vtable_type} {L}vt = default;",
        f"{L}vt.user_data = {L}box.Key;",
        f"{L}vt.release = CallbackBox.ReleaseAddress;",
    ]
    for position, slot in enumerate(d.slots):
        if slot.ok:
            field = identifier(slot.entry.field)
            lines.append(f"if ({L}over[{position}]) {L}vt.{field} = Thunks.Address_{slot.thunk};")
    return tuple(lines)


def director_constructor(
    cls: ClassPlan,
    ctor: CallablePlan,
) -> CallablePlan | None:
    """The function that creates the director for a constructor of the class, if it has one."""
    director = cls.director
    if director is None:
        return None
    return next((item for item in director.constructors if item.source == ctor.source), None)
