# SPDX-License-Identifier: Apache-2.0
"""The generated Python package of the "directors" case: Python classes that override the
virtual methods of the C++ classes, which C++ calls back."""

import gc
import sys

from drc import _runtime, shape, stage


def alive():
    return shape.Shape.alive()


# Classes that have a director work as before when nothing derives from them.
with shape.Circle(2.0) as circle:
    assert circle.area() == 12.0
    assert circle.name() == "round"
    assert circle.describe() == "round: 12 units"
with shape.Dot() as dot:
    assert dot.area() == 0.0
with shape.Plain() as plain:
    assert plain.value() == 5
assert alive() == 0


# A subclass of an abstract class: the pure virtual method has to be overridden, and the
# class is created through the constructors of the director.
class Square(shape.Shape):
    def __init__(self):  # not called by create_void(): the object is made without it
        raise AssertionError

    def area(self):
        return 4.0

    def name(self):
        return "sq:" + super().name()  # the implementation of C++, not this method

    def sides(self):
        return 4

    def center(self):
        return shape.Point(x=2.0, y=4.0)

    def mode(self, requested):
        return shape.Mode.Off if requested == shape.Mode.On else shape.Mode(5)

    def scale(self, factor):
        self.scaled = factor

    def add_int32_int32(self, a, b):
        return a * b

    def add_double_double(self, a, b):
        return a - b

    def greet(self, who, tag, mark):
        return f"{mark}|{tag}|{who}|" + super().greet(who, tag, mark)

    def quiet(self):
        return 9

    def changed(self, reason):
        self.seen = reason
        super().changed(reason * 2)  # a protected method of C++


with Square.create_void() as square:
    assert alive() == 1
    assert square.area() == 4.0
    # C++ calls the overrides: describe() uses name() and area().
    assert square.describe() == "sq:shape: 4 units"
    assert square.name() == "sq:shape"
    assert square.sum(3, 4) == 12  # add_int32_int32
    assert square.how_quiet() == 9
    assert square.total_of_three() == 6  # not overridable (a vector): the class's own
    assert square.middle(shape.Point(x=4.0, y=0.0)) == shape.Point(x=3.0, y=2.0)
    assert square.choose(shape.Mode.On) == shape.Mode.Off
    assert square.choose(shape.Mode.Off) == shape.Mode.Auto
    assert square.welcome("Ann") == ">>|hello|Ann|>> hello Ann, I am sq:shape"
    assert square.scaled_area(3.0) == 4.0 and square.scaled == 3.0
    square.notify(5)  # a protected virtual method, overridden
    assert square.seen == 5 and square.last_reason() == 10
    # The method that is not overridden keeps the implementation of the class.
    assert square.sides() == 4  # overridden: a Python call
    assert square.fixed() == 7  # final: never overridden
assert alive() == 0


# A class that does not override the pure virtual method cannot be instantiated.
class Incomplete(shape.Shape):
    def name(self):
        return "x"


for make in (Incomplete.create_void, shape.Shape.create_void):
    try:
        make()
    except TypeError as error:
        assert "area" in str(error) or "itself" in str(error), str(error)
    else:
        raise AssertionError("an abstract class was instantiated")
assert alive() == 0


# The constructors of the class are those of the Python class, with their arguments.
class Labelled(shape.Shape):
    def area(self):
        return 1.0


with Labelled.create_string_int32("box", 3) as labelled:
    assert labelled.name() == "box"  # not overridden: the implementation of the class
    assert labelled.sides() == 3
    assert labelled.label() == "box"
    assert labelled.describe() == "box: 1 units"
assert alive() == 0


# A subclass of a class that is not abstract, which has its own constructor.
class Disc(shape.Circle):
    def area(self):
        return 100.0 + super().area()  # Circle's implementation: 3 * r * r

    def name(self):
        return "disc"


with Disc(1.0) as disc:
    assert disc.area() == 103.0
    assert disc.describe() == "disc: 103 units"  # name() and area() are the overrides
    assert disc.radius() == 1.0
assert alive() == 0


# Without overrides the object is an ordinary one (no director).
class Plainer(shape.Circle):
    def radius_twice(self):
        return 2 * self.radius()


with Plainer(1.0) as plainer:
    assert plainer.radius_twice() == 2.0
    assert plainer._director is None
    assert plainer.describe() == "round: 3 units"


# The protected constructors and the protected pure virtual method of Stage.
class Steps(stage.Stage):
    def step(self, mode, number):
        return f"{mode.name}{number},"

    def origin(self):
        return shape.Point(x=7.0, y=0.0)


with Steps.create_int32(3) as steps:
    assert steps.run(shape.Mode.Auto) == "Auto1,Auto2,Auto3,@7"
    assert steps.steps() == 3
with Steps.create_void() as steps:
    assert steps.run(shape.Mode.On) == "On1,On2,@7"
try:
    stage.Stage.create_void()
except TypeError as error:
    assert "itself" in str(error)
else:
    raise AssertionError("an abstract class was instantiated")


# An exception in an override reaches the call that made C++ run it; C++ gets the default
# result in the meantime, and the remaining overrides of the call are skipped.
ran = []


class Failing(shape.Shape):
    def area(self):
        ran.append("area")
        raise ValueError("area failed")

    def name(self):
        ran.append("name")
        raise KeyError("name failed")


with Failing.create_void() as failing:
    try:
        failing.describe()  # C++ runs name() and area() in the order it likes
    except (ValueError, KeyError) as error:
        # The first one that ran failed, and the other one was skipped.
        assert len(ran) == 1, ran
        assert type(error) is (ValueError if ran == ["area"] else KeyError)
    else:
        raise AssertionError("the exception was lost")
    # The next call is not poisoned by the previous one.
    ran.clear()
    try:
        failing.sum(1, 2)  # add_int32_int32 is not overridden: the class's own
    finally:
        assert ran == []
    assert failing.sum(1, 2) == 3


class WrongType(shape.Shape):
    def area(self):
        return "large"

    def quiet(self):
        return 2**40


with WrongType.create_void() as wrong:
    try:
        wrong.describe()
    except TypeError as error:
        assert "area" in str(error) and "real number" in str(error), str(error)
    else:
        raise AssertionError("a wrong result was accepted")
    try:
        wrong.how_quiet()
    except OverflowError as error:
        assert "quiet" in str(error), str(error)
    else:
        raise AssertionError("a result out of range was accepted")


# A class that has no director of its own cannot override what it inherits.
try:

    class Final(shape.Dot):
        def area(self):
            return 1.0

except TypeError as error:
    assert "no director" in str(error) and "drc_Dot" in str(error), str(error)
else:
    raise AssertionError("an override that C++ would never call was accepted")


# An override that is not a method.
class NotAMethod(shape.Shape):
    area = 5.0


try:
    NotAMethod.create_void()
except TypeError as error:
    assert "must be a method" in str(error), str(error)
else:
    raise AssertionError("a number overrode a method")


# The director lives as long as its object, and goes with it.
with Square.create_void() as square:
    assert square._director is not None
square = None
gc.collect()
assert alive() == 0
square = Square.create_void()
director = square._director
assert _runtime._callbacks.get(director.key) is director
square.close()
assert _runtime._callbacks.get(director.key) is None, "released with the director"
try:
    square.describe()
except ValueError as error:
    assert "closed" in str(error)
else:
    raise AssertionError("a closed object was used")


# An object that is collected destroys its director.
def make_and_drop():
    obj = Square.create_void()
    return obj._director.key


key = make_and_drop()
gc.collect()
assert key not in _runtime._callbacks
assert alive() == 0


# C++ owns the object: the Python object is given up, and C++ calls it for as long as it
# holds the director, which keeps it alive.
with shape.Holder() as holder:
    obj = Square.create_void()
    key = obj._director.key
    holder.put(obj)
    try:
        obj.describe()
    except ValueError:
        pass  # the Python object is closed: C++ has the object now
    else:
        raise AssertionError("an object that was given up was used")
    del obj
    gc.collect()
    assert alive() == 1 and key in _runtime._callbacks
    assert holder.describe() == "sq:shape: 4 units"  # the override of an object nobody names
    assert holder.describe_on_thread() == "sq:shape: 4 units"  # from a thread of C++
    assert holder.apply(lambda number: number * 10) == 20  # add_int32_int32(1, 2) is 2
    # It comes back as an object of the class (not of the subclass), still a director.
    back = holder.take()
    assert type(back) is shape.Shape and holder.empty()
    assert back.describe() == "sq:shape: 4 units"
    back.close()
    assert key not in _runtime._callbacks, "released with the director"
    assert alive() == 0

    # Destroying what C++ holds releases the Python object.
    obj = Square.create_void()
    key = obj._director.key
    holder.put(obj)
    del obj
    gc.collect()
    assert key in _runtime._callbacks
assert key not in _runtime._callbacks
assert alive() == 0


# An exception in an override that a call with callbacks makes C++ run goes to that call,
# whose callback is then skipped.
class Boom(shape.Shape):
    def area(self):
        return 1.0

    def add_int32_int32(self, a, b):
        raise RuntimeError("boom")


with shape.Holder() as holder:
    holder.put(Boom.create_void())
    calls = []
    try:
        holder.apply(lambda number: calls.append(number) or 0)
    except RuntimeError as error:
        assert str(error) == "boom"
    else:
        raise AssertionError("the exception was lost")
    assert calls == [], "C++ was not called back after the failure"


# On a thread of C++ no call can raise it: it goes to sys.unraisablehook, as for a callback,
# and C++ gets the default result.
class ThreadFails(shape.Shape):
    def area(self):
        raise ValueError("on a thread")

    def name(self):
        return "n"


reported = []
previous_hook = sys.unraisablehook
sys.unraisablehook = lambda unraisable: reported.append(unraisable.exc_value)
try:
    with shape.Holder() as holder:
        holder.put(ThreadFails.create_void())
        assert holder.describe_on_thread() == "n: 0 units"
finally:
    sys.unraisablehook = previous_hook
assert [str(error) for error in reported] == ["on a thread"], reported

# An object that C++ holds when the interpreter exits (the director is disabled and left
# alone, as the callbacks are).
leftover = shape.Holder()
leftover.put(Square.create_void())

print("directors: Python round trip OK")
