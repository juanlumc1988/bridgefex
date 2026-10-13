# SPDX-License-Identifier: Apache-2.0
"""Round trip through the generated Python bindings of the "containers" case.

tests/test_roundtrip.py runs this file in a fresh interpreter, with the
generated package on PYTHONPATH and CONT_LIBRARY pointing at the built library.
"""

import array
import collections

import cont
from cont import vectors


def expect(exception_type, function, *args, message=None):
    try:
        function(*args)
    except exception_type as error:
        if message is not None:
            assert message in str(error), str(error)
        return error
    raise AssertionError(f"{function.__name__}{args} did not raise {exception_type.__name__}")


class Index:
    def __init__(self, value):
        self.value = value

    def __index__(self):
        return self.value


class FakeArray:
    """Only len() and indexing, like a numpy array."""

    def __init__(self, items):
        self.items = items

    def __len__(self):
        return len(self.items)

    def __getitem__(self, index):
        return self.items[index]


def check_inputs():
    expected = [10, -20, 30]
    for values in (
        [1, -2, 3],
        (1, -2, 3),
        range(1, 4),
        collections.deque([1, -2, 3]),
        array.array("q", [1, -2, 3]),
        [Index(1), -2, 3],
        FakeArray([1, -2, 3]),
    ):
        if isinstance(values, range):
            assert vectors.scale(values, 10) == [10, 20, 30]
        else:
            assert vectors.scale(values, 10) == expected, values
    assert vectors.scale([], 10) == []
    for values in ((x for x in [1]), {1}, {1: 2}, "abc", b"abc", None, 5):
        expect(TypeError, vectors.scale, values, 1)
    expect(OverflowError, vectors.scale, [0, 2**63], 1, message="'values[1]'")
    expect(TypeError, vectors.scale, [True], 1, message="'values[0]'")
    expect(TypeError, vectors.scale, [1.0], 1)
    assert vectors.invert([True, False]) == [False, True]
    expect(TypeError, vectors.invert, [1], message="'flags[0]' must be a bool")
    assert vectors.echo_bytes(b"\x00\xff") == [0, 255]
    assert vectors.echo_bytes(bytearray(b"ab")) == [97, 98]
    assert vectors.echo_bytes([1, 2]) == [1, 2]
    assert vectors.halves([1.0, 3]) == [0.5, 1.5]
    expect(OverflowError, vectors.halves, [1e39])
    assert vectors.total_size(["ab", b"c\x00d", bytearray(b"")]) == 5
    expect(TypeError, vectors.total_size, "abc")
    expect(TypeError, vectors.total_size, [1])
    expect(UnicodeEncodeError, vectors.total_size, ["\ud800"])
    assert vectors.sum([1, 2], 7) == 3007


def check_results():
    assert vectors.upper(["ab\x00c", "", "xyz"]) == ["AB\x00C", "", "XYZ"]
    assert vectors.empty_strings() == ["", ""]
    Color = vectors.Color
    assert vectors.rotate([Color.Red, Color.Blue, 1]) == [Color.Green, Color.Red, Color.Blue]
    assert vectors.count_high([vectors.Level.High, 0, 1]) == 2
    expect(ValueError, vectors.count_high, [2], message="'levels[0]'")
    assert vectors.levels(3) == [vectors.Level.Low, vectors.Level.High, vectors.Level.Low]
    Point = vectors.Point
    shifted = vectors.shift([Point(1, 2), Point(3, 4)], 0.5)
    assert shifted == [Point(1.5, 2), Point(3.5, 4)]
    expect(TypeError, vectors.shift, [(1, 2)], 0.5, message="'points[0]'")
    assert vectors.primes() == [2, 3, 5, 7]
    expect(cont.Error, vectors.too_big, message="max_size")
    with vectors.Bag(["a", "b"]) as bag:
        bag.add([1, 2])
        bag.add((3,))
        assert bag.counts() == [1, 2, 3]
        assert bag.items() == ["a", "b"]


def main():
    check_inputs()
    check_results()
    print("containers: Python round trip OK")


if __name__ == "__main__":
    main()
