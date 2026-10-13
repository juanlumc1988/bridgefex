# SPDX-License-Identifier: Apache-2.0
"""Round trip through the generated Python bindings of the "structs" case.

tests/test_roundtrip.py runs this file in a fresh interpreter, with the
generated package on PYTHONPATH and VALS_LIBRARY pointing at the built library.
"""

import copy
import ctypes
import dataclasses
import pickle

from vals import _types, geo, route


def expect(exception_type, function, *args, message=None):
    try:
        function(*args)
    except exception_type as error:
        if message is not None:
            assert message in str(error), str(error)
        return error
    raise AssertionError(f"{function.__name__}{args} did not raise {exception_type.__name__}")


def check_values():
    Point = geo.Point
    assert geo.mid(Point(1, 2), Point(3, 6)) == Point(2.0, 4.0)
    assert geo.mid(Point(x=1, y=2), Point(3.0, 6.0)).y == 4.0
    msg = geo.Msg(
        tag=8,
        where=Point(1.5, 0),
        samples=[1, 2, 123],
        ok=True,
        path=[Point(0, 0), Point(0, 9.5)],
        quality=geo.Quality.Best,
    )
    echoed = geo.echo(msg)
    assert echoed == msg, (echoed, msg)
    assert echoed is not msg
    assert type(echoed.samples) is list
    assert echoed.quality is geo.Quality.Best
    shifted = geo.shift(msg, 10)
    assert shifted.samples == [11, 12, 133]
    assert shifted.tag == 9
    assert msg.samples == [1, 2, 123]  # a copy
    wide = geo.widen(geo.Wide(f=3.0, big=21, size=7, l=-1, c=-5, bytes=b"\x00\x01\x02\xff"))
    assert wide.big == 42
    assert wide.bytes == [0, 1, 2, 255]
    assert geo.area_geo_Wide(wide) == 3
    assert geo.area_geo_Point(Point(3, 6)) == 18
    names = geo.rename(geo.Names(from_=1, restrict_=2, errno_=3))
    assert names == geo.Names(2, 2, 3)
    tagged = geo.toggle(geo.Tagged(geo.Tagged.Mode.Off, 5))
    assert tagged.mode is geo.Tagged.Mode.On
    leg = route.Leg(Point(0, 0), Point(3, 4), geo.Kind.Plain)
    assert route.length(leg) == 5.0
    assert route.reverse(leg).from_ == Point(3, 4)
    assert route.start(leg) == Point(0, 0)
    with geo.Path(Point(1, 1)) as path:
        path.add(Point(2, 3))
        assert path.last() == Point(2, 3)
    assert geo.Path.origin() == Point(0, 0)


def check_defaults():
    config = geo.Config()
    assert config == geo.Config(115200, True, geo.Kind.Marked), config
    assert geo.Config(baud=9600).parity is True
    assert geo.configure(geo.Config(baud=100)).baud == 200
    assert geo.Msg().tag == 7
    assert repr(geo.Point.__dataclass_fields__["x"].default) == "<C++ default>"


def check_rejections():
    Point = geo.Point
    msg = geo.Msg()
    expect(TypeError, geo.mid, (1, 2), Point(0, 0), message="argument 'a' must be Point, not tuple")
    expect(OverflowError, geo.echo, dataclasses.replace(msg, tag=300), message="'m.tag'")
    bad = dataclasses.replace(msg, samples=[1, 70000, 3])
    expect(OverflowError, geo.echo, bad, message="'m.samples[1]'")
    expect(ValueError, geo.echo, dataclasses.replace(msg, samples=[1, 2]), message="3 elements")
    for samples in ({1, 2, 3}, iter([1, 2, 3]), "abc", b"abc"):
        expect(TypeError, geo.echo, dataclasses.replace(msg, samples=samples))
    path = [Point(0, 0), Point("x", 0)]
    expect(TypeError, geo.echo, dataclasses.replace(msg, path=path), message="'m.path[1].x'")
    expect(TypeError, geo.echo, dataclasses.replace(msg, ok=2), message="'m.ok'")
    expect(ValueError, geo.echo, dataclasses.replace(msg, quality=4), message="'m.quality'")
    try:
        msg.tga = 1
    except AttributeError:
        pass
    else:
        raise AssertionError("slots must refuse unknown attributes")


def check_types():
    point = geo.Point(1, 2)
    assert repr(point) == "Point(x=1, y=2)"
    assert pickle.loads(pickle.dumps(point)) == point
    assert copy.deepcopy(geo.Msg()) == geo.Msg()
    assert dataclasses.asdict(route.Leg(point, point, geo.Kind.Plain))["to"] == {"x": 1, "y": 2}
    assert geo.Point.__module__ == "vals.geo"
    assert geo.Tagged.Mode.__qualname__ == "Tagged.Mode"
    assert route.Leg.__module__ == "vals.route"
    assert geo.Point.__hash__ is None


def check_mirrors():
    assert ctypes.sizeof(_types._geo_Point) == 16


def main():
    check_values()
    check_defaults()
    check_rejections()
    check_types()
    check_mirrors()
    print("structs: Python round trip OK")


if __name__ == "__main__":
    main()
