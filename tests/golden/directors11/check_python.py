# SPDX-License-Identifier: Apache-2.0
"""The generated Python package of the C++11 directors case: Python classes override the
virtual methods of Base and Derived, with enums, value structs, overloads and the
constructors that only a derived class can call."""

from dr1 import base

with base.Derived() as derived:
    assert derived.show() == "p!"
    assert derived.value_int32(1) == 2
    assert derived.pair() == base.Pair(first=1, second=2)
    try:
        derived.hook()  # protected: only an override calls it
    except TypeError as error:
        assert "protected" in str(error), str(error)
    else:
        raise AssertionError("a protected method was called")


class Mine(base.Base):
    def text(self, prefix):
        return prefix + "?" + str(self.seed())

    def tagged(self):
        return base.Tagged(kind=base.Kind.Small, number=self.seed())

    def kind(self, requested):
        return base.Kind.Large if requested == base.Kind.Small else base.Kind.Small

    def pair(self):
        pair = super().pair()  # the implementation of the class
        return base.Pair(first=pair.second, second=pair.first)

    def touch(self, amount, limit):
        super().touch(amount * 2, limit)

    def value_int32(self, x):
        return -x

    def value_double(self, x):
        return super().value_double(x) + 0.5

    def hook(self):
        self.hooked = True
        super().hook()

    def flag(self, on):
        assert isinstance(on, bool)  # a bool arrives as a bool
        return on  # the implementation of the class gives the opposite

    def series(self, bytes):
        assert isinstance(bytes, list) and bytes == [1, 255], bytes
        return [2**63 - 1, -(2**63)]

    def big(self, x, f, byte):
        assert isinstance(f, float) and isinstance(byte, int)
        return 2**63 + x + int(f) + byte  # beyond the range of an int64


# The three constructors: two public, one protected.
with Mine.create_int32(5) as mine:
    assert mine.show() == "p?5"  # C++ calls text(), which is pure in C++
    assert mine.show_tagged() == base.Tagged(
        kind=base.Kind.Small, number=5
    )  # a struct with an enum
    assert mine.show_kind(base.Kind.Small) == base.Kind.Large
    assert mine.show_kind(base.Kind.Large) == base.Kind.Small
    assert mine.pair() == base.Pair(first=6, second=5)  # the base call: seed, seed + 1
    mine.touch(3, 100)  # base call with a reference to a scalar: min(6, 100)
    assert mine.seed() == 6
    mine.touch(3, 4)
    assert mine.seed() == 4
    assert mine.value_int32(2) == -2
    assert mine.value_double(2.0) == 4.5
    mine.hook()
    assert mine.hooked
    assert mine.show_flag(True) is True
    assert mine.show_flag(False) is False
    assert mine.show_big(-3, 2.5, 200) == 2**63 + 199
    assert mine.show_series([1, 255]) == [2**63 - 1, -(2**63)]  # 64-bit elements both ways
with Mine.create_dr1_Kind_int32(base.Kind.Large, 1) as mine:
    assert mine.seed() == 11
with Mine.create_void() as mine:  # the protected constructor
    assert mine.seed() == 1


# A class that has no override of its own: the implementations of the class.
class Quiet(base.Base):
    def text(self, prefix):
        return prefix

    def kind(self, requested):
        return 99  # not an enumerator of an enum without a fixed type: refused


with Quiet.create_int32(2) as quiet:
    assert quiet.show_series([3, 4]) == [-3, -4]  # the implementation of the class
    assert quiet.show() == "p"
    assert quiet.show_tagged() == base.Tagged(kind=base.Kind.Large, number=7)
    try:
        quiet.show_kind(base.Kind.Small)
    except (ValueError, TypeError, OverflowError) as error:
        assert "kind" in str(error), str(error)
    else:
        raise AssertionError("a value that the enum cannot have was accepted")


# A subclass of a concrete class, with a constructor of its own.
class Extended(base.Derived):
    def text(self, prefix):
        return super().text(prefix) + "?"  # the implementation of Derived

    def pair(self):
        return super().pair()


with Extended() as extended:
    assert extended.show() == "p!?"
    assert extended.pair() == base.Pair(first=1, second=2)

# The class that is instantiated itself has no director.
try:
    base.Base.create_int32(1)
except TypeError as error:
    assert "itself" in str(error), str(error)
else:
    raise AssertionError("an abstract class was instantiated")

print("directors11: Python round trip OK")
