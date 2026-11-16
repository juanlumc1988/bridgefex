# SPDX-License-Identifier: Apache-2.0
"""What only an execution (or a compiler) shows about the directors of the Java bindings.

The golden case ``directors`` has the checks of the directors themselves (check_jvm.java, played by
tests/test_jvm_roundtrip.py on both flavors, and on older Javas by tests/test_jni_other_javas.py).
These tests are about the classes of the user: what Java refuses to compile, and the checks of the
JVM on the native code of the glue."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conformance import runner_jvm
from tests.support.build import build
from tests.support.cases import Case, all_cases
from tests.support.toolchain import Toolchain

pytestmark = pytest.mark.usefixtures("libclang_loaded")


@pytest.fixture(autouse=True)
def _java() -> None:
    runner_jvm.skip_without_java()


@pytest.fixture(scope="module", params=["jvm", "jni"])
def language(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture(scope="module")
def directors(
    toolchain: Toolchain, tmp_path_factory: pytest.TempPathFactory, language: str
) -> tuple[Case, Path, Path]:
    """The ``directors`` case, built: (case, library, output)."""
    runner_jvm.skip_without_java()
    case = next(case for case in all_cases() if case.name == "directors")
    library, output = build(case, toolchain, tmp_path_factory.mktemp(f"{language}_directors"))
    return case, library, output


def _program(directory: Path, name: str, text: str) -> Path:
    path = directory / f"{name}.java"
    path.write_text(text, encoding="utf-8")
    return path


# What Java refuses ----------------------------------------------------------------------------

_MISTAKES = {
    # C++ does not call an override of a method that is not virtual: it would never run.
    "a method that is not virtual": (
        "class Bad extends drc.shape.Shape {\n"
        "    @Override public double area() { return 1.0; }\n"
        '    public String describe() { return "mine"; }\n'
        "}\n"
    ),
    # A virtual method that cannot be overridden (final in C++).
    "a final method": (
        "class Bad extends drc.shape.Shape {\n"
        "    @Override public double area() { return 1.0; }\n"
        "    public int fixed() { return 1; }\n"
        "}\n"
    ),
    # The methods of a class below one that can be derived from are final as well.
    "a class below": (
        "class Bad extends drc.shape.Dot {\n    @Override public double area() { return 1.0; }\n}\n"
    ),
    # A virtual method with types that an override cannot have keeps the implementation of C++.
    "a method with an object by value": (
        "class Bad extends drc.shape.Shape {\n"
        "    @Override public double area() { return 1.0; }\n"
        "    public int byValue(drc.shape.Beam beam) { return 1; }\n"
        "}\n"
    ),
}


@pytest.mark.parametrize("mistake", list(_MISTAKES), ids=list(_MISTAKES))
def test_an_override_that_c_plus_plus_would_not_call_does_not_compile(
    directors: tuple[Case, Path, Path], tmp_path: Path, language: str, mistake: str
) -> None:
    case, _library, output = directors
    # The classes of a program are kept by its name: each mistake is a program of its own.
    name = "Mistake" + str(list(_MISTAKES).index(mistake))
    main = f"class {name} {{ public static void main(String[] a) {{}} }}\n"
    program = _program(tmp_path, name, _MISTAKES[mistake] + main)
    with pytest.raises(RuntimeError) as raised:
        runner_jvm.build_program(case, output, program, name, language=language)
    assert "final" in str(raised.value), str(raised.value)


# The checks of the JVM ------------------------------------------------------------------------


def test_the_directors_pass_the_checks_of_the_jni_functions_of_the_jvm(
    directors: tuple[Case, Path, Path], language: str
) -> None:
    """``-Xcheck:jni`` finds the mistakes of native code that a JVM does not: the trampolines of
    the glue run clean (C++ threads that call overrides, nested calls, results written where C++
    says)."""
    if language != "jni":
        pytest.skip("only the glue of JNI calls the JNI functions")
    case, library, output = directors
    classes = runner_jvm.build_program(
        case,
        output,
        case.check_jni,
        "CheckJni",
        support=(runner_jvm.SUPPORT,),
        language="jni",
    )
    completed = runner_jvm.run_program(
        case,
        classes,
        properties=("-Xcheck:jni", f"-D{case.module}.library={library}"),
    )
    text = completed.stdout + completed.stderr
    assert completed.returncode == 0, text
    assert "round trip OK" in completed.stdout
    assert "WARNING" not in text, text
    assert "FATAL" not in text, text
