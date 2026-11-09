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
import types
import weakref
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

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


def test_a_protected_constructor_of_a_class_that_has_public_ones_makes_subclasses(
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
    # The public constructor is the API of the class; the protected one makes the objects
    # of its subclasses only.
    assert "def create_int32(cls, x):" in text
    assert text.count("overrides(cls, subclass=True)") == 1


def test_a_virtual_method_may_not_take_the_name_of_a_constructor_of_a_subclass(
    tmp_path: Path,
) -> None:
    """The wrapper that B repeats for an inherited virtual method would silently replace the
    class method that makes the objects of a subclass."""
    with pytest.raises(BridgefexError) as raised:
        generate(
            tmp_path,
            """
            namespace n { class A { public: A(); virtual ~A();
              virtual std::int32_t create_void(); };
              class B : public A { public: explicit B(std::int32_t x); protected: B(); }; }
            """,
            "n_A",
            "n_B",
        )
    assert "and a constructor of '::n::B' get the Python name 'create_void'" in str(raised.value)


def test_the_callbacks_of_a_protected_constructor_have_their_thunks(tmp_path: Path) -> None:
    files = generate(
        tmp_path,
        """
        #include <functional>
        namespace n { class A { public: A(); virtual ~A(); virtual std::int32_t f();
          protected: explicit A(std::function<std::int32_t(std::int32_t)> callback); }; }
        """,
        "n_A",
    )
    assert "_callback__int32__int32__type" in files["python/mod/_types.py"]


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
    base._virtual_names = ("f",)  # type: ignore[attr-defined]
    base._director_slots = runtime.DirectorSlots(  # type: ignore[attr-defined]
        "n_A", "n::A", Vtable, (("f", "a", False, "thunk"),)
    )
    derived = type("Derived", (base,), {"_cxx_class": "n_B", "__slots__": ()})
    assert issubclass(derived, runtime_class)
    with pytest.raises(
        TypeError, match=r"defines f, a virtual method of n::A.*n_B has no director"
    ):

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


def test_a_virtual_method_that_cannot_be_overridden_is_not_silently_ignored(
    runtime: ModuleType,
) -> None:
    base = generated(runtime, "n_A")
    base._virtual_names = ("f", "fixed", "total", "add", "add_int32")  # type: ignore[attr-defined]
    base.fixed = lambda self: 7  # type: ignore[attr-defined]
    base._director_slots = runtime.DirectorSlots(  # type: ignore[attr-defined]
        "n_A", "n::A", Vtable, (("f", "a", False, "t"), ("add_int32", "b", False, "t"))
    )
    for name in ("fixed", "total", "add"):  # final, unsupported types, overloaded
        with pytest.raises(TypeError, match=rf"defines {name}, a virtual method of n_A that C\+\+"):
            type("Mine", (base,), {name: lambda self: 1})
    # What is not virtual is the user's own, also when a base has a virtual of that name.
    type("Fine", (base,), {"helper": lambda self: 1})


def test_a_non_virtual_method_that_hides_a_virtual_one_may_be_defined(runtime: ModuleType) -> None:
    base = generated(runtime, "n_A")
    base._virtual_names = ("f",)  # type: ignore[attr-defined]
    base._director_slots = runtime.DirectorSlots(  # type: ignore[attr-defined]
        "n_A", "n::A", Vtable, (("f", "a", False, "t"),)
    )
    # Mid declares f(int, int), not virtual: its wrapper hides the virtual f of A.
    mid = type("Mid", (base,), {"_cxx_class": "n_Mid", "__slots__": (), "f": lambda self, a, b: 1})
    mid._virtual_names = ()  # type: ignore[attr-defined]
    type("Mine", (mid,), {"f": lambda self, a, b: 2})  # no error: it overrides nothing


def test_every_director_of_a_class_with_several_bases_is_asked(runtime: ModuleType) -> None:
    first = generated(runtime, "n_A")
    first._virtual_names = ("f",)  # type: ignore[attr-defined]
    first._director_slots = runtime.DirectorSlots(  # type: ignore[attr-defined]
        "n_A", "n::A", Vtable, (("f", "a", False, "t"),)
    )
    second = generated(runtime, "n_B")
    second._virtual_names = ("h",)  # type: ignore[attr-defined]
    second._director_slots = runtime.DirectorSlots(  # type: ignore[attr-defined]
        "n_B", "n::B", Vtable, (("h", "a", False, "t"),)
    )
    both = type("Both", (first, second), {"_cxx_class": "n_Both", "__slots__": ()})
    with pytest.raises(
        TypeError, match=r"defines h, a virtual method of n::B, but its C\+\+ class"
    ):
        type("Mine", (both,), {"h": lambda self: 1})


def test_a_coroutine_or_a_generator_cannot_override(runtime: ModuleType) -> None:
    base = generated(runtime, "n_A")
    slots = runtime.DirectorSlots("n_A", "n::A", Vtable, (("f", "a", False, "t"),))

    async def coroutine(self: object) -> None: ...

    def generator(self: object) -> Any:
        yield

    for function in (coroutine, generator):
        mine = type("Mine", (base,), {"f": function})
        with pytest.raises(TypeError, match="coroutine or a generator"):
            slots.overrides(mine)


def test_a_name_that_only_a_base_director_calls_is_refused(runtime: ModuleType) -> None:
    # Q declares f(int), which its wrapper calls f, while P calls it f_int: a subclass of Q that
    # defines f_int overrides nothing, and C++ would never call it.
    base = generated(runtime, "n_P")
    base._virtual_names = ("f_int", "f_double")  # type: ignore[attr-defined]
    base._director_slots = runtime.DirectorSlots(  # type: ignore[attr-defined]
        "n_P", "n::P", Vtable, (("f_int", "a", False, "t1"), ("f_double", "b", False, "t2"))
    )
    derived = type("Derived", (base,), {"_cxx_class": "n_Q", "__slots__": ()})
    derived._director_slots = runtime.DirectorSlots(  # type: ignore[attr-defined]
        "n_Q", "n::Q", Vtable, (("f", "a", False, "t1"), ("f_double", "b", False, "t2"))
    )
    with pytest.raises(
        TypeError,
        match=r"defines f_int, which names a virtual method of n::P but not of n::Q"
        r".*calls f, f_double",
    ):

        class Mine(derived):  # type: ignore[misc, valid-type]
            def f_int(self) -> int:
                return 1

    class Fine(derived):  # type: ignore[misc, valid-type]
        def f(self) -> int:
            return 2

    assert Fine.f


def registered(runtime: ModuleType, obj: object) -> Any:
    """A director of ``obj`` in the registry, as the create function would leave it."""
    director = runtime.Director(obj)
    director.register(types.SimpleNamespace())
    director.pointer = "handle"
    return director


def test_the_registry_keeps_an_object_weakly_until_c_plus_plus_owns_it(
    runtime: ModuleType,
) -> None:
    base = generated(runtime, "n_A")
    obj = base.__new__(base)
    director = registered(runtime, obj)
    key = director.key
    assert runtime._callbacks[key] is director
    # Weak: the object goes when nothing else keeps it.
    del obj
    gc.collect()
    assert runtime._entry(key) == (director, None)
    # Strong, once C++ owns the director: the object lives until C++ releases it.
    obj = base.__new__(base)
    owned = registered(runtime, obj)
    owned.promote()
    del obj
    gc.collect()
    assert runtime._entry(owned.key)[1] is not None
    runtime.release(owned.key)  # what C++ calls once, when it destroys the director
    assert owned.key not in runtime._callbacks
    runtime.release(key)
    assert key not in runtime._callbacks


def test_the_object_of_a_released_director_is_freed_without_the_collector(
    runtime: ModuleType,
) -> None:
    base = generated(runtime, "n_A")
    base.__slots__ = ()  # the generated classes have the slots of Object
    obj = base.__new__(base)
    obj._director = None  # type: ignore[attr-defined]
    director = registered(runtime, obj)
    obj._director = director  # type: ignore[attr-defined]
    director.promote()
    reference = weakref.ref(obj)
    del obj
    gc.disable()
    try:
        assert reference() is not None  # C++ owns it
        runtime.release(director.key)  # C++ destroys the director
        del director
        assert reference() is None  # no cycle: reference counting is enough
    finally:
        gc.enable()


def test_the_base_call_of_an_object_that_was_given_up_needs_one_of_its_overrides(
    runtime: ModuleType,
) -> None:
    base = generated(runtime, "n_A")
    obj = base.__new__(base)
    obj._owned = [("destroy", "null")]  # type: ignore[attr-defined]
    director = registered(runtime, obj)
    assert runtime.director_pointer(director, "A") == "handle"
    obj._owned = []  # C++ owns it now (take())
    with pytest.raises(ValueError, match="A object is closed"):
        runtime.director_pointer(director, "A")
    # An override of the object runs on this thread, called by C++: it may call the base.
    _, token = runtime.director_method(director.key, "__class__")
    assert token is not None
    assert runtime.director_pointer(director, "A") == "handle"
    # Not another thread, which Python never gave the object to.
    assert run_on_thread(lambda: _refused(runtime, director)) is True
    del token
    assert director.running == {}  # the token ended with its frame
    with pytest.raises(ValueError, match="A object is closed"):
        runtime.director_pointer(director, "A")
    runtime.release(director.key)


def _refused(runtime: ModuleType, director: Any) -> bool:
    try:
        runtime.director_pointer(director, "A")
    except ValueError:
        return True
    return False


def test_the_base_call_needs_the_director_to_exist(runtime: ModuleType) -> None:
    base = generated(runtime, "n_A")
    obj = base.__new__(base)
    obj._owned = [("destroy", "null")]  # type: ignore[attr-defined]
    director = registered(runtime, obj)
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

        g = None

    obj = Mine()
    director = registered(runtime, obj)
    method, token = runtime.director_method(director.key, "f")
    assert method(1) == 2
    assert len(director.running) == 1  # it runs until the thunk's frame ends
    del token
    assert director.running == {}
    # An attribute that is not callable is an error, not a skipped override.
    with pytest.raises(TypeError, match=r"Mine.g is not callable: it is NoneType"):
        runtime.director_method(director.key, "g")
    runtime.release(director.key)
    with pytest.raises(RuntimeError, match="after its director was released"):
        runtime.director_method(director.key, "f")
    # A callback of another kind is not a director.
    with pytest.raises(RuntimeError):
        runtime.director_method(10**9, "f")


def test_an_override_that_ends_by_an_exception_does_not_stay_running(
    runtime: ModuleType,
) -> None:
    base = generated(runtime, "n_A")

    class Mine(base):  # type: ignore[misc, valid-type]
        __slots__ = ()

        def f(self) -> int:
            raise KeyboardInterrupt

    obj = Mine()
    director = registered(runtime, obj)

    def thunk() -> None:
        # What a thunk does: the token is a local, with no ``finally`` to undo it.
        function, active = runtime.director_method(director.key, "f")
        assert active is not None
        try:
            function()
        except BaseException:
            active = None  # (the thunk does this before it reports the error)
            raise

    with pytest.raises(KeyboardInterrupt):
        thunk()
    assert director.running == {}  # also in the traceback: the thunk dropped the token
    runtime.release(director.key)


def test_a_late_registration_leaves_nothing_when_the_creation_is_never_called(
    runtime: ModuleType,
) -> None:
    base = generated(runtime, "n_A")
    before = len(runtime._callbacks)
    director = runtime.Director(base.__new__(base))  # as make() leaves it
    assert len(runtime._callbacks) == before
    assert director.key is None
    vtable = director.register(types.SimpleNamespace())  # an argument of the create function
    assert vtable.user_data == director.key
    assert len(runtime._callbacks) == before + 1
    runtime.release(director.key)


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
    director = registered(runtime, base.__new__(base))
    assert runtime.director_method(director.key, "f") == (None, None)
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
        director = registered(runtime, base.__new__(base))
        assert runtime.director_method(director.key, "f") == (None, None)  # skipped
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
