# SPDX-License-Identifier: Apache-2.0
"""Round trip through the generated Python bindings of the "demo" case.

tests/test_roundtrip.py runs this file in a fresh interpreter, with the
generated package on PYTHONPATH and DEMO_LIBRARY pointing at the built library.
"""

import copy
import gc
import math
import pickle
import threading

import demo
from demo import counter, geometry


def expect(exception_type, function, *args, status=None, message=None):
    try:
        function(*args)
    except exception_type as error:
        if status is not None:
            assert error.status == status, (error.status, error)
        if message is not None:
            assert message in str(error), str(error)
        return
    raise AssertionError(f"{function.__name__}{args} did not raise {exception_type.__name__}")


def check_counter():
    assert counter.Counter.liveInstances() == 0
    expect(TypeError, counter.Counter)  # overloaded constructors need a factory

    first = counter.Counter.create_void()
    assert first.value() == 0
    assert first.isZero() is True
    second = counter.Counter.create_int32(41)
    second.increment()
    assert second.value() == 42
    assert counter.Counter.liveInstances() == 2

    # Overloads.
    second.add_int32(-2)
    second.add_double(0.6)
    assert second.value() == 41

    # Exceptions become demo.Error with the C++ message.
    expect(demo.Error, second.setLimit, -1, status=demo.ERROR_EXCEPTION, message="negative")
    second.setLimit(41)
    assert second.limit() == 41
    expect(demo.Error, second.increment, status=demo.ERROR_EXCEPTION, message="limit reached")
    expect(demo.Error, second.fail, status=demo.ERROR_UNKNOWN_EXCEPTION)
    expect(demo.Error, second.reserve, 42, status=demo.ERROR_OUT_OF_MEMORY)
    second.reserve(41)

    # Values that do not fit the C type are refused instead of wrapped.
    expect(OverflowError, second.add_int32, 2**31)
    expect(OverflowError, second.add_int32, -(2**31) - 1)
    expect(OverflowError, second.reserve, -1)
    expect(TypeError, second.add_int32, 1.5)
    expect(TypeError, second.add_int32, True)
    expect(TypeError, second.add_double, "1")
    # Both limits pass; the order keeps the C++ value in range (no overflow).
    second.add_int32(-(2**31) + 1)
    second.add_int32(2**31 - 1)
    assert second.value() == 41

    # Lifetime: close() is idempotent, use after close is an error, and the
    # garbage collector destroys forgotten objects.
    second.close()
    second.close()
    expect(ValueError, second.value)
    assert counter.Counter.liveInstances() == 1
    del first
    gc.collect()
    assert counter.Counter.liveInstances() == 0
    with counter.Counter.create_int32(5) as scoped:
        assert scoped.value() == 5
        assert counter.Counter.liveInstances() == 1
    assert counter.Counter.liveInstances() == 0

    # Copying would share the handle, so it is refused.
    with counter.Counter.create_void() as original:
        expect(TypeError, copy.copy, original)
        expect(TypeError, copy.deepcopy, original)
        expect(TypeError, pickle.dumps, original)

    assert counter.multiply_int32_int32(-3, 2**30) == -3 * 2**30


def check_errors_are_values():
    try:
        counter.Counter.create_void().setLimit(-1)
    except demo.Error as error:
        original = error
    for clone in (copy.copy(original), pickle.loads(pickle.dumps(original))):
        assert type(clone) is demo.Error
        assert (clone.status, clone.message, str(clone)) == (
            original.status,
            original.message,
            str(original),
        )


def check_threads():
    """Each thread reads the message of its own last failure."""
    failures = []

    def worker(number):
        circle = geometry.Circle(1.0)
        for _ in range(200):
            try:
                if number % 2:
                    circle.scale(-1.0)
                else:
                    counter.Counter.create_void().setLimit(-1)
            except demo.Error as error:
                expected = "positive" if number % 2 else "negative"
                if expected not in str(error):
                    failures.append(str(error))
            else:
                failures.append("no error raised")
        circle.close()

    threads = [threading.Thread(target=worker, args=(number,)) for number in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not failures, failures[:3]


def check_geometry():
    circle = geometry.Circle(2.0)
    assert circle.radius() == 2.0
    assert math.isclose(circle.area(), math.pi * 4.0)
    circle.scale(1.5)
    assert circle.radius() == 3.0
    expect(demo.Error, circle.scale, 0.0, status=demo.ERROR_EXCEPTION, message="positive")
    # A constructor that throws reports the error and creates nothing.
    expect(demo.Error, geometry.Circle, -1.0, status=demo.ERROR_EXCEPTION, message="radius")
    # Handles of one class are refused by functions of another.
    with counter.Counter.create_void() as other:
        bad = geometry.Circle.__new__(geometry.Circle)
        bad._adopt(other._handle)
        expect(Exception, bad.radius)
        bad._finalizer.detach()
    assert geometry.distance(0.0, 0.0, 3.0, 4.0) == 5.0
    assert geometry.multiply_double_double(1.5, 4) == 6.0


def main():
    assert demo.load() is demo.load()
    check_counter()
    check_errors_are_values()
    check_threads()
    check_geometry()
    print("demo: Python round trip OK")


main()
