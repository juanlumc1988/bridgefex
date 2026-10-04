# SPDX-License-Identifier: Apache-2.0
"""Unit tests of the generated Python runtime (_runtime.py), without a native library."""

from __future__ import annotations

import ctypes
import importlib.util
import math
import sys
from fractions import Fraction
from pathlib import Path
from types import ModuleType

import pytest

from tests.support.cases import GOLDEN_DIR

RUNTIME = GOLDEN_DIR / "scalars" / "expected" / "python" / "scalars" / "_runtime.py"


@pytest.fixture(scope="module")
def runtime() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bridgefex_test_runtime", RUNTIME)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
