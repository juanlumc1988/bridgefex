# SPDX-License-Identifier: Apache-2.0
"""Native checks of guarantees that the round trips do not exercise.

- the thread-local, truncated, UTF-8-safe last error of the generated runtime;
- glibc thread cancellation through a generated wrapper;
- the compile-time check for over-aligned classes before C++17;
- a unity build of the generated sources of a module;
- <MODULE>_NODISCARD really warns when a status is ignored;
- the test toolchain really turns warnings into errors (otherwise every
  "compiles without warnings" test would pass vacuously).
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest

import bridgefex
from tests.support.cases import GOLDEN_DIR
from tests.support.toolchain import Toolchain

NATIVE_DIR = Path(__file__).resolve().parent / "native"
DEMO_C = GOLDEN_DIR / "demo" / "expected" / "c"

pytestmark = pytest.mark.usefixtures("libclang_loaded")


def executable_name(name: str, toolchain: Toolchain) -> str:
    return f"{name}.exe" if toolchain.kind == "msvc" else name


def standard(toolchain: Toolchain, language: str, wanted: str, probe_dir: Path) -> str:
    flag = toolchain.resolve_standard(language, wanted, probe_dir)
    if flag is None:
        pytest.skip(f"the compiler does not support {wanted}")
    return flag


def test_last_error_is_thread_local_truncated_and_utf8_safe(
    toolchain: Toolchain, tmp_path: Path, probe_dir: Path
) -> None:
    program = tmp_path / executable_name("last_error_check", toolchain)
    sources = [NATIVE_DIR / "last_error_check.cpp", DEMO_C / "demo_runtime.cpp"]
    flag = standard(toolchain, "c++", "c++17", probe_dir)
    toolchain.build_cxx_executable(sources, [DEMO_C], program, flag).check()
    completed = subprocess.run([str(program)], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "last_error OK" in completed.stdout


@pytest.mark.skipif(
    not sys.platform.startswith("linux") or platform.libc_ver()[0] != "glibc",
    reason="thread cancellation by forced unwinding is specific to glibc",
)
def test_thread_exit_inside_a_wrapper_ends_the_thread(
    toolchain: Toolchain, tmp_path: Path, probe_dir: Path
) -> None:
    """pthread_exit() unwinds with a special exception that must not be swallowed."""
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    header = source_dir / "worker.h"
    header.write_text("#pragma once\nnamespace uw { void stop_thread(); }\n", encoding="utf-8")
    (source_dir / "worker.cpp").write_text(
        '#include "worker.h"\n#include <pthread.h>\n'
        "void uw::stop_thread() { pthread_exit(nullptr); }\n",
        encoding="utf-8",
    )
    (source_dir / "main.c").write_text(
        """#include "worker_c.h"
#include <pthread.h>
#include <stdio.h>

static void* body(void* argument)
{
    uw_status status = uw_stop_thread();
    (void)argument;
    (void)status;
    puts("not reached");
    return NULL;
}

int main(void)
{
    pthread_t thread;
    if (pthread_create(&thread, NULL, body, NULL) != 0 || pthread_join(thread, NULL) != 0) {
        return 1;
    }
    puts("thread ended normally");
    return 0;
}
""",
        encoding="utf-8",
    )
    result = bridgefex.generate([header], bridgefex.Options("uw", languages=()))
    output = tmp_path / "out"
    bridgefex.write_files(result.files, output)
    library = tmp_path / "libuw.so"
    sources = [*sorted((output / "c").glob("*.cpp")), source_dir / "worker.cpp"]
    flag = standard(toolchain, "c++", "c++17", probe_dir)
    toolchain.build_shared_library(sources, [output / "c", source_dir], library, flag).check()
    program = tmp_path / "main"
    c_flag = standard(toolchain, "c", "c11", probe_dir)
    toolchain.build_c_executable(
        source_dir / "main.c", [output / "c"], library, program, c_flag
    ).check()
    completed = subprocess.run([str(program)], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.splitlines() == ["thread ended normally"]


def test_over_aligned_classes_need_cxx17(
    toolchain: Toolchain, tmp_path: Path, probe_dir: Path
) -> None:
    header = tmp_path / "block.h"
    header.write_text(
        "#pragma once\nnamespace al {\n"
        "class alignas(64) Block { public: Block(); int get() const; "
        "private: unsigned char data_[64]; };\n}\n",
        encoding="utf-8",
    )
    result = bridgefex.generate([header], bridgefex.Options("al", languages=()))
    output = tmp_path / "out"
    bridgefex.write_files(result.files, output)
    source = output / "c" / "block_c.cpp"
    include_dirs = [output / "c", tmp_path]
    old = toolchain.syntax_check(
        source, "c++", standard(toolchain, "c++", "c++14", probe_dir), include_dirs
    )
    assert old.returncode != 0
    assert "over-aligned: compile this file as C++17 or later" in old.output
    new = toolchain.syntax_check(
        source, "c++", standard(toolchain, "c++", "c++17", probe_dir), include_dirs
    )
    new.check()


@pytest.mark.skipif(sys.maxsize <= 2**32, reason="operator new aligns to 8 bytes on 32-bit targets")
def test_16_byte_alignment_is_fine_before_cxx17_on_64_bit_targets(
    toolchain: Toolchain, tmp_path: Path, probe_dir: Path
) -> None:
    """operator new returns 16-byte aligned memory there, although MSVC's
    std::max_align_t (double) is only 8-byte aligned."""
    header = tmp_path / "vec.h"
    header.write_text(
        "#pragma once\nnamespace m3d {\n"
        "class alignas(16) Vec4 { public: Vec4(); float x() const; private: float v_[4]; };\n}\n",
        encoding="utf-8",
    )
    result = bridgefex.generate([header], bridgefex.Options("m3d", std="c++14", languages=()))
    output = tmp_path / "out"
    bridgefex.write_files(result.files, output)
    toolchain.syntax_check(
        output / "c" / "vec_c.cpp",
        "c++",
        standard(toolchain, "c++", "c++14", probe_dir),
        [output / "c", tmp_path],
    ).check()


# Modes in which an ignored status must be diagnosed. MSVC has no attribute
# for it in C, and only [[nodiscard]] (C++17 and later) in C++.
_NODISCARD_MODES = {
    "gnu": [("c", "c99"), ("c", "c17"), ("c", "c23"), ("c++", "c++11"), ("c++", "c++17")],
    "msvc": [("c++", "c++17"), ("c++", "c++latest")],
}


@pytest.mark.parametrize("mode", range(5))
def test_ignoring_a_status_is_diagnosed(
    mode: int, toolchain: Toolchain, tmp_path: Path, probe_dir: Path
) -> None:
    modes = _NODISCARD_MODES[toolchain.kind]
    if mode >= len(modes):
        pytest.skip(f"{toolchain.kind} has only {len(modes)} such modes")
    language, wanted = modes[mode]
    flag = standard(toolchain, language, wanted, probe_dir)
    extension = "c" if language == "c" else "cpp"
    ignored = tmp_path / f"ignored.{extension}"
    ignored.write_text(
        '#include "counter_c.h"\n'
        "void bump(demo_Counter* counter);\n"
        "void bump(demo_Counter* counter) { demo_Counter_increment(counter); }\n",
        encoding="utf-8",
    )
    used = tmp_path / f"used.{extension}"
    used.write_text(
        '#include "counter_c.h"\n'
        "int bump(demo_Counter* counter);\n"
        "int bump(demo_Counter* counter) { return demo_Counter_increment(counter); }\n",
        encoding="utf-8",
    )
    # A full compile: GCC reports -Wunused-result after the syntax checks.
    failed = toolchain.compile_object(ignored, language, flag, [DEMO_C], tmp_path / "ignored.o")
    assert failed.returncode != 0, failed.output
    toolchain.compile_object(used, language, flag, [DEMO_C], tmp_path / "used.o").check()


@pytest.mark.parametrize("language", ["c", "c++"])
def test_warnings_are_errors(
    language: str, toolchain: Toolchain, tmp_path: Path, probe_dir: Path
) -> None:
    """Canary: the flags used by the other tests must reject warnings."""
    flag = standard(toolchain, language, "c17" if language == "c" else "c++17", probe_dir)
    extension = "c" if language == "c" else "cpp"
    unused = tmp_path / f"unused.{extension}"
    unused.write_text(
        "int answer(void);\nint answer(void) { int unused = 0; return 42; }\n", encoding="utf-8"
    )
    assert toolchain.syntax_check(unused, language, flag, []).returncode != 0
    if toolchain.kind == "gnu":
        undefined = tmp_path / f"undefined.{extension}"
        undefined.write_text("#if UNDEFINED_MACRO\n#endif\nint x;\n", encoding="utf-8")
        assert toolchain.syntax_check(undefined, language, flag, []).returncode != 0


def test_objects_alive_at_exit_are_not_destroyed(
    toolchain: Toolchain, tmp_path: Path, probe_dir: Path
) -> None:
    """Other threads may still use them at exit; collected objects are destroyed as usual."""
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    header = source_dir / "life.h"
    header.write_text(
        "#pragma once\nnamespace life { class Res { public: Res(); ~Res(); int get() const; }; }\n",
        encoding="utf-8",
    )
    (source_dir / "life.cpp").write_text(
        '#include "life.h"\n#include <cstdio>\n'
        "life::Res::Res() {}\n"
        'life::Res::~Res() { std::puts("destroyed"); std::fflush(stdout); }\n'
        "int life::Res::get() const { return 1; }\n",
        encoding="utf-8",
    )
    result = bridgefex.generate([header], bridgefex.Options("life"))
    output = tmp_path / "out"
    bridgefex.write_files(result.files, output)
    library = tmp_path / toolchain.shared_library_name("life")
    sources = [*sorted((output / "c").glob("*.cpp")), source_dir / "life.cpp"]
    flag = standard(toolchain, "c++", "c++17", probe_dir)
    toolchain.build_shared_library(sources, [output / "c", source_dir], library, flag).check()
    code = (
        "from life import life\n"
        "collected = life.Res()\n"
        "del collected\n"
        "kept = life.Res()\n"
        "print('exiting', flush=True)\n"
    )
    environment = {
        **os.environ,
        "PYTHONPATH": str(output / "python"),
        "LIFE_LIBRARY": str(library),
    }
    completed = subprocess.run(
        [sys.executable, "-c", code], env=environment, capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.split() == ["destroyed", "exiting"]


def test_unity_build(toolchain: Toolchain, tmp_path: Path, probe_dir: Path) -> None:
    """The generated sources of a module can be compiled as one translation unit."""
    unity = tmp_path / "unity.cpp"
    unity.write_text(
        "".join(f'#include "{source.name}"\n' for source in sorted(DEMO_C.glob("*.cpp"))),
        encoding="utf-8",
    )
    include_dirs = [DEMO_C, GOLDEN_DIR / "demo" / "input"]
    for wanted in ("c++14", "c++17"):
        flag = standard(toolchain, "c++", wanted, probe_dir)
        toolchain.syntax_check(unity, "c++", flag, include_dirs).check()
