# SPDX-License-Identifier: Apache-2.0
"""The Python names of the C++ declarations, and the names the generated package defines."""

from __future__ import annotations

import keyword
import sys
from collections.abc import Iterable, Sequence

from ...naming import unique_name

KEYWORDS = frozenset(keyword.kwlist)
"""No parameter, member or field of the C API takes the name of a keyword."""

# The names that the generated functions use for their parameters and locals, and
# the globals of the generated modules, which a parameter of the same name would hide.
PARAMETER_NAMES = frozenset(
    """
    cls ctypes weakref threading _builtins _runtime _types _lib _bind _bind_lock _out_size
    _out_data _handle _self _scope _status _found _director _vtable _record
    """.split()
)

# Members that every generated Python class defines.
RESERVED_MEMBER_NAMES = frozenset(
    """
    close _watch _adopt _ptr _mptr _collected _handle _finalizer _owned _owners _readonly
    _retained _director _director_slots _watch_director _adopt_director _dptr
    __init__ __enter__ __exit__ __copy__ __deepcopy__ __reduce__ __slots__ __weakref__
    """.split()
)

# Globals of every generated per-header Python module, and the builtins that
# close() names directly (it cannot use _builtins at interpreter exit). weakref,
# _sys, _watch and _watchers are not generated any more, but stay reserved so
# that the names already given to C++ declarations do not change.
MODULE_GLOBALS = frozenset(
    """
    ctypes weakref threading _builtins _sys _runtime _types _lib _bind _bind_lock _watch
    _watchers
    __all__ AttributeError IndexError
    """.split()
)

# Names defined by the generated package's __init__.py. A header with one of
# these names would make a submodule that replaces them.
PACKAGE_NAMES = frozenset(
    """
    load Error OK ERROR_EXCEPTION ERROR_UNKNOWN_EXCEPTION ERROR_NULL_ARGUMENT
    ERROR_OUT_OF_MEMORY ERROR_INVALID_ARGUMENT
    """.split()
)


def module_alias(stem: str) -> str:
    """The name that the generated modules give the module of a header: ``_<stem>``,
    unless that is a global of theirs (``types.h`` would hide ``_types``)."""
    return f"_{stem}_" if f"_{stem}" in MODULE_GLOBALS else f"_{stem}"


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


# Names that an IntEnum member cannot take: 'mro' makes the class creation
# fail, the others would hide the int method of every member. Fixed, not
# computed from the running Python, so that the output does not depend on it.
_ENUM_MEMBER_RESERVED = frozenset(
    "mro as_integer_ratio bit_count bit_length conjugate from_bytes is_integer to_bytes".split()
)


def enum_member_name(name: str) -> str:
    """Python name of an enumerator in an IntEnum (``None`` becomes ``None_``)."""
    if keyword.iskeyword(name) or name in _ENUM_MEMBER_RESERVED:
        return f"{name}_"
    return name


# Names that ctypes gives a meaning to in a Structure subclass.
_CTYPES_STRUCTURE_NAMES = frozenset(
    "_fields_ _anonymous_ _pack_ _align_ _layout_ _swappedbytes_".split()
)


def ctypes_field_names(names: Sequence[str]) -> tuple[str, ...]:
    """Names of the members in the ctypes mirror: ``_fields_`` and the other names
    that ctypes reads from the class get a trailing ``_``."""
    result: list[str] = []
    for name in names:
        candidate = name
        if candidate in _CTYPES_STRUCTURE_NAMES:
            candidate = unique_name(candidate + "_", frozenset(names) | frozenset(result))
        result.append(candidate)
    return tuple(result)


def module_problem(name: str) -> str | None:
    """Why the package cannot be called ``name``, or None."""
    if keyword.iskeyword(name):
        return "it is a keyword"
    if name in sys.stdlib_module_names:
        return "the Python package would shadow the standard library module of the same name"
    return None


def header_problem(stem: str) -> str | None:
    """Why a header called ``stem`` cannot have a module of the package, or None."""
    if keyword.iskeyword(stem):
        return "it is a keyword of Python, and it names the generated Python module"
    if stem in PACKAGE_NAMES:
        return f"its Python module would replace '{stem}' of the generated package"
    return None
