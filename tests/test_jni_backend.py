# SPDX-License-Identifier: Apache-2.0
"""The JNI flavor of the Java bindings: the files it writes, that they are Java 8, and the glue.

The generator is the one of the foreign function flavor (see test_jvm_backend.py): these tests
are about what is different. The code that the backend writes is run by test_jvm_roundtrip.py and
the conformance suite.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

import bridgefex
from bridgefex.errors import GenerationError
from tests.conformance import runner_jvm
from tests.support.build import jni_includes
from tests.support.cases import GOLDEN_DIR, load_case
from tests.test_jvm_backend import (
    COMPILES,
    HIDING,
    OVERRIDES,
    SMALL,
    UNSUPPORTED,
    generate,
)

pytestmark = pytest.mark.usefixtures("libclang_loaded")

JNI = "jni/mod/src/main/java/mod"
LANGUAGES = ("jni",)


@pytest.fixture
def java() -> None:
    runner_jvm.skip_without_java()


def files_of(tmp_path: Path, code: str = SMALL, **options: object) -> dict[str, str]:
    return generate(tmp_path, code, languages=LANGUAGES, **options)  # type: ignore[arg-type]


def test_a_project_with_the_glue_and_a_package_per_header(tmp_path: Path) -> None:
    files = files_of(tmp_path)
    assert sorted(path for path in files if path.startswith("jni/")) == [
        "jni/mod/native/mod_jni.cpp",
        "jni/mod/pom.xml",
        f"{JNI}/BridgefexArena.java",
        f"{JNI}/BridgefexCleaner.java",
        f"{JNI}/BridgefexException.java",
        f"{JNI}/BridgefexFunction.java",
        f"{JNI}/BridgefexHandle.java",
        f"{JNI}/BridgefexKeep.java",
        f"{JNI}/BridgefexLayout.java",
        f"{JNI}/BridgefexLease.java",
        f"{JNI}/BridgefexNative.java",
        f"{JNI}/BridgefexObject.java",
        f"{JNI}/BridgefexRuntime.java",
        f"{JNI}/BridgefexSegment.java",
        f"{JNI}/BridgefexStruct.java",
        f"{JNI}/BridgefexUnsigned.java",
        f"{JNI}/api/Color.java",
        f"{JNI}/api/Counter.java",
        f"{JNI}/api/Functions.java",
        f"{JNI}/api/Point.java",
    ]
    assert not any(path.startswith("jvm/") for path in files)
    pom = files["jni/mod/pom.xml"]
    assert "<maven.compiler.source>1.8</maven.compiler.source>" in pom
    assert "mod-bindings-jni" in pom


def test_both_flavors_can_be_generated_together(tmp_path: Path) -> None:
    files = generate(tmp_path, SMALL, languages=("jvm", "jni"))
    assert any(path.startswith("jvm/") for path in files)
    assert any(path.startswith("jni/") for path in files)
    # The Java API is the same: the classes and their methods have the same names.
    ffm = {p.split("/src/main/java/")[1] for p in files if p.startswith("jvm/") and "/api/" in p}
    jni = {p.split("/src/main/java/")[1] for p in files if p.startswith("jni/") and "/api/" in p}
    assert ffm == jni


# What Java 8 does not have, and what the foreign function API is.
_NOT_JAVA_8 = re.compile(
    r"java\.lang\.foreign|\bvar\b|instanceof \w+ \w+\)|instanceof \w+ \w+ &&"
    r"|java\.lang\.ref\.Cleaner|compareAndExchange|\.repeat\(|List\.of|Map\.of|Set\.of"
    r'|"""|case [A-Z_]+ ->'
)


@pytest.mark.parametrize("code", [SMALL, *COMPILES.values()][:6])
def test_the_generated_java_is_java_8(tmp_path: Path, code: str) -> None:
    for path, text in files_of(tmp_path, code).items():
        if path.endswith(".java"):
            found = _NOT_JAVA_8.search(text)
            assert found is None, f"{path}: {found and found.group()}"


@pytest.mark.parametrize("code", COMPILES.values(), ids=COMPILES.keys())
def test_what_hides_the_names_of_the_generated_code_compiles(
    tmp_path: Path, java: None, code: str
) -> None:
    assert files_of(tmp_path, code, verify=True)


@pytest.mark.parametrize("code", OVERRIDES.values(), ids=OVERRIDES.keys())
def test_what_java_can_override_or_overload_is_accepted_and_compiles(
    tmp_path: Path, java: None, code: str
) -> None:
    assert files_of(tmp_path, code, verify=True)


@pytest.mark.parametrize("code", HIDING.values(), ids=HIDING.keys())
def test_a_method_that_would_hide_one_of_its_base_is_refused(tmp_path: Path, code: str) -> None:
    with pytest.raises(GenerationError) as raised:
        files_of(tmp_path, code)
    assert "Java cannot hide a method" in str(raised.value)


@pytest.mark.parametrize("code", UNSUPPORTED.values(), ids=UNSUPPORTED.keys())
def test_what_the_jni_bindings_do_not_have_is_refused_saying_what_to_leave_out(
    tmp_path: Path, code: str
) -> None:
    with pytest.raises(GenerationError) as raised:
        files_of(tmp_path, code)
    [diagnostic, advice] = raised.value.diagnostics
    assert "not supported in Java yet" in diagnostic.message
    assert diagnostic.location is not None
    assert "leave 'jni' out" in advice.message


# -- The glue --------------------------------------------------------------------------------------


def test_the_glue_registers_each_native_method_of_the_bindings(tmp_path: Path) -> None:
    files = files_of(tmp_path)
    glue = files["jni/mod/native/mod_jni.cpp"]
    native = files[f"{JNI}/BridgefexNative.java"]
    declared = set(re.findall(r"private static native \w+ (n_\w+)\(", native))
    registered = set(re.findall(r'const_cast<char\*>\("(n_\w+)"\)', glue))
    assert declared == registered
    assert len(declared) > 10
    # The helpers of the runtime are native methods of their own.
    for helper in ("addressOf", "length", "copyOut", "sizes"):
        assert f'("{helper}")' in glue
        assert re.search(rf"static native \w+(\[\])? {helper}\(", native)
    assert "JNI_OnLoad" in glue
    assert 'FindClass("mod/BridgefexNative")' in glue


def test_the_glue_includes_the_headers_of_the_c_layer_and_calls_its_functions(
    tmp_path: Path,
) -> None:
    glue = files_of(tmp_path)["jni/mod/native/mod_jni.cpp"]
    for header in ("mod_runtime.h", "mod_types.h", "api_c.h"):
        assert f'#include "{header}"' in glue
    assert "mod_Counter_create_int(" in glue
    assert "mod_Counter_destroy(" in glue
    assert "mod_last_error(" in glue
    # Pointers cross as addresses, as wide as the platform's pointer says.
    assert "static_cast<uintptr_t>" in glue


def test_a_function_whose_parameters_do_not_fit_a_method_is_refused(tmp_path: Path) -> None:
    parameters = ", ".join(f"const std::string& s{n}" for n in range(90))
    with pytest.raises(GenerationError) as raised:
        files_of(tmp_path, f"int many({parameters});")
    assert "too many parameters" in raised.value.diagnostics[0].message


def test_the_glue_is_the_same_whatever_the_other_languages(tmp_path: Path) -> None:
    alone = files_of(tmp_path)
    with_others = generate(tmp_path, SMALL, languages=("python", "csharp", "jni"))
    assert alone["jni/mod/native/mod_jni.cpp"] == with_others["jni/mod/native/mod_jni.cpp"]


def test_the_runtime_takes_its_layouts_from_the_platform_and_checks_them_when_it_loads(
    tmp_path: Path,
) -> None:
    runtime = files_of(tmp_path)[f"{JNI}/BridgefexRuntime.java"]
    assert "BridgefexNative.sizes()" in runtime
    assert "sun.arch.data.model" in runtime
    assert "is not built for it" in runtime


def test_a_class_asks_nothing_of_java_9(tmp_path: Path) -> None:
    object_runtime = files_of(tmp_path, SMALL)[f"{JNI}/BridgefexObject.java"]
    assert "BridgefexCleaner" in object_runtime
    assert "java.lang.ref.Cleaner" not in object_runtime


def test_the_two_flavors_say_the_same_in_the_options(tmp_path: Path) -> None:
    languages = bridgefex.LANGUAGES
    assert "jni" in languages
    assert "jvm" in languages


# -- The glue on the targets that have no JVM here -------------------------------------------------

_STUBS = {
    "stdio.h": "#pragma once\n#include <stddef.h>\n",
    "string.h": '#pragma once\n#include <stddef.h>\nextern "C" size_t strlen(const char*);\n',
}
"""What ``jni.h`` and the glue ask of the C library, for a target whose headers are not here."""


@pytest.mark.parametrize(
    "target", ["armv7a-linux-gnueabihf", "i686-linux-gnu", "aarch64-linux-gnu", "x86_64-linux-gnu"]
)
@pytest.mark.parametrize("name", ["demo", "containers", "inherit"])
def test_the_glue_compiles_for_32_and_64_bit_targets_without_warnings(
    tmp_path: Path, target: str, name: str
) -> None:
    """Pointers are 32 bits on 32-bit ARM: the glue converts the addresses of Java with explicit
    casts, and says nothing under -Wconversion. Checked with clang for the target, over stand-in
    headers of the C library (it only needs a syntax check, not a sysroot)."""
    clang = shutil.which("clang++")
    includes = jni_includes()
    if clang is None or not includes:
        pytest.skip("needs clang and a JDK with jni.h")
    case = load_case(GOLDEN_DIR / name)
    files = bridgefex.generate(case.headers, case.options(("jni",))).files
    bridgefex.write_files(files, tmp_path / "out")
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for header, text in _STUBS.items():
        (stubs / header).write_text(text, encoding="utf-8")
    resource = subprocess.run(
        ["clang", "-print-resource-dir"], capture_output=True, text=True, check=True
    ).stdout.strip()
    glue = next((tmp_path / "out" / "jni").glob("*/native/*.cpp"))
    completed = subprocess.run(
        [
            clang,
            f"--target={target}",
            "-std=c++17",
            "-fsyntax-only",
            "-Wall",
            "-Wextra",
            "-Wpedantic",
            "-Wconversion",
            "-Werror",
            "-nostdinc",
            "-isystem",
            f"{resource}/include",
            f"-I{stubs}",
            *(f"-I{path}" for path in includes),
            f"-I{tmp_path / 'out' / 'c'}",
            f"-I{case.input_dir}",
            str(glue),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
