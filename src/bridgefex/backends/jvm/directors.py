# SPDX-License-Identifier: Apache-2.0
"""The directors in the Java bindings: a Java class that derives from a wrapped class and overrides
some of its virtual methods makes objects whose C++ object is the director of the class (see
:class:`bridgefex.plan.DirectorPlan`).

A virtual method of the C++ class is a method of the Java class, and Java methods are virtual: the
Java class of the user overrides it. C++ calls an override through a thunk (a static method of
``BridgefexCallbacks`` with the C signature of the vtable member) that finds the Java object by the
``user_data`` of the vtable; ``super.method()`` runs the implementation of the C++ class through
the director. Every other method of a class that can be derived from is ``final``: C++ would not
call an override of it, and Java says so when the class is compiled.

The classes that can be derived from are those whose chain of bases is single and has a director
each, and whose pure virtual methods have types that the Java side supports (scalars, enums, strings
and value structs)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...model import (
    Access,
    ClassRef,
    ContainerType,
    EnumRef,
    ScalarType,
    StringType,
    StructRef,
)
from ...plan import CallablePlan, CallbackPart, ClassPlan, DirectorEntry, ModulePlan
from .callbacks import argument
from .containers import element_of
from .lower import lower_callable, method_name
from .mapping import scalar
from .naming import LOCAL_PREFIX, RESERVED_MEMBERS, parameter
from .source import Source

if TYPE_CHECKING:
    from .lower import Names

L = LOCAL_PREFIX


def supported_part(part: CallbackPart) -> bool:
    """True if an override can take (or give) the type: a scalar, an enum, a string, a struct or
    a container of those."""
    if isinstance(part.type, ContainerType):
        return part.element is not None and supported_part(
            CallbackPart(part.element.type, enum=part.element.enum, struct=part.element.struct)
        )
    if isinstance(part.type, EnumRef):
        return part.enum is not None
    if isinstance(part.type, StructRef):
        return part.struct is not None
    if isinstance(part.type, ClassRef):
        return part.cls is not None
    return isinstance(part.type, ScalarType | StringType)


def supported(entry: DirectorEntry) -> bool:
    """True if Java can override the virtual method (the other ones keep the implementation of the
    class)."""
    return supported_part(entry.callback.result) and all(
        supported_part(part) for part in entry.callback.arguments
    )


@dataclass(frozen=True, slots=True)
class Slot:
    """A virtual method in the vtable of a director of the class."""

    entry: DirectorEntry
    ok: bool
    """Java can override it (else the member of the vtable stays NULL)."""

    root: ClassPlan
    """The first class of the chain that has it: the Java method is declared there."""

    root_entry: DirectorEntry
    name: str
    """The name of the Java method."""

    thunk: str
    """What the thunk of the slot is called: the same for the directors of all the classes that
    derive from the root."""


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


def _member(cls: ClassPlan, entry: DirectorEntry) -> CallablePlan | None:
    """The wrapper that ``cls`` has for the virtual method, if it declares it."""
    return next((item for item in cls.members if item.source == entry.method), None)


def _method_name(cls: ClassPlan, entry: DirectorEntry) -> str:
    """The name of the Java method: the one the class that declares it gives it. A virtual method
    that is not part of the API (a protected one) gets its name from the member of the vtable."""
    member = _member(cls, entry)
    if member is not None:
        return method_name(member.name + member.suffix, RESERVED_MEMBERS)
    taken = {method_name(item.name + item.suffix, RESERVED_MEMBERS) for item in cls.members}
    name = method_name(entry.field, RESERVED_MEMBERS)
    while name in taken:
        name += "_"
    return name


def _sanitize(text: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in text)


def build(plan: ModulePlan) -> dict[str, DirectorClass]:
    """The classes that can be derived from in Java, by C type."""
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


def guarded(plan: ModulePlan, derivable: dict[str, DirectorClass]) -> frozenset[str]:
    """The classes that Java cannot derive from and whose methods are ``final`` because of it: C++
    would never call an override of them. They are those that have a class that can be derived
    from among their bases, at any depth (a class of the user that derives from one of them has
    nothing that C++ could call), and those that the annotations give a director that Java cannot
    use (a base class without one, several bases): their overrides would be ignored as well."""
    by_type = {
        item.c_type: item
        for header in plan.headers
        for item in header.items
        if isinstance(item, ClassPlan)
    }

    def below(cls: ClassPlan, seen: set[str]) -> bool:
        for base in cls.bases:
            if base.c_type in seen:
                continue
            seen.add(base.c_type)
            if base.c_type in derivable:
                return True
            parent = by_type.get(base.c_type)
            if parent is not None and (parent.director is not None or below(parent, seen)):
                return True
        return False

    return frozenset(
        c_type
        for c_type, cls in by_type.items()
        if c_type not in derivable and (cls.director is not None or below(cls, set()))
    )


def emits(names: Names, cls: ClassPlan, entry: DirectorEntry) -> bool:
    """True if the Java class ``cls`` declares the method for ``entry``: a wrapper it has, or a
    method that is not part of the API and no class above declares."""
    d = names.directors.get(cls.c_type)
    if d is None or not supported(entry):
        return False
    if _member(cls, entry) is not None:
        return True
    if entry.access is Access.PUBLIC:
        return False
    # Not part of the API: declared by the first class that has it.
    return next(s for s in d.slots if s.entry.key == entry.key).root is cls


def slot_of(names: Names, cls: ClassPlan, member: CallablePlan) -> Slot | None:
    """The slot that a wrapper of ``cls`` is the Java method of, if it is one that can be
    overridden."""
    d: DirectorClass | None = names.directors.get(cls.c_type)
    if d is None:
        return None
    for slot in d.slots:
        if slot.entry.method == member.source and emits(names, cls, slot.entry):
            return slot
    return None


def kinds_with(names: Names, cls: ClassPlan, key: str) -> list[tuple[DirectorClass, DirectorEntry]]:
    """The directors that ``cls`` is the class of, or a class above, and that have the method: a
    base call has to use the one of the object."""
    found = []
    for d in sorted(names.directors.values(), key=lambda item: item.kind):
        if cls.c_type not in {item.c_type for item in d.chain}:
            continue
        for slot in d.slots:
            if slot.entry.key == key:
                found.append((d, slot.entry))
                break
    return found


def types_of(entry: DirectorEntry, names: Names) -> list[tuple[str, str]]:
    """The (type, name) of the parameters of the method, as the override takes them."""
    result = []
    for position, part in enumerate(entry.callback.arguments, start=1):
        if entry.base is not None and position <= len(entry.base.params):
            # The names the base call is lowered with (the C layer renames some).
            declared = entry.base.params[position - 1].name
        else:
            declared = entry.method.parameters[position - 1].name if entry.method.parameters else ""
        result.append((argument(part, names).public, parameter(declared or f"arg{position}")))
    return result


def result_type(entry: DirectorEntry, names: Names) -> str:
    """The Java type that an override of the method gives back."""
    part = entry.callback.result
    kind = part.type
    if isinstance(kind, ScalarType):
        return "void" if kind.is_void else scalar(kind.c_name).public
    if isinstance(kind, StringType):
        return "java.lang.String"
    if isinstance(kind, EnumRef) and part.enum is not None:
        return names.enum(part.enum)
    if isinstance(kind, StructRef) and part.struct is not None:
        return names.struct(part.struct)
    if isinstance(kind, ContainerType):
        return f"{element_of(part.element, names).public}[]"
    if isinstance(kind, ClassRef) and part.cls is not None:
        return names.cls(part.cls)
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
    arguments: list[str],
) -> tuple[list[str], list[list[str]]]:
    """The body of the Java method of a virtual method: on an object that is a director, the
    implementation of the C++ class (through the director that the object has); else the call of
    the wrapper, or an error if the method is not part of the API. ``arguments`` are the names of
    the parameters of the method.

    The implementation of each director is a private method of its own, with the names of the
    parameters that its own class gives them (an override may name them as it likes): the body
    calls it. They are the second result, as the lines of methods, for the class to declare."""
    lines: list[str] = []
    helpers: list[list[str]] = []
    what = f"{cls.model.name}.{entry.name}"
    if entry.is_pure:
        lines += [
            "if (director() != null) {",
            "    throw new java.lang.IllegalStateException("
            f'"{what} is pure virtual: it has no implementation to call");',
            "}",
        ]
    else:
        lines += [
            f"BridgefexCallbacks.DirectorBox {L}d = director();",
            f"if ({L}d != null) {{",
            f"    switch ({L}d.kind()) {{",
        ]
        for d, other in kinds_with(names, cls, entry.key):
            lines += [f"        case {d.kind}: {{"]
            if other.base is None:
                lines += [
                    "            throw new java.lang.IllegalStateException("
                    f'"{d.cls.model.name}.{entry.name} is pure virtual: it has no '
                    'implementation to call");'
                ]
            else:
                lowered = lower_callable(other.base, names, plan)
                helper = f"{L}base{d.kind}_{_sanitize(entry.field)}"
                helpers.append(
                    [
                        f"private {lowered.returns} {helper}({', '.join(lowered.parameters)}) {{",
                        *_indent(lowered.body, 4),
                        "}",
                    ]
                )
                call = f"{helper}({', '.join(arguments)})"
                lines += [
                    f"            {call};" if returns == "void" else f"            return {call};"
                ]
                if returns == "void":
                    lines.append("            return;")
            lines.append("        }")
        lines += ["        default:", "            break;", "    }", "}"]
    if normal is None:
        lines.append(
            "throw new java.lang.IllegalStateException("
            f'"{what} is not part of the API: only an override of it can call it");'
        )
    else:
        lines += normal
    return lines, helpers


def vtable_prelude(names: Names, d: DirectorClass) -> list[str]:
    """The statements that make the box and the vtable of the director of an object, in the arena
    of the call. ``over`` says which methods the Java class overrides."""
    assert d.cls.director is not None
    assert names.thunks is not None
    pointer = f"{names.root}.BridgefexRuntime.ADDRESS.byteSize()"
    lines = [
        f"{names.flavor.declare('BridgefexCallbacks.DirectorBox')} {L}box = "
        f"{L}scope.director(this, {d.kind});",
        f"{names.flavor.declare(names.flavor.segment)} {L}vt = {L}arena.allocate("
        f"{len(d.slots) + 2}L * {pointer}, {pointer});",
        f"{L}vt.set(BridgefexRuntime.ADDRESS, 0L, {L}box.key());",
        f"{L}vt.set(BridgefexRuntime.ADDRESS, {pointer}, BridgefexCallbacks.Release.address());",
    ]
    for position, slot in enumerate(d.slots):
        if slot.ok:
            lines.append(
                f"if ({L}over[{position}]) {L}vt.set(BridgefexRuntime.ADDRESS, "
                f"{position + 2}L * {pointer}, "
                f"BridgefexCallbacks.Thunk{names.thunks.for_slot(slot.thunk).index}.address());"
            )
    return lines


def director_constructor(cls: ClassPlan, ctor: CallablePlan) -> CallablePlan | None:
    """The function that creates the director for a constructor of the class, if it has one."""
    director = cls.director
    if director is None:
        return None
    return next((item for item in director.constructors if item.source == ctor.source), None)


def dropped(names: Names, cls: ClassPlan) -> list[Slot]:
    """The slots of the class above ``cls`` that its own director lacks: C++ declares the method
    in ``cls`` in a way that cannot be overridden (a private implementation), so Java must not
    override it either. What an ancestor further up dropped is ``final`` in its class already."""
    d = names.directors.get(cls.c_type)
    if d is None or len(d.chain) < 2:
        return []
    own = {slot.entry.key for slot in d.slots}
    above = names.directors[d.chain[-2].c_type]
    return [slot for slot in above.slots if slot.ok and slot.entry.key not in own]


def inherited(names: Names, cls: ClassPlan) -> list[Slot]:
    """The methods that a class that Java cannot derive from has from the class it extends, which
    can be derived from: C++ would not call an override of them in this class, so they are made
    ``final`` here. (What is below such a class inherits them as ``final``.)"""
    if cls.c_type not in names.guarded or not cls.bases:
        return []
    d = names.directors.get(cls.bases[0].c_type)
    if d is None:
        return []
    return [slot for slot in d.slots if slot.ok and emits(names, slot.root, slot.root_entry)]


def overrides_helper(src: Source, names: Names, d: DirectorClass) -> None:
    """``DirectorOverrides.of``: which virtual methods does the Java class of an object override."""
    cls = d.cls
    name = names.classes[cls.c_type]
    generated = ", ".join(f"{names.classes[item.c_type]}.class" for item in d.chain)
    src.line()
    src.open("private static final class DirectorOverrides")
    src.line("private DirectorOverrides() {")
    src.line("}")
    src.line()
    src.line(
        "private static final java.lang.Class<?>[] GENERATED = "
        f"new java.lang.Class<?>[] {{ {generated} }};"
    )
    src.line()
    src.open(
        "private static final java.lang.ClassValue<boolean[]> FOUND = "
        "new java.lang.ClassValue<boolean[]>()"
    )
    src.line("@java.lang.Override")
    src.open("protected boolean[] computeValue(java.lang.Class<?> type)")
    src.open("return new boolean[]")
    for slot in d.slots:
        if not slot.ok:
            src.line("false,")
            continue
        parameters = ", ".join(
            f"{type_.removeprefix('@BridgefexUnsigned ')}.class"
            for type_, _ in types_of(slot.entry, names)
        )
        src.line(
            f'BridgefexDirectors.overrides(type, GENERATED, "{slot.name}", '
            f"new java.lang.Class<?>[] {{ {parameters} }}),"
        )
    src.close(";")
    src.close()
    src.close(";")
    src.line()
    src.doc(
        "Which methods the class of the object overrides; null if C++ has nothing to call "
        "(the class itself, or one that overrides nothing) and a plain object does."
    )
    src.open("static boolean[] of(java.lang.Class<?> type, boolean always)")
    src.line("boolean[] found = FOUND.get(type);")
    src.line("boolean any = false;")
    src.open("for (boolean item : found)")
    src.line("any |= item;")
    src.close()
    src.open(f"if (!always && (type == {name}.class || !any))")
    src.line("return null;")
    src.close()
    for position, slot in enumerate(d.slots):
        if slot.entry.is_pure and slot.ok:
            src.open(f"if (!found[{position}])")
            src.line(
                "throw new java.lang.IllegalStateException("
                "(type.getSimpleName().isEmpty() ? type.getName() : type.getSimpleName()) + "
                f'" must override the pure virtual method {slot.root.model.name}.'
                f'{slot.root_entry.name}");'
            )
            src.close()
    src.line("return found;")
    src.close()
    src.close()
