# SPDX-License-Identifier: Apache-2.0
"""Round-trip tests: build the generated C layer with the case's C++ code and call it.

For each golden case with an impl/ directory, the generated sources are
compiled into a shared library together with the C++ implementation. Then
check_c.c (plain C) and check_python.py (the generated Python bindings) run
against it, each in its own process.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import bridgefex
from tests.support.cases import Case, all_cases
from tests.support.toolchain import Toolchain

CASES = [case for case in all_cases() if case.impl_sources]

pytestmark = pytest.mark.usefixtures("libclang_loaded")

# Standard used to build the libraries in these tests; test_standards.py
# covers the other ones.
CXX_STANDARD = "c++17"


def build(case: Case, toolchain: Toolchain, directory: Path) -> tuple[Path, Path]:
    """Generate the bindings and build the shared library; return (library, output dir)."""
    output = directory / "out"
    result = bridgefex.generate(case.headers, case.options())
    bridgefex.write_files(result.files, output)
    library = directory / toolchain.shared_library_name(case.module)
    sources = sorted((output / "c").glob("*.cpp")) + list(case.impl_sources)
    toolchain.build_shared_library(
        sources, [output / "c", case.input_dir], library, CXX_STANDARD
    ).check()
    return library, output


def expected_exports(case: Case) -> set[str]:
    result = bridgefex.generate(case.headers, case.options())
    plan_header = result.files[f"c/{case.module}_runtime.h"]
    names = {f"{case.module}_last_error", f"{case.module}_api_fingerprint"}
    assert f"{case.module}_api_fingerprint" in plan_header
    for path, content in result.files.items():
        if path.startswith("c/") and path.endswith("_c.h"):
            for line in content.splitlines():
                if "_API " in line and "(" in line:
                    names.add(line.split("(", 1)[0].split()[-1])
    return names


def test_round_trip_cases_exist() -> None:
    assert {case.name for case in CASES} >= {"demo", "edge", "scalars"}


@pytest.fixture(scope="module", params=CASES, ids=lambda case: case.name)
def built(
    request: pytest.FixtureRequest,
    toolchain: Toolchain,
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Case, Path, Path]:
    case: Case = request.param
    directory = tmp_path_factory.mktemp(f"roundtrip_{case.name}")
    library, output = build(case, toolchain, directory)
    return case, library, output


def test_python_round_trip(built: tuple[Case, Path, Path]) -> None:
    case, library, output = built
    environment = {
        **os.environ,
        "PYTHONPATH": str(output / "python"),
        f"{case.module.upper()}_LIBRARY": str(library),
    }
    completed = subprocess.run(
        [sys.executable, "-X", "dev", "-W", "error", str(case.check_python)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "round trip OK" in completed.stdout


def test_python_loads_library_next_to_package(
    built: tuple[Case, Path, Path], tmp_path: Path
) -> None:
    """Without the environment variable, the library next to the package is used."""
    case, library, output = built
    package = output / "python" / case.module
    local_copy = package / library.name
    local_copy.write_bytes(library.read_bytes())
    environment = {**os.environ, "PYTHONPATH": str(output / "python")}
    environment.pop(f"{case.module.upper()}_LIBRARY", None)
    code = f"import {case.module} as m; m.load(); print(m._runtime._library_path)"
    completed = subprocess.run(
        [sys.executable, "-c", code], env=environment, capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    assert Path(completed.stdout.strip()) == local_copy


def test_import_does_not_load_and_load_takes_a_path(built: tuple[Case, Path, Path]) -> None:
    """Modules can be imported first; load(path) chooses the library before the first call."""
    case, library, output = built
    environment = {**os.environ, "PYTHONPATH": str(output / "python")}
    environment.pop(f"{case.module.upper()}_LIBRARY", None)
    modules = ", ".join(header.stem for header in case.headers)
    code = (
        f"import {case.module} as m\n"
        f"from {case.module} import {modules}\n"
        "assert m._runtime._library is None\n"
        f"m.load({str(library)!r})\n"
        "assert m._runtime._library_path == " + repr(str(library)) + "\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], env=environment, capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr


def test_stale_library_is_refused(built: tuple[Case, Path, Path], tmp_path: Path) -> None:
    case, library, output = built
    runtime = output / "python" / case.module / "_runtime.py"
    stale = tmp_path / "python" / case.module
    stale.mkdir(parents=True)
    for source in runtime.parent.glob("*.py"):
        text = source.read_text(encoding="utf-8")
        if source.name == "_runtime.py":
            text = text.replace('API_FINGERPRINT = "', 'API_FINGERPRINT = "0')
        (stale / source.name).write_text(text, encoding="utf-8")
    environment = {
        **os.environ,
        "PYTHONPATH": str(tmp_path / "python"),
        f"{case.module.upper()}_LIBRARY": str(library),
    }
    code = f"import {case.module} as m; m.load()"
    completed = subprocess.run(
        [sys.executable, "-c", code], env=environment, capture_output=True, text=True, check=False
    )
    assert completed.returncode != 0
    assert "does not match these bindings" in completed.stderr


def test_c_round_trip(
    built: tuple[Case, Path, Path], toolchain: Toolchain, probe_dir: Path
) -> None:
    case, library, output = built
    standard = toolchain.resolve_standard("c", toolchain.c_standards[0], probe_dir)
    assert standard is not None
    executable = library.parent / ("check_c.exe" if toolchain.kind == "msvc" else "check_c")
    toolchain.build_c_executable(
        case.check_c, [output / "c"], library, executable, standard
    ).check()
    completed = subprocess.run(
        [str(executable)], capture_output=True, text=True, check=False, cwd=library.parent
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "round trip OK" in completed.stdout


# Symbols that the linker defines in every shared library.
_LINKER_SYMBOLS = frozenset({"_init", "_fini", "_edata", "_end", "__bss_start"})


def test_only_the_c_api_is_exported(built: tuple[Case, Path, Path], toolchain: Toolchain) -> None:
    case, library, _ = built
    exported = toolchain.exported_symbols(library)
    if exported is None:
        pytest.skip("no tool to list exported symbols on this platform")
    # C++ symbols (mangled, '_Z...') can come from the test's own C++ code,
    # for example inline functions of the standard library.
    c_symbols = {name for name in exported if not name.startswith("_Z")} - _LINKER_SYMBOLS
    assert c_symbols == expected_exports(case)
    detail = f"{case.module}_detail"
    assert not [name for name in exported if f"{len(detail)}{detail}" in name]


@pytest.fixture(scope="module")
def demo_build(toolchain: Toolchain, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """The demo case built once: (library, output directory)."""
    case = next(case for case in CASES if case.name == "demo")
    return build(case, toolchain, tmp_path_factory.mktemp("demo"))


def run_demo(
    demo_build: tuple[Path, Path], code: str, *, extra_path: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run Python code against the demo bindings, with warnings as errors."""
    library, output = demo_build
    paths = [str(output / "python")] + ([str(extra_path)] if extra_path else [])
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(paths),
        "DEMO_LIBRARY": str(library),
    }
    return subprocess.run(
        [sys.executable, "-X", "dev", "-W", "error", "-c", code],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def test_close_works_during_interpreter_shutdown(demo_build: tuple[Path, Path]) -> None:
    """An object closed by an atexit handler is really destroyed."""
    code = """
import atexit
from demo import counter

def at_exit():
    late = counter.Counter.create_int32(1)
    late.close()
    print("live at exit:", counter.Counter.liveInstances())

atexit.register(at_exit)
# Created after the handler: its exit hooks run before at_exit.
early = counter.Counter.create_void()
early.close()
"""
    completed = run_demo(demo_build, code)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "live at exit: 0"


def test_objects_dropped_by_exit_handlers_are_destroyed(demo_build: tuple[Path, Path]) -> None:
    """Even by handlers that run after weakref.finalize's own exit hook."""
    code = """
import atexit
from demo import counter

def at_exit():
    temporary = counter.Counter.create_int32(1)
    del temporary
    print("live at exit:", counter.Counter.liveInstances())

atexit.register(at_exit)
early = counter.Counter.create_void()
early.close()
"""
    completed = run_demo(demo_build, code)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "live at exit: 0"


def test_arguments_that_close_the_object_are_refused(demo_build: tuple[Path, Path]) -> None:
    """The arguments are converted before the handle is read, not after."""
    code = """
import numbers
from demo import counter


def call(method):
    target = counter.Counter.create_int32(5)

    class Closing:
        def __index__(self):
            target.close()
            return 1

        def __float__(self):
            target.close()
            return 1.0

    numbers.Real.register(Closing)
    try:
        getattr(target, method)(Closing())
    except ValueError as error:
        print(method, error)
    else:
        print(method, "ran on a closed object")


call("add_int32")
call("add_double")
print("live:", counter.Counter.liveInstances())
"""
    completed = run_demo(demo_build, code)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == [
        "add_int32 Counter object is closed",
        "add_double Counter object is closed",
        "live: 0",
    ]


@pytest.mark.parametrize("imports", ["", "import logging\n", "import concurrent.futures\n"])
def test_close_in_del_during_interpreter_shutdown(
    demo_build: tuple[Path, Path], tmp_path: Path, imports: str
) -> None:
    """The usual "close what I own in __del__" works for module-level objects too.

    At exit the finalizer may be dropped without running: the last garbage
    collection clears its weak reference, or, when something keeps the
    registry of finalizers alive (logging does), it is called after
    finalizers were disabled.
    """
    (tmp_path / "owner.py").write_text(
        """from demo import counter


class Owner:
    def __init__(self):
        self.inner = counter.Counter.create_int32(7)

    def __del__(self):
        self.inner.close()
        self.inner.close()  # does nothing
        print("live after close:", counter.Counter.liveInstances(), flush=True)


kept = Owner()
""",
        encoding="utf-8",
    )
    code = f"{imports}import owner\nprint('exiting')"
    completed = run_demo(demo_build, code, extra_path=tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == ["exiting", "live after close: 0"]


_REENTRANT = """
import faulthandler
import inspect
import sys

import demo
from demo import _runtime, counter

faulthandler.dump_traceback_later(60, exit=True)


def call_inside(function, marker):
    \"\"\"Call the bindings from the same thread, as a finalizer or a signal handler
    can, just before function runs its first line that contains marker.\"\"\"
    lines, first = inspect.getsourcelines(function)
    target = first + next(i for i, line in enumerate(lines) if marker in line)
    fired = []

    def tracer(frame, event, arg):
        if frame.f_code is not function.__code__:
            return None
        if event == "line" and frame.f_lineno == target and not fired:
            fired.append(target)
            # Tracing is off while the tracer runs, so this is not traced.
            if mode == "load-default-nested-other":
                demo.load(sys.argv[2])
                print("nested: loaded")
            else:
                print("nested:", counter.Counter.liveInstances())
        return tracer

    sys.settrace(tracer)
    return fired


mode = sys.argv[1]
if mode == "bind":
    demo.load()
    fired = call_inside(counter._bind, ".argtypes = ")
else:
    # Inside the lock, after the library was loaded but before it is published.
    fired = call_inside(_runtime.load, "_check_fingerprint(library, path)")
if mode == "load-other":
    try:
        demo.load(sys.argv[2])
    except RuntimeError as error:
        print("refused:", "already loaded from" in str(error))
else:
    print("outer:", counter.multiply_int32_int32(2, 3))
sys.settrace(None)
assert fired, "the tracer never ran"
"""


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("load", ["nested:", "0", "outer:", "6"]),
        ("bind", ["nested:", "0", "outer:", "6"]),
        # The nested call loads the library of DEMO_LIBRARY, not the one that
        # the outer call was asked for: that call must fail, not return it.
        ("load-other", ["nested:", "0", "refused:", "True"]),
        # The outer call asked for no library in particular: the one that the
        # nested call loaded is fine.
        ("load-default-nested-other", ["nested:", "loaded", "outer:", "6"]),
    ],
)
def test_bindings_are_reentrant(
    demo_build: tuple[Path, Path], tmp_path: Path, mode: str, expected: list[str]
) -> None:
    library, output = demo_build
    other = tmp_path / library.name
    shutil.copyfile(library, other)
    environment = {
        **os.environ,
        "PYTHONPATH": str(output / "python"),
        "DEMO_LIBRARY": str(other if mode == "load-other" else library),
    }
    argument = other if mode == "load-default-nested-other" else library
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _REENTRANT, mode, str(argument)],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("deadlock: a reentrant call into the bindings never returned")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.split() == expected


_FORK = """
import faulthandler
import os
import threading

from demo import _runtime, counter

held, release = threading.Event(), threading.Event()


def hold_the_locks():
    with _runtime._lock, counter._bind_lock:
        held.set()
        release.wait()


thread = threading.Thread(target=hold_the_locks)
thread.start()
held.wait()
pid = os.fork()
if pid == 0:
    faulthandler.dump_traceback_later(30, exit=True)
    print("child:", counter.multiply_int32_int32(2, 3), flush=True)
    os._exit(0)
release.set()
thread.join()
print("child exit code:", os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]))
"""


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")
def test_locks_held_by_another_thread_are_free_after_fork(demo_build: tuple[Path, Path]) -> None:
    """The thread that held them does not exist in the child."""
    library, output = demo_build
    environment = {
        **os.environ,
        "PYTHONPATH": str(output / "python"),
        "DEMO_LIBRARY": str(library),
    }
    completed = subprocess.run(
        # No -W error: Python warns that forking a multi-threaded process may deadlock.
        [sys.executable, "-c", _FORK],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == ["child: 6", "child exit code: 0"]


_FORK_HOLDING = """
import faulthandler
import os

from demo import _runtime, counter

with _runtime._lock, counter._bind_lock:
    pid = os.fork()
    if pid == 0:
        faulthandler.dump_traceback_later(30, exit=True)
        print("child:", counter.multiply_int32_int32(2, 3), flush=True)
# Leaving the with block released the locks in the child too.
if pid == 0:
    os._exit(0)
print("child exit code:", os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]))
"""


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")
def test_locks_held_by_the_forking_thread_stay_held_after_fork(
    demo_build: tuple[Path, Path],
) -> None:
    completed = run_demo(demo_build, _FORK_HOLDING)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == ["child: 6", "child exit code: 0"]


def test_load_accepts_bytes_paths(demo_build: tuple[Path, Path]) -> None:
    library, _ = demo_build
    code = f"""
import os
import demo
from demo import _runtime, counter

path = {str(library)!r}
assert demo.load(os.fsencode(path)) is _runtime._library
assert _runtime._library_path == path, _runtime._library_path
assert demo.load(path) is _runtime._library
print(counter.multiply_int32_int32(2, 3))
"""
    completed = run_demo(demo_build, code)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "6"
