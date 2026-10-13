# SPDX-License-Identifier: Apache-2.0
"""Round trip through the generated Python bindings of the "enums" case.

tests/test_roundtrip.py runs this file in a fresh interpreter, with the
generated package on PYTHONPATH and ENM_LIBRARY pointing at the built library.
"""

import copy
import enum
import pickle

# shapes.h comes after colors.h, but its module can be imported first: the
# header modules do not import each other.
from enm import shapes  # isort: skip
from enm import colors  # isort: skip


def expect(exception_type, function, *args, message=None):
    try:
        function(*args)
    except exception_type as error:
        if message is not None:
            assert message in str(error), str(error)
        return error
    raise AssertionError(f"{function.__name__}{args} did not raise {exception_type.__name__}")


class Other(enum.IntEnum):
    X = 1


def check_values():
    Color = colors.Color
    mixed = colors.mix(Color.Red, Color.Green)
    assert mixed == 100  # not an enumerator: an int
    assert type(mixed) is int
    assert colors.mix(Color.Max, Color.Max) is Color.Max
    assert colors.mix(0, 0) is Color.Red  # ints are accepted
    assert colors.toggle(colors.Mode.Off) is colors.Mode.On
    assert colors.toggle(7) is colors.Mode.Off
    assert colors.negate(colors.Signed.First) == 3
    assert colors.negate(colors.Signed.Last) == -2
    flags = colors.Flags
    assert colors.flip(flags.Bit31) == 2**64 - 1 - 2**31
    assert colors.flip(flags.All) is flags.Nothing
    assert colors.flip(flags.Bit31 | 1) == 2**64 - 1 - 2**31 - 1  # combinations are ints
    assert colors.plain(colors.Plain.Low) is colors.Plain.Low
    assert colors.wide(colors.Wide.Min) is colors.Wide.Min
    assert colors.small(colors.Small.Top) is colors.Small.Top
    assert colors.pick(colors.Names.mro_) is colors.Names.mro_
    assert colors.Names.None_ == 0
    assert colors.Names.name.name == "name"
    assert colors.next(41) == 42
    assert type(colors.next(5)) is int  # an enum without enumerators gives ints
    assert colors.current() is colors.Old.Current
    assert colors.Old.Alias is colors.Old.Current
    assert colors.default_shape() is shapes.Shape.Square
    assert shapes.color_of(shapes.Shape.Circle) is colors.Color.Red
    assert shapes.shape_of(colors.Color.Green) is shapes.Shape.Square
    assert colors.f_colors_Color(Color.Max) == 1255
    assert colors.f_int(7) == 7


def check_rejections():
    for value in (True, 1.0, "1", Other.X, None):
        expect(TypeError, colors.mix, value, 0)
    expect(TypeError, colors.mix, shapes.Shape.Square, 0)
    expect(OverflowError, colors.mix, 256, 0)
    expect(OverflowError, colors.mix, -1, 0)
    expect(ValueError, colors.toggle, 8, message="must be a value of Mode in [0, 7]")
    expect(ValueError, colors.negate, -5, message="[-4, 3]")
    expect(OverflowError, colors.flip, 2**64)


def check_types():
    Color = colors.Color
    assert repr(Color.Green) == "<Color.Green: 200>"
    assert Color.__module__ == "enm.colors"
    assert colors.Lamp.State.__qualname__ == "Lamp.State"
    assert colors.Lamp.State.__module__ == "enm.colors"
    for member in (Color.Green, colors.Lamp.State.Lit, shapes.Shape.Circle, colors.Mode.Auto):
        assert pickle.loads(pickle.dumps(member)) is member
        assert copy.deepcopy(member) is member
    assert "Color" in colors.__all__
    assert colors.Color is shapes.color_of(shapes.Shape.Circle).__class__
    assert "Wraps" in Color.__doc__
    assert list(colors.Id) == []


def check_lamp():
    with colors.Lamp(colors.Color.Green) as lamp:
        assert lamp.state() is colors.Lamp.State.Dark
        lamp.set(colors.Lamp.State.Lit, colors.Lamp.Level.Bright)
        assert lamp.state() is colors.Lamp.State.Lit
        assert lamp.level() is colors.Lamp.Level.Bright
        expect(ValueError, lamp.set, colors.Lamp.State.Lit, 4)
        assert lamp.color() is colors.Color.Green


def main():
    check_values()
    check_rejections()
    check_types()
    check_lamp()
    print("enums: Python round trip OK")


if __name__ == "__main__":
    main()
