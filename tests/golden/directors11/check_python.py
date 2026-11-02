# SPDX-License-Identifier: Apache-2.0
"""The generated Python package of the C++11 directors case: the classes that have a
director work as before (the Python bindings do not use it yet)."""

from dr1 import base

with base.Derived() as derived:
    assert derived.show() == "p!"
    assert derived.value_int32(1) == 2
print("directors11: Python round trip OK")
