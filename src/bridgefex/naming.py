# SPDX-License-Identifier: Apache-2.0
"""Naming rules for the generated C and Python code."""

from __future__ import annotations

import keyword
import re
import sys
from collections.abc import Iterable, Sequence

from .errors import ConfigurationError
from .model import ScalarType
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

# Object-like macros of common environments: system headers, the GNU dialects
# (unix, linux, i386) and <windows.h>. A parameter with one of these names would
# be replaced by the preprocessor in the user's C code.
_COMMON_MACROS = frozenset(
    """
    errno stdin stdout stderr NULL EOF unix linux i386 small interface near far pascal
    cdecl hyper IN OUT OPTIONAL
    """.split()
)

# Names that the generated code uses for its own parameters, locals and globals.
_GENERATED_PARAMETER_NAMES = frozenset(
    """
    self cls out_self out_result ctypes weakref threading _builtins _runtime _lib _bind
    _bind_lock _out _handle _self
    """.split()
)

RESERVED_PARAMETER_NAMES = (
    frozenset(keyword.kwlist)
    | KEYWORDS
    | _COMMON_MACROS
    | _GENERATED_PARAMETER_NAMES
    # The generated C prototypes spell these types unqualified.
    | frozenset(STANDARD_TYPEDEFS)
)

# Members that every generated Python class defines.
RESERVED_MEMBER_NAMES = frozenset(
    """
    close _adopt _ptr _handle _finalizer __init__ __enter__ __exit__ __copy__ __deepcopy__
    __reduce__ __slots__ __weakref__
    """.split()
)

# Globals of every generated per-header Python module.
MODULE_GLOBALS = frozenset(
    "ctypes weakref threading _builtins _runtime _lib _bind _bind_lock __all__".split()
)

# Names defined by the generated package's __init__.py. A header with one of
# these names would make a submodule that replaces them.
PACKAGE_NAMES = frozenset(
    """
    load Error OK ERROR_EXCEPTION ERROR_UNKNOWN_EXCEPTION ERROR_NULL_ARGUMENT
    ERROR_OUT_OF_MEMORY
    """.split()
)


def check_module_name(name: str) -> str:
    """Validate the module name, which prefixes every runtime symbol.

    It must be a lowercase identifier that is valid in C, C++ and Python.
    """
    if not _MODULE_NAME.fullmatch(name):
        raise ConfigurationError(
            f"invalid module name '{name}': use lowercase letters, digits and single '_' "
            "between them, starting with a letter"
        )
    if name in KEYWORDS or keyword.iskeyword(name):
        raise ConfigurationError(f"invalid module name '{name}': it is a keyword")
    if name in sys.stdlib_module_names:
        raise ConfigurationError(
            f"invalid module name '{name}': the Python package would shadow the standard "
            "library module of the same name"
        )
    return name


def generated_macros(module: str) -> frozenset[str]:
    """Macros defined by the generated runtime headers and sources of ``module``."""
    prefix = module.upper()
    suffixes = """
        API C_API_BUILD C_API_STATIC NODISCARD OK ERROR_EXCEPTION ERROR_UNKNOWN_EXCEPTION
        ERROR_NULL_ARGUMENT ERROR_OUT_OF_MEMORY RUNTIME_H RUNTIME_INTERNAL_HPP
    """.split()
    return frozenset(f"{prefix}_{suffix}" for suffix in suffixes)


def header_guard(module: str, stem: str) -> str:
    return f"{module}_{stem}_c_h".upper()


def is_identifier(name: str) -> bool:
    """True for an ASCII identifier, valid in C, C++ and Python."""
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


def check_header_stem(stem: str) -> str:
    """Validate a header file name (without extension).

    It names the generated files and the Python module, so it must be an
    identifier, and it must not replace a name of the generated package.
    """
    if not _IDENTIFIER.fullmatch(stem) or keyword.iskeyword(stem):
        raise ConfigurationError(
            f"cannot use header name '{stem}': it must be a valid identifier "
            "(letters, digits and '_') because it names the generated Python module"
        )
    if stem.startswith("_"):
        raise ConfigurationError(f"cannot use header name '{stem}': it must not start with '_'")
    if stem in PACKAGE_NAMES:
        raise ConfigurationError(
            f"cannot use header name '{stem}': its Python module would replace "
            f"'{stem}' of the generated package"
        )
    return stem


def c_prefix(namespace: Sequence[str], module: str) -> str:
    """C prefix for a declaration: its namespaces, or the module name at global scope.

    The global scope also gets a prefix so that a generated C function never
    has the same name as the C++ function it wraps.
    """
    return "_".join(namespace) if namespace else module


def overload_suffix(parameter_types: Sequence[ScalarType]) -> str:
    """Suffix that tells overloads apart, for example ``_int32_double`` or ``_void``."""
    if not parameter_types:
        return "_void"
    return "_" + "_".join(scalar.token for scalar in parameter_types)


def python_name(name: str) -> str:
    """Make a C++ name usable as a Python name (``lambda`` becomes ``lambda_``)."""
    return f"{name}_" if keyword.iskeyword(name) else name


def _avoid(candidate: str, taken: frozenset[str]) -> str:
    while candidate in taken:
        candidate += "_"
    return candidate


def member_name(name: str, reserved: Iterable[str] = ()) -> str:
    """Python name of a class member, avoiding the names of generated members."""
    return _avoid(python_name(name), RESERVED_MEMBER_NAMES | frozenset(reserved))


def module_level_name(name: str, reserved: Iterable[str] = ()) -> str:
    """Python name of a class or function, avoiding the generated module globals."""
    return _avoid(python_name(name), MODULE_GLOBALS | frozenset(reserved))


def parameter_names(names: Sequence[str], reserved: Iterable[str] = ()) -> tuple[str, ...]:
    """Names of the parameters in the generated C and Python code.

    Unnamed parameters become ``arg1``, ``arg2``...; names that clash with a
    keyword of C, C++ or Python, a common macro, a type name or a name used
    by the generated code get a trailing ``_``, as do the names in
    ``reserved``. The result has no duplicates.
    """
    taken = RESERVED_PARAMETER_NAMES | frozenset(reserved)
    result: list[str] = []
    for position, name in enumerate(names, start=1):
        candidate = name or f"arg{position}"
        while candidate in taken or candidate in result:
            candidate += "_"
        result.append(candidate)
    return tuple(result)
