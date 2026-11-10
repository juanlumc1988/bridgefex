# SPDX-License-Identifier: Apache-2.0
"""The C# files of a module: the runtime, the P/Invoke declarations and one file per header."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from ...errors import Diagnostic, GenerationError
from ...model import Access, EnumRef, ScalarType, StructRef
from ...plan import (
    CallableKind,
    CallablePlan,
    CFunction,
    ClassPlan,
    ClassView,
    EnumPlan,
    FfiKind,
    FunctionPlan,
    HeaderPlan,
    ModulePlan,
    StructPlan,
)
from . import directors
from .callbacks import has_callbacks
from .lower import Names, lower_callable
from .mapping import (
    CDECL_ATTRIBUTE,
    COMPILER,
    INTEROP,
    Unsupported,
    native_type,
    scalar,
)
from .naming import CS_KEYWORDS, LOCAL_PREFIX, RESERVED_MEMBERS, identifier, parameter, pascal
from .source import Source

STRUCT_LAYOUT = f"[{INTEROP}.StructLayout({INTEROP}.LayoutKind.Sequential)]"

_FILE_TYPES = frozenset(
    """
    Runtime Native Lease Block StringBlock Keep ObjectHandle BridgefexObject BridgefexException
    BridgefexFunction BridgefexFunctionAttribute Functions BridgefexCallbacks CallScope CallbackBox
    Thunks
    """.split()
)
"""Types of the root namespace that a type of a header must not hide."""


def assembly_name(plan: ModulePlan) -> str:
    """The name of the assembly: not the one of the module, whose native library (``demo.dll``) has
    the same file name as ``Demo.dll`` where the file system ignores the case (Windows, macOS)."""
    return f"{namespace(plan)}.Bindings"


def namespace(plan: ModulePlan) -> str:
    return pascal(plan.name)


def header_namespace(plan: ModulePlan, header: HeaderPlan) -> str:
    return f"{namespace(plan)}.{pascal(header.stem)}"


def build_names(plan: ModulePlan) -> Names:
    root = namespace(plan)
    classes: dict[str, str] = {}
    enums: dict[str, str] = {}
    structs: dict[str, str] = {}
    handles: dict[str, str] = {}
    enum_plans: dict[str, EnumPlan] = {}
    struct_plans: dict[str, StructPlan] = {}
    errors: list[Diagnostic] = []
    for header in plan.headers:
        prefix = f"global::{header_namespace(plan, header)}"
        taken: dict[str, str] = {}

        def claim(
            name: str, what: str, header: HeaderPlan = header, taken: dict[str, str] = taken
        ) -> None:
            if name in _FILE_TYPES:
                errors.append(
                    Diagnostic(
                        f"'{what}' gets the C# name '{name}', which the generated code of "
                        f"namespace {root} uses",
                        header.header.declarations[0].location
                        if header.header.declarations
                        else None,
                    )
                )
            if name in taken:
                errors.append(
                    Diagnostic(
                        f"'{what}' and '{taken[name]}' get the C# name '{name}' in {header.stem}.h"
                    )
                )
            taken[name] = what

        for item in header.items:
            if isinstance(item, ClassPlan):
                name = pascal(item.model.name)
                claim(name, item.model.qualified_name)
                claim(f"{name}Handle", f"the handle of '{item.model.qualified_name}'")
                classes[item.c_type] = f"{prefix}.{name}"
                handles[item.c_type] = f"{prefix}.{name}Handle"
                for nested in item.enums:
                    hidden = pascal(nested.model.name)
                    if hidden in RESERVED_MEMBERS:
                        errors.append(
                            Diagnostic(
                                f"'{nested.model.qualified_name}' gets the C# name '{hidden}', "
                                f"which the generated code of class '{name}' uses"
                            )
                        )
                    enums[nested.c_type] = f"{prefix}.{name}.{hidden}"
                    enum_plans[nested.model.usr] = nested
            elif isinstance(item, EnumPlan):
                name = pascal(item.model.name)
                claim(name, item.model.qualified_name)
                enums[item.c_type] = f"{prefix}.{name}"
                enum_plans[item.model.usr] = item
            elif isinstance(item, StructPlan):
                name = pascal(item.model.name)
                claim(name, item.model.qualified_name)
                structs[item.c_type] = f"{prefix}.{name}"
                struct_plans[item.model.usr] = item
                for nested in item.enums:
                    enums[nested.c_type] = f"{prefix}.{name}.{pascal(nested.model.name)}"
                    enum_plans[nested.model.usr] = nested
    if errors:
        raise GenerationError(errors)
    large = frozenset(
        plan_.c_type
        for plan_ in struct_plans.values()
        if _size_bound(plan_, struct_plans) > LARGE_STRUCT
    )
    derivable = directors.build(plan)
    return Names(
        root,
        classes,
        enums,
        structs,
        handles,
        enum_plans,
        struct_plans,
        large,
        has_callbacks(plan) or bool(derivable),
        bool(derivable),
        derivable,
    )


LARGE_STRUCT = 4096
"""A struct whose mirror may be bigger than this many bytes does not go on the stack."""


def _size_bound(struct: StructPlan, plans: dict[str, StructPlan]) -> int:
    """An upper bound of the size of the mirror of a struct (padding aside)."""
    total = 0
    for field_plan in struct.fields:
        model = field_plan.model
        type_ = model.type
        size = _size_bound(plans[type_.usr], plans) if isinstance(type_, StructRef) else 8
        total += size * (model.length or 1)
    return total


# Native.cs -----------------------------------------------------------------------------


def native_source(plan: ModulePlan, names: Names) -> str:
    root = namespace(plan)
    src = Source()
    src.line("// Generated by bridgefex. Do not edit.")
    src.line("#nullable enable")
    src.line("#pragma warning disable CS1591, CS0169, CS0649, CS0108, CS0109")
    src.line("using System;")
    src.line("using System.Runtime.CompilerServices;")
    src.line("using System.Runtime.InteropServices;")
    src.line()
    src.line(f"namespace {root};")
    src.line()
    src.line("internal static unsafe partial class Native")
    src.open()
    src.line("static Native()")
    src.open()
    src.line("Runtime.Initialize();")
    src.close()
    functions: list[CFunction] = []
    for header in plan.headers:
        functions.extend(header.c_functions)
    # The strings of the containers: (data, size), also in the arrays that C++ gives.
    for mirror in ("StringIn", "StringOut"):
        src.line()
        src.line(
            "[global::System.Runtime.InteropServices.StructLayout(global::System.Runtime.InteropServices.LayoutKind.Sequential)]"
        )
        src.open(f"internal struct {mirror}")
        src.line("public byte* Data;")
        src.line("public nuint Size;")
        src.close()
    _declare(src, plan.last_error, "byte*", "")
    _declare(src, plan.api_fingerprint_function, "byte*", "")
    _declare(src, plan.free_function, "void", "nint memory")
    if names.has_callbacks:
        # For the callbacks that C++ keeps (see BridgefexCallbacks.cs).
        _declare(src, plan.enable_callbacks, "void", "nint release")
        _declare(src, plan.disable_callbacks, "void", "nint release")
    for function in functions:
        _declare_function(src, function)
    for line in mirrors_source(plan, names):
        src.line(line)
    if names.directors:
        _declare(src, plan.string_sink_set, "void", "nint sink, byte* data, nuint size")
        for d in names.directors.values():
            director = d.cls.director
            assert director is not None
            src.line()
            src.line(STRUCT_LAYOUT)
            src.open(f"internal struct {director.vtable_type}")
            src.line("public nint user_data;")
            src.line("public nint release;")
            for entry in director.entries:
                src.line(f"public nint {identifier(entry.field)};")
            src.close()
            for function in director.functions:
                _declare_function(src, function)
    src.close()
    return src.text()


def _declare(src: Source, name: str, returns: str, parameters: str) -> None:
    src.line()
    src.line(f"[{INTEROP}.LibraryImport(Runtime.Library)]")
    src.line(CDECL_ATTRIBUTE)
    src.line(f"internal static partial {returns} {name}({parameters});")


def _declare_function(src: Source, function: CFunction) -> None:
    try:
        parameters = ", ".join(
            f"{native_type(param.ffi)} {identifier(param.name)}" for param in function.params
        )
        returns = native_type(function.ffi_restype)
    except Unsupported as error:
        raise Unsupported(f"'{function.description}': {error}") from error
    if function.ffi_restype.kind is FfiKind.STATUS:
        returns = "int"
    _declare(src, function.name, returns, parameters)


# One file per header ----------------------------------------------------------------------


def header_source(plan: ModulePlan, header: HeaderPlan, names: Names) -> str:
    src = Source()
    src.line(f'// Generated by bridgefex from "{header.stem}.h". Do not edit.')
    src.line("#nullable enable")
    src.line("#pragma warning disable CS1591, CS0108, CS0109")
    src.line("using System;")
    src.line("using System.Runtime.InteropServices;")
    src.line()
    src.line(f"namespace {header_namespace(plan, header)};")
    by_type = {
        item.c_type: item
        for other in plan.headers
        for item in other.items
        if isinstance(item, ClassPlan)
    }
    functions: list[FunctionPlan] = []
    for item in header.items:
        if isinstance(item, ClassPlan):
            src.line()
            _class(src, plan, item, names, by_type)
        elif isinstance(item, EnumPlan):
            src.line()
            _enum(src, item)
        elif isinstance(item, FunctionPlan):
            functions.append(item)
        else:
            src.line()
            _struct(src, plan, item, names)
    if functions:
        src.line()
        _functions(src, plan, functions, names)
    return src.text()


def _enum(src: Source, enum: EnumPlan) -> None:
    underlying = scalar(enum.model.underlying.c_name).public
    src.doc(enum.comment)
    src.open(f"public enum {pascal(enum.model.name)} : {underlying}")
    seen: dict[str, str] = {}
    for item in enum.enumerators:
        name = pascal(item.model_name)
        if name in seen:
            raise GenerationError(
                [
                    Diagnostic(
                        f"the enumerators '{seen[name]}' and '{item.model_name}' of "
                        f"'{enum.model.qualified_name}' get the C# name '{name}'"
                    )
                ]
            )
        seen[name] = item.model_name
        src.line(f"{identifier(name)} = {item.value},")
    src.close()


def _functions(src: Source, plan: ModulePlan, functions: list[FunctionPlan], names: Names) -> None:
    src.open("public static unsafe class Functions")
    taken: dict[str, str] = {}
    reserved = RESERVED_MEMBERS | {"Functions"}  # a member cannot have the name of its class
    for position, item in enumerate(functions):
        if position:
            src.line()
        call = item.callable
        _method(src, plan, call, names, taken, static=True, reserved=reserved)
    src.close()


def _method(
    src: Source,
    plan: ModulePlan,
    call: CallablePlan,
    names: Names,
    taken: dict[str, str],
    *,
    static: bool,
    modifiers: str = "",
    reserved: frozenset[str] = RESERVED_MEMBERS,
    declaring: ClassPlan | None = None,
    virtual: tuple[directors.Slot, str] | None = None,
) -> str:
    name = pascal(call.name + call.suffix)
    while name in reserved:
        name += "_"
    if virtual is not None:
        # The name that the class that declares the virtual method gave it, whatever its overloads.
        name = virtual[0].name
    if name in taken:
        raise GenerationError(
            [
                Diagnostic(
                    f"'{call.function.description}' gets the C# name '{name}', which "
                    + (
                        f"'{taken[name]}' has" if name in taken else "every generated class defines"
                    ),
                    call.location,
                )
            ]
        )
    taken[name] = call.function.description
    lowered = lower_callable(call, names, plan, declaring=declaring)
    body = lowered.body
    if virtual is not None:
        assert declaring is not None
        slot, how = virtual
        modifiers = f"{how} "
        body = directors.virtual_body(
            plan, names, declaring, slot.entry, body, returns=lowered.returns
        )
    src.doc(call.function.description)
    src.remarks(lowered.remarks)
    src.line(f'[BridgefexFunction("{call.function.name}")]')
    keyword = "public static " if static else "public "
    header = (
        f"{keyword}{modifiers}{lowered.returns} {identifier(name)}({', '.join(lowered.parameters)})"
    )
    src.open(header)
    src.lines_of(body)
    src.close()
    return name


def _handle_class(src: Source, cls: ClassPlan, handle: str) -> None:
    """The SafeHandle of a class: it destroys the C++ object when it owns it."""
    src.open(f"internal sealed class {handle} : ObjectHandle")
    src.line(f"public {handle}() : base(true)")
    src.open()
    src.close()
    src.line(f"public {handle}(bool owns) : base(owns)")
    src.open()
    src.close()
    src.open("protected override void Destroy()")
    src.line(f"Native.{cls.destroy.name}(handle);")
    src.close()
    src.close()
    src.line()


def _borrow(src: Source, name: str, handle: str) -> None:
    """``Borrow``: the wrapper of an object that C++ owns (it keeps its ``owners`` alive)."""
    src.line()
    src.open(
        f"internal static {name}? Borrow(nint pointer, BridgefexObject?[]? owners, bool readOnly)"
    )
    src.line("if (pointer == 0)")
    src.open()
    src.line("return null;")
    src.close()
    src.line(f"var handle = new {handle}(false);")
    src.line("handle.Assign(pointer);")
    src.line(f"var result = new {name}(handle);")
    src.line("result.ReadOnly = readOnly;")
    src.line("result.Attach(owners);")
    src.line("return result;")
    src.close()


def _views(src: Source, cls: ClassPlan, chain: set[str]) -> None:
    """``Hold_<base>``: the object seen as each class that it derives from by its first bases
    (and as itself, if others derive from it)."""
    if cls.is_base:
        src.line()
        src.line(
            f"internal virtual Lease Hold_{cls.c_type}(bool mutable, bool give = false) "
            "=> Hold(mutable, give);"
        )
    for view in cls.views:
        if view.c_type in chain and view.c_type != cls.c_type:
            src.line()
            src.open(f"internal override Lease Hold_{view.c_type}(bool mutable, bool give = false)")
            src.line("Lease lease = Hold(mutable, give);")
            src.line(
                f"return lease.As(mutable ? Native.{view.upcast.name}(lease.Pointer) "
                f": Native.{view.const_upcast.name}(lease.Pointer));"
            )
            src.close()


def _signature(parameters: list[str]) -> tuple[str, ...]:
    """The types of the parameters of a C# method, which is what overloads are told apart by."""
    return tuple(parameter.rsplit(" ", 1)[0] for parameter in parameters)


def _constructors(
    src: Source,
    plan: ModulePlan,
    cls: ClassPlan,
    names: Names,
    *,
    handle: str,
    taken: dict[str, str],
    reserved: frozenset[str],
) -> None:
    """The constructors of a class. Those that C# cannot tell apart (a ``const std::string&`` and
    a ``const char*`` are both a ``string``) are static methods, ``Create`` and the suffix of the
    C API, as in the Python bindings."""
    name = pascal(cls.model.name)
    d = names.directors.get(cls.c_type)
    lowered = [(ctor, lower_callable(ctor, names, plan)) for ctor in cls.constructors]
    count = Counter(_signature(item.parameters) for _, item in lowered)
    for ctor, item in lowered:
        src.line()
        src.doc(ctor.function.description)
        src.remarks(item.remarks)
        src.line(f'[BridgefexFunction("{ctor.function.name}")]')
        if count[_signature(item.parameters)] == 1:
            src.open(f"public {name}({', '.join(item.parameters)}) : base(new {handle}())")
            director_ctor = directors.director_constructor(cls, ctor) if d is not None else None
            if d is None or director_ctor is None:
                src.lines_of(item.body)
            else:
                # A class of the user that derives from this one and overrides something makes a
                # director; this class itself, or one that overrides nothing, a plain object.
                made = lower_callable(
                    director_ctor, names, plan, prelude=directors.vtable_prelude(names, d)
                )
                src.line(f"bool[]? {LOCAL_PREFIX}over = DirectorOverrides(GetType(), false);")
                src.open(f"if ({LOCAL_PREFIX}over is null)")
                src.lines_of(item.body)
                src.close()
                src.open("else")
                src.lines_of(made.body)
                src.close()
            src.close()
            continue
        factory = pascal(f"create{ctor.suffix}")
        while factory in reserved or factory in taken:
            factory += "_"
        taken[factory] = ctor.function.description
        made = lower_callable(ctor, names, plan, factory=(name, handle))
        src.open(f"public static {name} {factory}({', '.join(made.parameters)})")
        src.lines_of(made.body)
        src.close()
    if d is not None:
        _protected_constructors(src, plan, cls, names, d, handle=handle, signatures=set(count))


def _protected_constructors(
    src: Source,
    plan: ModulePlan,
    cls: ClassPlan,
    names: Names,
    d: directors.DirectorClass,
    *,
    handle: str,
    signatures: set[tuple[str, ...]],
) -> None:
    """The constructors that only a class of the user can call: the ones of the C++ class that
    make no plain object (protected ones, or all of them if the class is abstract)."""
    assert cls.director is not None
    name = pascal(cls.model.name)
    plain = {ctor.source for ctor in cls.constructors}
    for director_ctor in cls.director.constructors:
        if director_ctor.source in plain:
            continue
        made = lower_callable(
            director_ctor, names, plan, prelude=directors.vtable_prelude(names, d)
        )
        signature = _signature(made.parameters)
        if signature in signatures:
            continue  # C# cannot tell it from another constructor
        signatures.add(signature)
        src.line()
        src.doc(director_ctor.function.description)
        src.remarks(made.remarks)
        src.line(f'[BridgefexFunction("{director_ctor.function.name}")]')
        src.open(f"protected {name}({', '.join(made.parameters)}) : base(new {handle}())")
        src.line(f"bool[] {LOCAL_PREFIX}over = DirectorOverrides(GetType(), true)!;")
        src.lines_of(made.body)
        src.close()


def _class(
    src: Source, plan: ModulePlan, cls: ClassPlan, names: Names, by_type: dict[str, ClassPlan]
) -> None:
    name = pascal(cls.model.name)
    handle = f"{name}Handle"
    base = names.classes[cls.bases[0].c_type] if cls.bases else "BridgefexObject"
    _handle_class(src, cls, handle)
    src.doc(cls.model.qualified_name)
    src.open(f"public unsafe partial class {name} : {base}")
    taken: dict[str, str] = {}
    inherited = _inherited_names(cls, by_type)
    # A member cannot have the name of its class nor of a type it defines.
    reserved = RESERVED_MEMBERS | {name} | {pascal(nested.model.name) for nested in cls.enums}
    src.line(f"internal {name}(ObjectHandle handle) : base(handle)")
    src.open()
    src.close()
    src.line()
    src.line(f"internal override global::System.Type CppType => typeof({name});")
    _constructors(src, plan, cls, names, handle=handle, taken=taken, reserved=reserved)
    _borrow(src, name, handle)
    chain = _primary_chain(cls, by_type)
    _views(src, cls, chain)
    for nested in cls.enums:
        src.line()
        _enum(src, nested)
    d = names.directors.get(cls.c_type)
    for member in cls.members:
        src.line()
        member_name = pascal(member.name + member.suffix)
        while member_name in reserved:
            member_name += "_"
        slot = _virtual_slot(names, cls, d, member)
        _method(
            src,
            plan,
            member,
            names,
            taken,
            static=member.kind is CallableKind.STATIC,
            modifiers="new " if slot is None and member_name in inherited else "",
            reserved=reserved,
            declaring=cls,
            virtual=slot,
        )
    if d is not None:
        _director_members(src, plan, cls, names, d, taken=taken)
    for view in cls.views:
        if view.c_type in chain or view.c_type == cls.c_type:
            continue
        # A base class that is not the first: C# has one base class, so the class has the
        # methods of the other ones itself, and is converted to them.
        _secondary_base(
            src,
            plan,
            cls,
            by_type[view.c_type],
            view,
            names=names,
            taken=taken,
            reserved=reserved,
            by_type=by_type,
        )
    src.close()


def _virtual_slot(
    names: Names, cls: ClassPlan, d: directors.DirectorClass | None, member: CallablePlan
) -> tuple[directors.Slot, str] | None:
    """The slot of the virtual method that the wrapper is, and how the C# method declares it."""
    if d is None or member.kind is not CallableKind.METHOD:
        return None
    for slot in d.slots:
        if slot.entry.method == member.source and directors.emits(names, cls, slot.entry):
            return slot, directors.modifier(names, cls, slot.entry)
    return None


def _director_members(
    src: Source,
    plan: ModulePlan,
    cls: ClassPlan,
    names: Names,
    d: directors.DirectorClass,
    *,
    taken: dict[str, str],
) -> None:
    """What the class has for its director besides the wrappers: the virtual methods that are not
    part of the API, the thunks that C++ calls, and the check of what a class of the user
    overrides."""
    for slot in d.slots:
        entry = slot.entry
        if not directors.emits(names, cls, entry):
            continue
        has_wrapper = any(item.source == entry.method for item in cls.members)
        if not has_wrapper:
            returns = directors.result_type(entry, names)
            parameters = ", ".join(
                f"{type_} {parameter(name)}" for type_, name in directors.types_of(entry, names)
            )
            if slot.name in taken:
                raise GenerationError(
                    [
                        Diagnostic(
                            f"'{entry.description}' gets the C# name '{slot.name}', which "
                            f"'{taken[slot.name]}' has",
                            entry.location,
                        )
                    ]
                )
            taken[slot.name] = entry.description
            body = directors.virtual_body(plan, names, cls, entry, None, returns=returns)
            src.line()
            src.doc(entry.description)
            if entry.base is not None:
                src.line(f'[BridgefexFunction("{entry.base.function.name}")]')
            how = directors.modifier(names, cls, entry)
            src.open(f"protected {how} {returns} {identifier(slot.name)}({parameters})")
            src.lines_of(body)
            src.close()
        method = identifier(slot.name)
        if slot.root is cls and entry.access is not Access.PUBLIC:
            # The thunks are outside the class: they call a method that is not protected.
            method = f"Bridgefex{slot.name}"
            arguments = ", ".join(parameter(name) for _, name in directors.types_of(entry, names))
            parameters = ", ".join(
                f"{type_} {parameter(name)}" for type_, name in directors.types_of(entry, names)
            )
            returns = directors.result_type(entry, names)
            src.line()
            call = f"{identifier(slot.name)}({arguments})"
            src.line(f"internal {returns} {method}({parameters}) => {call};")
        if slot.root is cls:
            names.thunks.director(
                slot.thunk,
                names.classes[cls.c_type],
                method,
                entry,
                names=names,
                sink_set=plan.string_sink_set,
            )
    directors.overrides_helper(src, names, d)


def _secondary_base(
    src: Source,
    plan: ModulePlan,
    cls: ClassPlan,
    base: ClassPlan,
    view: ClassView,
    *,
    names: Names,
    taken: dict[str, str],
    reserved: frozenset[str],
    by_type: dict[str, ClassPlan],
) -> None:
    name = pascal(cls.model.name)
    base_name = pascal(base.model.name)
    base_type = names.classes[base.c_type]
    src.line()
    src.open(f"internal Lease Hold_{base.c_type}(bool mutable, bool give = false)")
    src.line("Lease lease = Hold(mutable, give);")
    src.line(
        f"return lease.As(mutable ? Native.{view.upcast.name}(lease.Pointer) "
        f": Native.{view.const_upcast.name}(lease.Pointer));"
    )
    src.close()
    src.line()
    accessor = f"As{base_name}"
    while accessor in taken or accessor in reserved:
        accessor += "_"
    taken[accessor] = f"the conversion to '{base.model.qualified_name}'"
    src.doc(f"This object as a {base.model.qualified_name}, which belongs to it.")
    src.open(f"public {base_type} {accessor}()")
    src.line("using Lease lease = Hold(false);")
    src.line(
        f"var view = {base_type}.Borrow(Native.{view.const_upcast.name}(lease.Pointer), "
        f"new BridgefexObject?[] {{ this }}, ReadOnly)!;"
    )
    src.line("view.Origin = this;")
    src.line("return view;")
    src.close()
    src.line()
    src.line(f"public static implicit operator {base_type}({name} value) => value?.{accessor}()!;")
    # A method has the name it has in the class that declares it (`q()` of a class `Q` is
    # `Q_`), also in the classes that have a copy of it.
    declared = reserved | {pascal(base.model.name)} | {pascal(e.model.name) for e in base.enums}
    for member in base.members:
        if member.kind is CallableKind.STATIC:
            continue
        member_name = pascal(member.name + member.suffix)
        while member_name in declared:
            member_name += "_"
        if member_name in taken:
            continue  # the class declares it too: it hides the one of the base
        src.line()
        _method(
            src,
            plan,
            member,
            names,
            taken,
            static=False,
            modifiers="new " if member_name in _inherited_names(cls, by_type) else "",
            reserved=declared,
            declaring=base,
        )


def _primary_chain(cls: ClassPlan, by_type: dict[str, ClassPlan]) -> set[str]:
    """The classes that the C# class derives from (the first base of each)."""
    chain: set[str] = set()
    current = cls
    while current.bases:
        first = by_type[current.bases[0].c_type]
        chain.add(first.c_type)
        current = first
    return chain


def _inherited_names(cls: ClassPlan, by_type: dict[str, ClassPlan]) -> set[str]:
    names: set[str] = set()
    for c_type in _primary_chain(cls, by_type):
        for member in by_type[c_type].members:
            names.add(pascal(member.name + member.suffix))
    return names


_ = (CS_KEYWORDS,)


# Value structs -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Member:
    """A member of a value struct: its public face and its blittable one."""

    name: str
    native_name: str
    public: str
    native: str
    length: int | None
    element_public: str
    element_native: str
    to_native: str
    from_native: str
    check: str | None
    is_struct: bool = False
    """The member is a struct: it is written and read in place, through a pointer."""


_STRUCT_MEMBERS = (
    "Default",
    "ToNative",
    "FromNative",
    "Equals",
    "GetHashCode",
    "ToString",
    "GetType",
)


def _members(struct: StructPlan, names: Names) -> list[_Member]:
    # A field cannot take the name of the struct, of a type it defines, nor of a member that
    # the generated struct has (or hides, if it is one of ValueType).
    taken = {pascal(struct.model.name), *_STRUCT_MEMBERS, *RESERVED_MEMBERS}
    taken |= {pascal(nested.model.name) for nested in struct.enums}
    result: list[_Member] = []
    for field_plan in struct.fields:
        model = field_plan.model
        name = pascal(model.name)
        while name in taken:
            name += "_"
        taken.add(name)
        type_ = model.type
        check = None
        is_struct = False
        if isinstance(type_, ScalarType):
            info = scalar(type_.c_name)
            public, native = info.public, info.native
            to_native, from_native = info.to_native, info.from_native
        elif isinstance(type_, EnumRef):
            enum = names.enum_plans[type_.usr]
            info = scalar(enum.model.underlying.c_name)
            public, native = names.enum(enum), info.native
            to_native = info.to_native.replace("{}", f"({info.public}){{}}")
            from_native = f"({public}){info.from_native}"
            valid = enum.model.valid_range
            if valid is not None:
                check = f"{valid[0]}, {valid[1]}"
        elif isinstance(type_, StructRef):
            other = names.struct_plans[type_.usr]
            public, native = names.struct(other), f"Native.{other.c_type}"
            to_native = ""
            from_native = (
                f"{public}.FromNative(({native}*)global::System.Runtime.CompilerServices.Unsafe"
                ".AsPointer(ref {}))"
            )
            is_struct = True
        else:
            raise Unsupported(f"the member '{model.name}' of a struct is not supported in C# yet")
        length = model.length
        result.append(
            _Member(
                name=name,
                native_name=field_plan.name,
                public=f"{public}[]" if length is not None else public,
                native=f"{struct.c_type}_{field_plan.name}_Array" if length is not None else native,
                length=length,
                element_public=public,
                element_native=native,
                to_native=to_native,
                from_native=from_native,
                check=check,
                is_struct=is_struct,
            )
        )
    return result


def mirrors_source(plan: ModulePlan, names: Names) -> list[str]:
    """The blittable mirrors of the value structs, for Native.cs."""
    src = Source()
    for struct in plan.structs:
        members = _members(struct, names)
        for member in members:
            if member.length is not None:
                src.line()
                src.line(f"[{COMPILER}.InlineArray({member.length})]")
                src.open(f"internal struct {member.native}")
                src.line(f"private {member.element_native} _element;")
                src.close()
        src.line()
        src.line(
            "[global::System.Runtime.InteropServices.StructLayout(global::System.Runtime.InteropServices.LayoutKind.Sequential)]"
        )
        src.open(f"internal struct {struct.c_type}")
        for member in members:
            src.line(f"public {member.native} {identifier(member.native_name)};")
        src.close()
    return src.lines


def _struct(src: Source, plan: ModulePlan, struct: StructPlan, names: Names) -> None:
    name = pascal(struct.model.name)
    members = _members(struct, names)
    mirror = f"Native.{struct.c_type}"
    src.doc(struct.model.qualified_name)
    src.open(f"public unsafe partial struct {name}")
    for member in members:
        src.line(f"public {member.public} {identifier(member.name)};")
    for nested in struct.enums:
        src.line()
        _enum(src, nested)
    src.line()
    _default(src, struct, name, mirror, large=struct.c_type in names.large_structs)
    src.line()
    _write_native(src, members, mirror)
    src.line()
    _read_native(src, members, name, mirror)
    src.close()


def _default(src: Source, struct: StructPlan, name: str, mirror: str, *, large: bool) -> None:
    src.doc("A value with the members that C++ gives a default-constructed object.")
    src.open(f"public static {name} Default()")
    if large:
        src.line(f"using var raw = Block<{mirror}>.Alloc();")
        src.line(f"Runtime.Check(Native.{struct.init.name}(raw.Pointer));")
        src.line("return FromNative(raw.Pointer);")
    else:
        src.line(f"{mirror} raw = default;")
        src.line(f"Runtime.Check(Native.{struct.init.name}(&raw));")
        src.line("return FromNative(&raw);")
    src.close()


def _write_native(src: Source, members: list[_Member], mirror: str) -> None:
    """``ToNative``: in place, so that a struct with a big array is not copied through the stack."""
    src.open(f"internal void ToNative({mirror}* raw)")
    memory = "global::System.Runtime.InteropServices.NativeMemory"
    src.line(f"{memory}.Clear(raw, (nuint)sizeof({mirror}));")
    for member in members:
        field_name = identifier(member.name)
        raw = f"raw->{identifier(member.native_name)}"
        if member.length is None:
            _to_native(src, member, field_name, raw)
            continue
        message = f"{member.name} must have {member.length} elements"
        src.line(f"if ({field_name} is null || {field_name}.Length != {member.length})")
        src.open()
        src.line(f'throw new global::System.ArgumentException("{message}", "{member.name}");')
        src.close()
        src.open(f"for (int i = 0; i < {member.length}; i++)")
        _to_native(src, member, f"{field_name}[i]", f"{raw}[i]")
        src.close()
    src.close()


def _read_native(src: Source, members: list[_Member], name: str, mirror: str) -> None:
    src.open(f"internal static {name} FromNative({mirror}* raw)")
    src.line(f"{name} result = default;")
    for member in members:
        field_name = f"result.{identifier(member.name)}"
        raw = f"raw->{identifier(member.native_name)}"
        if member.length is None:
            src.line(f"{field_name} = {member.from_native.replace('{}', raw)};")
        else:
            src.line(f"{field_name} = new {member.element_public}[{member.length}];")
            src.open(f"for (int i = 0; i < {member.length}; i++)")
            src.line(f"{field_name}[i] = {member.from_native.replace('{}', f'{raw}[i]')};")
            src.close()
    src.line("return result;")
    src.close()


def _to_native(src: Source, member: _Member, value: str, target: str) -> None:
    if member.is_struct:
        unsafe_ = "global::System.Runtime.CompilerServices.Unsafe"
        pointer = f"({member.element_native}*){unsafe_}.AsPointer(ref {target})"
        src.line(f"{value}.ToNative({pointer});")
        return
    if member.check is not None:
        src.line(f'Runtime.CheckEnum((long){value}, {member.check}, "{member.name}");')
    src.line(f"{target} = {member.to_native.replace('{}', value)};")
