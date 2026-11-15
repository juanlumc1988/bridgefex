# SPDX-License-Identifier: Apache-2.0
"""What only an execution shows about the callbacks of the Java bindings.

The golden case ``callbacks`` has the checks of the callbacks themselves (check_jvm.java, played by
tests/test_jvm_roundtrip.py on both flavors, and on older Javas by tests/test_jni_other_javas.py).
These tests are about the library and the JVM around them: the first call into the module, a
redeploy, an exit that skips the shutdown hooks, the checks of the JNI functions of the JVM, and the
widest callbacks that a call can take.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.conformance import runner_jvm
from tests.support.build import build
from tests.support.cases import Case, all_cases, load_case
from tests.support.toolchain import Toolchain

pytestmark = pytest.mark.usefixtures("libclang_loaded")

REDEPLOY = Path(__file__).parent / "support" / "jvm" / "Redeploy.java"

MOST_STRINGS = {"jvm": 62, "jni": 63}
"""The most strings that a callback takes: 2 slots of user_data and 4 for each string, in the 252
slots of a call of the foreign function API, or the 255 of one over JNI."""

MOST_CALLBACKS = 42
"""A function takes at most 42 callbacks: a function, a user_data and a release function are 6
slots, and 252 is the most that both flavors can have (41 if the function has a result: its pointer
is 2 slots more)."""


@pytest.fixture(autouse=True)
def _java() -> None:
    runner_jvm.skip_without_java()


@pytest.fixture(scope="module", params=["jvm", "jni"])
def language(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture(scope="module")
def callbacks(
    toolchain: Toolchain, tmp_path_factory: pytest.TempPathFactory, language: str
) -> tuple[Case, Path, Path]:
    """The ``callbacks`` case, built: (case, library, output)."""
    runner_jvm.skip_without_java()
    case = next(case for case in all_cases() if case.name == "callbacks")
    library, output = build(case, toolchain, tmp_path_factory.mktemp(f"{language}_callbacks"))
    return case, library, output


def _program(directory: Path, name: str, text: str) -> Path:
    path = directory / f"{name}.java"
    path.write_text(text, encoding="utf-8")
    return path


# The first call into the module ----------------------------------------------------------------

_RETRY = """\
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

class Retry {
    public static void main(String[] args) {
        final List<String> seen = new ArrayList<String>();
        try {
            // The library is not found: the call fails, whichever it is that asks for it first (the
            // address of the thunk of the callback, over JNI, is one of the things that need it).
            cbx.events.Functions.setLogHandler((level, text) -> seen.add(text));
            throw new AssertionError("no error");
        } catch (UnsatisfiedLinkError expected) {
        }
        if (cbx.BridgefexCallbacks.registered() != 0 || !seen.isEmpty()) {
            throw new AssertionError("the failed call left a callback: " + seen);
        }
        System.setProperty("cbx.library", args[0]);
        cbx.events.Functions.setLogHandler((level, text) -> seen.add(text));
        if (!seen.equals(Arrays.asList("set"))) {
            throw new AssertionError("wrong " + seen);
        }
        cbx.events.Functions.setLogHandler(null);
        if (cbx.BridgefexCallbacks.registered() != 0) {
            throw new AssertionError("a callback is left");
        }
        System.out.println("retry OK");
    }
}
"""


def test_a_failure_to_load_the_library_by_a_call_that_gives_a_callback_is_not_for_ever(
    callbacks: tuple[Case, Path, Path], tmp_path: Path, language: str
) -> None:
    """The call fails, drops its callback, and the next one (once the library can be found) works:
    the class of the thunks is not left unusable by the first failure."""
    case, library, output = callbacks
    program = _program(tmp_path, "Retry", _RETRY)
    classes = runner_jvm.build_program(case, output, program, "Retry", language=language)
    completed = runner_jvm.run_program(case, classes, str(library), main="Retry")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "retry OK" in completed.stdout


# A redeploy ------------------------------------------------------------------------------------


def test_a_redeploy_of_bindings_that_gave_callbacks_finds_the_class_loader_collected(
    callbacks: tuple[Case, Path, Path], language: str
) -> None:
    """An application that gave callbacks (to a call, to an object that it forgot, and one that C++
    keeps in a global) is dropped, and loaded again in the same JVM: the class loader is collected
    (the bindings keep no thread, hook or reference of the JVM that holds it), and the library,
    which only one class loader can have, is free for the next. A C++ global that holds a callback
    of the old deployment is destroyed when the library is unloaded."""
    if language != "jni":
        pytest.skip("only one class loader can have a library over JNI")
    case, library, output = callbacks
    program = runner_jvm.build_program(case, output, REDEPLOY, "Redeploy", language="jni")
    completed = runner_jvm.run_program(
        case,
        program,
        "callbacks",
        properties=(f"-D{case.module}.library={library}",),
        main="Redeploy",
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "redeploy OK" in completed.stdout


# The process exits while C++ holds a callback ---------------------------------------------------

_EXIT = """\
class Exit {
    public static void main(String[] args) {
        int mode = Integer.parseInt(args[0]);
        // C++ holds the handler in a global; its static destructor calls it when the process exits.
        exitcb.exitcb.Functions.keep(value -> {
            System.out.println("HANDLER " + value);
            System.out.flush();
        }, mode);
        System.out.println("go");
        System.out.flush();
        if (args[1].equals("halt")) {
            Runtime.getRuntime().halt(0);
        } else if (args[1].equals("exit")) {
            System.exit(0);
        }
    }
}
"""

EXIT_CASE = Path(__file__).parent / "binding_cases" / "exit_callbacks"

# A thread that a static destructor starts and joins cannot run on Windows (the loader lock is held
# while the library is detached).
EXITS = [
    pytest.param(mode, style, id=f"mode{mode}-{style}")
    for mode in ("1", "2")
    for style in ("halt", "exit", "return")
]


@pytest.fixture(scope="module")
def exiting(
    toolchain: Toolchain, tmp_path_factory: pytest.TempPathFactory, language: str
) -> tuple[Case, Path, Path]:
    """The ``exit_callbacks`` case, built, with the program that exits compiled."""
    runner_jvm.skip_without_java()
    case = load_case(EXIT_CASE)
    library, output = build(case, toolchain, tmp_path_factory.mktemp(f"{language}_exit"))
    program = _program(tmp_path_factory.mktemp(f"{language}_exit_program"), "Exit", _EXIT)
    return case, library, runner_jvm.build_program(case, output, program, "Exit", language=language)


@pytest.mark.parametrize(("mode", "style"), EXITS)
def test_a_process_that_exits_does_not_call_the_callbacks_that_a_cpp_global_holds(
    exiting: tuple[Case, Path, Path], language: str, mode: str, style: str
) -> None:
    """The static destructor of a C++ global calls the callback that it holds (on the thread that
    exits, or on one that it starts and joins), when the process ends by ``Runtime.halt``,
    ``System.exit`` or the end of ``main``: the JVM is going away, and the destructor neither calls
    Java nor waits for it. Over JNI the glue disables the callbacks when the process exits (no
    hook of the JVM, which ``halt`` skips); over the foreign function API, a shutdown hook does,
    and ``halt`` (which runs none) crashes or blocks the process: the README says so."""
    case, library, classes = exiting
    if style == "halt" and language != "jni":
        pytest.skip("halt skips the shutdown hook that is what disables the callbacks over FFM")
    if mode == "2" and sys.platform == "win32":
        pytest.skip(
            "a thread cannot be started and joined by the destructor of a library on Windows"
        )
    completed = runner_jvm.run_program(
        case,
        classes,
        mode,
        style,
        properties=(f"-D{case.module}.library={library}",),
        main="Exit",
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "go" in completed.stdout
    assert "exitcb: destructor" in completed.stderr, completed.stderr
    assert "exitcb: done" in completed.stderr, completed.stderr
    assert "HANDLER" not in completed.stdout, completed.stdout


# The checks of the JVM -------------------------------------------------------------------------


def test_the_callbacks_pass_the_checks_of_the_jni_functions_of_the_jvm(
    callbacks: tuple[Case, Path, Path], language: str
) -> None:
    """``-Xcheck:jni`` finds the mistakes of native code that a JVM does not: a local reference
    used after it was deleted, an exception pending in a call, a wrong signature. The trampolines
    of the glue (C++ threads, nested calls, exceptions) run clean."""
    if language != "jni":
        pytest.skip("only the glue of JNI calls the JNI functions")
    case, library, output = callbacks
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


# The widest callbacks that a call can take -----------------------------------------------------


def _wide_case(directory: Path, language: str) -> Case:
    """A case made for the test: a callback with the most strings that a call of the flavor can
    take, and a function with the most callbacks."""
    strings = MOST_STRINGS[language]
    parameters = ", ".join("const std::string&" for _ in range(strings))
    callbacks = ", ".join(f"std::function<void()> f{n}" for n in range(MOST_CALLBACKS))
    (directory / "input").mkdir(parents=True)
    (directory / "impl").mkdir()
    (directory / "case.toml").write_text(
        f'module = "wide"\nheaders = ["wide.h"]\nstd = "c++17"\nlanguages = ["{language}"]\n',
        encoding="utf-8",
    )
    (directory / "input" / "wide.h").write_text(
        "#pragma once\n#include <cstdint>\n#include <functional>\n#include <string>\n"
        "namespace wide {\n"
        f"std::int32_t strings(std::function<void({parameters})> f);\n"
        f"void many({callbacks});\n"
        "}  // namespace wide\n",
        encoding="utf-8",
    )
    texts = ", ".join(f'"{n}"' for n in range(strings))
    calls = " ".join(f"f{n}();" for n in range(MOST_CALLBACKS))
    (directory / "impl" / "wide.cpp").write_text(
        '#include "wide.h"\n'
        "namespace wide {\n"
        f"std::int32_t strings(std::function<void({parameters})> f) {{\n"
        f"    f({texts});\n    return {strings};\n}}\n"
        f"void many({callbacks}) {{\n    {calls}\n}}\n"
        "}  // namespace wide\n",
        encoding="utf-8",
    )
    names = ", ".join(f"s{n}" for n in range(strings))
    runnables = ", ".join(f"() -> order.add({n})" for n in range(MOST_CALLBACKS))
    order_check = ", ".join(str(n) for n in range(MOST_CALLBACKS))
    expected = ",".join(str(n) for n in range(strings))
    (directory / "check_jvm.java").write_text(
        "import static checks.Checks.check;\n"
        "import java.util.ArrayList;\n"
        "import java.util.List;\n"
        "import wide.wide.Functions;\n\n"
        "class Check {\n"
        "    public static void main(String[] args) {\n"
        "        final List<String> seen = new ArrayList<String>();\n"
        f"        int count = Functions.strings(({names}) ->\n"
        f'            seen.add(String.join(",", {names})));\n'
        f'        check(count == {strings}, "strings");\n'
        f'        check(seen.size() == 1 && seen.get(0).equals("{expected}"),\n'
        '            "strings: " + seen);\n'
        "        final List<Integer> order = new ArrayList<Integer>();\n"
        f"        Functions.many({runnables});\n"
        f"        check(order.equals(java.util.Arrays.asList({order_check})),\n"
        '            "many: " + order);\n'
        '        check(wide.BridgefexCallbacks.registered() == 0, "nothing is left");\n'
        '        System.out.println("wide: round trip OK");\n'
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    return load_case(directory)


@pytest.fixture(scope="module")
def wide(
    toolchain: Toolchain, tmp_path_factory: pytest.TempPathFactory, language: str
) -> tuple[Case, Path, Path]:
    runner_jvm.skip_without_java()
    case = _wide_case(tmp_path_factory.mktemp(f"{language}_wide_case"), language)
    library, output = build(case, toolchain, tmp_path_factory.mktemp(f"{language}_wide"))
    return case, library, output


def test_the_widest_callbacks_that_a_call_can_take_are_called(
    wide: tuple[Case, Path, Path], language: str
) -> None:
    """The limits that the generator checks (252 slots over the foreign function API, 255 over JNI)
    are the ones of the JVM: a callback with the most strings, and a function with the most
    callbacks, link and run."""
    case, library, output = wide
    classes = runner_jvm.build_program(
        case, output, case.check_jvm, "Check", support=(runner_jvm.SUPPORT,), language=language
    )
    completed = runner_jvm.execute(case, classes, library, "Check")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "round trip OK" in completed.stdout
