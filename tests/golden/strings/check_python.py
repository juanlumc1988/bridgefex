# SPDX-License-Identifier: Apache-2.0
"""Round trip through the generated Python bindings of the "strings" case.

tests/test_roundtrip.py runs this file in a fresh interpreter, with the
generated package on PYTHONPATH and TXT_LIBRARY pointing at the built library.
"""

import array
import ctypes
import enum
import signal
import sys

import txt
from txt import _runtime, text, views


def expect(exception_type, function, *args, status=None, message=None):
    try:
        function(*args)
    except exception_type as error:
        if status is not None:
            assert error.status == status, (error.status, error)
        if message is not None:
            assert message in str(error), str(error)
        return error
    raise AssertionError(f"{function.__name__}{args} did not raise {exception_type.__name__}")


class LyingBytes(bytes):
    def __len__(self):
        return 100000


class Shouting(str):
    def encode(self, *args, **kwargs):
        raise AssertionError("the bindings must not call a subclass's encode()")


class Color(enum.StrEnum):
    RED = "red"


def check_inputs():
    assert text.echo("héllo") == "héllo"
    assert text.echo(b"a\x00b") == "a\x00b"
    assert text.echo(bytearray(b"xyz")) == "xyz"
    assert text.echo(memoryview(b"0123456789")[2:5]) == "234"
    assert text.echo(memoryview(b"abcdef")[::2]) == "ace"  # not contiguous
    assert text.echo(array.array("B", b"arr")) == "arr"
    assert text.echo(LyingBytes(b"abc")) == "abc"
    assert text.echo(Shouting("shout")) == "shout"
    assert text.echo(Color.RED) == "red"
    assert text.echo("") == ""
    assert text.count_string("a\x00b") == 3
    assert text.count_cstr("abc") == 1003
    for value in (None, 5, 1.5, ["a"]):
        expect(TypeError, text.echo, value)
        expect(TypeError, text.count_cstr, value)
    expect(TypeError, text.echo, array.array("i", [1]), message="bytes-like object of bytes")
    expect(UnicodeEncodeError, text.echo, "\udcff")
    expect(ValueError, text.count_cstr, "a\x00b", message="must not contain NUL")
    assert text.length("abc", 7) == 3007
    assert text.total("a", "bc", "def") == 123
    assert text.total(from_="a", x_="bc", arg3="def") == 123
    assert text.alias("x") == "x!"
    assert text.c_alias("y") == "y"


def check_results():
    assert text.maybe_null(True) is None
    assert text.maybe_null(False) == "not null"
    assert text.first_char("x" * 100) == "x" * 100
    assert text.concat("a\x00", "\x00b") == "a\x00\x00b"
    error = expect(UnicodeDecodeError, text.binary)
    assert error.object == b"\x00\xffx", error.object
    expect(txt.Error, text.fail, "boom é", status=txt.ERROR_EXCEPTION, message="boom é")
    assert views.first_word("hello world") == "hello"
    assert views.size_of("abc") == 3
    assert views.empty() == ""
    assert views.literal() == "a literal"
    # Results larger than a chunk of ctypes.string_at.
    chunk = _runtime._STRING_AT_CHUNK
    _runtime._STRING_AT_CHUNK = 4
    try:
        for size in range(14):
            data = bytes(range(65, 65 + size))
            assert text.echo(data) == data.decode(), size
    finally:
        _runtime._STRING_AT_CHUNK = chunk


def check_person():
    expect(TypeError, text.Person)
    with text.Person.create_cstr_int("Ada", 36) as person:
        assert person.name() == "Ada"
        assert person.age() == 36
        person.set_name("Grace")
        assert person.name_ref() == "Grace"
        assert person.c_name() == "Grace"
        assert person.greet("Bob", "Hi") == "Hi Bob, I am Grace"
        person.set_name(b"raw\x00nul")
        assert person.name() == "raw\x00nul"
        assert person.c_name() == "raw"  # stops at the NUL
        expect(ValueError, person.greet, "Bob", "a\x00b")
    with text.Person.create_string("Eve") as person:
        assert person.age() == 0
    assert text.Person.species() == "human"


def _heap_in_use():
    """Bytes allocated with malloc (glibc), or None elsewhere."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        libc = ctypes.CDLL(None)
        mallinfo2 = libc.mallinfo2
    except AttributeError:
        return None  # not glibc, or glibc older than 2.33

    class Info(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_size_t)
            for name in (
                "arena ordblks smblks hblks hblkhd usmblks fsmblks uordblks fordblks keepcost"
            ).split()
        ]

    mallinfo2.restype = Info
    info = mallinfo2()
    return info.uordblks + info.hblkhd


def _interrupt(signum, frame):
    raise KeyboardInterrupt(signum)


def check_interrupted_calls_do_not_leak():
    """Two signals arrive during a call that returns a string: the first raises
    KeyboardInterrupt right after the call, the second at the next chance. The
    result must still be released."""
    if _heap_in_use() is None or not hasattr(signal, "SIGUSR1"):
        return
    previous = signal.signal(signal.SIGUSR1, _interrupt)
    size, calls, interrupted = 1 << 16, 200, 0
    try:
        before = _heap_in_use()
        for _ in range(calls):
            try:
                try:
                    text.signal_twice(size)
                except KeyboardInterrupt:
                    interrupted += 1
            except KeyboardInterrupt:
                interrupted += 1
        leaked = _heap_in_use() - before
    finally:
        signal.signal(signal.SIGUSR1, previous)
    assert interrupted >= calls, interrupted
    # A leak would keep calls * size = 12.5 MiB.
    assert leaked < calls * size // 8, leaked


def main():
    check_inputs()
    check_results()
    check_person()
    check_interrupted_calls_do_not_leak()
    print("strings: Python round trip OK")


if __name__ == "__main__":
    main()
