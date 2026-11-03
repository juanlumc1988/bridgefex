# SPDX-License-Identifier: Apache-2.0
"""The generated Python package of the "directors" case: the classes that have a director
work as before (the Python bindings do not use it yet)."""

from drc import shape

with shape.Circle(2.0) as circle:
    assert circle.area() == 12.0
    assert circle.name() == "round"
    assert circle.describe() == "round: 12 units"
with shape.Dot() as dot:
    assert dot.area() == 0.0
with shape.Plain() as plain:
    assert plain.value() == 5
print("directors: Python round trip OK")
