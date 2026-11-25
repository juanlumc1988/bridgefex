# SPDX-License-Identifier: Apache-2.0
"""Round trip through the generated Python bindings of the "spans" case.

tests/test_roundtrip.py runs this file in a fresh interpreter, with the
generated package on PYTHONPATH and SPN_LIBRARY pointing at the built library.
"""

import gc

from spn import views


def churn():
    """Allocate and free objects of many sizes, so that memory that was freed is reused (and
    overwritten) before C++ reads it."""
    junk = [bytes([index % 251]) * size for size in range(1, 300) for index in range(40)]
    del junk
    gc.collect()
    views.Ledger.scribble()  # and the memory of C++


def keeping():
    """What C++ stores of a span is the memory of the call, which is gone when it returns: the
    object that keeps it holds it (the Ledger reads it again, also when it is destroyed)."""
    ledger = views.Ledger([1, 2, 3])
    ledger.add([10, 20])
    ledger.addDoubles([1.5, 2.5])
    ledger.addFlags([True, False])
    ledger.addLongs([7, 8, 9])
    churn()
    assert ledger.intact()
    assert ledger.total() == 36 and ledger.kept() == 3 + 2 + 2 + 2 + 3
    ledger.addCopy([4, 5])  # a vector is a copy: nothing to keep
    assert ledger.kept() == 12
    ledger.add([])  # an empty span
    churn()
    assert ledger.intact() and ledger.total() == 6
    del ledger
    churn()
    # A function keeps it for ever.
    views.Ledger.pin([5, 6, 7])
    churn()
    assert views.Ledger.keptIntact()
    assert views.Ledger.badDestructions() == 0, "no destructor found what it points to gone"


def main():
    keeping()
    assert views.sum([1, 2, 3]) == 6
    assert views.sum([]) == 0
    assert views.numbers() == [-1, 2, -3]
    assert views.count_red([views.Color.Red, 1, 0]) == 2
    assert views.sum_x([views.Point(1.5, 0), views.Point(2.5, 0)]) == 4.0
    assert views.total(["ab", b"c"]) == 3
    print("spans: Python round trip OK")


if __name__ == "__main__":
    main()
