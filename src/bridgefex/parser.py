# SPDX-License-Identifier: Apache-2.0
"""Reads C++ headers with libclang and builds the model.

Only declarations written in the header itself are considered; everything
pulled in through ``#include`` is ignored. Anything that bridgefex cannot
translate correctly is reported as an error. Every problem in a header is
collected before giving up, so that the user can fix them in one go.
"""

from __future__ import annotations

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
from .model import Class, Constructor, Declaration, Function, Header, Method, Parameter, ScalarType


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
        return args


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
            options=cindex.TranslationUnit.PARSE_SKIP_FUNCTION_BODIES,
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
    for diagnostic in unit.diagnostics:
        converted = Diagnostic(str(diagnostic.spelling), _location(diagnostic.location))
        if diagnostic.severity >= cindex.Diagnostic.Error:
            errors.append(converted)
        elif diagnostic.severity >= cindex.Diagnostic.Warning:
            warnings.append(converted)
    # After an error libclang recovers by guessing (an unknown type becomes
    # 'int', for example), so the AST cannot be trusted.
    if errors:
        raise GenerationError(sorted(errors, key=_diagnostic_order))

    visitor = _Visitor(path)
    visitor.visit_scope(unit.cursor)
    if visitor.errors:
        raise GenerationError(visitor.errors)
    header = Header(path=path, include=include, declarations=tuple(visitor.declarations))
    return ParseResult(header=header, warnings=tuple(sorted(warnings, key=_diagnostic_order)))


class _Visitor:
    def __init__(self, path: Path) -> None:
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
    ) -> ScalarType | None:
        spelling = str(clang_type.spelling)
        try:
            if clang_type.kind == TypeKind.LVALUEREFERENCE:
                # A const reference to a scalar is copied across the boundary.
                pointee = clang_type.get_pointee()
                if not pointee.is_const_qualified():
                    self._error(cursor, f"{context}: non-const references are not supported yet")
                    return None
                clang_type = pointee
                spelling = str(clang_type.spelling)
                allow_void = False
            return typemap.resolve(
                kind=clang_type.kind.name,
                spelling=spelling,
                canonical_kind=clang_type.get_canonical().kind.name,
                size=int(clang_type.get_size()),
                is_volatile=bool(clang_type.is_volatile_qualified()),
                allow_void=allow_void,
            )
        except typemap.UnsupportedTypeError as error:
            self._error(cursor, f"{context}: {error}")
        except ValueError:
            self._error(cursor, f"{context}: type '{spelling}' is unknown to the libclang bindings")
        return None

    def _parameters(self, cursor: cindex.Cursor, where: str) -> tuple[Parameter, ...] | None:
        if cursor.type.is_function_variadic():
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
            scalar = self._type(argument.type, argument, f"{label} of {where}", allow_void=False)
            if scalar is None:
                valid = False
            else:
                parameters.append(Parameter(name, scalar))
        return tuple(parameters) if valid else None

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
            self._visit(cursor, kind)

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
                parsed_class = self._parse_class(cursor, kind)
                if parsed_class is not None:
                    self.declarations.append(parsed_class)
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
            self._error(cursor, f"enum '{cursor.spelling}' is not supported yet")
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

        constructors, methods, declares_constructor = self._parse_members(cursor, qualified)

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
        return Class(name, namespace, class_key, tuple(constructors), tuple(methods), location)

    def _parse_members(
        self, cursor: cindex.Cursor, qualified: str
    ) -> tuple[list[Constructor], list[Method], bool]:
        """Public constructors and methods, and whether any constructor is declared."""
        constructors: list[Constructor] = []
        methods: list[Method] = []
        declares_constructor = False
        for member in cursor.get_children():
            try:
                member_kind = member.kind
            except ValueError:
                self._error(member, f"member of '{qualified}' of a kind unknown to the bindings")
                continue
            if member_kind.is_attribute() or member_kind in _IGNORED_MEMBER_KINDS:
                continue
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
                if not public or self._unavailable(member):
                    self._error(
                        member,
                        f"class '{qualified}' has no public destructor; "
                        "bridgefex needs one to destroy objects",
                    )
            elif member_kind == CursorKind.CXX_METHOD:
                if public and not self._unavailable(member):
                    method = self._parse_method(member, qualified)
                    if method is not None:
                        methods.append(method)
            elif access not in (AccessSpecifier.PRIVATE, AccessSpecifier.PROTECTED):
                self._reject_member(member, member_kind, qualified)
        return constructors, methods, declares_constructor

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
        if cursor.type.get_ref_qualifier() != RefQualifierKind.NONE:
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
