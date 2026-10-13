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
import signal
import subprocess
import sys
from collections.abc import Sequence
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
        sources, [output / "c", case.input_dir], library, CXX_STANDARD, extra_flags
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


_PENDING_SIGNALS = """
import faulthandler
import gc
import signal
import sys
import threading
import time

from demo import sleeper

faulthandler.dump_traceback_later(240, exit=True)
scenario, rounds = sys.argv[1], int(sys.argv[2])


def interrupt(signum, frame):
    raise KeyboardInterrupt


signal.signal(signal.SIGINT, interrupt)
signal.signal(signal.SIGUSR1, interrupt)
signal.signal(signal.SIGUSR2, signal.SIG_IGN)
main = threading.main_thread().ident
go, sent = threading.Event(), threading.Event()


def shoot():
    # One thread for all the rounds: no thread is torn down during a round.
    while True:
        go.wait()
        go.clear()
        time.sleep(0.01)
        signal.pthread_kill(main, signal.SIGINT)
        signal.pthread_kill(main, signal.SIGUSR1)
        sent.set()


threading.Thread(target=shoot, daemon=True).start()


def one_round():
    if scenario == "constructor":
        go.set()
        sleeper.Sleeper(50)
    else:
        # Dropped while the exception unwinds, with a signal still pending.
        napping = sleeper.Sleeper(0)
        go.set()
        napping.nap(50)


interrupted = 0
for _ in range(rounds):
    # A flag, not a count: Python 3.12 can catch the second signal here too.
    hit = False
    try:
        one_round()
    except KeyboardInterrupt:
        hit = True
    # After a handler raised, the other pending one runs at an unrelated
    # later check (a collection, another thread...). raise_signal() runs the
    # pending handlers itself: no signal is left for the next round.
    while True:
        try:
            sent.wait()
            signal.raise_signal(signal.SIGUSR2)
            break
        except KeyboardInterrupt:
            pass
    sent.clear()
    interrupted += hit
gc.collect()
print("interrupted:", interrupted, "live:", sleeper.Sleeper.liveInstances())
"""

_SIGNAL_ROUNDS = 200


def _asan_runtime(toolchain: Toolchain) -> str | None:
    """Path of the ASan runtime of the C++ compiler, to preload into Python."""
    if toolchain.kind != "gnu" or not sys.platform.startswith("linux"):
        return None
    completed = subprocess.run(
        [toolchain.cxx, "-print-file-name=libasan.so"],
        capture_output=True,
        text=True,
        check=False,
    )
    path = completed.stdout.strip()
    return path if completed.returncode == 0 and os.path.isabs(path) else None


@pytest.fixture(scope="module")
def demo_asan_build(
    toolchain: Toolchain, tmp_path_factory: pytest.TempPathFactory
) -> tuple[Path, Path, str]:
    """The demo case built with AddressSanitizer: (library, output dir, runtime)."""
    runtime = _asan_runtime(toolchain)
    if runtime is None:
        pytest.skip("needs GCC or Clang with AddressSanitizer on Linux")
    case = next(case for case in CASES if case.name == "demo")
    flags = ["-fsanitize=address", "-fno-omit-frame-pointer"]
    library, output = build(case, toolchain, tmp_path_factory.mktemp("demo_asan"), flags)
    return library, output, runtime


def _run_signals(
    library: Path, output: Path, scenario: str, preload: str | None = None
) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "PYTHONPATH": str(output / "python"),
        "DEMO_LIBRARY": str(library),
    }
    if preload is not None:
        environment["LD_PRELOAD"] = preload
        # The interpreter itself is not built for LeakSanitizer.
        environment["ASAN_OPTIONS"] = "detect_leaks=0:abort_on_error=0"
    command = [sys.executable, "-X", "dev", "-W", "error", "-c", _PENDING_SIGNALS]
    return subprocess.run(
        [*command, scenario, str(_SIGNAL_ROUNDS)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


def _check_no_leak(completed: subprocess.CompletedProcess[str]) -> None:
    assert completed.returncode == 0, completed.stdout + completed.stderr
    # The collection runs no Python function: nothing for a signal to abandon.
    assert "Exception ignored" not in completed.stderr, completed.stderr
    words = completed.stdout.split()
    assert words[0::2] == ["interrupted:", "live:"], completed.stdout
    interrupted, live = int(words[1]), int(words[3])
    assert live == 0
    # The signals came while the C++ code slept in almost every round; a busy
    # machine may delay a few of them past the call.
    assert _SIGNAL_ROUNDS * 9 // 10 <= interrupted <= _SIGNAL_ROUNDS, completed.stdout


_POSIX_SIGNALS = pytest.mark.skipif(
    not hasattr(signal, "pthread_kill") or not hasattr(signal, "SIGUSR1"),
    reason="needs signal.pthread_kill and SIGUSR1",
)


@_POSIX_SIGNALS
@pytest.mark.parametrize("scenario", ["constructor", "drop"])
def test_pending_signals_do_not_leak_objects(demo_build: tuple[Path, Path], scenario: str) -> None:
    """Two signals arrive while the C++ code sleeps; both handlers raise.

    "constructor": a signal raised right after the C constructor returns must
    not leak the new object. "drop": an object collected while a signal is
    pending must still be destroyed.
    """
    library, output = demo_build
    _check_no_leak(_run_signals(library, output, scenario))


@_POSIX_SIGNALS
@pytest.mark.parametrize("scenario", ["constructor", "drop"])
def test_pending_signals_do_not_double_free(
    demo_asan_build: tuple[Path, Path, str], scenario: str
) -> None:
    library, output, runtime = demo_asan_build
    completed = _run_signals(library, output, scenario, preload=runtime)
    assert "AddressSanitizer" not in completed.stderr, completed.stderr
    _check_no_leak(completed)


def test_asan_build_reports_a_double_free(demo_asan_build: tuple[Path, Path, str]) -> None:
    """The check above would see one: destroying a closed object twice is reported."""
    library, output, runtime = demo_asan_build
    code = (
        "from demo import sleeper\n"
        "s = sleeper.Sleeper(0)\n"
        "handle = s._handle\n"
        "s.close()\n"
        "sleeper._bind().demo_Sleeper_destroy(handle)\n"
    )
    environment = {
        **os.environ,
        "PYTHONPATH": str(output / "python"),
        "DEMO_LIBRARY": str(library),
        "LD_PRELOAD": runtime,
        "ASAN_OPTIONS": "detect_leaks=0",
    }
    completed = subprocess.run(
        [sys.executable, "-c", code],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert completed.returncode != 0
    assert "attempting double-free" in completed.stderr, completed.stderr


_CYCLIC_DEL = """
import gc
from demo import counter


class Cyclic(counter.Counter):
    def __del__(self):
        try:
            self.value()
        except ValueError as error:
            print("del:", error)
        self.close()


cyclic = Cyclic.create_int32(3)
cyclic.me = cyclic
del cyclic
gc.collect()
print("live:", counter.Counter.liveInstances())
"""


@pytest.mark.parametrize("sanitized", [False, True], ids=["plain", "asan"])
def test_cyclic_object_is_closed_in_its_del(
    request: pytest.FixtureRequest, sanitized: bool
) -> None:
    """The collection destroyed it before __del__ ran: __del__ must not use or
    destroy it again."""
    environment = dict(os.environ)
    if sanitized:
        library, output, runtime = request.getfixturevalue("demo_asan_build")
        environment["LD_PRELOAD"] = runtime
        environment["ASAN_OPTIONS"] = "detect_leaks=0"
    else:
        library, output = request.getfixturevalue("demo_build")
    environment["PYTHONPATH"] = str(output / "python")
    environment["DEMO_LIBRARY"] = str(library)
    completed = subprocess.run(
        [sys.executable, "-X", "dev", "-W", "error", "-c", _CYCLIC_DEL],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert completed.stdout.splitlines() == ["del: Counter object is closed", "live: 0"]
