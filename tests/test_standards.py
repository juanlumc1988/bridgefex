# SPDX-License-Identifier: Apache-2.0
"""The generated code must compile without warnings in every supported language standard.

C headers: C99, C11, C17 and C23 (MSVC: C11, C17 and its latest mode), and as
C++. C++ sources: C++11 to C++23 (MSVC: C++14 to its latest mode). Warnings
are errors.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import bridgefex
from tests.support.cases import Case, all_cases
from tests.support.toolchain import (
    GNU_C_STANDARDS,
    GNU_CXX_STANDARDS,
    MSVC_C_STANDARDS,
    MSVC_CXX_STANDARDS,
    Toolchain,
)

CASES = all_cases()

pytestmark = pytest.mark.usefixtures("libclang_loaded")

C_STANDARDS = sorted(set(GNU_C_STANDARDS) | set(MSVC_C_STANDARDS))
CXX_STANDARDS = sorted(set(GNU_CXX_STANDARDS) | set(MSVC_CXX_STANDARDS))


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    outputs = {}
    for case in CASES:
        output = tmp_path_factory.mktemp(f"standards_{case.name}")
        result = bridgefex.generate(case.headers, case.options())
        bridgefex.write_files(result.files, output)
        outputs[case.name] = output
    return outputs


def resolve(toolchain: Toolchain, language: str, standard: str, probe_dir: Path) -> str:
    supported = toolchain.c_standards if language == "c" else toolchain.cxx_standards
    if standard not in supported:
        pytest.skip(f"{standard} is not a {toolchain.kind} mode")
    flag = toolchain.resolve_standard(language, standard, probe_dir)
    if flag is None:
        pytest.skip(f"the compiler does not support {standard}")
    return flag


def consumer_source(case: Case, directory: Path, extension: str) -> Path:
    """A translation unit that includes every generated public header."""
    result = bridgefex.generate(case.headers, case.options())
    headers = sorted(
        Path(path).name for path in result.files if path.startswith("c/") and path.endswith(".h")
    )
    source = directory / f"consumer_{case.name}.{extension}"
    lines = [f'#include "{header}"' for header in headers]
    lines += ["int bridgefex_consumer(void);", "int bridgefex_consumer(void) { return 0; }"]
    source.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return source


@pytest.mark.parametrize("standard", C_STANDARDS)
@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_c_headers_compile_as_c(
    case: Case,
    standard: str,
    toolchain: Toolchain,
    generated: dict[str, Path],
    probe_dir: Path,
    tmp_path: Path,
) -> None:
    flag = resolve(toolchain, "c", standard, probe_dir)
    source = consumer_source(case, tmp_path, "c")
    include = generated[case.name] / "c"
    toolchain.syntax_check(source, "c", flag, [include]).check()


@pytest.mark.parametrize("standard", CXX_STANDARDS)
@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_cxx_sources_compile(
    case: Case,
    standard: str,
    toolchain: Toolchain,
    generated: dict[str, Path],
    probe_dir: Path,
    tmp_path: Path,
) -> None:
    flag = resolve(toolchain, "c++", standard, probe_dir)
    include = generated[case.name] / "c"
    # The C headers included from C++ code.
    consumer = consumer_source(case, tmp_path, "cpp")
    toolchain.syntax_check(consumer, "c++", flag, [include]).check()
    # The generated C++ sources, which include the wrapped headers.
    for source in sorted(include.glob("*.cpp")):
        toolchain.syntax_check(source, "c++", flag, [include, case.input_dir]).check()
