# SPDX-License-Identifier: Apache-2.0
"""Round trip through the generated Java bindings.

For each golden case with a check_jvm.java, the library of the case is built (the generated C
layer and the C++ implementation), the bindings are generated and compiled with the check, and
the check is run in its own process. The tests of the conformance suite play the same scenarios
in every language; these ones check what is particular of Java: the collector, the threads, and
how the library is found.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conformance import runner_jvm
from tests.support.build import build
from tests.support.cases import Case, all_cases, load_case
from tests.support.toolchain import Toolchain

# The golden cases that have a check, and the cases of the bindings alone (tests/binding_cases).
EXTRA_DIR = Path(__file__).parent / "binding_cases"
EXTRA_NAMES = {"lifetime", "wire"}
"""The cases of the bindings alone: they ask for no language in their case.toml (the check is
what they are for), and have both Java bindings."""
CASES = [
    case
    for case in [
        *all_cases(),
        *(load_case(path.parent) for path in sorted(EXTRA_DIR.glob("*/case.toml"))),
    ]
    if case.check_jvm.exists()
]

TWO_LOADERS = Path(__file__).parent / "support" / "jvm" / "TwoLoaders.java"
REDEPLOY = Path(__file__).parent / "support" / "jvm" / "Redeploy.java"

pytestmark = pytest.mark.usefixtures("libclang_loaded")

Built = tuple[Case, Path, Path]


@pytest.fixture(autouse=True)
def _java() -> None:
    runner_jvm.skip_without_java()


def test_there_are_checks() -> None:
    assert {case.name for case in CASES} >= {"demo", "edge", "inherit", "ownership", "lifetime"}


@pytest.fixture(scope="module", params=CASES, ids=lambda case: case.name)
def built(
    request: pytest.FixtureRequest,
    toolchain: Toolchain,
    tmp_path_factory: pytest.TempPathFactory,
) -> Built:
    case: Case = request.param
    directory = tmp_path_factory.mktemp(f"jvm_{case.name}")
    library, output = build(case, toolchain, directory)
    return case, library, output


def _check(case: Case, output: Path, language: str = "jvm") -> Path:
    program = case.check_jni if language == "jni" else case.check_jvm
    return runner_jvm.build_program(
        case, output, program, "Check", support=(runner_jvm.SUPPORT,), language=language
    )


@pytest.mark.parametrize("language", ["jvm", "jni"])
def test_jvm_round_trip(built: Built, language: str) -> None:
    case, library, output = built
    if language not in case.languages and case.name not in EXTRA_NAMES:
        pytest.skip(f"the case does not ask for the {language} bindings")
    classes = _check(case, output, language)
    completed = runner_jvm.execute(case, classes, library, "Check")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "round trip OK" in completed.stdout


@pytest.fixture(scope="module", params=["jvm", "jni"])
def language(request: pytest.FixtureRequest) -> str:
    """The Java bindings that the tests of the library run on: over the foreign function API, or
    over JNI."""
    return str(request.param)


@pytest.fixture(scope="module")
def demo(
    toolchain: Toolchain, tmp_path_factory: pytest.TempPathFactory, language: str
) -> tuple[Case, Path, Path, Path]:
    """The demo case, built, with its check compiled: (case, library, output, classes)."""
    runner_jvm.skip_without_java()
    case = next(case for case in CASES if case.name == "demo")
    directory = tmp_path_factory.mktemp(f"{language}_demo_extra")
    library, output = build(case, toolchain, directory)
    return case, library, output, _check(case, output, language)


def _run(
    case: Case,
    classes: Path,
    *arguments: str,
    properties: tuple[str, ...] = (),
    environment: dict[str, str] | None = None,
    main: str = "Check",
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a program without the variable that gives the library, unless ``environment`` does."""
    command = runner_jvm.javac()
    assert command is not None
    variables = {
        key: value for key, value in os.environ.items() if key != f"{case.module.upper()}_LIBRARY"
    }
    variables.update(environment or {})
    return subprocess.run(
        [
            runner_jvm.java_runtime(command),
            *runner_jvm.ARGUMENTS,
            *properties,
            "-cp",
            str(classes),
            main,
            *arguments,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=variables,
        timeout=600,
        cwd=cwd,
    )


def test_the_property_gives_the_library(demo: tuple[Case, Path, Path, Path]) -> None:
    case, library, _output, classes = demo
    completed = _run(case, classes, properties=(f"-D{case.module}.library={library}",))
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "round trip OK" in completed.stdout


def test_the_property_comes_before_the_environment_variable(
    demo: tuple[Case, Path, Path, Path], tmp_path: Path
) -> None:
    """The property that was given on purpose wins over the variable of the environment."""
    case, library, _output, classes = demo
    completed = _run(
        case,
        classes,
        properties=(f"-D{case.module}.library={library}",),
        environment={f"{case.module.upper()}_LIBRARY": str(tmp_path / "nothing.so")},
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "round trip OK" in completed.stdout


def test_the_library_path_of_java_is_searched(
    demo: tuple[Case, Path, Path, Path], tmp_path: Path
) -> None:
    """Without the property or the variable, the default name of the library
    (``lib<module>.so``, ``<module>.dll`` or ``lib<module>.dylib``) is looked up in
    ``java.library.path``."""
    case, library, _output, classes = demo
    folder = tmp_path / "libraries"
    folder.mkdir()
    shutil.copy(library, folder / library.name)
    completed = _run(case, classes, properties=(f"-Djava.library.path={folder}",))
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "round trip OK" in completed.stdout


def test_a_missing_library_is_reported(demo: tuple[Case, Path, Path, Path], tmp_path: Path) -> None:
    case, _library, _output, classes = demo
    completed = _run(case, classes, environment={"DEMO_LIBRARY": str(tmp_path / "nothing.so")})
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "UnsatisfiedLinkError" in output
    assert "nothing.so" in output
    assert "DEMO_LIBRARY" in output


def test_a_library_that_is_not_found_says_how_to_give_it(
    demo: tuple[Case, Path, Path, Path],
) -> None:
    case, _library, _output, classes = demo
    completed = _run(case, classes)
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "UnsatisfiedLinkError" in output
    assert "demo.library" in output
    assert "DEMO_LIBRARY" in output


def test_a_failure_to_load_the_library_is_not_for_ever(
    demo: tuple[Case, Path, Path, Path], tmp_path: Path, language: str
) -> None:
    """The next call tries again: the property can be set after a first failure."""
    case, library, output, _classes = demo
    program = tmp_path / "Retry.java"
    program.write_text(
        "class Retry {\n"
        "    public static void main(String[] args) {\n"
        "        try {\n"
        "            demo.counter.Functions.multiplyInt32Int32(1, 2);\n"
        '            throw new AssertionError("no error");\n'
        "        } catch (UnsatisfiedLinkError expected) {\n"
        "        }\n"
        '        System.setProperty("demo.library", args[0]);\n'
        "        if (demo.counter.Functions.multiplyInt32Int32(3, 4) != 12) {\n"
        '            throw new AssertionError("wrong");\n'
        "        }\n"
        '        System.out.println("retry OK");\n'
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    classes = runner_jvm.build_program(case, output, program, "Retry", language=language)
    completed = _run(case, classes, str(library), main="Retry")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "retry OK" in completed.stdout


@pytest.mark.skipif(
    sys.platform != "linux", reason="the search path of the loader is that of Linux"
)
def test_a_bare_name_is_searched_by_the_system(
    demo: tuple[Case, Path, Path, Path], tmp_path: Path
) -> None:
    """The property can be a bare name (``libdemo.so``), which the loader of the system searches,
    as the other languages do."""
    case, library, _output, classes = demo
    folder = tmp_path / "search"
    folder.mkdir()
    shutil.copy(library, folder / library.name)
    completed = _run(
        case,
        classes,
        properties=(f"-D{case.module}.library={library.name}",),
        environment={"LD_LIBRARY_PATH": str(folder)},
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "round trip OK" in completed.stdout


@pytest.mark.skipif(
    sys.platform != "linux", reason="the search path of the loader and the names are those of Linux"
)
def test_a_bare_name_with_a_version_is_searched_in_the_library_path(
    demo: tuple[Case, Path, Path, Path], tmp_path: Path
) -> None:
    """``libdemo.so.1`` (a soname with a version, as the system keeps libraries) is found by its
    name where the system looks for libraries (``LD_LIBRARY_PATH``, which Java adds to its
    ``java.library.path``): ``System.loadLibrary`` cannot name a version, the FFM flavor opens it
    with the loader of the system."""
    case, library, _output, classes = demo
    folder = tmp_path / "versions"
    folder.mkdir()
    shutil.copy(library, folder / f"{library.name}.1")
    completed = _run(
        case,
        classes,
        properties=(f"-D{case.module}.library={library.name}.1",),
        environment={"LD_LIBRARY_PATH": str(folder)},
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "round trip OK" in completed.stdout


@pytest.mark.skipif(
    sys.platform != "linux", reason="the search path of the loader and the names are those of Linux"
)
def test_a_bare_name_is_not_a_directory_of_the_working_directory(
    demo: tuple[Case, Path, Path, Path], tmp_path: Path
) -> None:
    """``-Ddemo.library=libdemo.so`` is a name to search even when the working directory has a
    directory with that name: it is not the library."""
    case, library, _output, classes = demo
    folder = tmp_path / "search"
    folder.mkdir()
    shutil.copy(library, folder / library.name)
    work = tmp_path / "work"
    (work / library.name).mkdir(parents=True)
    completed = _run(
        case,
        classes,
        properties=(f"-D{case.module}.library={library.name}",),
        environment={"LD_LIBRARY_PATH": str(folder)},
        cwd=work,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "round trip OK" in completed.stdout


@pytest.mark.parametrize("how", ["property", "library-path"])
def test_a_second_class_loader_is_told_that_the_library_is_loaded_already(
    demo: tuple[Case, Path, Path, Path], tmp_path: Path, language: str, how: str
) -> None:
    """A native library can be loaded by one class loader only (JNI): the second one gets the
    error of the JDK, and the message says it, and does not blame the search path. The other flavor
    has no such limit."""
    if language != "jni":
        pytest.skip("a restriction of JNI")
    case, library, output, _classes = demo
    program = runner_jvm.build_program(case, output, TWO_LOADERS, "TwoLoaders", language="jni")
    runtime = f"{case.module}.BridgefexRuntime"
    folder = tmp_path / "libraries"
    folder.mkdir()
    shutil.copy(library, folder / library.name)
    properties = (
        (f"-D{case.module}.library={library}",)
        if how == "property"
        else (f"-Djava.library.path={folder}",)
    )
    completed = _run(case, program, runtime, properties=properties, main="TwoLoaders")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "two loaders OK" in completed.stdout


# The library is built for the platform of this machine: a JVM that says another one is refused. A
# long of 32 bits is Windows'; on Windows, the other system is Linux (a long of 64 bits).
_OTHER_SYSTEM = "Linux" if sys.platform == "win32" else "Windows 10"


@pytest.mark.parametrize(
    "properties",
    [("-Dsun.arch.data.model=32",), (f"-Dos.name={_OTHER_SYSTEM}",)],
    ids=["32-bit", "other-system"],
)
def test_a_library_that_is_not_built_for_the_platform_is_refused(
    demo: tuple[Case, Path, Path, Path], language: str, properties: tuple[str, ...]
) -> None:
    """The layouts of the JNI bindings come from what Java says about the platform, and the
    library says its sizes when it is loaded: a difference (a 32-bit JVM over a 64-bit library, a
    ``long`` of 32 bits over one of 64) is an error that says both, not a corrupted struct."""
    if language != "jni":
        pytest.skip("the layouts of the FFM flavor are those of the linker of the JDK")
    case, library, _output, classes = demo
    completed = _run(case, classes, properties=(f"-D{case.module}.library={library}", *properties))
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "IllegalStateException" in output
    assert "is not built for it" in output


def test_a_platform_without_the_data_model_is_taken_from_the_architecture(
    demo: tuple[Case, Path, Path, Path], language: str
) -> None:
    """Where the JVM does not say its data model, the architecture does (Android, other JVMs)."""
    if language != "jni":
        pytest.skip("only the JNI bindings take their layouts from the properties")
    case, library, _output, classes = demo
    completed = _run(
        case,
        classes,
        properties=(f"-D{case.module}.library={library}", "-Dsun.arch.data.model=unknown"),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "round trip OK" in completed.stdout


def test_a_library_without_the_glue_says_so(
    demo: tuple[Case, Path, Path, Path],
    toolchain: Toolchain,
    tmp_path: Path,
    language: str,
) -> None:
    """The likeliest mistake with the JNI bindings: a library with the C layer and not the glue.
    The error says what to build, not the name of a native method."""
    if language != "jni":
        pytest.skip("only the JNI bindings need the glue")
    case, _library, _output, classes = demo
    library, _ = build(case, toolchain, tmp_path, glue=False)
    completed = _run(case, classes, properties=(f"-D{case.module}.library={library}",))
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "UnsatisfiedLinkError" in output
    assert "has no JNI glue" in output
    assert f"{case.module}_jni.cpp" in output


_OWN_ON_LOAD = """\
#include <jni.h>

// A library that has its JNI_OnLoad for other things, and registers the bindings from it.
extern "C" JNIEXPORT jint bridgefex_%(module)s_jni_register(JNIEnv* env);

extern "C" JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM* vm, void*) {
    JNIEnv* env = nullptr;
    if (vm->GetEnv(reinterpret_cast<void**>(&env), JNI_VERSION_1_6) != JNI_OK) {
        return JNI_ERR;
    }
    return bridgefex_%(module)s_jni_register(env) == JNI_OK ? JNI_VERSION_1_6 : JNI_ERR;
}
"""


def test_a_library_with_its_own_jni_on_load_registers_the_glue_from_it(
    demo: tuple[Case, Path, Path, Path], toolchain: Toolchain, tmp_path: Path, language: str
) -> None:
    """The glue defines JNI_OnLoad unless it is built with <MODULE>_JNI_NO_ONLOAD: a library that
    has its own (Android libraries do, or two modules in one library) calls the registration of
    the glue from it, and the bindings work."""
    if language != "jni":
        pytest.skip("only the JNI bindings have the glue")
    case, _library, _output, classes = demo
    own = tmp_path / "own_on_load.cpp"
    own.write_text(_OWN_ON_LOAD % {"module": case.module}, encoding="utf-8")
    library, _ = build(
        case,
        toolchain,
        tmp_path,
        glue=True,
        glue_prefix=f"#define {case.module.upper()}_JNI_NO_ONLOAD",
        extra_sources=(own,),
    )
    completed = _run(case, classes, properties=(f"-D{case.module}.library={library}",))
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "round trip OK" in completed.stdout


def test_a_redeploy_finds_the_class_loader_collected_and_the_library_free(
    toolchain: Toolchain, tmp_path: Path
) -> None:
    """The class loader of the bindings can be collected: the thread that destroys the objects
    that nobody closed ends when it has nothing to wait for, and a thread that is not in a call
    keeps nothing of the bindings. A redeploy of an application (an application server) loads
    the bindings, and the library, in a new class loader."""
    case = next(case for case in CASES if case.name == "arena")
    library, output = build(case, toolchain, tmp_path)
    program = runner_jvm.build_program(case, output, REDEPLOY, "Redeploy", language="jni")
    completed = _run(
        case,
        program,
        f"{case.module}.{case.module}.Box",
        properties=(f"-D{case.module}.library={library}",),
        main="Redeploy",
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "redeploy OK" in completed.stdout


def test_a_library_that_is_refused_says_why(
    demo: tuple[Case, Path, Path, Path], tmp_path: Path
) -> None:
    """A file that is not a library: the message has what the system said, and the cause is the
    error of the JDK."""
    case, _library, _output, classes = demo
    notes = tmp_path / "notes.txt"
    notes.write_text("this is not a library", encoding="utf-8")
    completed = _run(case, classes, environment={"DEMO_LIBRARY": str(notes)})
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "UnsatisfiedLinkError" in output
    assert "notes.txt" in output
    assert "Caused by" in output


def test_a_function_that_the_library_lacks_fails_alone(
    demo: tuple[Case, Path, Path, Path], tmp_path: Path, language: str
) -> None:
    """Each function is bound when it is first called: one that the library does not have fails
    with the name of the function, and the rest of the library works."""
    case, library, output, _classes = demo
    program = tmp_path / "Lacks.java"
    program.write_text(
        "class Lacks {\n"
        "    public static void main(String[] args) {\n"
        "        try {\n"
        "            demo.counter.Functions.multiplyInt32Int32(1, 2);\n"
        '            throw new AssertionError("no error");\n'
        "        } catch (UnsatisfiedLinkError expected) {\n"
        '            if (!expected.getMessage().contains("demo_does_not_exist")) {\n'
        "                throw new AssertionError(expected);\n"
        "            }\n"
        "        }\n"
        "        try (var counter = new demo.counter.Counter(5)) {\n"
        "            if (counter.value() != 5) {\n"
        '                throw new AssertionError("wrong");\n'
        "            }\n"
        "        }\n"
        '        System.out.println("lacks OK");\n'
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    # What the library lacks: the name that the bindings bind (a string over FFM, a native method
    # over JNI, which the library registers when it is loaded).
    wanted = '"demo_multiply_int32_int32"' if language == "jvm" else "n_demo_multiply_int32_int32"
    missing = '"demo_does_not_exist"' if language == "jvm" else "n_demo_does_not_exist"

    def lacking(files: dict[str, str]) -> dict[str, str]:
        return {
            path: content.replace(wanted, missing)
            if path.endswith("BridgefexNative.java")
            else content
            for path, content in files.items()
        }

    classes = runner_jvm.build_program(
        case, output, program, "Lacks", edit=lacking, language=language
    )
    completed = _run(
        case, classes, str(library), main="Lacks", environment={"DEMO_LIBRARY": str(library)}
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "lacks OK" in completed.stdout


def test_a_stale_library_is_refused(
    demo: tuple[Case, Path, Path, Path], tmp_path: Path, language: str
) -> None:
    """Bindings whose fingerprint is not the one of the library refuse to run."""
    case, library, _output, _classes = demo

    def stale(files: dict[str, str]) -> dict[str, str]:
        marker = 'FINGERPRINT = "'
        return {
            path: content.replace(marker, marker + "0", 1)
            if path.endswith("BridgefexRuntime.java")
            else content
            for path, content in files.items()
        }

    classes = runner_jvm.build_program(
        case,
        tmp_path,
        case.check_jvm,
        "Check",
        support=(runner_jvm.SUPPORT,),
        edit=stale,
        language=language,
    )
    completed = runner_jvm.execute(case, classes, library, "Check")
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "IllegalStateException" in output
    assert "does not match these bindings" in output


def test_native_access_without_the_flag_only_warns(
    demo: tuple[Case, Path, Path, Path],
) -> None:
    """Without ``--enable-native-access`` Java 22 to 23 warn and later versions may refuse:
    the program still works where it warns, and the warning names the flag."""
    _case, library, _output, classes = demo
    command = runner_jvm.javac()
    assert command is not None
    completed = subprocess.run(
        [runner_jvm.java_runtime(command), "-cp", str(classes), "Check"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "DEMO_LIBRARY": str(library)},
        timeout=600,
    )
    if completed.returncode == 0:
        assert "round trip OK" in completed.stdout
    else:
        assert "--enable-native-access" in completed.stdout + completed.stderr
