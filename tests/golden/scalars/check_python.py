# SPDX-License-Identifier: Apache-2.0
"""Round trip through the generated Python bindings of the "scalars" case.

Every scalar type is sent to C++ and back at its limits; values one step
outside the limits must be refused before reaching C.
"""

import ctypes
import math

import scalars
from scalars import numbers


def expect(exception_type, function, *args):
    try:
        function(*args)
    except exception_type as error:
        return error
    raise AssertionError(f"{function.__name__}{args} did not raise {exception_type.__name__}")


def integer_limits(ctype):
    bits = 8 * ctypes.sizeof(ctype)
    if ctype(-1).value < 0:
        return -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    return 0, (1 << bits) - 1


INTEGER_FUNCTIONS = {
    numbers.echo_schar: ctypes.c_byte,
    numbers.echo_uchar: ctypes.c_ubyte,
    numbers.echo_short: ctypes.c_short,
    numbers.echo_ushort: ctypes.c_ushort,
    numbers.echo_int: ctypes.c_int,
    numbers.echo_uint: ctypes.c_uint,
    numbers.echo_long: ctypes.c_long,
    numbers.echo_ulong: ctypes.c_ulong,
    numbers.echo_llong: ctypes.c_longlong,
    numbers.echo_ullong: ctypes.c_ulonglong,
    numbers.echo_int8: ctypes.c_int8,
    numbers.echo_int16: ctypes.c_int16,
    numbers.echo_int32: ctypes.c_int32,
    numbers.echo_int64: ctypes.c_int64,
    numbers.echo_uint8: ctypes.c_uint8,
    numbers.echo_uint16: ctypes.c_uint16,
    numbers.echo_uint32: ctypes.c_uint32,
    numbers.echo_uint64: ctypes.c_uint64,
    numbers.echo_size: ctypes.c_size_t,
    numbers.echo_const: ctypes.c_int32,
}


def check_integers():
    for function, ctype in INTEGER_FUNCTIONS.items():
        low, high = integer_limits(ctype)
        for value in (low, high, 0, 1, (low + high) // 2):
            result = function(value)
            assert result == value, (function.__name__, value, result)
            assert type(result) is int
        expect(OverflowError, function, low - 1)
        expect(OverflowError, function, high + 1)
        expect(TypeError, function, 1.0)
        expect(TypeError, function, True)
        expect(TypeError, function, None)


def check_bool_and_floats():
    assert numbers.echo_bool(True) is True
    assert numbers.echo_bool(False) is False
    expect(TypeError, numbers.echo_bool, 1)
    expect(TypeError, numbers.echo_bool, None)

    for value in (0.0, -0.0, 1.5, -2.25, 1e300, math.inf, -math.inf):
        assert numbers.echo_double(value) == value
    assert math.isnan(numbers.echo_double(math.nan))
    assert numbers.echo_double(3) == 3.0
    expect(TypeError, numbers.echo_double, "1.0")
    expect(TypeError, numbers.echo_double, True)

    assert numbers.echo_float(1.5) == 1.5
    assert numbers.echo_float(3.4028234663852886e38) == 3.4028234663852886e38
    assert numbers.echo_float(math.inf) == math.inf
    expect(OverflowError, numbers.echo_float, 1e39)
    expect(OverflowError, numbers.echo_float, -1e39)


def check_functions():
    assert numbers.sum_renamed(1, 2, 3, 4) == 10
    assert numbers.sum_renamed(lambda_=1, self_=2, restrict_=3, out_result_=4) == 10
    assert numbers.twice(21) == 42
    assert numbers.twice(arg1=4) == 8
    assert numbers.scale_double(1.5) == 3.0
    assert numbers.scale_double_double(1.5, 3.0) == 4.5
    assert numbers.divide(7, 2) == 3
    error = expect(scalars.Error, numbers.divide, 1, 0)
    assert error.status == scalars.ERROR_EXCEPTION
    assert error.status_name == "ERROR_EXCEPTION"
    assert str(error) == "division by zero"
    assert numbers.do_nothing() is None


def check_accumulator():
    assert numbers.Accumulator.version() == 3
    with numbers.Accumulator() as accumulator:
        assert accumulator.count() == 0
        accumulator.push(1.5)
        accumulator.push(2.5)
        assert accumulator.total() == 4.0
        assert accumulator.count() == 2
        accumulator.reset()
        assert accumulator.count() == 0


def main():
    check_integers()
    check_bool_and_floats()
    check_functions()
    check_accumulator()
    print("scalars: Python round trip OK")


main()
