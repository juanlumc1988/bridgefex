# SPDX-License-Identifier: Apache-2.0
"""Round trip through the generated Python bindings of the "edge" case."""

import edge
from edge import edge as api


def main():
    assert api.add(2, 3) == 5
    assert api.add(arg1=1, arg2=1) == 2
    assert api.combine(1, 2, 3) == 123
    assert api.combine(int32_t_=4, size_t_=5, EDGE_OK_=6) == 456
    # A function named getattr does not break the generated classes, which
    # use the real builtins.
    assert api.getattr(41) == 42
    assert api.threading_() == 4

    assert api.Registry.liveCount() == 0
    registry = api.Registry()
    assert registry.classmethod() == 1
    assert registry.staticmethod() == 2
    try:
        api.Registry().__init__()
    except TypeError as error:
        assert "already initialized" in str(error)
    else:
        raise AssertionError("re-initialization must be refused")
    registry.close()
    assert api.Registry.liveCount() == 0

    # A destructor that throws: close() still releases the object, and the
    # message of an earlier failure is not overwritten.
    with api.Registry() as failing:
        try:
            failing.fail()
        except edge.Error as error:
            assert str(error) == "failed on purpose"
        failing.throwOnDestroy(True)
    assert api.Registry.liveCount() == 0
    library = edge._runtime.library()
    assert library.edge_last_error() == b"failed on purpose"
    print("edge: Python round trip OK")


main()
