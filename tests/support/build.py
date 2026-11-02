# SPDX-License-Identifier: Apache-2.0
"""Build the library of a golden case: the generated C layer and its C++ implementation."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import bridgefex
from tests.support.cases import Case
from tests.support.toolchain import Toolchain

# Standard used to build the libraries in these tests; test_standards.py
# covers the other ones.
CXX_STANDARD = "c++17"


def cxx_standard(case: Case) -> str:
    """CXX_STANDARD, or the newer standard that a case needs (std::span)."""
    order = ("c++11", "c++14", "c++17", "c++20", "c++23")
    return max(CXX_STANDARD, case.min_cxx, key=order.index)


def build(
    case: Case, toolchain: Toolchain, directory: Path, extra_flags: Sequence[str] = ()
) -> tuple[Path, Path]:
    """Generate the bindings and build the shared library; return (library, output dir)."""
    output = directory / "out"
    result = bridgefex.generate(case.headers, case.options())
    bridgefex.write_files(result.files, output)
    library = directory / toolchain.shared_library_name(case.module)
    sources = sorted((output / "c").glob("*.cpp")) + list(case.impl_sources)
    toolchain.build_shared_library(
        sources, [output / "c", case.input_dir], library, cxx_standard(case), extra_flags
    ).check()
    return library, output
