# SPDX-License-Identifier: Apache-2.0
"""Round trip through the generated Python bindings of the "spans" case.

tests/test_roundtrip.py runs this file in a fresh interpreter, with the
generated package on PYTHONPATH and SPN_LIBRARY pointing at the built library.
"""

from spn import views


def main():
    assert views.sum([1, 2, 3]) == 6
    assert views.sum([]) == 0
    assert views.numbers() == [-1, 2, -3]
    assert views.count_red([views.Color.Red, 1, 0]) == 2
    assert views.sum_x([views.Point(1.5, 0), views.Point(2.5, 0)]) == 4.0
    assert views.total(["ab", b"c"]) == 3
    print("spans: Python round trip OK")


if __name__ == "__main__":
    main()
