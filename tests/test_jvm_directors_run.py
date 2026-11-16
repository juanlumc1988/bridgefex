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
from tests.support.cases import Case, all_cases, load_case
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


# What the review of the runtime found ---------------------------------------------------------

RUNTIME_CASE = Path(__file__).parent / "binding_cases" / "directors_runtime"

_RUNTIME = """\
import drt.BridgefexCallbacks;
import drt.drt.Functions;
import drt.drt.Item;
import drt.drt.Pad;

class Runtime1 {
    static class Probe extends Item {
        private final boolean fail;

        Probe(boolean fail) {
            super(1);
            this.fail = fail;
        }

        @Override public int pure() { return 1; }

        @Override public int value() {
            if (fail) {
                throw new IllegalStateException("boom");
            }
            return 1;
        }

        @Override public int adopt(Item item) {
            item.close();
            return 5;
        }
    }

    static class Partial extends Probe {
        private final short u8;

        Partial(short u8) {
            super(false);
            this.u8 = u8;
        }

        @Override public Pad pad() { return new Pad(7, u8); }
    }

    static void collect() throws Exception {
        for (int i = 0; i < 5; i++) {
            System.gc();
            Thread.sleep(20);
        }
    }

    static void check(boolean condition, String what) {
        if (!condition) {
            throw new AssertionError(what);
        }
    }

    public static void main(String[] args) throws Exception {
        switch (args[0]) {
            case "adopt": {
                // The object that C++ gives up is destroyed when the override that would
                // get it never runs (the one before it failed).
                Probe good = new Probe(false);
                Probe bad = new Probe(true);
                int before = Item.alive();
                for (int i = 0; i < 10; i++) {
                    good.adoptAfter(Functions.makePlain(i));
                }
                collect();
                check(Item.alive() == before, "control leaks " + (Item.alive() - before));
                for (int i = 0; i < 10; i++) {
                    try {
                        bad.adoptAfter(Functions.makePlain(i));
                        check(false, "no failure");
                    } catch (IllegalStateException expected) {
                        check("boom".equals(expected.getMessage()), expected.getMessage());
                    }
                }
                collect();
                check(Item.alive() == before, "leaked " + (Item.alive() - before));
                break;
            }
            case "pad": {
                // A struct that an override gives out of range reaches C++ as nothing, not half.
                Item whole = new Partial((short) 200);
                whole.storePad();
                check(whole.storedPad().n == 7 && whole.storedPad().u8 == 200, "valid");
                Item half = new Partial((short) 300);
                try {
                    half.storePad();
                    check(false, "an out of range member was accepted");
                } catch (RuntimeException expected) {
                    check(String.valueOf(expected.getMessage()).contains("u8"), "message");
                }
                Pad stored = half.storedPad();
                check(stored.n == 0 && stored.u8 == 0, "part of it: n=" + stored.n);
                break;
            }
            case "thread": {
                // C++ destroys the director in a thread_local, after the thread was detached.
                int boxes = BridgefexCallbacks.registered();
                int items = Item.alive();
                for (int i = 0; i < 50; i++) {
                    check(Functions.tlRun(new Probe(false)) == 1, "no override");
                }
                collect();
                check(Item.alive() == items, "objects of C++ left");
                int left = BridgefexCallbacks.registered() - boxes;
                check(left == 0, "the release did not reach Java: " + left + " left");
                break;
            }
            case "anonymous": {
                // An anonymous class has no simple name: the message names it all the same.
                try {
                    new Item() { };
                    check(false, "a pure virtual method was not overridden");
                } catch (IllegalStateException expected) {
                    String message = expected.getMessage();
                    check(message.startsWith("Runtime1$"), message);
                    check(message.contains("Item.pure"), message);
                }
                break;
            }
            default:
                throw new IllegalArgumentException(args[0]);
        }
        System.out.println("OK " + args[0]);
    }
}
"""

# The object of a call is used for nothing but the call: it must not be collected while C++ runs its
# overrides. A compiled method does not keep a local alive after its last use (an interpreted one
# does), so the call is made often before the collections that matter: the first stage is the one
# that finds it on the JVMs we run, the second is a safety net for other states of the compiler.
_COLLECT = """\
import drt.drt.Functions;
import drt.drt.Item;

class Collect {
    static class Probe extends Item {
        Probe() {
            super(1);
        }

        @Override public int pure() { return 1; }

        @Override public int value() { return 1; }
    }

    static volatile boolean collecting;

    // One object for all the calls: a lambda that differs would send the compiled code back to the
    // interpreter.
    static final Runnable BEFORE = () -> {
        if (collecting) {
            System.gc();
        }
    };

    public static void main(String[] args) throws Exception {
        for (int stage : new int[] {3000, 30000}) {
            collecting = false;
            for (int i = 0; i < stage; i++) {
                Functions.callAfter(new Probe(), BEFORE);
            }
            Thread.sleep(300);
            collecting = true;
            for (int i = 0; i < 20; i++) {
                int result = Functions.callAfter(new Probe(), BEFORE);
                if (result != 1) {
                    throw new AssertionError("value() gave " + result);
                }
            }
        }
        System.out.println("OK collect");
    }
}
"""


@pytest.fixture(scope="module")
def runtime(
    toolchain: Toolchain, tmp_path_factory: pytest.TempPathFactory, language: str
) -> tuple[Case, Path, Path]:
    """The ``directors_runtime`` case, built: (case, library, output)."""
    runner_jvm.skip_without_java()
    case = load_case(RUNTIME_CASE)
    library, output = build(case, toolchain, tmp_path_factory.mktemp(f"{language}_runtime"))
    return case, library, output


def _run(
    runtime: tuple[Case, Path, Path],
    tmp_path: Path,
    language: str,
    name: str,
    text: str,
    *arguments: str,
    options: tuple[str, ...] = (),
) -> None:
    case, library, output = runtime
    program = _program(tmp_path, name, text)
    classes = runner_jvm.build_program(case, output, program, name, language=language)
    completed = runner_jvm.run_program(
        case,
        classes,
        *arguments,
        properties=(*options, f"-D{case.module}.library={library}"),
        main=name,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "OK" in completed.stdout, completed.stdout + completed.stderr


@pytest.mark.parametrize("scenario", ["adopt", "pad", "thread", "anonymous"])
def test_what_the_runtime_review_found_of_the_overrides(
    runtime: tuple[Case, Path, Path], tmp_path: Path, language: str, scenario: str
) -> None:
    """An object that C++ gives up is destroyed when its override is skipped; a struct result that
    fails its checks writes nothing; the release of a director that C++ destroys when its thread
    ends reaches Java."""
    _run(runtime, tmp_path, language, "Runtime1", _RUNTIME, scenario)


def test_the_object_of_a_call_is_not_collected_while_c_plus_plus_runs_its_overrides(
    runtime: tuple[Case, Path, Path], tmp_path: Path, language: str
) -> None:
    _run(runtime, tmp_path, language, "Collect", _COLLECT)
