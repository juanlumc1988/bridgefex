# SPDX-License-Identifier: Apache-2.0
"""Unit tests of the generated Python runtime (_runtime.py), without a native library."""

from __future__ import annotations

import array
import copy
import ctypes
import dataclasses
import enum
import importlib.util
import math
import os
import pickle
import re
import sys
from collections.abc import Iterator
from fractions import Fraction
from pathlib import Path
from types import ModuleType

import pytest

from tests.support.cases import GOLDEN_DIR, all_cases

RUNTIME = GOLDEN_DIR / "scalars" / "expected" / "python" / "scalars" / "_runtime.py"


@pytest.fixture(scope="module")
def runtime() -> Iterator[ModuleType]:
    name = "bridgefex_test_runtime"
    spec = importlib.util.spec_from_file_location(name, RUNTIME)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered so that pickle can find the Error class.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    del sys.modules[name]


class FakeFunction:
    """Stands for a ctypes function of the native library."""

    def __init__(self, result: object) -> None:
        self.result = result
        self.argtypes: object = None
        self.restype: object = None

    def __call__(self) -> object:
        return self.result


@pytest.mark.parametrize(
    ("ctype", "low", "high"),
    [
        (ctypes.c_int8, -128, 127),
        (ctypes.c_uint8, 0, 255),
        (ctypes.c_int16, -(2**15), 2**15 - 1),
        (ctypes.c_uint16, 0, 2**16 - 1),
        (ctypes.c_int32, -(2**31), 2**31 - 1),
        (ctypes.c_uint32, 0, 2**32 - 1),
        (ctypes.c_int64, -(2**63), 2**63 - 1),
        (ctypes.c_uint64, 0, 2**64 - 1),
        (ctypes.c_byte, -128, 127),
        (ctypes.c_ubyte, 0, 255),
    ],
)
def test_to_int_limits(runtime: ModuleType, ctype: type, low: int, high: int) -> None:
    assert runtime.to_int(low, ctype, "x") == low
    assert runtime.to_int(high, ctype, "x") == high
    with pytest.raises(OverflowError, match=r"argument 'x' must be in"):
        runtime.to_int(low - 1, ctype, "x")
    with pytest.raises(OverflowError):
        runtime.to_int(high + 1, ctype, "x")


def test_to_int_platform_dependent_types(runtime: ModuleType) -> None:
    for ctype in (ctypes.c_long, ctypes.c_ulong, ctypes.c_size_t, ctypes.c_int):
        bits = 8 * ctypes.sizeof(ctype)
        high = (1 << (bits - 1)) - 1 if ctype(-1).value < 0 else (1 << bits) - 1
        assert runtime.to_int(high, ctype, "x") == high
        with pytest.raises(OverflowError):
            runtime.to_int(high + 1, ctype, "x")


def test_to_int_types(runtime: ModuleType) -> None:
    class Index:
        def __index__(self) -> int:
            return 7

    assert runtime.to_int(Index(), ctypes.c_int32, "x") == 7
    for value in (1.0, "1", None, True, Fraction(1, 1)):
        with pytest.raises(TypeError, match="must be an integer"):
            runtime.to_int(value, ctypes.c_int32, "x")


def test_to_bool(runtime: ModuleType) -> None:
    assert runtime.to_bool(True, "flag") is True
    assert runtime.to_bool(False, "flag") is False
    for value in (0, 1, None, "yes"):
        with pytest.raises(TypeError, match="must be a bool"):
            runtime.to_bool(value, "flag")


def test_to_float(runtime: ModuleType) -> None:
    assert runtime.to_float(2, ctypes.c_double, "v") == 2.0
    assert runtime.to_float(Fraction(1, 4), ctypes.c_double, "v") == 0.25
    assert runtime.to_float(1e300, ctypes.c_double, "v") == 1e300
    assert runtime.to_float(math.inf, ctypes.c_float, "v") == math.inf
    assert math.isnan(runtime.to_float(math.nan, ctypes.c_float, "v"))
    assert runtime.to_float(3.4028234663852886e38, ctypes.c_float, "v") == 3.4028234663852886e38
    with pytest.raises(OverflowError):
        runtime.to_float(3.5e38, ctypes.c_float, "v")
    for value in ("1.0", None, True, 1j):
        with pytest.raises(TypeError, match="must be a real number"):
            runtime.to_float(value, ctypes.c_double, "v")


def test_error(runtime: ModuleType) -> None:
    error = runtime.Error(runtime.ERROR_EXCEPTION, "boom")
    assert str(error) == "boom"
    assert error.status == 1
    assert error.message == "boom"
    assert error.status_name == "ERROR_EXCEPTION"
    assert runtime.Error(99, "").status_name == "UNKNOWN_STATUS_99"
    assert runtime.check(runtime.OK) is None


def test_load_reports_a_missing_library(runtime: ModuleType, tmp_path: Path) -> None:
    missing = tmp_path / "missing" / ("scalars.dll" if sys.platform == "win32" else "libx.so")
    with pytest.raises(OSError, match="cannot load the scalars library"):
        runtime.load(missing)
    assert runtime._library is None


def test_error_can_be_copied_and_pickled(runtime: ModuleType) -> None:
    error = runtime.Error(runtime.ERROR_EXCEPTION, "boom")
    for clone in (copy.copy(error), pickle.loads(pickle.dumps(error))):
        assert type(clone) is runtime.Error
        assert (clone.status, clone.message, str(clone)) == (1, "boom", "boom")


def test_check_decodes_utf8_messages(runtime: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    class Library:
        scalars_last_error = FakeFunction("caf\u00e9 \udcff".encode("utf-8", "surrogateescape"))

    monkeypatch.setattr(runtime, "_library", Library())
    with pytest.raises(runtime.Error) as raised:
        runtime.check(runtime.ERROR_EXCEPTION)
    # Valid UTF-8 is decoded; invalid bytes are replaced instead of failing.
    assert raised.value.message == "caf\u00e9 \ufffd"


def test_library_without_fingerprint_is_refused(runtime: ModuleType) -> None:
    with pytest.raises(RuntimeError, match="does not match these bindings"):
        runtime._check_fingerprint(object(), "libscalars.so")


def test_library_with_another_fingerprint_is_refused(runtime: ModuleType) -> None:
    class Library:
        scalars_api_fingerprint = FakeFunction(b"0" * 32)

    with pytest.raises(RuntimeError, match="does not match these bindings"):
        runtime._check_fingerprint(Library(), "libscalars.so")


def test_load_after_loading(
    runtime: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loaded = object()
    first = tmp_path / "a" / "libscalars.so"
    monkeypatch.setattr(runtime, "_library", loaded)
    monkeypatch.setattr(runtime, "_library_path", str(first))
    assert runtime.load() is loaded
    assert runtime.load(first) is loaded
    assert runtime.load(tmp_path / "a" / ".." / "a" / "libscalars.so") is loaded
    with pytest.raises(RuntimeError, match="already loaded"):
        runtime.load(tmp_path / "b" / "libscalars.so")
    # Found through the system search path: only the same bare name matches.
    monkeypatch.setattr(runtime, "_library_path", "libscalars.so")
    assert runtime.load("libscalars.so") is loaded
    with pytest.raises(RuntimeError, match="already loaded"):
        runtime.load(os.path.abspath("libscalars.so"))


def test_empty_environment_variable_is_ignored(
    runtime: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(runtime.LIBRARY_ENV_VAR, "")
    monkeypatch.setattr(runtime, "_default_library_name", lambda: "libscalars-missing.so")
    with pytest.raises(OSError, match=r"libscalars-missing\.so"):
        runtime.load()
    assert runtime._library is None


@pytest.mark.parametrize("case", all_cases(), ids=lambda case: case.name)
def test_status_codes_match_the_c_header(case: object) -> None:
    """The C header and the Python runtime define the same status codes."""
    module = case.module  # type: ignore[attr-defined]
    expected = case.expected_dir  # type: ignore[attr-defined]
    header = (expected / "c" / f"{module}_runtime.h").read_text(encoding="utf-8")
    prefix = module.upper()
    c_codes = {
        name: int(value)
        for name, value in re.findall(rf"#define {prefix}_(OK|ERROR_\w+) (\d+)", header)
    }
    spec = importlib.util.spec_from_file_location(
        f"bridgefex_status_{module}", expected / "python" / module / "_runtime.py"
    )
    assert spec is not None
    assert spec.loader is not None
    python_runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(python_runtime)
    python_codes = {name: getattr(python_runtime, name) for name in c_codes}
    assert c_codes == python_codes
    assert len(c_codes) == 6
    assert {value: name for name, value in c_codes.items()} == python_runtime._STATUS_NAMES


class LyingBytes(bytes):
    def __len__(self) -> int:
        return 100000


def test_to_bytes(runtime: ModuleType) -> None:
    assert runtime.to_bytes("é", "s") == b"\xc3\xa9"
    data = b"abc"
    assert runtime.to_bytes(data, "s") is data  # no copy
    lying = runtime.to_bytes(LyingBytes(b"abc"), "s")
    assert type(lying) is bytes
    assert len(lying) == 3
    assert runtime.to_bytes(bytearray(b"xy"), "s") == b"xy"
    assert runtime.to_bytes(memoryview(b"abcdef")[1::2], "s") == b"bdf"
    with pytest.raises(TypeError, match="'s' must be str or a bytes-like object, not NoneType"):
        runtime.to_bytes(None, "s")
    with pytest.raises(TypeError, match="bytes-like object of bytes"):
        runtime.to_bytes(memoryview(b"abcd").cast("i"), "s")
    with pytest.raises(UnicodeEncodeError):
        runtime.to_bytes("\udcff", "s")


def test_to_c_string(runtime: ModuleType) -> None:
    assert runtime.to_c_string("abc", "s") == b"abc"
    with pytest.raises(ValueError, match="'s' must not contain NUL characters"):
        runtime.to_c_string(b"a\x00", "s")


def test_string_at_in_chunks(runtime: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    buffer = ctypes.create_string_buffer(bytes(range(1, 14)), 13)
    pointer = ctypes.c_void_p(ctypes.addressof(buffer))
    monkeypatch.setattr(runtime, "_STRING_AT_CHUNK", 4)
    for size in range(14):
        assert runtime.string_at(pointer, size) == buffer.raw[:size]
    assert runtime.string_at(ctypes.c_void_p(), 0) == b""
    with pytest.raises(RuntimeError, match="NULL with a size"):
        runtime.string_at(ctypes.c_void_p(), 1)


def test_decode_is_strict(runtime: ModuleType) -> None:
    assert runtime.decode(b"a\x00\xc3\xa9") == "a\x00é"
    with pytest.raises(UnicodeDecodeError) as raised:
        runtime.decode(b"\x00\xffx")
    assert raised.value.object == b"\x00\xffx"


def test_to_enum(runtime: ModuleType) -> None:
    Color = enum.IntEnum("Color", [("red", 0), ("green", 200)])
    Other = enum.IntEnum("Other", [("x", 1)])

    class Index:
        def __index__(self) -> int:
            return 3

    assert runtime.to_enum(Color.green, Color, ctypes.c_uint8, "c") == 200
    assert runtime.to_enum(5, Color, ctypes.c_uint8, "c") == 5
    assert runtime.to_enum(Index(), Color, ctypes.c_uint8, "c") == 3
    for value in (True, Other.x, 1.0, "1", None):
        with pytest.raises(TypeError, match="must be Color or int"):
            runtime.to_enum(value, Color, ctypes.c_uint8, "c")
    with pytest.raises(OverflowError, match=r"\[0, 255\]"):
        runtime.to_enum(256, Color, ctypes.c_uint8, "c")
    with pytest.raises(ValueError, match=r"must be a value of Color in \[0, 7\]"):
        runtime.to_enum(8, Color, ctypes.c_int, "c", (0, 7))


def test_enum_result(runtime: ModuleType) -> None:
    Color = enum.IntEnum("Color", [("red", 0), ("green", 1), ("alias", 1)])
    Empty = enum.IntEnum("Empty", [])
    assert runtime.enum_result(Color, 1) is Color.green
    assert runtime.enum_result(Color, 7) == 7
    assert type(runtime.enum_result(Color, 7)) is int
    assert runtime.enum_result(Empty, 3) == 3


def test_to_items(runtime: ModuleType) -> None:
    assert runtime.to_items([1, 2], 2, "a") == [1, 2]
    assert runtime.to_items((1, 2), 2, "a") == [1, 2]
    assert runtime.to_items(range(2), 2, "a") == [0, 1]
    assert runtime.to_items(b"\x01\xff", 2, "a", True) == [1, 255]
    assert runtime.to_items(bytearray(b"ab"), 2, "a", True) == [97, 98]
    with pytest.raises(ValueError, match="must have 3 elements, got 2"):
        runtime.to_items([1, 2], 3, "a")
    for value in ({1, 2}, iter([1, 2]), "ab", {1: 2, 3: 4}, 5, None):
        with pytest.raises(TypeError, match="'a'"):
            runtime.to_items(value, 2, "a")
    with pytest.raises(TypeError, match="not bytes"):
        runtime.to_items(b"ab", 2, "a")


def test_struct_defaults(runtime: ModuleType) -> None:
    calls = []

    @dataclasses.dataclass
    class Point:
        x: int = runtime.DEFAULT
        y: int = runtime.DEFAULT

        def __post_init__(self) -> None:
            runtime.fill_defaults(self, lambda: calls.append(1) or Point(7, 8))

    assert Point(1, 2) == Point(1, 2)
    assert calls == []  # nothing omitted: no call
    assert Point(y=5) == Point(7, 5)
    assert repr(runtime.DEFAULT) == "<C++ default>"
    assert runtime.check_struct(Point(1, 2), Point, "p") == Point(1, 2)
    with pytest.raises(TypeError, match=r"argument 'p' must be .*Point, not tuple"):
        runtime.check_struct((1, 2), Point, "p")


def test_to_array_of_bytes(runtime: ModuleType) -> None:
    def to_bytes(value: object) -> list[int]:
        return list(runtime.to_array(value, ctypes.c_uint8, "a", runtime.check_int, True))

    assert to_bytes(b"\x01\xff") == [1, 255]
    assert to_bytes(bytearray(b"ab")) == [97, 98]
    # Non-contiguous views are copied too.
    assert to_bytes(memoryview(b"abcdef")[::2]) == [97, 99, 101]
    assert to_bytes(memoryview(b"")) == []
    # Signed bytes are integers, -1 is not 255.
    assert to_bytes(memoryview(array.array("b", [1, 2]))) == [1, 2]
    with pytest.raises(OverflowError, match=r"'a\[0\]'"):
        to_bytes(memoryview(array.array("b", [-1])))
    with pytest.raises(OverflowError, match=r"'a\[0\]'"):
        to_bytes(array.array("b", [-1]))
    with pytest.raises(TypeError, match="one-dimensional"):
        to_bytes(memoryview(b"abcd").cast("B", (2, 2)))
    with pytest.raises(TypeError, match="one-dimensional"):
        to_bytes(memoryview(array.array("h", [1])))


def test_messages_name_the_module_of_the_type(runtime: ModuleType) -> None:
    class Flag:
        def __bool__(self) -> bool:
            return True

    with pytest.raises(TypeError, match=r"must be a bool, not tests\.test_python_runtime\..*Flag"):
        runtime.to_bool(Flag(), "a")
    with pytest.raises(TypeError, match=r"must be a bool, not int$"):
        runtime.to_bool(1, "a")


def test_sequences_that_cannot_be_iterated(runtime: ModuleType) -> None:
    class Scalar:
        """Like a 0-d numpy array: len() and indexing, but iteration fails."""

        def __len__(self) -> int:
            raise TypeError("len() of unsized object")

        def __getitem__(self, index: int) -> int:
            raise IndexError(index)

        def __iter__(self) -> Iterator[int]:
            raise TypeError("iteration over a 0-d array")

    with pytest.raises(TypeError, match="argument 'a' must be a sequence"):
        runtime.to_array(Scalar(), ctypes.c_int32, "a", runtime.check_int)
