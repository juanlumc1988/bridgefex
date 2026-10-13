# SPDX-License-Identifier: Apache-2.0
"""Reads C++ headers with libclang and builds the model.

Only declarations written in the header itself are considered; everything
pulled in through ``#include`` is ignored. Anything that bridgefex cannot
translate correctly is reported as an error. Every problem in a header is
collected before giving up, so that the user can fix them in one go.
"""

from __future__ import annotations

import dataclasses
import functools
import os
import re
from dataclasses import dataclass
from pathlib import Path

from clang import cindex
from clang.cindex import (
    AccessSpecifier,
    AvailabilityKind,
    CursorKind,
    RefQualifierKind,
    TypeKind,
)

from . import libclang, naming, typemap
from .errors import ConfigurationError, Diagnostic, GenerationError, Location
from .model import (
    Class,
    Constructor,
    Declaration,
    Enumeration,
    Enumerator,
    EnumRef,
    Field,
    FieldType,
    Function,
    Header,
    Method,
    Parameter,
    ScalarType,
    StringKind,
    StringType,
    Struct,
    StructRef,
    Type,
    TypeCategory,
    VectorType,
    VisibleNames,
)


@dataclass(frozen=True, slots=True)
class ParseOptions:
    std: str = "c++17"
    include_dirs: tuple[str, ...] = ()
    defines: tuple[str, ...] = ()
    extra_args: tuple[str, ...] = ()

    def clang_args(self) -> list[str]:
        args = ["-x", "c++-header", f"-std={self.std}"]
        args += [f"-I{directory}" for directory in self.include_dirs]
        args += [f"-D{definition}" for definition in self.defines]
        args += list(self.extra_args)
        # MSVC gives an enum without a fixed underlying type the type int, and
        # truncates larger enumerators: with its target, they are rejected in
        # the parsed header (see parse_header), as on the other targets.
        args.append(_MSVC_ENUM_WARNING)
        return args


_MSVC_ENUM_WARNING = "-Wmicrosoft-enum-value"


@dataclass(frozen=True, slots=True)
class ParseResult:
    header: Header
    warnings: tuple[Diagnostic, ...]


def _optional_kinds(*names: str) -> frozenset[CursorKind]:
    return frozenset(getattr(CursorKind, name) for name in names if hasattr(CursorKind, name))


# Namespace-scope declarations that do not add anything callable.
_IGNORED_SCOPE_KINDS = frozenset(
    {
        CursorKind.TYPEDEF_DECL,
        CursorKind.TYPE_ALIAS_DECL,
        CursorKind.TYPE_ALIAS_TEMPLATE_DECL,
        CursorKind.USING_DIRECTIVE,
        CursorKind.USING_DECLARATION,
        CursorKind.NAMESPACE_ALIAS,
        CursorKind.STATIC_ASSERT,
        # Out-of-line definitions of members already declared in their class.
        CursorKind.CXX_METHOD,
        CursorKind.CONSTRUCTOR,
        CursorKind.DESTRUCTOR,
        CursorKind.CONVERSION_FUNCTION,
    }
) | _optional_kinds("CONCEPT_DECL")

_TEMPLATE_KINDS = frozenset(
    {
        CursorKind.CLASS_TEMPLATE,
        CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION,
        CursorKind.FUNCTION_TEMPLATE,
    }
)

# Class members that do not add anything callable, whatever their access.
_IGNORED_MEMBER_KINDS = frozenset(
    {
        CursorKind.CXX_ACCESS_SPEC_DECL,
        CursorKind.FRIEND_DECL,
        CursorKind.TYPEDEF_DECL,
        CursorKind.TYPE_ALIAS_DECL,
        CursorKind.TYPE_ALIAS_TEMPLATE_DECL,
        CursorKind.STATIC_ASSERT,
        CursorKind.USING_DECLARATION,
    }
)

_NESTED_TYPE_KINDS = frozenset(
    {
        CursorKind.CLASS_DECL,
        CursorKind.STRUCT_DECL,
        CursorKind.UNION_DECL,
        CursorKind.ENUM_DECL,
        CursorKind.CLASS_TEMPLATE,
        CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION,
    }
)

_OPERATOR = re.compile(r"operator(?![A-Za-z0-9_])")


def _path_key(name: str) -> str:
    return os.path.normcase(os.path.realpath(name))


def _location(source_location: cindex.SourceLocation) -> Location | None:
    file = source_location.file
    if file is None:
        return None
    return Location(str(file.name), int(source_location.line), int(source_location.column))


@dataclass(frozen=True, slots=True)
class _Context:
    names: VisibleNames
    standard_kinds: dict[str, str]
    """Canonical type kind of each standard typedef (int32_t...) declared by a
    system header at global scope or in namespace std, e.g. {"int64_t": "LONG"}
    on Linux x86_64."""


_GLOBAL_NAME_KINDS = frozenset(
    {
        CursorKind.FUNCTION_DECL,
        CursorKind.VAR_DECL,
        CursorKind.CLASS_DECL,
        CursorKind.STRUCT_DECL,
        CursorKind.UNION_DECL,
        CursorKind.ENUM_DECL,
        CursorKind.TYPEDEF_DECL,
        CursorKind.TYPE_ALIAS_DECL,
        CursorKind.NAMESPACE,
        CursorKind.NAMESPACE_ALIAS,
        CursorKind.CLASS_TEMPLATE,
        CursorKind.FUNCTION_TEMPLATE,
    }
)


def _record_standard_typedef(cursor: cindex.Cursor, standard_kinds: dict[str, str]) -> None:
    name = str(cursor.spelling)
    if name in typemap.STANDARD_TYPEDEFS and cursor.location.is_in_system_header:
        canonical = cursor.underlying_typedef_type.get_canonical()
        standard_kinds.setdefault(name, canonical.kind.name)


def _record_std_typedefs(namespace: cindex.Cursor, standard_kinds: dict[str, str]) -> None:
    """Standard typedefs declared in namespace std itself.

    libstdc++ declares std::size_t there (the global ::size_t only exists if
    <stddef.h> was included too); libc++ declares it with 'using' in the inline
    namespace std::__1.
    """
    pending = [namespace]
    while pending:
        for child in pending.pop().get_children():
            try:
                kind = child.kind
            except ValueError:
                continue
            if kind in (CursorKind.TYPEDEF_DECL, CursorKind.TYPE_ALIAS_DECL):
                _record_standard_typedef(child, standard_kinds)
            elif kind == CursorKind.NAMESPACE and libclang.is_inline_namespace(child):
                pending.append(child)


def _defines_function_like_macro(cursor: cindex.Cursor) -> bool:
    """True if the macro definition takes arguments: '(' right after its name.

    Read from the definition itself; libclang's clang_Cursor_isMacroFunctionLike
    looks at the macro at the end of the translation unit, so it says False
    for a function-like macro that was #undef'd (min and max after
    <windows.h>, isnan after <cmath>).
    """
    tokens = iter(cursor.get_tokens())
    name, parenthesis = next(tokens, None), next(tokens, None)
    return (
        name is not None
        and parenthesis is not None
        and parenthesis.spelling == "("
        and parenthesis.extent.start.offset == name.extent.end.offset
    )


_C_TAG_KINDS = frozenset({CursorKind.STRUCT_DECL, CursorKind.UNION_DECL, CursorKind.ENUM_DECL})


def _collect_context(unit_cursor: cindex.Cursor, *, c_tags: bool = False) -> _Context:
    """Names visible at global scope, macros and the standard typedefs of a translation unit.

    With ``c_tags`` (a C translation unit), struct, union and enum names go to
    ``tag_names``: in C they do not clash with a function of the same name.
    """
    global_names: set[str] = set()
    tag_names: set[str] = set()
    macro_names: set[str] = set()
    object_macro_names: set[str] = set()
    standard_kinds: dict[str, str] = {}
    pending = list(unit_cursor.get_children())
    while pending:
        cursor = pending.pop()
        try:
            kind = cursor.kind
        except ValueError:
            continue  # unknown to the bindings; cannot be one of the kinds below
        name = str(cursor.spelling)
        if kind == CursorKind.MACRO_DEFINITION:
            macro_names.add(name)
            if not _defines_function_like_macro(cursor):
                object_macro_names.add(name)
        elif kind == CursorKind.LINKAGE_SPEC:
            pending.extend(cursor.get_children())
        elif kind in _GLOBAL_NAME_KINDS and name:
            if c_tags and kind in (CursorKind.STRUCT_DECL, CursorKind.UNION_DECL):
                # In C, tags and enumerators declared inside a struct have file scope.
                pending.extend(
                    node
                    for node in cursor.walk_preorder()
                    if node != cursor and node.kind in _C_TAG_KINDS
                )
            # Not the spelling of an anonymous one, 'enum (unnamed at f.h:1:1)'.
            if naming.is_identifier(name):
                (tag_names if c_tags and kind in _C_TAG_KINDS else global_names).add(name)
            if kind == CursorKind.ENUM_DECL and not cursor.is_scoped_enum():
                global_names.update(str(child.spelling) for child in cursor.get_children())
            if kind == CursorKind.TYPEDEF_DECL:
                _record_standard_typedef(cursor, standard_kinds)
            elif kind == CursorKind.NAMESPACE and name == "std":
                _record_std_typedefs(cursor, standard_kinds)
    names = VisibleNames(
        frozenset(global_names),
        frozenset(macro_names),
        frozenset(object_macro_names),
        frozenset(tag_names),
    )
    return _Context(names, standard_kinds)


# Headers of the C library, POSIX and glibc (and <windows.h> on Windows) whose
# names generated C functions must not take, even if the wrapped headers do not
# include them: a C program that includes both would not compile, or, worse,
# would call the generated function instead of the library's (ELF interposition).
# Only the headers that exist on the platform are read.
SYSTEM_HEADERS = """
    assert.h complex.h ctype.h errno.h fenv.h float.h inttypes.h limits.h locale.h math.h
    setjmp.h signal.h stdalign.h stdarg.h stdatomic.h stdbit.h stdbool.h stdckdint.h
    stddef.h stdint.h stdio.h stdlib.h stdnoreturn.h string.h tgmath.h threads.h time.h
    uchar.h wchar.h wctype.h
    aio.h arpa/inet.h dirent.h dlfcn.h fcntl.h fnmatch.h ftw.h glob.h grp.h iconv.h
    langinfo.h libgen.h monetary.h mqueue.h net/if.h netdb.h netinet/in.h netinet/tcp.h
    nl_types.h poll.h pthread.h pwd.h regex.h sched.h search.h semaphore.h spawn.h
    strings.h sys/ipc.h sys/mman.h sys/msg.h sys/resource.h sys/select.h sys/sem.h
    sys/shm.h sys/socket.h sys/stat.h sys/statvfs.h sys/time.h sys/times.h sys/types.h
    sys/uio.h sys/un.h sys/utsname.h sys/wait.h syslog.h termios.h unistd.h utime.h
    utmpx.h wordexp.h
    argz.h envz.h err.h error.h execinfo.h fts.h getopt.h gnu/libc-version.h ifaddrs.h
    link.h malloc.h mntent.h netinet/ether.h printf.h pty.h resolv.h shadow.h
    sys/epoll.h sys/eventfd.h sys/fanotify.h sys/file.h sys/inotify.h sys/ioctl.h
    sys/mount.h sys/prctl.h sys/random.h sys/sendfile.h sys/signalfd.h sys/sysinfo.h
    sys/timerfd.h utmp.h
""".split()


@functools.cache
def system_names() -> VisibleNames:
    """Names declared by the platform's system headers (see SYSTEM_HEADERS).

    Found by parsing, as C, every header of a fixed list that exists on this
    platform. Requires :func:`bridgefex.libclang.load`.

    Raises:
        GenerationError: libclang cannot parse the probe.
    """
    libclang.load()
    # <windows.h> first: <stdnoreturn.h> defines a 'noreturn' macro that breaks
    # its __declspec(noreturn).
    lines = ["#if defined(_WIN32)\n#define WIN32_LEAN_AND_MEAN\n#include <windows.h>\n#endif\n"]
    lines += [
        f"#if __has_include(<{header}>)\n#include <{header}>\n#endif\n" for header in SYSTEM_HEADERS
    ]
    probe = "bridgefex_system_names.c"
    # No error limit: an error must not stop the headers after it.
    args = ["-x", "c", "-std=c17", "-D_GNU_SOURCE", "-ferror-limit=0"]
    try:
        unit = cindex.Index.create().parse(
            probe,
            args=args,
            unsaved_files=[(probe, "".join(lines))],
            options=(
                cindex.TranslationUnit.PARSE_SKIP_FUNCTION_BODIES
                | cindex.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD
            ),
        )
    except cindex.TranslationUnitLoadError as error:
        raise GenerationError(
            [Diagnostic(f"libclang cannot parse the C library headers ({error})")]
        ) from error
    # Errors (a header that needs another one first, for example) only make
    # the list shorter; the names that were declared are still valid.
    return _collect_context(unit.cursor, c_tags=True).names


_CXX11_MARKER = "bridgefex_needs_cxx11"


def selects_cxx11(options: ParseOptions) -> bool:
    """True if the headers would be parsed as C++11 or later.

    libclang decides, with the real arguments: besides -std, options such as
    -ansi or '-Xclang -std=c++03' select the standard too. Requires
    :func:`bridgefex.libclang.load`.
    """
    probe = "bridgefex_standard_probe.hpp"
    text = f"#if !defined(__cplusplus) || __cplusplus < 201103L\n#error {_CXX11_MARKER}\n#endif\n"
    try:
        unit = cindex.Index.create().parse(
            probe, args=options.clang_args(), unsaved_files=[(probe, text)]
        )
    except cindex.TranslationUnitLoadError:
        return True  # parsing the headers reports the problem
    return not any(_CXX11_MARKER in str(diagnostic.spelling) for diagnostic in unit.diagnostics)


def macro_defined_after(header: Path, macro: str, options: ParseOptions) -> bool:
    """True if ``macro`` is still defined after ``header``, in C++17 or later.

    Requires :func:`bridgefex.libclang.load`.
    """
    probe = "bridgefex_macro_probe.hpp"
    text = (
        f'#include "{header.resolve().as_posix()}"\n'
        f"#if defined({macro}) && __cplusplus >= 201703L\n#error {_CXX11_MARKER}_macro\n#endif\n"
    )
    try:
        unit = cindex.Index.create().parse(
            probe, args=options.clang_args(), unsaved_files=[(probe, text)]
        )
    except cindex.TranslationUnitLoadError:
        return True
    return any(f"{_CXX11_MARKER}_macro" in str(d.spelling) for d in unit.diagnostics)


_INT_MIN = -(2**31)
_INT_MAX = 2**31 - 1


def _array_type(clang_type: cindex.Type) -> cindex.Type:
    """The array type behind aliases (typedef std::uint8_t mac_t[6];), whose
    element keeps its spelling."""
    while clang_type.kind != TypeKind.CONSTANTARRAY:
        if clang_type.kind == TypeKind.ELABORATED:
            clang_type = clang_type.get_named_type()
        elif clang_type.kind == TypeKind.TYPEDEF:
            clang_type = clang_type.get_declaration().underlying_typedef_type
        else:
            return clang_type.get_canonical()
    return clang_type


def _may_throw(function: cindex.Cursor) -> bool:
    """True unless the function cannot throw: no specification (a destructor's
    default), noexcept, noexcept(true) or throw(). libclang does not evaluate
    noexcept(expression): any other expression counts as one that may throw."""
    kind = function.exception_specification_kind
    if kind == cindex.ExceptionSpecificationKind.COMPUTED_NOEXCEPT:
        return "noexcept(true)" not in str(function.type.spelling).replace(" ", "")
    return kind in (
        cindex.ExceptionSpecificationKind.DYNAMIC,
        cindex.ExceptionSpecificationKind.MS_ANY,
    )


def is_value_struct(definition: cindex.Cursor) -> bool:
    """True for a class definition with a public non-static data member: its
    values are copied across the boundary, member by member. Other classes are
    handles to objects."""
    return any(
        field.access_specifier == AccessSpecifier.PUBLIC for field in definition.type.get_fields()
    )


_NOT_BY_VALUE = "classes passed by value are not supported yet"

# Classes of the standard library that get their own message.
_UNSUPPORTED_STD_CLASSES = frozenset(
    """
    array span deque list forward_list map multimap set multiset unordered_map
    unordered_multimap unordered_set unordered_multiset optional variant any valarray
    initializer_list pair tuple unique_ptr shared_ptr weak_ptr function bitset
    """.split()
)


def _std_record_name(clang_type: cindex.Type) -> str | None:
    """The name of the std class (template) of a type, such as 'vector', or None."""
    canonical = clang_type.get_canonical()
    if canonical.kind != TypeKind.RECORD:
        return None
    return libclang.std_declaration_name(canonical.get_declaration())


def _struct_ref(clang_type: cindex.Type, *, by_reference: bool) -> StructRef:
    """A use of a value struct (through any alias).

    Raises:
        typemap.UnsupportedTypeError: a class that cannot be passed by value.
    """
    spelling = str(clang_type.spelling)
    if clang_type.is_volatile_qualified():
        raise typemap.UnsupportedTypeError("volatile-qualified types are not supported")
    declaration = clang_type.get_canonical().get_declaration()
    if declaration.kind == CursorKind.UNION_DECL:
        raise typemap.UnsupportedTypeError(f"'{spelling}': unions are not supported")
    if (
        libclang.std_declaration_name(declaration) is not None
        or declaration.is_anonymous()
        or not declaration.spelling
        or int(clang_type.get_canonical().get_num_template_arguments()) > 0
    ):
        raise typemap.UnsupportedTypeError(f"'{spelling}': {_NOT_BY_VALUE}")
    definition = declaration.get_definition()
    if definition is not None and not is_value_struct(definition):
        raise typemap.UnsupportedTypeError(
            f"'{spelling}': {_NOT_BY_VALUE} (only structs with public data members are)"
        )
    parts = [str(declaration.spelling)]
    parent = declaration.semantic_parent
    while parent is not None and parent.kind != CursorKind.TRANSLATION_UNIT:
        if parent.kind not in (CursorKind.NAMESPACE, CursorKind.LINKAGE_SPEC):
            raise typemap.UnsupportedTypeError(f"'{spelling}': nested classes are not supported")
        if parent.kind == CursorKind.NAMESPACE:
            parts.append(str(parent.spelling))
        parent = parent.semantic_parent
    return StructRef(
        usr=str(declaration.get_usr()),
        qualified_name="::".join(reversed(parts)),
        definition=_location(definition.location) if definition is not None else None,
        by_reference=by_reference,
        system=bool(declaration.location.is_in_system_header),
    )


def _enum_ref(clang_type: cindex.Type) -> EnumRef:
    """A use of an enum type (through any alias).

    Raises:
        typemap.UnsupportedTypeError: an enum that cannot cross the boundary.
    """
    if clang_type.is_volatile_qualified():
        raise typemap.UnsupportedTypeError("volatile-qualified types are not supported")
    declaration = clang_type.get_canonical().get_declaration()
    std_name = libclang.std_declaration_name(declaration)
    if std_name == "byte":
        raise typemap.UnsupportedTypeError("std::byte is not supported yet; use std::uint8_t")
    if std_name is not None:
        raise typemap.UnsupportedTypeError(
            f"standard library enum 'std::{std_name}' is not supported"
        )
    if declaration.is_anonymous() or not declaration.spelling:
        raise typemap.UnsupportedTypeError("anonymous enums are not supported yet")
    parts = [str(declaration.spelling)]
    parent = declaration.semantic_parent
    while parent is not None and parent.kind != CursorKind.TRANSLATION_UNIT:
        if parent.kind in (
            CursorKind.CLASS_DECL,
            CursorKind.STRUCT_DECL,
        ) and declaration.access_specifier in (AccessSpecifier.PRIVATE, AccessSpecifier.PROTECTED):
            raise typemap.UnsupportedTypeError(
                f"enum '{declaration.spelling}' is not public in its class"
            )
        if parent.kind != CursorKind.LINKAGE_SPEC:
            parts.append(str(parent.spelling))
        parent = parent.semantic_parent
    definition = declaration.get_definition()
    return EnumRef(
        usr=str(declaration.get_usr()),
        qualified_name="::".join(reversed(parts)),
        definition=_location(definition.location) if definition is not None else None,
        system=bool(declaration.location.is_in_system_header),
    )


# Plain char: CHAR_U where it is unsigned (ARM, -funsigned-char).
_CHAR_KINDS = frozenset({TypeKind.CHAR_S, TypeKind.CHAR_U})

_ONLY_STD_STRINGS = (
    "only std::string and std::string_view (of char, with the default traits and "
    "allocator) are supported"
)


def _is_plain_char(clang_type: cindex.Type) -> bool:
    return (
        clang_type.kind in _CHAR_KINDS
        and not clang_type.is_const_qualified()
        and not clang_type.is_volatile_qualified()
    )


def _std_specialization(clang_type: cindex.Type, name: str) -> list[cindex.Type] | None:
    """The canonical template arguments if ``clang_type`` is a specialization
    of the class template ``std::<name>``, else None."""
    canonical = clang_type.get_canonical()
    if canonical.kind != TypeKind.RECORD:
        return None
    if libclang.std_declaration_name(canonical.get_declaration()) != name:
        return None
    count = int(canonical.get_num_template_arguments())
    if count < 0:
        return None
    return [canonical.get_template_argument_type(i).get_canonical() for i in range(count)]


def _is_std_of_char(clang_type: cindex.Type, name: str) -> bool:
    arguments = _std_specialization(clang_type, name)
    return arguments is not None and len(arguments) == 1 and _is_plain_char(arguments[0])


def string_type(clang_type: cindex.Type) -> StringType | None:
    """The string type that ``clang_type`` is, or None if it is not one.

    Recognised by declaration, not by spelling, which differs between standard
    libraries ('std::basic_string<char>' with libstdc++, 'std::string' with
    libc++). Aliases are accepted. Requires :func:`bridgefex.libclang.load`.

    Raises:
        typemap.UnsupportedTypeError: a string form that is not supported.
    """
    spelling = str(clang_type.spelling)
    canonical = clang_type.get_canonical()
    if canonical.kind == TypeKind.POINTER:
        pointee = canonical.get_pointee()
        if pointee.kind not in _CHAR_KINDS:
            return None
        if pointee.is_volatile_qualified():
            raise typemap.UnsupportedTypeError("pointers to volatile char are not supported")
        if not pointee.is_const_qualified():
            raise typemap.UnsupportedTypeError(
                f"'{spelling}' is not supported: use 'const char*' or std::string"
            )
        if canonical.is_volatile_qualified():
            raise typemap.UnsupportedTypeError("volatile-qualified types are not supported")
        return StringType(StringKind.C_STRING)
    reference = canonical.kind in (TypeKind.LVALUEREFERENCE, TypeKind.RVALUEREFERENCE)
    value = canonical.get_pointee() if reference else canonical
    arguments = _std_specialization(value, "basic_string")
    if arguments is not None:
        kind = StringKind.STRING
        valid = (
            len(arguments) == 3
            and _is_plain_char(arguments[0])
            and _is_std_of_char(arguments[1], "char_traits")
            and _is_std_of_char(arguments[2], "allocator")
        )
    else:
        arguments = _std_specialization(value, "basic_string_view")
        if arguments is None:
            return None
        kind = StringKind.STRING_VIEW
        valid = (
            len(arguments) == 2
            and _is_plain_char(arguments[0])
            and _is_std_of_char(arguments[1], "char_traits")
        )
    if not valid:
        raise typemap.UnsupportedTypeError(f"'{spelling}': {_ONLY_STD_STRINGS}")
    name = f"std::{kind.value}"
    if value.is_volatile_qualified():
        raise typemap.UnsupportedTypeError(f"volatile {name} is not supported")
    if canonical.kind == TypeKind.RVALUEREFERENCE:
        raise typemap.UnsupportedTypeError(f"rvalue references to {name} are not supported yet")
    if reference and not value.is_const_qualified():
        raise typemap.UnsupportedTypeError(
            f"non-const references to {name} are not supported: they are output parameters"
        )
    return StringType(kind, by_reference=reference)


def _diagnostic_order(diagnostic: Diagnostic) -> tuple[str, int, int, str]:
    location = diagnostic.location
    if location is None:
        return ("", 0, 0, diagnostic.message)
    return (location.file, location.line, location.column, diagnostic.message)


def parse_header(path: Path, include: str, options: ParseOptions) -> ParseResult:
    """Parse one header and describe the API it declares.

    Raises:
        ConfigurationError: the header does not exist.
        GenerationError: libclang reported errors, or the header declares
            something that bridgefex cannot translate.
    """
    libclang.load()
    if not path.is_file():
        raise ConfigurationError(f"input header not found: {path}")

    index = cindex.Index.create()
    try:
        unit = index.parse(
            str(path),
            args=options.clang_args(),
            options=(
                cindex.TranslationUnit.PARSE_SKIP_FUNCTION_BODIES
                # Macro definitions are needed to check generated names against them.
                | cindex.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD
            ),
        )
    except cindex.TranslationUnitLoadError as error:
        arguments = " ".join(options.clang_args())
        raise GenerationError(
            [
                Diagnostic(
                    f"libclang cannot parse the header ({error}); check the arguments: {arguments}",
                    Location(str(path), 1, 1),
                )
            ]
        ) from error

    errors: list[Diagnostic] = []
    warnings: list[Diagnostic] = []
    target = _path_key(str(path))
    for diagnostic in unit.diagnostics:
        converted = Diagnostic(str(diagnostic.spelling), _location(diagnostic.location))
        file = diagnostic.location.file
        in_target = file is not None and _path_key(str(file.name)) == target
        if diagnostic.severity >= cindex.Diagnostic.Error or (
            in_target and str(diagnostic.option) == _MSVC_ENUM_WARNING
        ):
            errors.append(converted)
        elif diagnostic.severity >= cindex.Diagnostic.Warning:
            warnings.append(converted)
    # After an error libclang recovers by guessing (an unknown type becomes
    # 'int', for example), so the AST cannot be trusted.
    if errors:
        raise GenerationError(sorted(errors, key=_diagnostic_order))

    context = _collect_context(unit.cursor)
    visitor = _Visitor(path, context.standard_kinds)
    visitor.visit_scope(unit.cursor)
    if visitor.errors:
        raise GenerationError(visitor.errors)
    header = Header(
        path=path,
        include=include,
        declarations=tuple(visitor.declarations),
        names=context.names,
    )
    return ParseResult(header=header, warnings=tuple(sorted(warnings, key=_diagnostic_order)))


class _Visitor:
    def __init__(self, path: Path, standard_kinds: dict[str, str]) -> None:
        self._standard_kinds = standard_kinds
        self._target = _path_key(str(path))
        self._in_target_cache: dict[str, bool] = {}
        self._seen: set[str] = set()
        self.declarations: list[Declaration] = []
        self.errors: list[Diagnostic] = []

    # Helpers -----------------------------------------------------------

    def _error(self, cursor: cindex.Cursor, message: str) -> None:
        self.errors.append(Diagnostic(message, _location(cursor.location)))

    def _check_name(self, cursor: cindex.Cursor, name: str) -> bool:
        """Names end up in C and Python code, so they must be ASCII identifiers."""
        if naming.is_identifier(name):
            return True
        self._error(cursor, f"'{name}' is not an ASCII identifier, which bridgefex requires")
        return False

    def _in_target(self, cursor: cindex.Cursor) -> bool:
        file = cursor.location.file
        if file is None:
            return False
        name = str(file.name)
        cached = self._in_target_cache.get(name)
        if cached is None:
            cached = _path_key(name) == self._target
            self._in_target_cache[name] = cached
        return cached

    def _first_time(self, cursor: cindex.Cursor) -> bool:
        """False for a redeclaration of an entity that was already handled."""
        usr = str(cursor.get_usr())
        if not usr:
            return True
        if usr in self._seen:
            return False
        self._seen.add(usr)
        return True

    @staticmethod
    def _unavailable(cursor: cindex.Cursor) -> bool:
        """True for deleted functions (and other unusable ones)."""
        return bool(cursor.availability == AvailabilityKind.NOT_AVAILABLE)

    def _namespace_of(self, cursor: cindex.Cursor) -> tuple[str, ...] | None:
        parts: list[str] = []
        parent = cursor.semantic_parent
        while parent is not None and parent.kind != CursorKind.TRANSLATION_UNIT:
            if parent.kind != CursorKind.NAMESPACE:
                self._error(cursor, f"'{cursor.spelling}': nested declarations are not supported")
                return None
            if not parent.spelling:
                self._error(cursor, "anonymous namespaces are not supported")
                return None
            if not self._check_name(cursor, str(parent.spelling)):
                return None
            if libclang.is_inline_namespace(parent):
                self._error(cursor, f"inline namespace '{parent.spelling}' is not supported yet")
                return None
            parts.append(str(parent.spelling))
            parent = parent.semantic_parent
        return tuple(reversed(parts))

    def _type(
        self, clang_type: cindex.Type, cursor: cindex.Cursor, context: str, *, allow_void: bool
    ) -> Type | None:
        spelling = str(clang_type.spelling)
        try:
            # Before the scalars: they reject plain char and references.
            string = string_type(clang_type)
            if string is not None:
                return string
            by_reference = rvalue_reference = False
            if (
                clang_type.kind == TypeKind.RVALUEREFERENCE
                and not allow_void  # a parameter, not a result
                and _std_record_name(clang_type.get_pointee()) == "vector"
            ):
                # A sink parameter: the wrapper passes a temporary anyway.
                clang_type = clang_type.get_pointee()
                rvalue_reference = True
            if clang_type.kind == TypeKind.LVALUEREFERENCE:
                # A const reference to a scalar is copied across the boundary.
                pointee = clang_type.get_pointee()
                if not pointee.is_const_qualified():
                    self._error(cursor, f"{context}: non-const references are not supported yet")
                    return None
                clang_type = pointee
                spelling = str(clang_type.spelling)
                allow_void = False
                by_reference = True
            if clang_type.get_canonical().kind == TypeKind.ENUM:
                return _enum_ref(clang_type)
            if clang_type.get_canonical().kind == TypeKind.RECORD:
                std_name = _std_record_name(clang_type)
                if std_name == "vector":
                    vector = self._vector(clang_type, cursor, context, by_reference=by_reference)
                    if vector is not None and rvalue_reference:
                        vector = dataclasses.replace(vector, rvalue_reference=True)
                    return vector
                if std_name in _UNSUPPORTED_STD_CLASSES:
                    raise typemap.UnsupportedTypeError(f"std::{std_name} is not supported yet")
                return _struct_ref(clang_type, by_reference=by_reference)
            user_alias = False
            if clang_type.kind == TypeKind.ELABORATED:
                # The named type is spelled with its real qualification
                # ('emb::size_t'), whatever the source wrote ('size_t').
                named = str(clang_type.get_named_type().spelling)
                const = "const " if clang_type.is_const_qualified() else ""
                spelling = named if named.startswith("const ") else const + named
            standard_kinds = self._standard_kinds
            declaration = clang_type.get_declaration()
            if declaration.kind in (CursorKind.TYPEDEF_DECL, CursorKind.TYPE_ALIAS_DECL):
                # The first declaration decides: redeclaring a standard typedef
                # with the same type ('typedef int int32_t;' after <cstdint>) is
                # valid and still names the standard type.
                first = declaration.canonical
                if first.location.file is None:
                    # Declared by the compiler itself, as size_t is for the
                    # MSVC target: it is the standard type.
                    canonical = first.underlying_typedef_type.get_canonical()
                    standard_kinds = {**standard_kinds, str(first.spelling): canonical.kind.name}
                else:
                    user_alias = not first.location.is_in_system_header
            return typemap.resolve(
                kind=clang_type.kind.name,
                spelling=spelling,
                canonical_kind=clang_type.get_canonical().kind.name,
                size=int(clang_type.get_size()),
                is_volatile=bool(clang_type.is_volatile_qualified()),
                allow_void=allow_void,
                user_alias=user_alias,
                standard_kinds=standard_kinds,
            )
        except typemap.UnsupportedTypeError as error:
            self._error(cursor, f"{context}: {error}")
        except ValueError:
            self._error(cursor, f"{context}: type '{spelling}' is unknown to the libclang bindings")
        return None

    def _parameters(self, cursor: cindex.Cursor, where: str) -> tuple[Parameter, ...] | None:
        function_type = cursor.type.get_canonical()
        if function_type.kind == TypeKind.FUNCTIONPROTO and function_type.is_function_variadic():
            self._error(cursor, f"{where}: variadic functions are not supported")
            return None
        parameters: list[Parameter] = []
        valid = True
        for position, argument in enumerate(cursor.get_arguments(), start=1):
            name = str(argument.spelling)
            if name and not self._check_name(argument, name):
                valid = False
                continue
            label = f"parameter '{name}'" if name else f"parameter {position}"
            type_ = self._type(argument.type, argument, f"{label} of {where}", allow_void=False)
            if type_ is None:
                valid = False
            else:
                parameters.append(Parameter(name, type_))
        return tuple(parameters) if valid else None

    def _vector(
        self, written: cindex.Type, cursor: cindex.Cursor, context: str, *, by_reference: bool
    ) -> VectorType | None:
        """A std::vector, whose element type is read from the type as written
        (std::int64_t, not the platform's 'long')."""
        canonical = written.get_canonical()
        if written.kind not in (TypeKind.ELABORATED, TypeKind.TYPEDEF, TypeKind.RECORD):
            raise typemap.UnsupportedTypeError("deduced types (auto, decltype) are not supported")
        if written.is_volatile_qualified():
            raise typemap.UnsupportedTypeError("volatile-qualified types are not supported")
        actual = [
            canonical.get_template_argument_type(index).get_canonical()
            for index in range(int(canonical.get_num_template_arguments()))
        ]
        count = int(written.get_num_template_arguments())
        arguments = [written.get_template_argument_type(index) for index in range(max(count, 0))]
        # An alias template may give its own arguments, in another order.
        for index, argument in enumerate(arguments):
            if argument.kind != TypeKind.INVALID and (
                index >= len(actual) or argument.get_canonical() != actual[index]
            ):
                raise typemap.UnsupportedTypeError(
                    f"cannot read the element type of '{written.spelling}' (alias templates are "
                    "not supported); write std::vector<...>"
                )
        if not arguments or arguments[0].kind == TypeKind.INVALID:
            raise typemap.UnsupportedTypeError(
                f"cannot read the element type of '{written.spelling}'"
            )
        if len(actual) != 2 or _std_record_name(actual[1]) != "allocator":
            raise typemap.UnsupportedTypeError(
                f"'{written.spelling}': only std::vector with std::allocator is supported"
            )
        element_type = arguments[0]
        if _std_record_name(element_type) in ("vector", *_UNSUPPORTED_STD_CLASSES):
            raise typemap.UnsupportedTypeError(
                f"std::vector of '{element_type.spelling}' is not supported"
            )
        element = self._type(element_type, cursor, f"element of {context}", allow_void=False)
        if element is None:
            return None
        if isinstance(element, VectorType) or (
            isinstance(element, StringType) and element.kind is not StringKind.STRING
        ):
            raise typemap.UnsupportedTypeError(
                f"std::vector of '{element_type.spelling}' is not supported"
            )
        if isinstance(element, StringType | StructRef) and element.by_reference:
            raise typemap.UnsupportedTypeError("std::vector of references is not supported")
        return VectorType(element, by_reference=by_reference)

    # Namespace scope -----------------------------------------------------

    def visit_scope(self, scope: cindex.Cursor) -> None:
        for cursor in scope.get_children():
            if not self._in_target(cursor):
                continue
            try:
                kind = cursor.kind
            except ValueError:
                self._error(cursor, "declaration of a kind unknown to the libclang bindings")
                continue
            if kind.is_preprocessing():
                continue  # macro definitions, expansions and #include directives
            try:
                self._visit(cursor, kind)
            except (AssertionError, ValueError) as error:
                # A construct the libclang bindings cannot describe: report it
                # instead of guessing.
                self._error(
                    cursor,
                    f"cannot read the declaration of '{cursor.spelling}' "
                    f"({type(error).__name__}: {error}); it is not supported",
                )

    def _visit(self, cursor: cindex.Cursor, kind: CursorKind) -> None:
        if kind == CursorKind.NAMESPACE:
            if not cursor.spelling:
                self._error(cursor, "anonymous namespaces are not supported")
            elif libclang.is_inline_namespace(cursor):
                self._error(cursor, f"inline namespace '{cursor.spelling}' is not supported yet")
            else:
                self.visit_scope(cursor)
        elif kind in (CursorKind.CLASS_DECL, CursorKind.STRUCT_DECL):
            # Forward declarations are skipped; the definition is what counts.
            if cursor.is_definition() and self._first_time(cursor):
                parsed: Class | Struct | None = (
                    self._parse_struct(cursor)
                    if is_value_struct(cursor)
                    else self._parse_class(cursor, kind)
                )
                if parsed is not None:
                    self.declarations.append(parsed)
        elif kind == CursorKind.FUNCTION_DECL:
            if self._first_time(cursor):
                function = self._parse_function(cursor)
                if function is not None:
                    self.declarations.append(function)
        elif kind in _IGNORED_SCOPE_KINDS:
            pass
        elif kind in _TEMPLATE_KINDS:
            self._error(cursor, f"template '{cursor.spelling}' is not supported")
        elif kind == CursorKind.ENUM_DECL:
            enum = self._parse_enum(cursor, (), None)
            if enum is not None:
                self.declarations.append(enum)
        elif kind == CursorKind.UNION_DECL:
            self._error(cursor, f"union '{cursor.spelling}' is not supported")
        elif kind == CursorKind.VAR_DECL:
            self._error(cursor, f"global variable '{cursor.spelling}' is not supported")
        elif kind == CursorKind.LINKAGE_SPEC:
            self._error(
                cursor,
                'extern "C" blocks are not supported: their declarations already form a C API',
            )
        else:
            self._error(cursor, f"unsupported declaration '{cursor.spelling}' ({kind.name})")

    def _parse_function(self, cursor: cindex.Cursor) -> Function | None:
        if self._unavailable(cursor):
            return None
        namespace = self._namespace_of(cursor)
        if namespace is None:
            return None
        name = str(cursor.spelling)
        where = "'" + "::".join((*namespace, name)) + "'"
        if _OPERATOR.match(name):
            self._error(cursor, f"operator function {where} is not supported yet")
            return None
        if not self._check_name(cursor, name):
            return None
        if int(cursor.get_num_template_arguments()) > 0:
            self._error(cursor, f"template specialization {where} is not supported")
            return None
        parameters = self._parameters(cursor, where)
        result = self._type(cursor.result_type, cursor, f"return type of {where}", allow_void=True)
        if parameters is None or result is None:
            return None
        location = _location(cursor.location)
        assert location is not None  # cursors of the target header always have one
        return Function(name, namespace, parameters, result, location)

    # Enums -------------------------------------------------------------------

    def _parse_enum(
        self, cursor: cindex.Cursor, scope: tuple[str, ...], namespace: tuple[str, ...] | None
    ) -> Enumeration | None:
        """An enum definition; ``scope`` is the class it is nested in, whose
        ``namespace`` is given (None: the enum is at namespace scope)."""
        if cursor.type.kind != TypeKind.ENUM:
            return None  # C++20 'using enum E;', which libclang reports as an enum
        if not cursor.is_definition() or not self._first_time(cursor):
            return None  # an opaque declaration: the definition is what counts
        name = str(cursor.spelling)
        parent = cursor.semantic_parent
        if (
            namespace is None
            and parent is not None
            and parent.kind
            in (
                CursorKind.CLASS_DECL,
                CursorKind.STRUCT_DECL,
            )
        ):
            self._error(
                cursor,
                f"enum '{parent.spelling}::{name}' is defined outside its class; this is not "
                "supported yet: define it in the class",
            )
            return None
        if cursor.is_anonymous() or not name:
            self._error(cursor, "anonymous enums are not supported yet; give the enum a name")
            return None
        if not self._check_name(cursor, name):
            return None
        if namespace is None:
            namespace = self._namespace_of(cursor)
            if namespace is None:
                return None
        qualified = "::".join((*namespace, *scope, name))
        fixed = libclang.has_fixed_underlying_type(cursor)
        underlying = cursor.enum_type
        if fixed:
            c_type = self._type(
                underlying, cursor, f"underlying type of enum '{qualified}'", allow_void=False
            )
            if c_type is None:
                return None
            if not isinstance(c_type, ScalarType) or c_type.category not in (
                TypeCategory.SIGNED_INTEGER,
                TypeCategory.UNSIGNED_INTEGER,
            ):
                self._error(
                    cursor,
                    f"enum '{qualified}': underlying type '{underlying.spelling}' is not "
                    "supported; use a fixed-width integer type",
                )
                return None
            if c_type.c_name == "size_t":
                self._error(
                    cursor,
                    f"enum '{qualified}': underlying type 'size_t' is not supported (C has no "
                    "constants of that type); use a fixed-width integer type",
                )
                return None
        else:
            c_type = typemap.BUILTIN_TYPES["INT"]
        enumerators: list[Enumerator] = []
        unavailable: list[int] = []
        for child in cursor.get_children():
            if child.kind != CursorKind.ENUM_CONSTANT_DECL:
                continue
            if child.availability == AvailabilityKind.NOT_AVAILABLE:
                unavailable.append(libclang.enumerator_value(child, underlying))
                continue
            constant = str(child.spelling)
            if not self._check_name(child, constant):
                return None
            value = libclang.enumerator_value(child, underlying)
            if not fixed and not _INT_MIN <= value <= _INT_MAX:
                self._error(
                    child,
                    f"enumerator '{qualified}::{constant}' = {value} does not fit in int: the "
                    f"underlying type of '{qualified}' then depends on the compiler (MSVC "
                    "truncates it); give the enum a fixed underlying type, for example "
                    "': std::uint32_t'",
                )
                return None
            deprecated = child.availability == AvailabilityKind.DEPRECATED
            enumerators.append(Enumerator(constant, value, deprecated))
        location = _location(cursor.location)
        assert location is not None
        return Enumeration(
            name=name,
            namespace=namespace,
            scope=scope,
            usr=str(cursor.get_usr()),
            scoped=bool(cursor.is_scoped_enum()),
            underlying=c_type,
            fixed=fixed,
            enumerators=tuple(enumerators),
            location=location,
            unavailable=tuple(unavailable),
        )

    # Value structs -------------------------------------------------------------

    def _parse_struct(self, cursor: cindex.Cursor) -> Struct | None:
        """A class with public data members, which crosses the boundary by value."""
        errors_before = len(self.errors)
        name = str(cursor.spelling)
        if not name or cursor.is_anonymous():
            self._error(cursor, "anonymous structs are not supported")
            return None
        if not self._check_name(cursor, name):
            return None
        namespace = self._namespace_of(cursor)
        if namespace is None:
            return None
        qualified = "::".join((*namespace, name))
        if int(cursor.type.get_num_template_arguments()) > 0:
            self._error(cursor, f"template specialization '{qualified}' is not supported")
            return None
        enums = self._check_struct_members(cursor, qualified, namespace)
        fields = [self._parse_field(field, qualified) for field in cursor.type.get_fields()]
        if len(self.errors) > errors_before:
            return None
        location = _location(cursor.location)
        assert location is not None
        return Struct(
            name=name,
            namespace=namespace,
            usr=str(cursor.get_usr()),
            fields=tuple(field for field in fields if field is not None),
            location=location,
            enums=tuple(enums),
        )

    def _check_struct_members(
        self, cursor: cindex.Cursor, qualified: str, namespace: tuple[str, ...]
    ) -> list[Enumeration]:
        """Report the members that a value struct cannot have; return its public enums.

        Its value is made by default construction and member assignments, so it
        needs a public default constructor, and copies.
        """
        where = f"value struct '{qualified}'"
        enums: list[Enumeration] = []
        declares_constructor = has_default_constructor = False
        declares_copy = declares_move = False
        for member in cursor.get_children():
            try:
                kind = member.kind
            except ValueError:
                self._error(member, f"member of '{qualified}' of a kind unknown to the bindings")
                continue
            if (
                kind.is_attribute()
                or kind in _IGNORED_MEMBER_KINDS
                or kind
                in (
                    CursorKind.FIELD_DECL,
                    CursorKind.VAR_DECL,  # static data members are not part of the value
                )
            ):
                continue
            public = member.access_specifier == AccessSpecifier.PUBLIC
            member_name = f"'{qualified}::{member.spelling}'"
            if kind == CursorKind.CXX_BASE_SPECIFIER:
                self._error(member, f"{where}: inheritance is not supported yet")
            elif kind == CursorKind.CONSTRUCTOR:
                declares_constructor = True
                role = self._struct_constructor(member, where)
                declares_copy |= role == "copy"
                declares_move |= role == "move"
                has_default_constructor |= role == "default"
            elif kind == CursorKind.FUNCTION_TEMPLATE and member.spelling == cursor.spelling:
                declares_constructor = True
                self._error(member, f"{where}: constructor templates are not supported")
            elif kind == CursorKind.DESTRUCTOR:
                self._check_struct_destructor(member, where)
            elif kind == CursorKind.CXX_METHOD and (
                member.is_copy_assignment_operator_method()
                or member.is_move_assignment_operator_method()
            ):
                declares_move |= bool(member.is_move_assignment_operator_method())
            elif kind == CursorKind.CXX_METHOD:
                self._error(member, f"{where}: methods are not supported yet ({member_name})")
            elif kind == CursorKind.CONVERSION_FUNCTION:
                self._error(member, f"{where}: conversion operators are not supported yet")
            elif kind == CursorKind.ENUM_DECL:
                if public:
                    enum = self._parse_enum(member, (str(cursor.spelling),), namespace)
                    if enum is not None:
                        enums.append(enum)
            elif kind in _NESTED_TYPE_KINDS:
                if not member.is_anonymous():  # anonymous members are reported as fields
                    self._error(member, f"nested type {member_name} is not supported yet")
            else:
                self._error(member, f"{where}: unsupported member {member_name} ({kind.name})")
        if declares_constructor and not has_default_constructor:
            self._error(cursor, f"{where} has no public default constructor")
        if declares_move and not declares_copy:
            self._error(cursor, f"{where} cannot be copied: it declares a move operation only")
        return enums

    def _check_struct_destructor(self, member: cindex.Cursor, where: str) -> None:
        if (
            member.access_specifier != AccessSpecifier.PUBLIC
            or self._unavailable(member)
            or member.is_virtual_method()
        ):
            self._error(member, f"{where} needs a public, non-virtual destructor")
        elif _may_throw(member):
            # The generated code destroys temporaries after it writes the
            # result, which must stay unchanged on failure.
            self._error(member, f"{where}: destructors that may throw are not supported")

    def _struct_constructor(self, member: cindex.Cursor, where: str) -> str:
        """What a constructor of a value struct is: "copy", "move", "default" (a
        usable default constructor) or "" (reported if it is not allowed)."""
        usable = member.access_specifier == AccessSpecifier.PUBLIC and not self._unavailable(member)
        if member.is_copy_constructor():
            arguments = list(member.get_arguments())
            from_const = bool(arguments) and arguments[0].type.get_pointee().is_const_qualified()
            if not usable or member.is_explicit_method() or not from_const:
                self._error(
                    member,
                    f"{where} cannot be copied: its copy constructor is deleted, not public, "
                    "explicit or takes a non-const reference",
                )
            return "copy"
        if member.is_move_constructor():
            if not usable or member.is_explicit_method():
                # Returning a local by value needs it (it is not ignored like a
                # missing one).
                self._error(
                    member,
                    f"{where} cannot be moved: its move constructor is deleted, not public or "
                    "explicit",
                )
            return "move"
        if not list(member.get_arguments()):
            return "default" if usable else ""
        self._error(
            member,
            f"{where}: constructors with parameters are not supported yet "
            "(a value struct is built member by member)",
        )
        return ""

    def _parse_field(self, field: cindex.Cursor, qualified: str) -> Field | None:
        name = str(field.spelling)
        where = f"member '{qualified}::{name}'"
        if field.access_specifier != AccessSpecifier.PUBLIC:
            self._error(
                field,
                f"{where} is not public: every data member of a value struct must be public",
            )
            return None
        declaration = field.type.get_declaration()
        if (
            not name
            or field.is_anonymous_record_decl()
            or (
                declaration.kind
                in (CursorKind.STRUCT_DECL, CursorKind.UNION_DECL, CursorKind.CLASS_DECL)
                and declaration.is_anonymous()
            )
        ):
            self._error(field, f"anonymous unions and structs in '{qualified}' are not supported")
            return None
        if self._unavailable(field):
            self._error(field, f"{where} is unavailable: the value cannot be copied")
            return None
        if not self._check_name(field, name):
            return None
        if (name.startswith("_") and (len(name) == 1 or name[1].isupper())) or "__" in name:
            self._error(field, f"{where}: the name is reserved in C and C++")
            return None
        if field.is_bitfield():
            self._error(field, f"{where}: bit-fields are not supported yet")
            return None
        clang_type = field.type
        length: int | None = None
        canonical = clang_type.get_canonical()
        if canonical.kind == TypeKind.INCOMPLETEARRAY:
            self._error(field, f"{where}: arrays without a size are not supported")
            return None
        if canonical.kind == TypeKind.CONSTANTARRAY:
            clang_type = _array_type(clang_type)
            length = int(clang_type.get_array_size())
            if length < 1:
                self._error(field, f"{where}: arrays of size 0 are not supported")
                return None
            clang_type = clang_type.get_array_element_type()
            canonical = clang_type.get_canonical()
            if canonical.kind in (TypeKind.CONSTANTARRAY, TypeKind.INCOMPLETEARRAY):
                self._error(field, f"{where}: arrays of arrays are not supported yet")
                return None
            if canonical.kind in _CHAR_KINDS:
                self._error(
                    field, f"{where}: char arrays (fixed-size strings) are not supported yet"
                )
                return None
        if canonical.kind in (TypeKind.LVALUEREFERENCE, TypeKind.RVALUEREFERENCE):
            self._error(field, f"{where}: reference members are not supported")
            return None
        if canonical.is_const_qualified():
            self._error(
                field, f"{where}: const members are not supported (bridgefex assigns every member)"
            )
            return None
        type_ = self._type(clang_type, field, where, allow_void=False)
        if type_ is None:
            return None
        if isinstance(type_, StringType):
            self._error(field, f"{where}: strings in value structs are not supported yet")
            return None
        if isinstance(type_, VectorType):
            self._error(field, f"{where}: containers in value structs are not supported yet")
            return None
        location = _location(field.location)
        assert location is not None
        field_type: FieldType = type_
        return Field(name, field_type, length, location)

    # Classes ---------------------------------------------------------------

    def _parse_class(self, cursor: cindex.Cursor, kind: CursorKind) -> Class | None:
        errors_before = len(self.errors)
        name = str(cursor.spelling)
        if not name or cursor.is_anonymous():
            self._error(cursor, "anonymous classes are not supported")
            return None
        if not self._check_name(cursor, name):
            return None
        namespace = self._namespace_of(cursor)
        if namespace is None:
            return None
        qualified = "::".join((*namespace, name))
        if int(cursor.type.get_num_template_arguments()) > 0:
            self._error(cursor, f"template specialization '{qualified}' is not supported")
            return None
        if cursor.is_abstract_record():
            self._error(cursor, f"class '{qualified}' is abstract and cannot be instantiated")

        constructors, methods, enums, declares_constructor = self._parse_members(
            cursor, qualified, namespace
        )

        location = _location(cursor.location)
        assert location is not None  # cursors of the target header always have one
        if not declares_constructor:
            constructors.append(Constructor((), location, implicit=True))
        elif not constructors and len(self.errors) == errors_before:
            self._error(
                cursor,
                f"class '{qualified}' has no public constructor that bridgefex can use "
                "(copy and move constructors are not exposed)",
            )
        if len(self.errors) > errors_before:
            return None
        class_key = "struct" if kind == CursorKind.STRUCT_DECL else "class"
        return Class(
            name,
            namespace,
            class_key,
            tuple(constructors),
            tuple(methods),
            location,
            tuple(enums),
        )

    def _parse_members(
        self, cursor: cindex.Cursor, qualified: str, namespace: tuple[str, ...]
    ) -> tuple[list[Constructor], list[Method], list[Enumeration], bool]:
        """Public constructors, methods and enums, and whether any constructor is declared."""
        constructors: list[Constructor] = []
        methods: list[Method] = []
        enums: list[Enumeration] = []
        declares_constructor = False
        has_virtual_method = False
        virtual_destructor = False
        is_final = False
        for member in cursor.get_children():
            try:
                member_kind = member.kind
            except ValueError:
                self._error(member, f"member of '{qualified}' of a kind unknown to the bindings")
                continue
            if member_kind == CursorKind.CXX_FINAL_ATTR:
                is_final = True
                continue
            if member_kind.is_attribute() or member_kind in _IGNORED_MEMBER_KINDS:
                continue
            if member_kind == CursorKind.ENUM_DECL:
                # Private and protected enums are not part of the API; a public
                # signature that uses one is reported there.
                if member.access_specifier == AccessSpecifier.PUBLIC:
                    enum = self._parse_enum(member, (str(cursor.spelling),), namespace)
                    if enum is not None:
                        enums.append(enum)
                continue
            if member_kind == CursorKind.FUNCTION_TEMPLATE and member.spelling == cursor.spelling:
                # A constructor template, whatever its access, suppresses the
                # implicit default constructor.
                declares_constructor = True
            access = member.access_specifier
            public = access == AccessSpecifier.PUBLIC
            if member_kind == CursorKind.CXX_BASE_SPECIFIER:
                self._error(
                    member,
                    f"class '{qualified}': inheritance is not supported yet "
                    f"(base '{member.type.spelling}')",
                )
            elif member_kind == CursorKind.CONSTRUCTOR:
                declares_constructor = True
                if (
                    public
                    and not self._unavailable(member)
                    and not member.is_copy_constructor()
                    and not member.is_move_constructor()
                ):
                    constructor = self._parse_constructor(member, qualified)
                    if constructor is not None:
                        constructors.append(constructor)
            elif member_kind == CursorKind.DESTRUCTOR:
                virtual_destructor = bool(member.is_virtual_method())
                if not public or self._unavailable(member):
                    self._error(
                        member,
                        f"class '{qualified}' has no public destructor; "
                        "bridgefex needs one to destroy objects",
                    )
            elif member_kind == CursorKind.CXX_METHOD:
                has_virtual_method = has_virtual_method or bool(member.is_virtual_method())
                if public and not self._unavailable(member):
                    method = self._parse_method(member, qualified)
                    if method is not None:
                        methods.append(method)
            elif access not in (AccessSpecifier.PRIVATE, AccessSpecifier.PROTECTED):
                self._reject_member(member, member_kind, qualified)
        if has_virtual_method and not virtual_destructor and not is_final:
            # 'delete' through the C API would then be flagged by
            # -Wdelete-non-virtual-dtor.
            self._error(
                cursor,
                f"class '{qualified}' has virtual methods but no virtual destructor and is "
                "not final; this is not supported",
            )
        return constructors, methods, enums, declares_constructor

    def _reject_member(self, member: cindex.Cursor, kind: CursorKind, qualified: str) -> None:
        """Report a public member that bridgefex cannot expose."""
        name = f"'{qualified}::{member.spelling}'"
        if kind == CursorKind.CONVERSION_FUNCTION:
            self._error(member, f"conversion operator of '{qualified}' is not supported yet")
        elif kind == CursorKind.FUNCTION_TEMPLATE:
            self._error(member, f"member template {name} is not supported")
        elif kind in (CursorKind.FIELD_DECL, CursorKind.VAR_DECL):
            self._error(
                member,
                f"public data member {name} is not supported yet; "
                "make it private and add accessors",
            )
        elif kind in _NESTED_TYPE_KINDS:
            self._error(member, f"nested type {name} is not supported yet")
        else:
            self._error(member, f"unsupported member {name} ({kind.name})")

    def _parse_constructor(self, cursor: cindex.Cursor, qualified: str) -> Constructor | None:
        parameters = self._parameters(cursor, f"constructor of '{qualified}'")
        if parameters is None:
            return None
        location = _location(cursor.location)
        assert location is not None
        return Constructor(parameters, location)

    def _parse_method(self, cursor: cindex.Cursor, qualified: str) -> Method | None:
        name = str(cursor.spelling)
        where = f"'{qualified}::{name}'"
        if _OPERATOR.match(name):
            self._error(cursor, f"operator {where} is not supported yet")
            return None
        if not self._check_name(cursor, name):
            return None
        if cursor.is_virtual_method():
            self._error(cursor, f"virtual method {where} is not supported yet")
            return None
        if cursor.type.get_canonical().get_ref_qualifier() != RefQualifierKind.NONE:
            self._error(cursor, f"ref-qualified method {where} is not supported")
            return None
        parameters = self._parameters(cursor, where)
        result = self._type(cursor.result_type, cursor, f"return type of {where}", allow_void=True)
        if parameters is None or result is None:
            return None
        location = _location(cursor.location)
        assert location is not None
        return Method(
            name=name,
            parameters=parameters,
            result=result,
            is_const=bool(cursor.is_const_method()),
            is_static=bool(cursor.is_static_method()),
            location=location,
        )
