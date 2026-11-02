# SPDX-License-Identifier: Apache-2.0
"""Naming rules for the generated C code and the names the bindings must leave alone."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence

from .errors import ConfigurationError
from .typemap import STANDARD_TYPEDEFS

# Lowercase; no trailing '_' and no '__', which would make every generated
# identifier reserved in C++.
_MODULE_NAME = re.compile(r"[a-z](?:_?[a-z0-9])*")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Keywords of C (up to C23) and C++ (up to C++23).
_C_KEYWORDS = frozenset(
    """
    alignas alignof auto bool break case char const constexpr continue default do double
    else enum extern false float for goto if inline int long nullptr register restrict
    return short signed sizeof static static_assert struct switch thread_local true typedef
    typeof typeof_unqual union unsigned void volatile while _Alignas _Alignof _Atomic _BitInt
    _Bool _Complex _Decimal128 _Decimal32 _Decimal64 _Generic _Imaginary _Noreturn
    _Static_assert _Thread_local
    """.split()
)
_CPP_KEYWORDS = frozenset(
    """
    and and_eq asm bitand bitor catch char16_t char32_t char8_t class co_await co_return
    co_yield compl concept const_cast consteval constinit decltype delete dynamic_cast
    explicit export friend mutable namespace new noexcept not not_eq operator or or_eq
    private protected public reinterpret_cast requires static_cast template this throw try
    typeid typename using virtual wchar_t xor xor_eq
    """.split()
)
KEYWORDS = _C_KEYWORDS | _CPP_KEYWORDS
"""Keywords of C and C++: never valid as generated identifiers."""

# Object-like macros of common environments: the C library (<complex.h> turns a
# 'double complex' parameter into an unnamed 'double _Complex' one), POSIX and
# glibc struct-field macros, the GNU dialects (unix, linux, i386) and
# <windows.h>. A parameter with one of these names would be replaced by the
# preprocessor in the user's C code. The list is fixed, so that the generated
# code does not depend on the platform that generates it.
_COMMON_MACROS = frozenset(
    """
    errno stdin stdout stderr NULL EOF complex imaginary I noreturn math_errhandling
    h_errno h_addr st_atime st_mtime st_ctime d_fileno s6_addr sched_priority sa_handler
    sa_sigaction si_pid si_uid si_status si_addr si_value si_band si_int si_ptr si_fd
    unix linux i386 small interface near far pascal cdecl hyper IN OUT OPTIONAL
    """.split()
)

# Names that the generated C code uses for its own parameters and locals.
_GENERATED_PARAMETER_NAMES = frozenset(
    "self out_self out_result out_result_size _out _item _result".split()
)

RESERVED_PARAMETER_NAMES = (
    KEYWORDS
    | _COMMON_MACROS
    | _GENERATED_PARAMETER_NAMES
    # The generated C prototypes spell these types unqualified.
    | frozenset(STANDARD_TYPEDEFS)
)


def check_module_name(name: str, problem: Callable[[str], str | None] | None = None) -> str:
    """Validate the module name, which prefixes every runtime symbol.

    It must be a lowercase identifier that is valid in C and C++, and that the
    bindings can use: ``problem`` says why they cannot (see
    :class:`bridgefex.plan.Reservations`).
    """
    if not _MODULE_NAME.fullmatch(name):
        raise ConfigurationError(
            f"invalid module name '{name}': use lowercase letters, digits and single '_' "
            "between them, starting with a letter"
        )
    if name in KEYWORDS:
        raise ConfigurationError(f"invalid module name '{name}': it is a keyword")
    reason = problem(name) if problem is not None else None
    if reason is not None:
        raise ConfigurationError(f"invalid module name '{name}': {reason}")
    return name


def generated_macros(module: str) -> frozenset[str]:
    """Macros defined by the generated runtime headers and sources of ``module``."""
    prefix = module.upper()
    suffixes = """
        API C_API_BUILD C_API_STATIC C_API_AFTER_GLIBC NODISCARD OK ERROR_EXCEPTION
        ERROR_UNKNOWN_EXCEPTION ERROR_NULL_ARGUMENT ERROR_OUT_OF_MEMORY ERROR_INVALID_ARGUMENT
        RUNTIME_H RUNTIME_INTERNAL_HPP TYPES_H
    """.split()
    return frozenset(f"{prefix}_{suffix}" for suffix in suffixes)


def header_guard(module: str, stem: str) -> str:
    return f"{module}_{stem}_c_h".upper()


def is_identifier(name: str) -> bool:
    """True for an ASCII identifier, valid in C and C++."""
    return _IDENTIFIER.fullmatch(name) is not None


def c_identifier_problem(name: str) -> str | None:
    """Why ``name`` cannot be a generated C identifier, or None if it can."""
    if name in KEYWORDS:
        return "is a C or C++ keyword"
    # Generated names are global, where C and C++ reserve every name that
    # starts with '_'.
    if "__" in name or name.startswith("_"):
        return "is a reserved identifier in C and C++ (it contains '__' or starts with '_')"
    return None


def check_header_stem(stem: str, problem: Callable[[str], str | None] | None = None) -> str:
    """Validate a header file name (without extension).

    It names the generated files, so it must be an identifier; and the bindings
    must be able to give a module to it: ``problem`` says why they cannot (see
    :class:`bridgefex.plan.Reservations`).
    """
    if not _IDENTIFIER.fullmatch(stem) or stem in KEYWORDS:
        raise ConfigurationError(
            f"cannot use header name '{stem}': it must be a valid identifier "
            "(letters, digits and '_') that is not a keyword"
        )
    if stem.startswith("_"):
        raise ConfigurationError(f"cannot use header name '{stem}': it must not start with '_'")
    reason = problem(stem) if problem is not None else None
    if reason is not None:
        raise ConfigurationError(f"cannot use header name '{stem}': {reason}")
    return stem


def c_prefix(namespace: Sequence[str], module: str) -> str:
    """C prefix for a declaration: its namespaces, or the module name at global scope.

    The global scope also gets a prefix so that a generated C function never
    has the same name as the C++ function it wraps.
    """
    return "_".join(namespace) if namespace else module


def overload_suffix(tokens: Sequence[str]) -> str:
    """Suffix that tells overloads apart from the tokens of their parameter types,
    for example ``_int32_double``, or ``_void`` without parameters."""
    if not tokens:
        return "_void"
    return "_" + "_".join(tokens)


def unique_name(candidate: str, taken: Iterable[str]) -> str:
    """``candidate``, else with a trailing ``_``, else with a number (``x2``,
    ``x3``...): never ``__``, which C and C++ reserve."""
    if candidate not in taken:
        return candidate
    if not candidate.endswith("_") and candidate + "_" not in taken:
        return candidate + "_"
    number = 2
    while f"{candidate}{number}" in taken:
        number += 1
    return f"{candidate}{number}"


def parameter_names(names: Sequence[str], reserved: Iterable[str] = ()) -> tuple[str, ...]:
    """Names of the parameters in the generated C code and the bindings.

    Unnamed parameters become ``arg1``, ``arg2``...; names that clash with a
    keyword of C or C++, a common macro, a type name or a name used
    by the generated code, or with the names in ``reserved``, are made unique
    with :func:`unique_name` (a trailing ``_`` first). The result has no
    duplicates.
    """
    return _unique_names(
        [name or f"arg{position}" for position, name in enumerate(names, start=1)],
        RESERVED_PARAMETER_NAMES | frozenset(reserved),
    )


def _unique_names(names: Sequence[str], taken: frozenset[str]) -> tuple[str, ...]:
    """``names`` without those in ``taken`` or repeated: a name that must change
    avoids every other given name too, so that it never takes the name of a
    later one."""
    result: list[str] = []
    for position, name in enumerate(names):
        if name not in taken and name not in result:
            result.append(name)
            continue
        others = frozenset(names[position + 1 :])
        result.append(unique_name(name, taken | others | frozenset(result)))
    return tuple(result)


def derived_names(
    cpp_names: Sequence[str],
    final_names: Sequence[str],
    wants: Sequence[bool],
    suffix: str,
    reserved: Iterable[str] = (),
) -> tuple[str | None, ...]:
    """Names of the C parameters derived from other parameters, such as the
    ``_size`` of a string: one per parameter, None where ``wants`` is False.

    The name is the C++ name without trailing ``_`` (``argN`` when unnamed)
    plus ``suffix``, never the final name plus ``suffix``, which could contain
    ``__`` (``from`` becomes ``from_``). When it clashes with a reserved name,
    a name in ``reserved``, a final name or an earlier derived name, it is made
    unique with :func:`unique_name`. Call it once the final names are known.
    """
    taken = RESERVED_PARAMETER_NAMES | frozenset(reserved) | frozenset(final_names)
    used: set[str] = set()
    result: list[str | None] = []
    for position, (cpp_name, wanted) in enumerate(zip(cpp_names, wants, strict=True), start=1):
        if not wanted:
            result.append(None)
            continue
        candidate = unique_name(
            (cpp_name.rstrip("_") or f"arg{position}") + suffix, taken | frozenset(used)
        )
        used.add(candidate)
        result.append(candidate)
    return tuple(result)


_FIELD_RESERVED = KEYWORDS | _COMMON_MACROS | frozenset(STANDARD_TYPEDEFS)


def field_names(names: Sequence[str], reserved: Iterable[str] = ()) -> tuple[str, ...]:
    """Names of the members of a value struct in its C mirror and in the bindings:
    like :func:`parameter_names`, without the names that only
    parameters avoid (``self``, ``out_result``...)."""
    return _unique_names(names, _FIELD_RESERVED | frozenset(reserved))
