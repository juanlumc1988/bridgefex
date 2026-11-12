# SPDX-License-Identifier: Apache-2.0
"""The Java class of a wrapped C++ class: constructors, methods, the views of its bases."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from ...errors import Diagnostic, GenerationError
from ...model import Method
from ...plan import CallableKind, CallablePlan, ClassPlan, ClassView, HeaderPlan, ModulePlan
from .enums import enum_class
from .lower import Lowering, Names, lower_callable, method_name
from .mapping import SEGMENT
from .module import header_package
from .naming import RESERVED_MEMBERS, pascal
from .source import Source, escape_doc

OVERRIDE = "@java.lang.Override"


def _virtual(member: CallablePlan) -> bool:
    return isinstance(member.source, Method) and member.source.is_virtual


def _signature(parameters: list[str]) -> tuple[str, ...]:
    """The types of the parameters of a Java method, which is what overloads are told apart by."""
    return tuple(parameter.rsplit(" ", 1)[0] for parameter in parameters)


@dataclass(frozen=True, slots=True)
class _Member:
    """A method of a class as Java sees it: what a method of a class below it can override."""

    static: bool
    virtual: bool
    returns: str
    owner: str
    """The C++ class that declares it, for the messages."""


def primary_chain(cls: ClassPlan, by_type: dict[str, ClassPlan]) -> list[ClassPlan]:
    """The classes that the Java class derives from (the first base of each), nearest first."""
    chain: list[ClassPlan] = []
    current = cls
    while current.bases:
        first = by_type[current.bases[0].c_type]
        chain.append(first)
        current = first
    return chain


class _Class:
    """What is needed to write one class."""

    def __init__(
        self,
        plan: ModulePlan,
        cls: ClassPlan,
        names: Names,
        by_type: dict[str, ClassPlan],
    ) -> None:
        self.plan = plan
        self.cls = cls
        self.names = names
        self.by_type = by_type
        self.name = pascal(cls.model.name)
        self.chain = primary_chain(cls, by_type)
        self.taken: dict[str, str] = {}
        """The names of the methods of the class, by the declaration that has them."""

        self.reserved = RESERVED_MEMBERS
        self.signatures: dict[tuple[str, tuple[str, ...]], _Member] = {}
        """What the class has, by name and parameters (to see how it hides what it inherits)."""

        self._inherited: dict[tuple[str, tuple[str, ...]], _Member] | None = None

    # -- what the classes above have -----------------------------------------------------------

    def inherited(self) -> dict[tuple[str, tuple[str, ...]], _Member]:
        if self._inherited is None:
            found: dict[tuple[str, tuple[str, ...]], _Member] = {}
            for above in reversed(self.chain):
                for member in above.members:
                    lowered = lower_callable(member, self.names, self.plan, declaring=above)
                    key = (
                        method_name(member.name + member.suffix, RESERVED_MEMBERS),
                        _signature(lowered.parameters),
                    )
                    found[key] = _Member(
                        member.kind is CallableKind.STATIC,
                        _virtual(member),
                        lowered.returns,
                        above.model.qualified_name,
                    )
            self._inherited = found
        return self._inherited

    def check_hiding(
        self, member: CallablePlan, name: str, lowered: Lowering, *, copied: bool = False
    ) -> bool:
        """Java cannot hide an instance method: one with the same name and parameters as a
        method of a class above overrides it. That is what C++ does with a virtual method; for any
        other it would change what a call through the base class runs, so it is an error. Returns
        False for a copy of a method of a secondary base that would override one."""
        key = (name, _signature(lowered.parameters))
        above = self.inherited().get(key)
        if above is None:
            return True
        static = member.kind is CallableKind.STATIC
        virtual = _virtual(member)
        same_kind = static == above.static and (static or virtual)
        if copied:
            # The class has the methods of its other bases too, but C++ would not let it call
            # a name that two of its bases have: the conversion to the base has it.
            return False
        if same_kind and self._returns_ok(lowered.returns, above.returns):
            return True
        raise GenerationError(
            [
                Diagnostic(
                    f"'{member.function.description}' gets the Java method '{name}("
                    f"{', '.join(_signature(lowered.parameters))})', which '{above.owner}' has "
                    "too: Java cannot hide a method (a method can only replace one of the same "
                    "kind that gives the same result: a virtual method or a static one)",
                    member.location,
                )
            ]
        )

    def _returns_ok(self, returns: str, above: str) -> bool:
        """The same type, or a class that derives from the one above (a covariant result)."""
        if returns == above:
            return True
        by_java = {self.names.classes[c_type]: plan for c_type, plan in self.by_type.items()}
        current = by_java.get(returns)
        while current is not None and current.bases:
            first = self.by_type[current.bases[0].c_type]
            if self.names.classes[first.c_type] == above:
                return True
            current = first
        return False

    # -- the file ---------------------------------------------------------------------------

    def write(self, src: Source) -> None:
        cls, name, names = self.cls, self.name, self.names
        base = names.classes[cls.bases[0].c_type] if cls.bases else "BridgefexObject"
        src.doc(
            cls.model.qualified_name,
            "The C++ object is destroyed by close(), or when the object is collected, unless "
            "C++ owns it (it was returned by reference or pointer, or it was given to C++).",
        )
        # The constructors use the object (to keep what C++ keeps), and subclasses are the code
        # of the user.
        src.line('@SuppressWarnings("this-escape")')
        src.open(f"public class {name} extends {base}")
        src.doc("For the generated code: the handle of a C++ object that already exists.")
        # Not public: a call with null would be ambiguous with the constructors of the class.
        src.open(f"protected {name}(BridgefexHandle handle)")
        src.line("super(handle);")
        src.close()
        src.line()
        src.doc("For the generated code: the wrapper of an object that the handle owns or borrows.")
        src.open(f"public static {name} wrap(BridgefexHandle handle)")
        src.line(f"return new {name}(handle);")
        src.close()
        src.line()
        src.line(OVERRIDE)
        src.open("protected java.lang.Class<?> cppType()")
        src.line(f"return {name}.class;")
        src.close()
        self.constructors(src)
        self.borrow(src)
        self.views(src)
        for nested in cls.enums:
            src.line()
            enum_class(src, nested, nested=True)
        for member in cls.members:
            self.member(src, member)
        for view in cls.views:
            if view.c_type in {above.c_type for above in self.chain} or view.c_type == cls.c_type:
                continue
            self.secondary_base(src, view)
        src.close()

    # -- construction -----------------------------------------------------------------------

    def constructors(self, src: Source) -> None:
        """The constructors of a class. Those that Java cannot tell apart (a ``const std::string&``
        and a ``const char*`` are both a ``String``) are static methods, ``create`` and the suffix
        of the C API, as in the other languages."""
        cls, name, names, plan = self.cls, self.name, self.names, self.plan
        lowered = [
            (ctor, lower_callable(ctor, names, plan, declaring=cls)) for ctor in cls.constructors
        ]
        count = Counter(_signature(item.parameters) for _, item in lowered)
        for ctor, item in lowered:
            src.line()
            src.doc(ctor.function.description, *item.remarks)
            src.line(f'@BridgefexFunction("{ctor.function.name}")')
            if count[_signature(item.parameters)] == 1:
                src.open(f"public {name}({', '.join(item.parameters)})")
                # The handle is made before the call: the destruction of what C++ makes is
                # guaranteed from the moment the C function writes the pointer.
                src.line(f"super({names.destroyer(cls.c_type)});")
                src.lines_of(item.body)
                src.close()
                continue
            factory = method_name(f"create{ctor.suffix}", RESERVED_MEMBERS | set(self.taken))
            self.taken[factory] = ctor.function.description
            made = lower_callable(ctor, names, plan, declaring=cls, factory=name)
            src.open(f"public static {name} {factory}({', '.join(made.parameters)})")
            src.lines_of(made.body)
            src.close()

    def borrow(self, src: Source) -> None:
        """``borrow``: the wrapper of an object that C++ owns (it keeps its ``owners`` alive)."""
        name, cls, names = self.name, self.cls, self.names
        src.line()
        src.doc(
            f"For the generated code: the wrapper of a {escape_doc(cls.model.qualified_name)} that "
            "C++ owns, which is valid while its owners are; null for a NULL pointer."
        )
        src.open(
            f"public static {name} borrow({SEGMENT} pointer, BridgefexObject[] owners, "
            "boolean readOnly)"
        )
        src.open("if (pointer.address() == 0L)")
        src.line("return null;")
        src.close()
        src.line(
            "var handle = new BridgefexHandle(BridgefexNative::"
            f"{names.destroyers[cls.c_type]}, false);"
        )
        src.line("handle.assign(pointer);")
        src.line("return BridgefexObject.borrowed(wrap(handle), owners, readOnly);")
        src.close()

    # -- the views of the bases -------------------------------------------------------------

    def views(self, src: Source) -> None:
        """``hold_<base>``: the object seen as each class that it derives from by its first bases
        (and as itself, if others derive from it)."""
        cls = self.cls
        pointer = SEGMENT
        if cls.is_base:
            src.line()
            src.doc("For the generated code: a hold on the object, as this class.")
            src.open(f"public BridgefexLease hold_{cls.c_type}(boolean mutable, boolean give)")
            src.line("return hold(mutable, give);")
            src.close()
        chain = {above.c_type for above in self.chain}
        for view in cls.views:
            if view.c_type in chain and view.c_type != cls.c_type:
                src.line()
                src.line(OVERRIDE)
                src.open(f"public BridgefexLease hold_{view.c_type}(boolean mutable, boolean give)")
                self.upcast(src, view, pointer)
                src.close()

    def upcast(self, src: Source, view: ClassView, pointer: str) -> None:
        src.line("var lease = hold(mutable, give);")
        src.open("try")
        src.line(
            f"{pointer} view = mutable ? BridgefexNative.{view.upcast.name}(lease.pointer())"
            f" : BridgefexNative.{view.const_upcast.name}(lease.pointer());"
        )
        src.line("return lease.as(view);")
        src.close()
        src.open("catch (java.lang.Throwable error)")
        src.line("lease.close();")
        src.line("throw error;")
        src.close()

    def secondary_base(self, src: Source, view: ClassView) -> None:
        """A base class that is not the first: Java has one superclass, so the class has the
        methods of the other ones itself, and ``as<Base>()`` converts to them."""
        names = self.names
        base = self.by_type[view.c_type]
        base_name = pascal(base.model.name)
        base_type = names.classes[base.c_type]
        src.line()
        src.doc("For the generated code: a hold on the object, as one of its other base classes.")
        src.open(f"public BridgefexLease hold_{base.c_type}(boolean mutable, boolean give)")
        self.upcast(src, view, SEGMENT)
        src.close()
        accessor = method_name(f"as{base_name}", RESERVED_MEMBERS | set(self.taken))
        self.taken[accessor] = f"the conversion to '{base.model.qualified_name}'"
        src.line()
        src.doc(
            f"This object as a {escape_doc(base.model.qualified_name)}, which belongs to it: it is "
            "valid while this object is, and closing it does not destroy this one."
        )
        src.open(f"public {base_type} {accessor}()")
        src.open("try (var lease = hold(false, false))")
        src.line(
            f"var view = {base_type}.borrow(BridgefexNative.{view.const_upcast.name}"
            "(lease.pointer()), new BridgefexObject[] {this}, isReadOnly());"
        )
        src.line("view.setOrigin(this);")
        src.line("return view;")
        src.close()
        src.close()
        for member in base.members:
            if member.kind is CallableKind.STATIC:
                continue
            self.member(src, member, declaring=base, copied=True)

    # -- the methods ------------------------------------------------------------------------

    def member(
        self,
        src: Source,
        member: CallablePlan,
        *,
        declaring: ClassPlan | None = None,
        copied: bool = False,
    ) -> None:
        cls = declaring or self.cls
        name = method_name(member.name + member.suffix, RESERVED_MEMBERS)
        if name in self.taken and copied:
            return  # the class declares it too: it hides the one of the base
        lowered = lower_callable(member, self.names, self.plan, declaring=cls)
        if not self.check_hiding(member, name, lowered, copied=copied):
            return
        key = (name, _signature(lowered.parameters))
        if key in self.signatures:
            if copied:
                return
            raise GenerationError(
                [
                    Diagnostic(
                        f"'{member.function.description}' gets the Java method '{name}', which "
                        f"'{self.taken.get(name, '?')}' has",
                        member.location,
                    )
                ]
            )
        self.signatures[key] = _Member(
            member.kind is CallableKind.STATIC,
            _virtual(member),
            lowered.returns,
            cls.model.qualified_name,
        )
        self.taken.setdefault(name, member.function.description)
        src.line()
        src.doc(member.function.description, *lowered.remarks)
        src.line(f'@BridgefexFunction("{member.function.name}")')
        src.lines_of(lowered.annotations)
        keyword = "public static " if member.kind is CallableKind.STATIC else "public "
        src.open(f"{keyword}{lowered.returns} {name}({', '.join(lowered.parameters)})")
        src.lines_of(lowered.body)
        src.close()


def class_source(
    plan: ModulePlan,
    header: HeaderPlan,
    cls: ClassPlan,
    names: Names,
    by_type: dict[str, ClassPlan],
) -> str:
    """The file of a class: its own Java class, in the package of the header."""
    src = Source()
    src.line(f'// Generated by bridgefex from "{header.stem}.h". Do not edit.')
    src.line(f"package {header_package(plan, header)};")
    src.line()
    for imported in (
        "BridgefexFunction",
        "BridgefexHandle",
        "BridgefexLease",
        "BridgefexNative",
        "BridgefexObject",
        "BridgefexRuntime",
        "BridgefexUnsigned",
    ):
        src.line(f"import {names.root}.{imported};")
    src.line()
    _Class(plan, cls, names, by_type).write(src)
    return src.text()
