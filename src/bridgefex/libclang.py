# SPDX-License-Identifier: Apache-2.0
"""Locating and loading libclang.

The libclang Python bindings (``clang.cindex``) do not ship the shared
library, and they only work reliably with a library of the same major
version. This module picks the library, checks its version before the
bindings use it and exposes the few C functions the bindings lack.
"""

from __future__ import annotations

import ctypes
import importlib.metadata
import os
import re
import sys
import threading
from pathlib import Path
from typing import Any

from clang import cindex

from .errors import LibclangError

SUPPORTED_MAJOR_VERSIONS = (20,)
"""libclang major versions that bridgefex is tested with."""

ENV_VAR = "BRIDGEFEX_LIBCLANG"
"""Environment variable with the path of the libclang shared library."""


class _CXString(ctypes.Structure):
    _fields_ = [("data", ctypes.c_void_p), ("private_flags", ctypes.c_uint)]


class _State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.path: Path | None = None
        self.version = ""
        self.is_inline_namespace: Any = None
        self.is_macro_function_like: Any = None


_state = _State()


def default_candidates() -> list[Path]:
    """Usual install locations of libclang 20, in order of preference."""
    if sys.platform == "win32":
        roots = (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMW6432"))
        unique_roots = dict.fromkeys([*(root for root in roots if root), r"C:\Program Files"])
        return [Path(root) / "LLVM" / "bin" / "libclang.dll" for root in unique_roots]
    if sys.platform == "darwin":
        return [
            Path("/opt/homebrew/opt/llvm@20/lib/libclang.dylib"),
            Path("/usr/local/opt/llvm@20/lib/libclang.dylib"),
        ]
    return [
        # Debian and Ubuntu (apt package libclang1-20).
        Path("/usr/lib/llvm-20/lib/libclang-20.so.1"),
        Path("/usr/lib/llvm-20/lib/libclang.so.1"),
        # Fedora and others that install into the system library directory.
        Path("/usr/lib64/libclang.so.20.1"),
        Path("/usr/lib/libclang.so.20.1"),
    ]


def _choose_path(explicit: str | os.PathLike[str] | None) -> Path:
    if explicit is not None:
        return Path(explicit)
    from_env = os.environ.get(ENV_VAR)
    if from_env:
        return Path(from_env)
    for candidate in default_candidates():
        if candidate.is_file():
            return candidate
    searched = "\n  ".join(str(path) for path in default_candidates())
    raise LibclangError(
        "cannot find libclang 20. Install it (on Ubuntu: apt install libclang1-20 "
        f"libclang-common-20-dev), or pass --libclang PATH, or set {ENV_VAR}. "
        f"Searched:\n  {searched}"
    )


def parse_major_version(version: str) -> int | None:
    """Extract the major version from a string such as 'clang version 20.1.2'."""
    match = re.search(r"clang version (\d+)\.", version)
    return int(match.group(1)) if match else None


def _bindings_major_version() -> int | None:
    """Major version of the installed 'clang' distribution, if it has metadata.

    Bindings installed by a system package manager may have none.
    """
    try:
        version = importlib.metadata.version("clang")
    except importlib.metadata.PackageNotFoundError:
        return None
    major = version.split(".", 1)[0]
    return int(major) if major.isdigit() else None


def _query_version(library: ctypes.CDLL) -> str:
    get_version = library.clang_getClangVersion
    get_version.argtypes = []
    get_version.restype = _CXString
    get_c_string = library.clang_getCString
    get_c_string.argtypes = [_CXString]
    get_c_string.restype = ctypes.c_char_p
    dispose = library.clang_disposeString
    dispose.argtypes = [_CXString]
    dispose.restype = None

    text = get_version()
    try:
        raw = get_c_string(text)
        return raw.decode("utf-8", "replace") if raw else ""
    finally:
        dispose(text)


def load(path: str | os.PathLike[str] | None = None) -> str:
    """Load libclang once per process and return its version string.

    The library is taken from ``path``, else from the ``BRIDGEFEX_LIBCLANG``
    environment variable, else from the usual install locations.

    Raises:
        LibclangError: the library is missing, cannot be loaded, has an
            unsupported version, or a different library is already loaded.
    """
    with _state.lock:
        if _state.path is not None:
            if path is not None and Path(path).resolve() != _state.path.resolve():
                raise LibclangError(
                    f"libclang is already loaded from {_state.path}; cannot switch to {path}"
                )
            return _state.version

        chosen = _choose_path(path)
        if not chosen.is_file():
            raise LibclangError(f"libclang not found at {chosen}")
        try:
            library = ctypes.CDLL(str(chosen))
            version = _query_version(library)
        except (OSError, AttributeError) as error:
            raise LibclangError(f"cannot load libclang from {chosen}: {error}") from error

        major = parse_major_version(version)
        if major not in SUPPORTED_MAJOR_VERSIONS:
            supported = ", ".join(str(number) for number in SUPPORTED_MAJOR_VERSIONS)
            raise LibclangError(
                f"{chosen} is '{version}', but bridgefex needs libclang {supported}"
            )

        bindings_major = _bindings_major_version()
        if bindings_major is not None and bindings_major != major:
            raise LibclangError(
                f"the libclang Python bindings are version {bindings_major} but {chosen} is "
                f"version {major}; install matching bindings (pip install 'clang=={major}.*')"
            )

        try:
            cindex.Config.set_library_file(str(chosen))
            # Make the bindings load and register the library now, so that
            # problems show up here instead of in the middle of parsing.
            _ = cindex.conf.lib
        except Exception as error:
            raise LibclangError(
                f"the libclang Python bindings cannot use {chosen}: {error}"
            ) from error

        # Missing from the Python bindings; available in the C API since LLVM 9.
        is_inline_namespace = library.clang_Cursor_isInlineNamespace
        is_inline_namespace.argtypes = [cindex.Cursor]
        is_inline_namespace.restype = ctypes.c_uint
        is_macro_function_like = library.clang_Cursor_isMacroFunctionLike
        is_macro_function_like.argtypes = [cindex.Cursor]
        is_macro_function_like.restype = ctypes.c_uint

        _state.path = chosen
        _state.version = version
        _state.is_inline_namespace = is_inline_namespace
        _state.is_macro_function_like = is_macro_function_like
        return version


def is_inline_namespace(cursor: cindex.Cursor) -> bool:
    """True if ``cursor`` is an ``inline namespace``. Requires :func:`load`."""
    if _state.is_inline_namespace is None:
        raise LibclangError("libclang is not loaded")
    return bool(_state.is_inline_namespace(cursor))


def is_macro_function_like(cursor: cindex.Cursor) -> bool:
    """True if the macro definition ``cursor`` takes arguments. Requires :func:`load`."""
    if _state.is_macro_function_like is None:
        raise LibclangError("libclang is not loaded")
    return bool(_state.is_macro_function_like(cursor))
