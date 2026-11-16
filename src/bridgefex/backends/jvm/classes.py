# SPDX-License-Identifier: Apache-2.0
"""The Java class of a wrapped C++ class: constructors, methods, the views of its bases."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from ...errors import Diagnostic, GenerationError
from ...model import Access, Method
from ...plan import CallableKind, CallablePlan, ClassPlan, ClassView, HeaderPlan, ModulePlan
from . import directors
from .enums import enum_class
from .lower import Lowering, Names, lower_callable, method_name
from .mapping import Unsupported
from .module import header_package
from .naming import RESERVED_MEMBERS, pascal
from .source import Source, escape_doc

OVERRIDE = "@java.lang.Override"


def _static(member: CallablePlan) -> bool:
    return member.kind is CallableKind.STATIC


def _virtual(member: CallablePlan) -> bool:
    return isinstance(member.source, Method) and member.source.is_virtual


def _signature(parameters: list[str]) -> tuple[str, ...]:
    """The types of the parameters of a Java method, which is what overloads are told apart by."""
    found = []
    for parameter in parameters:
        type_ = parameter.rsplit(" ", 1)[0]
        # Type arguments nest (``Function<Integer, Consumer<String>>``): remove the innermost first.
        while re.search(r"<[^<>]*>", type_):
            type_ = re.sub(r"<[^<>]*>", "", type_)
        found.append(type_)
    return tuple(found)


def _cpp_signature(member: CallablePlan) -> tuple[object, ...] | None:
    """What C++ tells overloads apart by: the types of the parameters (a ``std::string`` and a
    ``const char*`` are one ``String`` in Java, a ``long`` and a ``long long`` one ``long``), and
    whether the method is ``const``. A virtual method overrides one of the base with the same."""
    source = member.source
    if not isinstance(source, Method):
        return None
    return _method_signature(source)


def _method_signature(source: Method) -> tuple[object, ...]:
    return (
        tuple((parameter.type, parameter.by_reference) for parameter in source.parameters),
        source.is_const,
    )


@dataclass(frozen=True, slots=True)
class _Member:
    """A method of a class as Java sees it: what a method of a class below it can override."""

    static: bool
    virtual: bool
    returns: str
    owner: str
    """The C++ class that declares it, for the messages."""

    cpp: tuple[object, ...] | None = None
    """Its signature in C++ (see ``_cpp_signature``)."""

    final: bool = False
    """The Java method is ``final``: what a class below declares with the same signature (C++
    overrides it) is the same, and Java would not let it override."""


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

        self.refusals: list[Diagnostic] = []
        """What the Java bindings cannot do in this class, all of it (not only the first)."""

    # -- what the classes above have -----------------------------------------------------------

    def inherited(self) -> dict[tuple[str, tuple[str, ...]], _Member]:
        """The methods of the classes above, as Java sees it: those they declare, those that they
        have of their other bases (the copies) and the ``final`` ones that stand for what they
        inherit and cannot be overridden."""
        if self._inherited is None:
            found: dict[tuple[str, tuple[str, ...]], _Member] = {}
            for above in reversed(self.chain):
                for member, declaring in [
                    *self._copies(above),
                    *((m, above) for m in above.members),
                ]:
                    try:
                        lowered = lower_callable(member, self.names, self.plan, declaring=declaring)
                    except Unsupported:
                        continue  # it is refused with the class that declares it
                    name = method_name(member.name + member.suffix, RESERVED_MEMBERS)
                    found[(name, _signature(lowered.parameters))] = _Member(
                        member.kind is CallableKind.STATIC,
                        _virtual(member),
                        lowered.returns,
                        declaring.model.qualified_name,
                        _cpp_signature(member),
                        self._final_in(above, member, name, copied=declaring is not above),
                    )
                for slot in [
                    *directors.dropped(self.names, above),
                    *directors.inherited(self.names, above),
                ]:
                    entry = slot.root_entry
                    typed = directors.types_of(entry, self.names)
                    found[(slot.name, _signature([f"{type_} {name}" for type_, name in typed]))] = (
                        _Member(
                            False,
                            True,
                            directors.result_type(entry, self.names),
                            above.model.qualified_name,
                            _method_signature(entry.method),
                            True,
                        )
                    )
            self._inherited = found
        return self._inherited

    def _final_in(
        self, holder: ClassPlan, member: CallablePlan, name: str, *, copied: bool
    ) -> bool:
        """True if the Java class ``holder`` has the method as a ``final`` one (see ``member``)."""
        if member.kind is CallableKind.STATIC:
            return False
        if holder.c_type not in self.names.directors and holder.c_type not in self.names.guarded:
            return False
        slot = None if copied else directors.slot_of(self.names, holder, member)
        return slot is None or slot.name != name

    def _copies(self, cls: ClassPlan) -> list[tuple[CallablePlan, ClassPlan]]:
        """The methods that ``cls`` has of its bases that are not its first (the ones that
        ``secondary_base`` writes): a name that the class declares, or that an earlier base has,
        is not copied."""
        names = {method_name(m.name + m.suffix, RESERVED_MEMBERS) for m in cls.members}
        chain = {above.c_type for above in primary_chain(cls, self.by_type)}
        copies: list[tuple[CallablePlan, ClassPlan]] = []
        for view in cls.views:
            if view.c_type in chain or view.c_type == cls.c_type:
                continue
            base = self.by_type[view.c_type]
            for member in base.members:
                name = method_name(member.name + member.suffix, RESERVED_MEMBERS)
                if member.kind is CallableKind.STATIC or name in names:
                    continue
                names.add(name)
                copies.append((member, base))
        return copies

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
        if copied:
            # The class has the methods of its other bases too, but C++ would not let it call
            # a name that two of its bases have: the conversion to the base has it.
            return False
        static = member.kind is CallableKind.STATIC
        # A static method hides the static one above, as in C++. A method replaces the one above
        # only if C++ overrides it: both virtual, with the same parameters. Anything else that has
        # the Java signature of one above would make a call through the base class run this one.
        same_kind = (static and above.static) or (
            not static
            and not above.static
            and _virtual(member)
            and above.virtual
            and _cpp_signature(member) == above.cpp
        )
        if same_kind and self._returns_ok(lowered.returns, above.returns):
            return True
        raise GenerationError(
            [
                Diagnostic(
                    f"'{member.function.description}' gets the Java method '{name}("
                    f"{', '.join(_signature(lowered.parameters))})', which '{above.owner}' has "
                    "too: Java cannot hide a method (a method can only replace one that C++ "
                    "overrides, a virtual method with the same parameters that gives the same "
                    "result, or a static one): rename one of them in C++, or generate the other "
                    "languages without 'jvm'",
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
            *self.director_notes(),
        )
        # The constructors use the object (to keep what C++ keeps), and subclasses are the code
        # of the user.
        src.line('@java.lang.SuppressWarnings("this-escape")')
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
            enum_class(src, nested, nested=True, modern=self.names.flavor.modern)
        for member in cls.members:
            self.attempt(self.member, src, member)
        self.attempt(self.director_members, src)
        self.attempt(self.inherited_members, src)
        for view in cls.views:
            if view.c_type in {above.c_type for above in self.chain} or view.c_type == cls.c_type:
                continue
            self.attempt(self.secondary_base, src, view)
        src.close()
        if self.refusals:
            raise GenerationError(self.refusals)

    def attempt(self, action: Callable[..., None], *arguments: object) -> None:
        """Does ``action``; what the bindings cannot do is noted, and the rest of the class is
        still written, so that every problem of a class is reported at once."""
        try:
            action(*arguments)
        except Unsupported as error:
            self.refusals.append(Diagnostic(f"Java: {error}", error.location))
        except GenerationError as error:
            self.refusals.extend(error.diagnostics)

    # -- construction -----------------------------------------------------------------------

    def constructors(self, src: Source) -> None:
        """The constructors of a class. Those that Java cannot tell apart (a ``const std::string&``
        and a ``const char*`` are both a ``String``) are static methods, ``create`` and the suffix
        of the C API, as in the other languages."""
        cls, name, names, plan = self.cls, self.name, self.names, self.plan
        lowered: list[tuple[CallablePlan, Lowering]] = []
        for ctor in cls.constructors:
            try:
                lowered.append((ctor, lower_callable(ctor, names, plan, declaring=cls)))
            except Unsupported as error:
                self.refusals.append(Diagnostic(f"Java: {error}", error.location))
        count = Counter(_signature(item.parameters) for _, item in lowered)
        d = names.directors.get(cls.c_type)
        for ctor, item in lowered:
            src.line()
            src.doc(ctor.function.description, *item.remarks)
            src.line(f'@BridgefexFunction("{ctor.function.name}")')
            if count[_signature(item.parameters)] == 1:
                src.lines_of(item.annotations)
                src.open(f"public {name}({', '.join(item.parameters)})")
                # The handle is made before the call: the destruction of what C++ makes is
                # guaranteed from the moment the C function writes the pointer.
                src.line(f"super({names.destroyer(cls.c_type)});")
                director_ctor = directors.director_constructor(cls, ctor) if d is not None else None
                if d is None or director_ctor is None:
                    src.lines_of(item.body)
                else:
                    # A class of the user that derives from this one and overrides something
                    # makes a director; this class itself, or one that overrides nothing, a plain
                    # object.
                    made = lower_callable(
                        director_ctor,
                        names,
                        plan,
                        declaring=cls,
                        prelude=directors.vtable_prelude(names, d),
                    )
                    src.line("boolean[] __over = DirectorOverrides.of(getClass(), false);")
                    src.open("if (__over == null)")
                    src.lines_of(item.body)
                    src.close()
                    src.open("else")
                    src.lines_of(made.body)
                    src.close()
                src.close()
                continue
            factory = method_name(f"create{ctor.suffix}", RESERVED_MEMBERS | set(self.taken))
            self.taken[factory] = ctor.function.description
            made = lower_callable(ctor, names, plan, declaring=cls, factory=name)
            src.lines_of(made.annotations)
            src.open(f"public static {name} {factory}({', '.join(made.parameters)})")
            src.lines_of(made.body)
            src.close()
        if d is not None:
            self.protected_constructors(
                src, d, {_signature(item.parameters) for _, item in lowered}
            )

    def protected_constructors(
        self, src: Source, d: directors.DirectorClass, signatures: set[tuple[str, ...]]
    ) -> None:
        """The constructors that only a class of the user can call: the ones of the C++ class that
        make no plain object (protected ones, or all of them if the class is abstract)."""
        cls, name, names, plan = self.cls, self.name, self.names, self.plan
        assert cls.director is not None
        plain = {ctor.source for ctor in cls.constructors}
        for director_ctor in cls.director.constructors:
            if director_ctor.source in plain:
                continue
            made = lower_callable(
                director_ctor,
                names,
                plan,
                declaring=cls,
                prelude=directors.vtable_prelude(names, d),
            )
            signature = _signature(made.parameters)
            if signature in signatures:
                continue  # Java cannot tell it from another constructor
            signatures.add(signature)
            src.line()
            src.doc(director_ctor.function.description, *made.remarks)
            src.line(f'@BridgefexFunction("{director_ctor.function.name}")')
            src.lines_of(made.annotations)
            src.open(f"protected {name}({', '.join(made.parameters)})")
            src.line(f"super({names.destroyer(cls.c_type)});")
            src.line("boolean[] __over = DirectorOverrides.of(getClass(), true);")
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
            f"public static {name} borrow({names.flavor.segment} pointer, "
            "BridgefexObject[] owners, boolean readOnly)"
        )
        src.open("if (pointer.address() == 0L)")
        src.line("return null;")
        src.close()
        src.line(
            f"{names.flavor.declare('BridgefexHandle')} handle = new BridgefexHandle("
            f"BridgefexNative::{names.destroyers[cls.c_type]}, false);"
        )
        src.line("handle.assign(pointer);")
        src.line("return BridgefexObject.borrowed(wrap(handle), owners, readOnly);")
        src.close()

    # -- the views of the bases -------------------------------------------------------------

    def views(self, src: Source) -> None:
        """``hold_<base>``: the object seen as each class that it derives from by its first bases
        (and as itself, if others derive from it)."""
        cls = self.cls
        pointer = self.names.flavor.segment
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
        # The locals have the prefix of the generated ones: a module can be named ``lease``.
        declare = self.names.flavor.declare
        src.line(f"{declare('BridgefexLease')} __lease = hold(mutable, give);")
        src.open("try")
        src.line(
            f"{pointer} __view = mutable ? BridgefexNative.{view.upcast.name}(__lease.pointer())"
            f" : BridgefexNative.{view.const_upcast.name}(__lease.pointer());"
        )
        src.line("return __lease.as(__view);")
        src.close()
        src.open("catch (java.lang.Throwable __error)")
        src.line("__lease.close();")
        src.line("throw __error;")
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
        self.upcast(src, view, self.names.flavor.segment)
        src.close()
        accessor = method_name(f"as{base_name}", RESERVED_MEMBERS | set(self.taken))
        self.taken[accessor] = f"the conversion to '{base.model.qualified_name}'"
        src.line()
        src.doc(
            f"This object as a {escape_doc(base.model.qualified_name)}, which belongs to it: it is "
            "valid while this object is, and closing it does not destroy this one."
        )
        src.open(f"public {base_type} {accessor}()")
        declare = names.flavor.declare
        src.open(f"try ({declare('BridgefexLease')} __lease = hold(false, false))")
        src.line(
            f"{declare(base_type)} __view = {base_type}.borrow("
            f"BridgefexNative.{view.const_upcast.name}(__lease.pointer()), "
            "new BridgefexObject[] {this}, isReadOnly());"
        )
        src.line("__view.setOrigin(this);")
        src.line("return __view;")
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
        above = self.inherited().get(key)
        if above is not None and above.final and not copied and not _static(member):
            # The method above is ``final`` and C++ overrides it: calling it runs the override,
            # and Java would not let this class declare it again.
            return
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
            _cpp_signature(member),
        )
        self.taken.setdefault(name, member.function.description)
        slot = None if copied else directors.slot_of(self.names, cls, member)
        if slot is not None and slot.name != name:
            # The class names the method differently from the class that declares it (an overload
            # that the first one does not have): an override of this name is not what the director
            # looks for, so it is ``final``. The method of the other name is the one to override.
            slot = None
        body = lowered.body
        helpers: list[list[str]] = []
        if slot is not None:
            body, helpers = directors.virtual_body(
                self.plan,
                self.names,
                cls,
                slot.entry,
                lowered.body,
                returns=lowered.returns,
                arguments=[parameter.rsplit(" ", 1)[1] for parameter in lowered.parameters],
            )
        final = member.kind is not CallableKind.STATIC and slot is None and self.derivable_family()
        remarks = list(lowered.remarks)
        if slot is not None:
            remarks.append(
                "Virtual: a class of yours can override it, and C++ calls the override"
                + (" (it is pure virtual: it has to be overridden)." if slot.entry.is_pure else ".")
            )
        elif final:
            remarks.append("Final: C++ would not call an override of it.")
        src.line()
        src.doc(member.function.description, *remarks)
        src.line(f'@BridgefexFunction("{member.function.name}")')
        src.lines_of(lowered.annotations)
        keyword = "public static " if member.kind is CallableKind.STATIC else "public "
        if final:
            # C++ would not call an override of it: Java says so when the class is compiled.
            keyword += "final "
        src.open(f"{keyword}{lowered.returns} {name}({', '.join(lowered.parameters)})")
        src.lines_of(body)
        src.close()
        self.helpers(src, helpers)

    @staticmethod
    def helpers(src: Source, helpers: list[list[str]]) -> None:
        """The private methods that the body of a virtual method calls."""
        for helper in helpers:
            src.line()
            src.lines_of(helper)

    def director_notes(self) -> list[str]:
        """What the documentation of the class says of deriving from it, in a module with
        directors."""
        c_type = self.cls.c_type
        if c_type in self.names.directors:
            if self.subclassable():
                return [
                    "A class of yours can derive from it: it overrides the virtual methods that "
                    "are not final, and C++ calls the overrides."
                ]
            return [
                "Java cannot derive from it: C++ tells its constructors apart by types that Java "
                "cannot, so they are static create... methods and a subclass has none to call."
            ]
        if c_type in self.names.guarded:
            return [
                "Java cannot derive from it with effect: C++ would not call the overrides of "
                "its methods (it has no director, or the one it has cannot be used), so they "
                "are final."
            ]
        return []

    def subclassable(self) -> bool:
        """True if a class of the user can call a constructor of the class: one that Java tells
        apart from the others, or a protected one."""
        cls = self.cls
        signatures: Counter[tuple[str, ...]] = Counter()
        for ctor in cls.constructors:
            try:
                signatures[
                    _signature(
                        lower_callable(ctor, self.names, self.plan, declaring=cls).parameters
                    )
                ] += 1
            except Unsupported:
                continue
        if any(count == 1 for count in signatures.values()):
            return True
        if cls.director is None:
            return False
        plain = {ctor.source for ctor in cls.constructors}
        for director_ctor in cls.director.constructors:
            if director_ctor.source in plain:
                continue
            try:
                made = lower_callable(director_ctor, self.names, self.plan, declaring=cls)
            except Unsupported:
                continue
            if _signature(made.parameters) not in signatures:
                return True
        return False

    def derivable_family(self) -> bool:
        """True for a class that others derive from, and for those below one that cannot be."""
        return self.cls.c_type in self.names.directors or self.cls.c_type in self.names.guarded

    # -- the director -----------------------------------------------------------------------

    def final_delegator(self, src: Source, slot: directors.Slot, why: str) -> None:
        """A ``final`` method that does what the one of the class above does: an override of the
        method would never be called by C++ (``why``), and Java refuses it."""
        names = self.names
        typed = directors.types_of(slot.root_entry, names)
        parameters = ", ".join(f"{type_} {name}" for type_, name in typed)
        arguments = ", ".join(name for _, name in typed)
        # Overloads are told apart by the types of the parameters, not by the name.
        key = (slot.name, _signature([f"{type_} {name}" for type_, name in typed]))
        if key in self.signatures:
            return  # the class declares it too (and it is ``final``)
        above = self.inherited().get(key)
        if above is not None and above.final:
            return  # a class above has made it ``final`` already
        returns = directors.result_type(slot.root_entry, names)
        access = "public" if slot.root_entry.access is Access.PUBLIC else "protected"
        self.taken.setdefault(slot.name, slot.root_entry.description)
        self.signatures[key] = _Member(False, True, returns, self.cls.model.qualified_name, None)
        src.line()
        src.doc(f"{slot.root_entry.description}: {why}")
        src.line(OVERRIDE)
        src.open(f"{access} final {returns} {slot.name}({parameters})")
        call = f"super.{slot.name}({arguments})"
        src.line(call + ";" if returns == "void" else f"return {call};")
        src.close()

    def inherited_members(self, src: Source) -> None:
        """A class that Java cannot derive from, below one that it can: what it inherits that C++
        calls the overrides of is ``final`` in it."""
        for slot in directors.inherited(self.names, self.cls):
            self.final_delegator(
                src,
                slot,
                "C++ calls the overrides of this method only for the classes that have a director, "
                "which this one does not, so it cannot be overridden from Java.",
            )

    def director_members(self, src: Source) -> None:
        """What the class has for its director besides the wrappers: the virtual methods that are
        not part of the API, the bridges that the thunks call, and the check of what a class of
        the user overrides."""
        cls, names, plan = self.cls, self.names, self.plan
        d = names.directors.get(cls.c_type)
        if d is None:
            return
        for slot in d.slots:
            entry = slot.entry
            if not directors.emits(names, cls, entry):
                continue
            typed = directors.types_of(entry, names)
            parameters = ", ".join(f"{type_} {name}" for type_, name in typed)
            arguments = ", ".join(name for _, name in typed)
            returns = directors.result_type(entry, names)
            if not any(item.source == entry.method for item in cls.members):
                if slot.name in self.taken:
                    raise GenerationError(
                        [
                            Diagnostic(
                                f"'{entry.description}' gets the Java method '{slot.name}', which "
                                f"'{self.taken[slot.name]}' has",
                                entry.location,
                            )
                        ]
                    )
                self.taken[slot.name] = entry.description
                body, helpers = directors.virtual_body(
                    plan,
                    names,
                    cls,
                    entry,
                    None,
                    returns=returns,
                    arguments=[name for _, name in typed],
                )
                src.line()
                src.doc(entry.description)
                if entry.base is not None:
                    src.line(f'@BridgefexFunction("{entry.base.function.name}")')
                src.open(f"protected {returns} {slot.name}({parameters})")
                src.lines_of(body)
                src.close()
                self.helpers(src, helpers)
            if slot.root is cls and entry.access is not Access.PUBLIC:
                # The thunks are in another package: they cannot call a protected method.
                src.line()
                src.doc(
                    "For the generated code: C++ calls the override of the method through this."
                )
                call = f"{slot.name}({arguments})"
                src.open(f"public final {returns} bridgefex_{slot.name}({parameters})")
                src.line(call + ";" if returns == "void" else f"return {call};")
                src.close()
        for slot in directors.dropped(names, cls):
            self.final_delegator(
                src,
                slot,
                "the C++ class implements it where it cannot be overridden, so it cannot be "
                "overridden from Java either.",
            )
        directors.overrides_helper(src, names, d)


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
    for imported in dict.fromkeys(
        (
            "BridgefexFunction",
            "BridgefexHandle",
            "BridgefexLease",
            "BridgefexNative",
            "BridgefexObject",
            "BridgefexRuntime",
            "BridgefexUnsigned",
            *names.imports,
        )
    ):
        src.line(f"import {names.root}.{imported};")
    src.line()
    _Class(plan, cls, names, by_type).write(src)
    return src.text()
