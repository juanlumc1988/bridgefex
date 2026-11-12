# SPDX-License-Identifier: Apache-2.0
"""Build the library of a golden case: the generated C layer and its C++ implementation."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

import bridgefex
from bridgefex.backends.jvm.check import javac
from tests.support.cases import Case
from tests.support.toolchain import Toolchain

# Standard used to build the libraries in these tests; test_standards.py
# covers the other ones.
CXX_STANDARD = "c++17"


def cxx_standard(case: Case) -> str:
    """CXX_STANDARD, or the newer standard that a case needs (std::span)."""
    order = ("c++11", "c++14", "c++17", "c++20", "c++23")
    return max(CXX_STANDARD, case.min_cxx, key=order.index)


def jni_includes() -> list[Path]:
    """The include directories of ``jni.h`` and of its platform, from the JDK that compiles the
    bindings; empty if there is no JDK with them."""
    command = javac(9)
    if command is None:
        return []
    home = Path(command).resolve().parent.parent
    system = {"win32": "win32", "darwin": "darwin"}.get(sys.platform, "linux")
    found = [home / "include", home / "include" / system]
    return found if all(path.is_dir() for path in found) else []


def build(
    case: Case,
    toolchain: Toolchain,
    directory: Path,
    extra_flags: Sequence[str] = (),
    *,
    glue: bool | None = None,
) -> tuple[Path, Path]:
    """Generate the bindings and build the shared library; return (library, output dir).

    A case that asks for the JNI bindings (and a machine with a JDK that has ``jni.h``) has the
    glue of those in the library too, as the bindings need it: the other languages do not mind.
    ``glue=False`` builds the C layer alone (what its exports are checked on).
    """
    if glue is None:
        glue = "jni" in case.languages
    glue = glue and bool(jni_includes())
    output = directory / "out"
    result = bridgefex.generate(
        case.headers, case.options(("python", "jni") if glue else ("python",))
    )
    bridgefex.write_files(result.files, output)
    library = directory / toolchain.shared_library_name(case.module)
    sources = sorted((output / "c").glob("*.cpp")) + list(case.impl_sources)
    include_dirs = [output / "c", case.input_dir]
    if glue:
        sources += sorted((output / "jni").glob("*/native/*.cpp"))
        include_dirs += jni_includes()
    toolchain.build_shared_library(
        sources, include_dirs, library, cxx_standard(case), extra_flags
    ).check()
    return library, output
