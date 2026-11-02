# SPDX-License-Identifier: Apache-2.0
"""Round trip through the generated Python bindings of the "inherit" case.

tests/test_roundtrip.py runs this file in a fresh interpreter, with the
generated package on PYTHONPATH and INH_LIBRARY pointing at the built library.
"""

import gc

import inh
from inh import shape, shapes, widget


def expect(exception_type, function, *args, message=None):
    try:
        function(*args)
    except exception_type as error:
        if message is not None:
            assert message in str(error), str(error)
        return error
    raise AssertionError(f"{function.__name__}{args} did not raise {exception_type.__name__}")


def alive():
    gc.collect()
    return shapes.shapes_alive()


# The classes derive from each other as in C++, across headers.
assert shape.Shape.__mro__[1:3] == (shape.Named, inh._runtime.Object)
assert issubclass(shapes.Ring, shapes.Circle) and issubclass(shapes.Circle, shape.Shape)
assert issubclass(widget.Widget, widget.Counter) and issubclass(widget.Widget, widget.Labeled)
assert not issubclass(widget.Plain, widget.Counter)

# Abstract classes cannot be instantiated, and say why.
expect(TypeError, shape.Shape, message="Shape is abstract")
expect(TypeError, shape.Named, message="Named is abstract")

# Inherited methods, and virtual calls that run the override of the object's class.
with shapes.Circle(2.0) as circle:
    assert circle.area() == 12.0
    assert circle.name() == "circle"
    assert circle.greeting() == "I am circle"  # Named::greeting, two levels up
    assert circle.describe() == "circle: 12 units"  # Shape::describe calls the overrides
    assert circle.sides() == 0  # the default of a virtual method
    assert circle.radius() == 2.0
    assert isinstance(circle, shape.Shape) and isinstance(circle, shape.Named)
with shapes.Square(3.0) as square:
    assert square.sides() == 4  # overridden
    assert square.describe() == "square: 9 units"

# A method that a derived class hides is the one of the class; the base's is still there.
with shapes.Circle(2.0) as circle:
    assert circle.scale(2.0) == 4.0
    assert circle.radius() == 4.0
    assert shape.Shape.scale(circle, 3) == 3  # Shape::scale(int), on the Shape part
    assert circle.scaled() == 3
    assert circle.radius() == 4.0

# Three levels: Ring is a Circle, a Shape and a Named.
with shapes.Ring(10.0, 5.0) as ring:
    assert ring.area() == 225.0 and ring.name() == "ring"
    assert ring.radius() == 10.0  # Circle::radius on a Ring
    assert ring.describe() == "ring: 225 units"
    assert ring.greeting() == "I am ring"
    assert shapes.call_name(ring) == "I am ring"  # a Ring where a Named is expected
    assert shapes.total_area(ring, ring) == 450.0
    assert shapes.describe_first(ring) == "ring: 225 units"
    assert shapes.grow(ring, 2) == 450.0


# A user subclass in Python is the same C++ class.
class Mine(shapes.Circle):
    def twice(self):
        return 2 * self.area()


with Mine(1.0) as mine:
    assert mine.twice() == 6.0
    assert shapes.total_area(mine, mine) == 6.0

# A closed object fails like any other, through the base class too.
dead = shapes.Square(1.0)
dead.close()
expect(ValueError, shapes.total_area, dead, dead, message="closed")
expect(ValueError, dead.describe, message="closed")
expect(TypeError, shapes.total_area, 1, 2, message="argument 'first' must be Shape")
expect(TypeError, shapes.call_name, widget.Counter(), message="argument 'named' must be Named")
expect(
    TypeError, shapes.describe_first, None, message="argument 'shape' must be Shape, not NoneType"
)

# Objects that C++ returns as the base class: that is the class of the Python object,
# whatever the object is, and its virtual methods run the right code.
made = [shapes.make_shape(kind, 3.0) for kind in range(3)]
assert [type(item) for item in made] == [shape.Shape] * 3
assert [item.name() for item in made] == ["circle", "square", "ring"]
assert [item.sides() for item in made] == [0, 4, 0]
assert [item.area() for item in made] == [27.0, 9.0, 27.0 - 3.0 * 0.6**2]
assert alive() == 3
del made
assert alive() == 0
assert shapes.make_shape(9, 1.0) is None

# A borrowed result is a Shape that belongs to its arguments.
with shapes.Circle(1.0) as small, shapes.Square(5.0) as big:
    chosen = shapes.largest(small, big)
    assert type(chosen) is shape.Shape and chosen.name() == "square"
    assert chosen.sides() == 4
    big.close()
    expect(ValueError, chosen.name, message="no longer valid")

# Giving an object up as the base class: the object is closed, C++ destroys it.
for make in (lambda: shapes.Circle(1.0), lambda: shapes.Square(1.0), lambda: shapes.Ring(2.0, 1.0)):
    kind = make()
    assert alive() == 1
    expected_sides = 4 if isinstance(kind, shapes.Square) else 0
    assert shapes.consume(kind) == expected_sides
    expect(ValueError, kind.area, message="closed")
    assert alive() == 0
returned = shapes.make_shape(1, 1.0)
assert shapes.consume(returned) == 4
expect(ValueError, returned.area, message="closed")
expect(ValueError, shapes.consume, returned, message="closed")
assert alive() == 0

# Several bases: each of them is a different part of the object.
with widget.Widget() as w:
    assert w.next() == 1  # Counter::next
    assert widget.bump(w) == 2  # a Widget where a Counter& is expected
    assert w.count() == 2
    assert widget.peek(w) == 2  # a copy of the Counter part
    assert w.label() == "widget unnamed"  # overridden Labeled::label
    w.setLabel("ab")
    assert widget.name_of(w) == "widget ab"  # through Labeled*, virtual
    assert w.both() == 2 * 100 + len("widget ab")
    assert isinstance(w, widget.Counter) and isinstance(w, widget.Labeled)
with widget.Counter() as counter, widget.Labeled.create_void() as labeled:
    assert widget.bump(counter) == 1
    assert widget.name_of(labeled) == "unnamed"
with widget.Labeled.create_string("given") as labeled:
    assert labeled.label() == "given"
# The constructors of a base class make objects of that class only: Widget inherits
# the functions that make a Labeled, which would put it in a Widget object.
expect(TypeError, widget.Widget.create_string, "x", message="constructs a Labeled, not a Widget")


# Without a virtual destructor, C++ would destroy a derived object through the base
# class: only an object of exactly that class (or a Python subclass of it) is accepted.
class MyPlain(widget.Plain):
    pass


assert widget.take_plain(widget.Plain()) == 5
assert widget.take_plain(MyPlain()) == 5
derived = widget.PlainDerived()
expect(TypeError, widget.take_plain, derived, message="no virtual destructor")
assert derived.extra() == 9 and derived.value() == 5  # still usable: nothing was taken
derived.close()

assert alive() == 0
print("inherit: Python round trip OK")
