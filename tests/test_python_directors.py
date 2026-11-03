# SPDX-License-Identifier: Apache-2.0
"""The Python bindings of the directors: what is generated for a class that has one, and the
runtime that finds the objects that C++ calls back (the round trip, with a built library, is
tests/golden/directors/check_python.py)."""

from __future__ import annotations

import _thread
import ctypes
import gc
import importlib.util
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

import pytest

import bridgefex
from bridgefex.errors import BridgefexError
from tests.support.cases import GOLDEN_DIR

PRELUDE = "#pragma once\n#include <cstdint>\n#include <string>\n"


def generate(directory: Path, code: str, *directors: str) -> dict[str, str]:
    """The files of the Python package of a header, whose classes ask for directors."""
    header = directory / "api.h"
    header.write_text(PRELUDE + code + "\n", encoding="utf-8")
    annotations = directory / "annotations.toml"
    names = ", ".join(f'"{name}"' for name in directors)
    annotations.write_bytes(f"directors = [{names}]\n".encode())
    options = bridgefex.Options("mod", annotations=(annotations,))
    files = bridgefex.generate([header], options).files
    return {path: text for path, text in files.items() if path.startswith("python/")}


@pytest.fixture(autouse=True)
def _libclang(libclang_loaded: str) -> None:
    pass


# -- what is generated ----------------------------------------------------------------------


def slots_of(files: dict[str, str]) -> list[str]:
    """The names of the methods that a subclass may override, in the order of the vtable."""
    text = files["python/mod/api.py"]
    block = text.split("_director_slots = _runtime.DirectorSlots(", 1)[1].split("    )\n", 1)[0]
    return [
        line.split('("')[1].split('"')[0] for line in block.splitlines() if line.strip()[:2] == '("'
    ]


def test_the_method_that_a_subclass_overrides_is_the_one_that_a_user_calls(
    tmp_path: Path,
) -> None:
    # The vtable tells the overloads of public and protected methods apart (h_int32, h_double),
    # the Python class has one public method called h, and the protected one keeps its suffix.
    files = generate(
        tmp_path,
        """
        namespace n { class A { public: A(); virtual ~A();
          virtual std::int32_t f(std::int32_t x); virtual double f(double x);
          virtual std::int32_t h(std::int32_t x);
          protected: virtual double h(double x); }; }
        """,
        "n_A",
    )
    assert slots_of(files) == ["f_int32", "f_double", "h", "h_double"]
    text = files["python/mod/api.py"]
    assert "def f_int32(self, x):" in text
    assert "def _base_f_int32(self, x):" in text
    assert "def h_double(self, x):" in text  # the protected one: only an override calls it
    assert "def _base_h_double(self, x):" in text


def test_a_protected_constructor_is_not_part_of_the_api_of_a_class_that_has_public_ones(
    tmp_path: Path,
) -> None:
    files = generate(
        tmp_path,
        """
        namespace n { class A { public: A(); virtual ~A(); virtual std::int32_t f();
          protected: explicit A(std::int32_t x); }; }
        """,
        "n_A",
    )
    text = files["python/mod/api.py"]
    assert "def __init__(self):" in text
    assert "def _init_director(self, _found):" in text
    # Nothing makes an A from an int: it is protected.
    assert "def create_int32" not in text


def test_a_class_with_only_protected_constructors_is_made_by_its_subclasses(
    tmp_path: Path,
) -> None:
    files = generate(
        tmp_path,
        """
        namespace n { class A { public: virtual ~A(); virtual std::int32_t f() = 0;
          protected: A(); explicit A(std::int32_t x); }; }
        """,
        "n_A",
    )
    text = files["python/mod/api.py"]
    assert "def create_void(cls):" in text
    assert "def create_int32(cls, x):" in text
    assert text.count("overrides(cls, subclass=True)") == 2
    assert "_found = self._director_slots.overrides" not in text


def test_what_a_director_adds_must_not_take_the_name_of_a_method(tmp_path: Path) -> None:
    with pytest.raises(BridgefexError) as raised:
        generate(
            tmp_path,
            """
            namespace n { class A { public: A(); virtual ~A();
              void foo(double x);
              protected: virtual void foo(std::int32_t x); }; }
            """,
            "n_A",
        )
    message = str(raised.value)
    assert "'foo'" in message
    assert "may override" in message


def test_only_the_modules_with_directors_have_the_runtime_of_directors(tmp_path: Path) -> None:
    code = "namespace n { class A { public: A(); virtual ~A(); virtual std::int32_t f(); }; }"
    plain = tmp_path / "plain"
    plain.mkdir()
    without = bridgefex.generate([_write(plain, code)], bridgefex.Options("mod")).files
    with_directors = generate(tmp_path, code, "n_A")
    assert "DirectorSlots" not in without["python/mod/_runtime.py"]
    assert "_director" not in without["python/mod/plain.py"]
    assert "class DirectorSlots" in with_directors["python/mod/_runtime.py"]


def _write(directory: Path, code: str) -> Path:
    header = directory / "plain.h"
    header.write_text(PRELUDE + code + "\n", encoding="utf-8")
    return header


# -- the runtime ----------------------------------------------------------------------------

RUNTIME = GOLDEN_DIR / "directors" / "expected" / "python" / "drc" / "_runtime.py"


@pytest.fixture(scope="module")
def runtime() -> Iterator[ModuleType]:
    name = "bridgefex_test_director_runtime"
    spec = importlib.util.spec_from_file_location(name, RUNTIME)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    previous = sys.unraisablehook
    spec.loader.exec_module(module)
    yield module
    sys.unraisablehook = previous
    del sys.modules[name]


class Vtable(ctypes.Structure):
    _fields_ = [("user_data", ctypes.c_void_p), ("release", ctypes.c_void_p), ("a", ctypes.c_int)]


def generated(runtime: ModuleType, c_type: str) -> type:
    """A class as bridgefex generates it: it names its C++ class."""
    return type("Generated" + c_type, (runtime.Object,), {"_cxx_class": c_type, "__slots__": ()})


def test_a_class_overrides_what_a_class_of_the_user_defines(runtime: ModuleType) -> None:
    base = generated(runtime, "n_A")
    base.f = lambda self: 1  # type: ignore[attr-defined]
    base.g = lambda self: 2  # type: ignore[attr-defined]
    slots = runtime.DirectorSlots(
        "n_A", "n::A", Vtable, (("f", "a", False, "thunk_f"), ("g", "b", True, "thunk_g"))
    )

    class Mine(base):  # type: ignore[misc, valid-type]
        def g(self) -> int:
            return 3

    class Other(Mine):
        pass

    # The pure method has to be overridden, the other one may be.
    assert slots.overrides(Mine) == [("b", "thunk_g")]
    assert slots.overrides(Other) == [("b", "thunk_g")]
    with pytest.raises(TypeError, match=r"abstract.*must override g"):
        slots.overrides(base)
    with pytest.raises(TypeError, match="itself"):
        slots.overrides(base, subclass=True)

    class Both(Mine):
        def f(self) -> int:
            return 4

    assert slots.overrides(Both) == [("a", "thunk_f"), ("b", "thunk_g")]

    class NotAMethod(Mine):
        f = 5  # type: ignore[assignment]

    with pytest.raises(TypeError, match="must be a method"):
        slots.overrides(NotAMethod)


def test_a_class_without_a_director_cannot_override_what_it_inherits(runtime: ModuleType) -> None:
    runtime_class = runtime.Object
    base = generated(runtime, "n_A")
    base.f = lambda self: 1  # type: ignore[attr-defined]
    base._director_slots = runtime.DirectorSlots(  # type: ignore[attr-defined]
        "n_A", "n::A", Vtable, (("f", "a", False, "thunk"),)
    )
    derived = type("Derived", (base,), {"_cxx_class": "n_B", "__slots__": ()})
    assert issubclass(derived, runtime_class)
    with pytest.raises(TypeError, match=r"overrides f.*n_B has no director"):

        class Mine(derived):  # type: ignore[misc, valid-type]
            def f(self) -> int:
                return 2

    class Fine(derived):  # type: ignore[misc, valid-type]
        def other(self) -> int:
            return 2

    # A class of the director itself may override.
    class Direct(base):  # type: ignore[misc, valid-type]
        def f(self) -> int:
            return 2

    assert Fine.other
    assert Direct.f


def test_the_registry_keeps_an_object_weakly_until_c_plus_plus_owns_it(
    runtime: ModuleType,
) -> None:
    base = generated(runtime, "n_A")
    obj = base.__new__(base)
    director = runtime.Director(obj)
    key = director.key
    assert runtime._callbacks[key] is director
    assert director.target() is obj
    # Weak: the object goes when nothing else keeps it.
    del obj
    gc.collect()
    assert director.target() is None
    # Strong, once C++ owns the director: the object lives until C++ releases it.
    obj = base.__new__(base)
    owned = runtime.Director(obj)
    owned.promote()
    del obj
    gc.collect()
    assert owned.target() is not None
    runtime.release(owned.key)  # what C++ calls once, when it destroys the director
    assert owned.key not in runtime._callbacks
    runtime.release(key)
    assert key not in runtime._callbacks


def test_the_base_call_needs_the_director_to_exist(runtime: ModuleType) -> None:
    base = generated(runtime, "n_A")
    director = runtime.Director(base.__new__(base))
    director.pointer = "handle"
    assert runtime.director_pointer(director, "A") == "handle"
    runtime.release(director.key)
    with pytest.raises(ValueError, match="A object is closed"):
        runtime.director_pointer(director, "A")
    with pytest.raises(ValueError, match="closed"):
        runtime.director_pointer(None, "A")


def test_the_thunk_finds_the_method_of_the_object(runtime: ModuleType) -> None:
    base = generated(runtime, "n_A")

    class Mine(base):  # type: ignore[misc, valid-type]
        __slots__ = ()

        def f(self, x: int) -> int:
            return x + 1

    obj = Mine()
    director = runtime.Director(obj)
    assert runtime.director_method(director.key, "f")(1) == 2
    runtime.release(director.key)
    with pytest.raises(RuntimeError, match="after its director was released"):
        runtime.director_method(director.key, "f")
    # A callback of another kind is not a director.
    with pytest.raises(RuntimeError):
        runtime.director_method(10**9, "f")


def run_on_thread(function: Callable[[], object], *, foreign: bool = False) -> object:
    """The result of ``function`` on another thread: one of ``threading``, or one that C++
    would have started (which ``threading`` does not know)."""
    outcome: list[object] = []
    done = threading.Event()

    def body() -> None:
        try:
            outcome.append(function())
        finally:
            done.set()

    if foreign:
        _thread.start_new_thread(body, ())
    else:
        threading.Thread(target=body).start()
    assert done.wait(10)
    return outcome[0]


def test_an_error_of_an_override_goes_to_the_next_check(runtime: ModuleType) -> None:
    error = ValueError("first")
    assert runtime.fail_director(error) is True
    assert runtime.fail_director(ValueError("second")) is True  # only the first is kept
    # Until the call that ran the override returns, the other overrides are skipped.
    base = generated(runtime, "n_A")
    director = runtime.Director(base.__new__(base))
    assert runtime.director_method(director.key, "f") is None
    with pytest.raises(ValueError, match="first"):
        runtime.check(runtime.OK)
    runtime.check(runtime.OK)  # raised once
    runtime.release(director.key)


def test_an_error_of_an_override_goes_to_the_innermost_call_with_callbacks(
    runtime: ModuleType,
) -> None:
    scope = runtime.CallScope()
    scope.begin()
    try:
        assert runtime.fail_director(KeyError("in the scope")) is True
        assert isinstance(scope.error, KeyError)
        base = generated(runtime, "n_A")
        director = runtime.Director(base.__new__(base))
        assert runtime.director_method(director.key, "f") is None  # skipped
        runtime.release(director.key)
        with pytest.raises(KeyError):
            scope.check(runtime.OK)
    finally:
        scope.close()


def test_an_error_on_a_thread_of_cxx_is_not_given_to_any_call(runtime: ModuleType) -> None:
    assert run_on_thread(lambda: runtime.fail_director(ValueError("x")), foreign=True) is False
    # A thread of Python gets its own check, which another thread does not see.
    assert run_on_thread(lambda: runtime.fail_director(ValueError("y"))) is True
    runtime.check(runtime.OK)  # nothing for this thread
