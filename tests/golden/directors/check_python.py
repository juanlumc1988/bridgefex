# SPDX-License-Identifier: Apache-2.0
"""The generated Python package of the "directors" case: Python classes that override the
virtual methods of the C++ classes, which C++ calls back."""

import gc
import signal
import sys
import threading
import time
import weakref

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


for make, expected in (
    (Incomplete.create_void, "Incomplete is abstract: it must override area"),
    (shape.Shape.create_void, "Shape cannot be instantiated itself"),
):
    try:
        make()
    except TypeError as error:
        assert expected in str(error), str(error)
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


# Circle declares add(int, int), which hides Shape::add(double, double) from its name: the
# implementation that super() runs for the hidden overload is still Shape's.
class Hiding(shape.Circle):
    def add_double_double(self, a, b):
        return super().add_double_double(a, b)

    def add(self, a, b):  # Circle's own: the only add(int, int) that Circle shows is called add
        return super().add(a, b)


# The name that Shape gives that overload does nothing in a subclass of Circle: refused.
try:

    class WrongName(shape.Circle):
        def add_int32_int32(self, a, b):
            return 0

except TypeError as error:
    assert "defines add_int32_int32" in str(error), str(error)
    assert "drc::Shape but not of drc::Circle" in str(error), str(error)
else:
    raise AssertionError("an override that C++ would never call was accepted")


with Hiding(1.0) as hiding:
    assert hiding.sum_double(2.5, 1.0) == 3.5
    assert hiding.sum(2, 1) == 1003  # Circle's
with shape.Circle(1.0) as plain_circle:
    assert plain_circle.sum_double(2.5, 1.0) == 3.5
    assert plain_circle.sum(2, 1) == 1003


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
    except ValueError as error:
        assert str(error) == "Shape object is closed", str(error)  # C++ has the object now
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


# An object that Python gave up refuses what it refuses for any other call, even the
# implementation of its class (unless one of its own overrides runs, which C++ calls).
with shape.Holder() as holder:
    given = Square.create_void()
    holder.put(given)
    try:
        given.name()  # the override of Python, which calls super().name()
    except ValueError as error:
        assert "closed" in str(error), str(error)
    else:
        raise AssertionError("an object that was given up reached C++")
    assert holder.describe() == "sq:shape: 4 units"


# C++ is running a method on the object: closing it would free what the method uses.
class Closer(shape.Shape):
    def area(self):
        return 4.0

    def scale(self, factor):
        self.close()


with Closer.create_void() as closer:
    try:
        closer.scaled_area(2.0)
    except RuntimeError as error:
        assert "cannot be closed while one of its overrides runs" in str(error), str(error)
    else:
        raise AssertionError("an object was closed under the method that C++ runs")
    assert closer.describe() == "shape: 4 units"  # it is still open and usable
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


# A subclass of Circle that overrides something: the name that Shape gives add(int, int)
# reaches the director of Circle (it used to reach that of Shape, with a handle of another type).
class Named(shape.Circle):
    def name(self):
        return "named"


with Named(1.0) as named:
    assert named.add_int32_int32(2, 1) == 1003
    assert named.add(2, 1) == 1003


# The virtual methods that C++ cannot call from Python are refused, not silently ignored.
for method, why in (
    ("fixed", "final"),
    ("by_value", "types"),
    ("add", "overloaded"),
):
    try:
        type("NoWay", (shape.Shape,), {method: lambda self, *args: 0, "area": lambda self: 1.0})
    except TypeError as error:
        assert f"defines {method}, a virtual method of drc_Shape that C++ cannot call" in str(
            error
        ), str(error)
    else:
        raise AssertionError(f"{method} ({why}) was accepted")
try:

    class NoDirector(shape.Plain):
        def value(self):
            return 1

except TypeError as error:
    assert "drc_Plain has no director" in str(error), str(error)
else:
    raise AssertionError("an override of a class without director was accepted")


# Coroutines and generators are not run by C++: refused when the object is made.
class Coroutine(shape.Shape):
    def area(self):
        return 1.0

    async def scale(self, factor):
        pass


try:
    Coroutine.create_void()
except TypeError as error:
    assert "Coroutine.scale is a coroutine or a generator function" in str(error), str(error)
else:
    raise AssertionError("a coroutine function overrode a method")


# There is no implementation of a pure virtual method to call.
class PureSuper(shape.Shape):
    def area(self):
        return super().area()


with PureSuper.create_void() as pure_super:
    try:
        pure_super.describe()
    except NotImplementedError as error:
        assert "pure virtual method" in str(error), str(error)
    else:
        raise AssertionError("the implementation of a pure virtual method was called")


# Once Python gave an object up, only the thread that runs one of its overrides can call
# the implementation of its class: another thread would use what C++ may free.
seen = []


class Probe(shape.Shape):
    def area(self):
        def other():
            try:
                self.sides()
            except ValueError:
                seen.append("closed")
            else:
                seen.append("reached")

        thread = threading.Thread(target=other)
        thread.start()
        thread.join()
        seen.append(self.sides())  # this thread runs the override: allowed
        return 1.0


with shape.Holder() as holder:
    holder.put(Probe.create_void())
    assert holder.describe() == "shape: 1 units"
assert seen == ["closed", 0], seen


# C++ releasing the director frees the Python object at once, without the collector.
gc.collect()
gc.disable()
try:
    with shape.Holder() as holder:
        released = Square.create_void()
        reference = weakref.ref(released)
        holder.put(released)
        del released
        assert reference() is not None  # C++ owns it
    assert reference() is None
finally:
    gc.enable()


# Signals at any point of an override leave nothing running: every object can be closed.
class Quick(shape.Shape):
    def area(self):
        return 1.0


def signals_stress():
    # The handler raises only while the test is inside its try, and only once each time it
    # enters it: a signal can never arrive where nothing catches it (between the end of a
    # try and the next statement, in an except clause...).
    inside = [False]
    interrupted = [0]

    def interrupt(signum, frame):
        if inside[0]:
            inside[0] = False
            interrupted[0] += 1
            raise KeyboardInterrupt

    signal.signal(signal.SIGALRM, interrupt)
    objects = [Quick.create_void() for _ in range(8)]
    deadline = time.monotonic() + 0.7
    signal.setitimer(signal.ITIMER_REAL, 0.001, 0.001)
    while True:
        try:
            inside[0] = True
            while time.monotonic() < deadline:
                for quick in objects:
                    quick.describe()
            inside[0] = False
            break
        except KeyboardInterrupt:
            pass
    signal.setitimer(signal.ITIMER_REAL, 0, 0)
    assert interrupted[0] > 0, "no signal arrived"
    for quick in objects:
        assert quick._director.running == {}
        quick.close()
    assert alive() == 0


if hasattr(signal, "setitimer"):  # not on Windows
    signals_stress()


# Containers, in both directions: lists go in and come back, each element checked.
class Lists(shape.Shape):
    def area(self):
        return 1.0

    def numbers(self, values):
        assert isinstance(values, list) and all(type(item) is int for item in values), values
        return [item * 2 for item in values]

    def total(self, values):
        return sum(values) + 1000

    def words(self, input):
        assert input == ["ab", "c\0d", "é"], input
        return [word + "?" for word in reversed(input)]

    def modes(self, wanted):
        assert wanted == [shape.Mode.On, shape.Mode.Auto] and all(
            isinstance(item, shape.Mode) for item in wanted
        )
        return (shape.Mode.Off, shape.Mode.Auto)  # a tuple will do

    def points(self, list):
        assert list == [shape.Point(1.0, 2.0)], list
        return [shape.Point(p.y, p.x) for p in list] * 2

    def flags(self, input):
        assert input == [True, False, True] and all(type(item) is bool for item in input)
        return [not item for item in input]

    def triple(self, values):
        assert values == [1.0, 2.0, 3.0], values
        return values[::-1]


with Lists.create_void() as lists:
    assert lists.call_numbers([1, 2, 3]) == [2, 4, 6]
    assert lists.call_numbers([]) == []
    assert lists.call_total([5, 6]) == 1011
    assert lists.call_words(["ab", "c\0d", "é"]) == ["é?", "c\0d?", "ab?"]
    assert lists.call_modes([shape.Mode.On, shape.Mode.Auto]) == [shape.Mode.Off, shape.Mode.Auto]
    assert lists.call_points([shape.Point(1.0, 2.0)]) == [shape.Point(2.0, 1.0)] * 2
    assert lists.call_flags([True, False, True]) == [False, True, False]
    assert lists.call_triple([1.0, 2.0, 3.0]) == [3.0, 2.0, 1.0]


class ListsAgain(shape.Shape):
    """The implementations of the class, through super(), with containers."""

    def area(self):
        return 1.0

    def numbers(self, values):
        return [*super().numbers(values), len(values)]

    def words(self, input):
        return super().words(input)[:1]

    def triple(self, values):
        return [2 * item for item in super().triple(values)]


with ListsAgain.create_void() as again:
    assert again.call_numbers([1, 2]) == [2, 1, 2]
    assert again.call_words(["a", "b"]) == ["a!"]
    assert again.call_triple([1.0, 2.0, 3.0]) == [4.0, 8.0, 12.0]

# What an override gives back that is not what the result is: the call reports it.
bad_results = {
    "numbers": ([1, 2, 3], "not a sequence", 5, TypeError),
    "numbers ": ([1], "an element of another type", [1, "x"], TypeError),
    "numbers  ": ([1], "an element out of range", [2**40], OverflowError),
    "numbers   ": ([1], "a string", "ab", TypeError),
    "words": (["a"], "a number", [1], TypeError),
    "modes": ([shape.Mode.On], "a number beyond the type", [shape.Mode.On, 300], OverflowError),
    "points": ([shape.Point(1.0, 2.0)], "a tuple", [(1.0, 2.0)], TypeError),
    "triple": ([1.0, 2.0, 3.0], "two elements for three", [1.0, 2.0], ValueError),
    "triple ": ([1.0, 2.0, 3.0], "an iterator", iter([1.0, 2.0, 3.0]), TypeError),
}
for key, (args, why, value, error) in bad_results.items():
    name = key.strip()
    cls = type("Bad", (shape.Shape,), {"area": lambda self: 1.0, name: lambda self, *a, v=value: v})
    with cls.create_void() as bad:
        try:
            getattr(bad, f"call_{name}")(args)
        except error as raised:
            assert f"override {name!r}" in str(raised) or any(
                name in note for note in raised.__notes__
            ), (
                why,
                raised,
            )
        else:
            raise AssertionError(f"{name}: {why} was accepted")
        assert bad._director.running == {}


# A container the override does not give is the empty one.
class Silent(shape.Shape):
    def area(self):
        return 1.0

    def numbers(self, values):
        return None if values else []


with Silent.create_void() as silent:
    try:
        silent.call_numbers([1])
    except TypeError as raised:
        assert "numbers" in "".join([str(raised), *raised.__notes__])
    else:
        raise AssertionError("None was accepted for a container")
    assert silent.call_numbers([]) == []


# A class with public constructors and protected ones: the protected ones make the objects of
# its subclasses only.
class Short(shape.Beam):
    def length(self):
        return 5


with Short(7) as short:  # the public constructor, with an override
    assert short.call_length() == 5 and short.made_by() == 1
with Short.create_void() as short:
    assert short.call_length() == 5 and short.made_by() == 2
with Short.create_string("four") as short:
    assert short.call_length() == 5 and short.made_by() == 3


class Plain(shape.Beam):
    """Overrides nothing: the protected constructors still make a plain object of it."""


with Plain.create_string("four") as plain_beam:
    assert plain_beam.call_length() == 4 and plain_beam.made_by() == 3
with Plain(3) as plain_beam:
    assert plain_beam.call_length() == 3 and plain_beam.made_by() == 1

for call in (shape.Beam.create_void, lambda: shape.Beam.create_string("x")):
    try:
        call()
    except TypeError as error:
        assert "itself" in str(error), str(error)
    else:
        raise AssertionError("a protected constructor was called on the class itself")
with shape.Beam(2) as beam:  # the class itself: the public constructor
    assert beam.call_length() == 2 and beam.made_by() == 1


# Objects in the signatures: lent to the override during the call (references and pointers),
# or given to it and by it (std::unique_ptr).
class Objects(shape.Shape):
    def area(self):
        return 2.0

    def peek(self, holder):
        assert isinstance(holder, shape.Holder) and holder.empty()
        self.seen = holder
        return 42

    def compare(self, other):
        self.received = other
        return other.area() * 10

    def same(self, other):
        self.received = other
        return other is self.expected

    def grow(self, beam):
        self.received = beam
        return beam.call_length() + 1000

    def adopt(self, beam):
        self.received = beam
        return beam.call_length() + 2000

    def make_beam(self, length):
        self.made = shape.Beam(length * 2)
        return self.made


class Sq(shape.Shape):
    def area(self):
        return 4.0


with Objects.create_void() as objects, shape.Holder() as holder:
    # A reference to a const object: lent for the call, and no longer valid afterwards.
    assert objects.call_peek(holder) == 42
    assert objects.seen is not holder and isinstance(objects.seen, shape.Holder)
    try:
        objects.seen.empty()
    except ValueError as error:
        assert "no longer valid" in str(error), str(error)
    else:
        raise AssertionError("an object lent to an override was used after the call")

    # An object that is a director: the override gets the Python object itself.
    with Sq.create_void() as square:
        assert objects.call_compare(square) == 40.0
        assert objects.received is square
        objects.expected = square
        assert objects.call_same(square) is True
        assert objects.received is square
    # Any other object: a new one, of the class of the parameter, lent for the call.
    with shape.Circle(1.0) as circle:
        assert objects.call_compare(circle) == 30.0
        assert type(objects.received) is shape.Shape and objects.received is not circle
        try:
            objects.received.area()
        except ValueError:
            pass
        else:
            raise AssertionError("an object lent to an override was used after the call")
    # A pointer: None is NULL; the object itself is the Python object.
    objects.expected = None
    assert objects.call_same(None) is True and objects.received is None
    objects.expected = objects
    assert objects.call_same(objects) is True and objects.received is objects

    # A reference to an object that is not const.
    with shape.Beam(5) as beam:
        assert objects.call_grow(beam) == 1005
        assert beam.call_length() == 5  # still Python's

    # A std::unique_ptr that C++ gives: the override owns the object.
    with shape.Beam(6) as beam:
        assert objects.call_adopt(beam) == 2006
        try:
            beam.call_length()
        except ValueError:
            pass
        else:
            raise AssertionError("an object that was given up was used")
    assert objects.received.call_length() == 6  # the override's now
    objects.received.close()

    # A std::unique_ptr that the override gives: C++ owns it from then on.
    result = objects.call_make_beam(3)
    assert result.call_length() == 6 and result.made_by() == 1
    try:
        objects.made.call_length()
    except ValueError:
        pass
    else:
        raise AssertionError("an object that the override gave away was used")
    result.close()


class Gives(shape.Shape):
    def area(self):
        return 1.0

    def make_beam(self, length):
        return self.what


class Short5(shape.Beam):
    def length(self):
        return 5


with Gives.create_void() as gives:
    gives.what = None
    assert gives.call_make_beam(1) is None  # a NULL pointer
    # A director object that the override gives: C++ keeps calling its overrides.
    gives.what = Short5(7)
    with gives.call_make_beam(1) as given:
        assert given.call_length() == 5
    gives.what = shape.Circle(1.0)  # not a Beam
    try:
        gives.call_make_beam(1)
    except TypeError as error:
        assert "make_beam" in str(error) and "Beam" in str(error), str(error)
    else:
        raise AssertionError("an object of another class was accepted")
    closed = shape.Beam(1)
    closed.close()
    gives.what = closed
    try:
        gives.call_make_beam(1)
    except ValueError as error:
        assert "closed" in str(error), str(error)
    else:
        raise AssertionError("a closed object was accepted")
    assert gives.call_make_beam.__name__  # still usable after the failures


# The implementations of the class, with objects.
class Plainly(shape.Shape):
    def area(self):
        return 9.0


with Plainly.create_void() as plainly, shape.Beam(4) as beam:
    assert plainly.call_compare(plainly) == 0.0
    assert plainly.call_same(plainly) is True and plainly.call_same(None) is False
    assert plainly.call_grow(beam) == 4
    with shape.Beam(8) as other:
        assert plainly.call_adopt(other) == 8
    with plainly.call_make_beam(3) as made:
        assert made.call_length() == 3

# An object that C++ holds when the interpreter exits (the director is disabled and left
# alone, as the callbacks are).
leftover = shape.Holder()
leftover.put(Square.create_void())

print("directors: Python round trip OK")
