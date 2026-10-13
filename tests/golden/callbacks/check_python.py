# SPDX-License-Identifier: Apache-2.0
"""Round trip through the generated Python bindings of the "callbacks" case.

tests/test_roundtrip.py runs this file in a fresh interpreter, with the
generated package on PYTHONPATH and CBX_LIBRARY pointing at the built library.
"""

import atexit
import gc
import os
import signal
import sys
import threading


def after_disable():
    # Registered before the bindings: runs after they disable the callbacks at
    # exit. A call that passes one then fails instead of returning a default.
    try:
        events.sum(1, int)
    except RuntimeError as error:
        if "callbacks are disabled at exit" in str(error):
            return
    print("FAILED: a callback passed after exit", file=sys.stderr, flush=True)
    os._exit(1)


atexit.register(after_disable)

import cbx  # noqa: E402
from cbx import _runtime, events  # noqa: E402


def expect(exception_type, function, *args, message=None):
    try:
        function(*args)
    except exception_type as error:
        if message is not None:
            assert message in str(error), str(error)
        return error
    raise AssertionError(f"{function.__name__}{args} did not raise {exception_type.__name__}")


def registered():
    return len(_runtime._callbacks)


# Results, and the registry is empty after synchronous calls.
assert events.sum(5, lambda i: i * 2) == 20
assert events.count_if(6, lambda i: i % 3 == 0) == 2
assert events.is_empty(None) is True
assert events.is_empty(lambda: None) is False
assert events.classify(1, lambda value: events.Level.Error) is events.Level.Error
assert events.classify(1, lambda value: 2) is events.Level.Error
assert events.pick(lambda: events.Mode.Auto) is events.Mode.Auto
assert abs(events.integrate(lambda x: x, 0.0, 2.0, 4) - 2.0) < 1e-12
assert events.Timer.apply(21, lambda value: value * 2) == 42
assert registered() == 0

# Arguments: structs, const char* (None for NULL), the three string types, enums.
seen = []
assert events.walk(lambda point, name: seen.append((point, name)) or point.x) == 4.5
assert seen == [(events.Point(1.5, 2.5), "first"), (events.Point(3.0, 4.0), None)]
seen.clear()
events.shift(lambda point, mode: seen.append((point, mode)))
assert seen == [(events.Point(-1.0, 1.0), events.Mode.Auto)]
assert type(seen[0][1]) is events.Mode
seen.clear()
events.strings(lambda *texts: seen.extend(texts))
assert seen == ["a\x00b"] * 3, seen
assert registered() == 0

# Results are checked: ctypes would silently wrap them.
expect(OverflowError, events.sum, 2, lambda i: 2**40, message="result of callback 'f'")
expect(TypeError, events.sum, 2, lambda i: None, message="must be an integer")
expect(TypeError, events.count_if, 2, lambda i: 1, message="must be a bool, not int")
expect(TypeError, events.integrate, lambda x: "1", 0.0, 1.0, 1, message="real number")
expect(ValueError, events.pick, lambda: 42, message="Mode in [0, 3]")
expect(TypeError, events.classify, 1, lambda value: events.Mode.On, message="Level or int")
expect(TypeError, events.sum, 2, 5, message="argument 'f' must be callable or None")
assert registered() == 0

# The first exception of a callback is raised by the call; C++ gets defaults
# for the remaining calls, which do not run Python.
calls = []


def failing(i):
    calls.append(i)
    if i == 2:
        raise ValueError("boom")
    return i


error = expect(ValueError, events.sum, 1000, failing, message="boom")
assert calls == [0, 1, 2], calls
assert registered() == 0

# A C++ exception: Error, and the callback is released.
expect(cbx.Error, events.integrate, lambda x: x, 0.0, 1.0, 0, message="steps must be positive")
assert registered() == 0

# An empty std::function called by C++: bad_function_call.
expect(cbx.Error, events.sum, 1, None, message="function")


# Callbacks from C++ threads; an exception there is raised by the call.
hits = []
lock = threading.Lock()


def record(thread):
    with lock:
        hits.append(thread)


events.parallel(4, 25, record)
assert sorted(set(hits)) == [0, 1, 2, 3] and len(hits) == 100
expect(ZeroDivisionError, events.parallel, 3, 5, lambda thread: 1 // 0)
assert registered() == 0

# Stored callbacks: kept while C++ keeps them, released on replacement.
log = []
events.set_log_handler(lambda level, text: log.append((level, text)))
assert log == [(events.Level.Info, "set")]
assert events.log(events.Level.Debug, "x\x00y") is True
assert log[-1] == (events.Level.Debug, "x\x00y")
assert registered() == 1
events.set_log_handler(None)
assert registered() == 0
assert events.log(events.Level.Debug, "lost") is False

# An exception in a stored callback, called later: unraisable, not raised.
reported = []
previous = sys.unraisablehook
sys.unraisablehook = reported.append
try:
    events.set_log_handler(lambda level, text: 1 / 0 if text == "late" else None)
    assert events.log(events.Level.Info, "late") is True
finally:
    sys.unraisablehook = previous
    events.set_log_handler(None)
# The call that stored it is over: the error belongs to no call.
assert [type(item.exc_value) for item in reported] == [ZeroDivisionError], reported

# Objects: the callback lives as long as the C++ object.
ticks = []
timer = events.Timer(ticks.append)
timer.tick(3)
assert ticks == [0, 1, 2] and timer.ticks() == 3
assert registered() == 1
timer.set_on_tick(None)
assert registered() == 0
timer.set_on_tick(ticks.append)
timer.close()
assert registered() == 0
timer = events.Timer(ticks.append)
del timer
gc.collect()
assert registered() == 0

# A call that fails before the C call drops its registrations.
timer = events.Timer(None)
timer.close()
expect(ValueError, timer.set_on_tick, ticks.append)
assert registered() == 0

# Ctrl-C while C++ calls Python in a loop is raised by the call, also when the
# signal arrives as a callback starts (before its try). C++ cannot be
# cancelled: it finishes its loop, but its callbacks no longer run Python.
for _ in range(5):
    interrupted = []
    timer_thread = threading.Timer(0.02, os.kill, (os.getpid(), signal.SIGINT))
    timer_thread.start()
    expect(KeyboardInterrupt, events.sum, 10**6, lambda i, seen=interrupted: seen.append(i) or 1)
    timer_thread.join()
    assert 0 < len(interrupted) < 10**6, len(interrupted)
assert registered() == 0
assert isinstance(sys.unraisablehook, _runtime._InterruptForwarder)

# Beside by-reference parameters.
assert events.prefixed("ab", lambda text: len(text) if text == "ab!" else -1) == 3
each = []
timer = events.Timer(None)
assert timer.each([4, 5, 6], each.append) == 3 and each == [4, 5, 6]
timer.close()
notified = []
watcher = events.Watcher("w", events.Point(1.0, 2.0), lambda *args: notified.append(args))
watcher.notify()
assert notified == [("w", events.Point(1.0, 2.0))], notified
assert registered() == 1
watcher.close()
assert registered() == 0

# A signal raised as the call returns: C++ keeps the callback, so the registry
# keeps it too.
timer_thread = threading.Timer(0.05, os.kill, (os.getpid(), signal.SIGINT))
timer_thread.start()
expect(KeyboardInterrupt, events.store_and_sleep, lambda value: value * 2, 500)
timer_thread.join()
assert registered() == 1
assert events.fire(21) == 42
events.store_and_sleep(None, 0)
assert registered() == 0
assert events.fire(21) == -1


# Ctrl-C in a stored callback that a call without callbacks runs: raised once
# C++ returns.
def stop(level, text):
    if text == "stop":
        raise KeyboardInterrupt


events.set_log_handler(stop)
expect(KeyboardInterrupt, events.log, events.Level.Info, "stop")
assert events.log(events.Level.Info, "go") is True
events.set_log_handler(None)
assert registered() == 0

# At exit, a C++ global still holds a callback and a static destructor calls
# it: the bindings disable the callbacks first, so neither happens.
events.set_log_handler(lambda level, text: log.append((level, text)))
events.log_at_exit(True)
assert registered() == 1

print("callbacks: Python round trip OK")
