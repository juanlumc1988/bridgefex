// SPDX-License-Identifier: Apache-2.0
// Round trip through the generated C# bindings of the "callbacks" case: a std::function goes in as
// a Func or an Action (or null), and C++ calls it, on any thread, while the call runs or later.
//
// tests/test_csharp_roundtrip.py builds this file together with the generated project and runs
// it with CBX_LIBRARY pointing at the built library.

using System.Collections.Concurrent;
using System.Runtime.CompilerServices;
using Cbx;
using Cbx.Events;
using F = Cbx.Events.Functions;
using Timer = Cbx.Events.Timer;

// A callback that C++ keeps at exit (see the end of the file) must not be called when the process
// ends, and one that is given then is refused. This runs after the one of the bindings, which
// subscribed when the first callback was given.
var unobserved = new ConcurrentQueue<Exception>();
BridgefexCallbacks.Unobserved += unobserved.Enqueue;
F.Sum(1, i => i);
AppDomain.CurrentDomain.ProcessExit += (_, _) =>
{
    try
    {
        F.Sum(1, i => i);
    }
    catch (InvalidOperationException error) when (error.Message.Contains("disabled at exit"))
    {
        return;
    }
    catch (Exception error)
    {
        Console.Error.WriteLine("FAILED: " + error);
    }
    Console.Error.WriteLine("FAILED: a callback given after exit was accepted");
    Environment.ExitCode = 1;
};

static void Check(bool condition, string what)
{
    if (!condition)
    {
        throw new Exception("check failed: " + what);
    }
}

static T Expect<T>(Action action, string? message = null) where T : Exception
{
    try
    {
        action();
    }
    catch (T error)
    {
        if (message is not null && !error.Message.Contains(message))
        {
            throw new Exception($"'{error.Message}' lacks '{message}'");
        }
        return error;
    }
    throw new Exception($"no {typeof(T).Name}");
}

static void NoCallbacks(string when)
{
    Check(BridgefexCallbacks.Registered == 0, $"C++ holds no callback {when}, not {BridgefexCallbacks.Registered}");
}

// -- Results, and the registry is empty after synchronous calls --------------------------------

Check(F.Sum(5, i => i * 2) == 20, "sum");
Check(F.CountIf(6, i => i % 3 == 0) == 2, "count_if: a bool result and a 64-bit argument");
Check(F.IsEmpty(null), "null is an empty std::function");
Check(!F.IsEmpty(() => { }), "an Action without arguments");
Check(F.Classify(1, value => Level.Error) == Level.Error, "an enum result");
Check(F.Pick(() => Mode.Auto) == Mode.Auto, "an enum without a fixed type");
Check(Math.Abs(F.Integrate(x => x, 0.0, 2.0, 4) - 2.0) < 1e-12, "a double");
Check(Timer.Apply(21, value => value * 2) == 42, "a static method");
// A method group and a local function are delegates too.
Check(F.Sum(4, Math.Abs) == 6, "a method group");
NoCallbacks("after synchronous calls");

// -- Arguments: structs, const char* (null for NULL), the three string types, enums --------------

var seen = new List<(Point, string?)>();
Check(F.Walk((point, name) => { seen.Add((point, name)); return point.X; }) == 4.5, "walk");
Check(seen.Count == 2, "walk calls twice");
Check(seen[0].Item1.X == 1.5 && seen[0].Item1.Y == 2.5 && seen[0].Item2 == "first", "a struct and a string");
Check(seen[1].Item1.X == 3.0 && seen[1].Item1.Y == 4.0 && seen[1].Item2 is null, "a NULL const char* is null");

var shifted = new List<(Point, Mode)>();
F.Shift((point, mode) => shifted.Add((point, mode)));
Check(shifted.Count == 1 && shifted[0].Item1.X == -1.0 && shifted[0].Item1.Y == 1.0 && shifted[0].Item2 == Mode.Auto, "shift");

var texts = new List<string>();
F.Strings((a, b, c) => { texts.Add(a); texts.Add(b); texts.Add(c); });
Check(texts.SequenceEqual(new[] { "a\0b", "a\0b", "a\0b" }), "string, std::string and std::string_view keep a NUL");
NoCallbacks("after the callbacks with arguments");

// -- A result that C++ cannot hold is refused, and the call raises it ---------------------------------

Expect<InvalidOperationException>(() => F.Pick(() => (Mode)42), "which is not a value of the enum (0..3)");
Expect<InvalidOperationException>(() => F.Pick(() => (Mode)(-1)), "returned -1");
Check(F.Classify(1, value => (Level)200) == (Level)200, "an enum with a fixed type holds every value of it");
NoCallbacks("after the refused results");

// -- The first exception of a callback is the one the call raises; C++ gets defaults for the rest -----

var calls = new List<int>();
var boom = new InvalidOperationException("boom");
int Failing(int i)
{
    calls.Add(i);
    if (i == 2)
    {
        throw boom;
    }
    return i;
}
var raised = Expect<InvalidOperationException>(() => F.Sum(1000, Failing), "boom");
Check(ReferenceEquals(raised, boom), "it is the exception of the callback, not a copy");
Check(calls.SequenceEqual(new[] { 0, 1, 2 }), "the callbacks after the failure do not run: " + string.Join(",", calls));
Check(raised.StackTrace!.Contains("Failing"), "the stack trace is that of the callback");
NoCallbacks("after a callback that throws");
// The next call is not affected.
Check(F.Sum(3, i => i) == 3, "the scope of the failed call is over");

// Of two callbacks that fail, the call raises the first.
var twice = Expect<ArgumentException>(
    () => F.Sum(5, i =>
    {
        if (i == 0)
        {
            throw new ArgumentException("first");
        }
        throw new InvalidOperationException("second");
    }),
    "first");
Check(twice.Message == "first", "the first one");

// An exception in a call that a callback makes is raised by that call, and the callback may catch it.
Check(
    F.Sum(2, i =>
    {
        try
        {
            F.Sum(1, _ => throw new FormatException("inner"));
            return -100;
        }
        catch (FormatException)
        {
            return i;
        }
    }) == 1,
    "an inner call raises its own callbacks' exceptions");
Check((int)F.Sum(3, i => (int)F.Sum(i, j => j)) == 1, "a call inside a callback");
// ...and if the callback does not catch it, it is the exception of the outer callback.
Expect<FormatException>(() => F.Sum(2, i => (int)F.Sum(1, _ => throw new FormatException("inner"))), "inner");
NoCallbacks("after nested calls");

// A C++ exception: BridgefexException, and the callback is released.
Expect<BridgefexException>(() => F.Integrate(x => x, 0.0, 1.0, 0), "steps must be positive");
NoCallbacks("after a C++ exception");

// An empty std::function called by C++: bad_function_call.
Expect<BridgefexException>(() => F.Sum(1, null), "function");

// -- Callbacks from C++ threads; an exception there is raised by the call that gave the callback ------

var hits = new ConcurrentQueue<int>();
F.Parallel(4, 25, thread => hits.Enqueue(thread));
Check(hits.Count == 100 && hits.Distinct().OrderBy(x => x).SequenceEqual(new[] { 0, 1, 2, 3 }), "parallel");
Expect<DivideByZeroException>(() => F.Parallel(3, 5, thread => throw new DivideByZeroException()));
int threadCalls = 0;
Expect<DivideByZeroException>(() => F.Parallel(1, 25, thread => { threadCalls++; throw new DivideByZeroException(); }));
Check(threadCalls == 1, "the callbacks after the failure do not run, also on a C++ thread: " + threadCalls);
NoCallbacks("after the callbacks of C++ threads");
Check(unobserved.IsEmpty, "an exception of a callback that a call raised is not also unobserved");

// -- Stored callbacks: kept while C++ keeps them, released on replacement ----------------------------

var log = new List<(Level, string)>();
F.SetLogHandler((level, text) => log.Add((level, text)));
Check(log.Count == 1 && log[0] == (Level.Info, "set"), "the handler is called when it is stored");
Check(F.Log(Level.Debug, "x\0y"), "log");
Check(log[^1] == (Level.Debug, "x\0y"), "a NUL in a string argument");
Check(BridgefexCallbacks.Registered == 1, "C++ holds the handler");
F.SetLogHandler(null);
NoCallbacks("after replacing the handler");
Check(!F.Log(Level.Debug, "lost"), "no handler");

// What C++ holds is alive for the garbage collector, and is free once C++ lets it go.
static WeakReference Store(List<string> sink)
{
    var captured = new object();
    F.SetLogHandler((level, text) =>
    {
        GC.KeepAlive(captured);
        sink.Add(text);
    });
    return new WeakReference(captured);
}
var sink = new List<string>();
WeakReference weak = Store(sink);
GC.Collect();
GC.WaitForPendingFinalizers();
GC.Collect();
Check(weak.IsAlive, "C++ keeps the handler, which keeps what it captures");
Check(F.Log(Level.Info, "still"), "the handler survived a collection");
Check(sink[^1] == "still", "and runs");
F.SetLogHandler(null);
GC.Collect();
GC.WaitForPendingFinalizers();
GC.Collect();
Check(!weak.IsAlive, "once released, what it captured can be collected");
NoCallbacks("after the collection");

// -- An exception in a stored callback, called later, belongs to no call -----------------------------

F.SetLogHandler((level, text) =>
{
    if (text == "late")
    {
        throw new KeyNotFoundException("late");
    }
});
Check(F.Log(Level.Info, "late"), "a call whose stored callback fails returns normally");
Check(unobserved.Count == 1 && unobserved.TryDequeue(out var late) && late is KeyNotFoundException, "it is unobserved");
Check(F.Log(Level.Info, "fine"), "the handler goes on");
Check(unobserved.IsEmpty, "an exception is reported once");
F.SetLogHandler(null);

// -- Objects: the callback lives as long as the C++ object ------------------------------------------

var ticks = new List<int>();
var timer = new Timer(ticks.Add);
timer.Tick(3);
Check(ticks.SequenceEqual(new[] { 0, 1, 2 }) && timer.Ticks() == 3, "a timer");
Check(BridgefexCallbacks.Registered == 1, "the timer holds its callback");
timer.SetOnTick(null);
NoCallbacks("after SetOnTick(null)");
timer.SetOnTick(ticks.Add);
timer.Dispose();
NoCallbacks("after Dispose");

[MethodImpl(MethodImplOptions.NoInlining)]
static void Abandon(List<int> sink)
{
    var abandoned = new Timer(sink.Add);
    abandoned.Tick(1);
}
Abandon(ticks);
GC.Collect();
GC.WaitForPendingFinalizers();
GC.Collect();
NoCallbacks("after the finalizer of an object");

// The callback of a timer is stored: the call that runs it later is not the call that gave it, so
// the exception of the callback belongs to no call (every tick is reported, and C++ goes on).
timer = new Timer(value => throw new FormatException("tick " + value));
timer.Tick(5);
Check(timer.Ticks() == 5, "the loop of C++ ran to its end: " + timer.Ticks());
Check(
    unobserved.Select(error => error.Message).SequenceEqual(new[] { "tick 0", "tick 1", "tick 2", "tick 3", "tick 4" }),
    "unobserved: " + string.Join(", ", unobserved.Select(error => error.Message)));
unobserved.Clear();
timer.SetOnTick(null);
timer.Tick(2);
Check(timer.Ticks() == 7 && unobserved.IsEmpty, "the object is usable after the exceptions");
timer.Dispose();

// A call that fails before the C call drops what it registered.
timer = new Timer(null);
timer.Dispose();
Expect<ObjectDisposedException>(() => timer.SetOnTick(ticks.Add));
NoCallbacks("after a call that failed before the C function");

// -- Beside by-reference parameters -----------------------------------------------------------------

Check(F.Prefixed("ab", text => text == "ab!" ? text.Length : -1) == 3, "a std::string argument and a callback");
var each = new List<int>();
using (var plain = new Timer(null))
{
    Check(plain.Each(new[] { 4, 5, 6 }, each.Add) == 3 && each.SequenceEqual(new[] { 4, 5, 6 }), "a span and a callback");
}
var notified = new List<(string, Point)>();
using (var watcher = new Watcher("w", new Point { X = 1.0, Y = 2.0 }, (name, at) => notified.Add((name, at))))
{
    watcher.Notify();
    Check(notified.Count == 1 && notified[0].Item1 == "w" && notified[0].Item2.X == 1.0 && notified[0].Item2.Y == 2.0, "a watcher");
    Check(BridgefexCallbacks.Registered == 1, "the watcher holds its callback");
}
NoCallbacks("after the watcher");

// -- A callback that C++ keeps, in a call that has returned ---------------------------------------------

F.StoreAndSleep(value => value * 2, 10);
Check(BridgefexCallbacks.Registered == 1, "C++ keeps the handler after the call");
Check(F.Fire(21) == 42, "fire");
F.StoreAndSleep(null, 0);
NoCallbacks("after storing null");
Check(F.Fire(21) == -1, "no handler");

// A stored callback that an unrelated call runs on another thread, while the call that stored it
// still runs: the exception is not the exception of that call (which returns normally and whose
// callback goes on), and the call that ran it gave no callbacks, so it is unobserved. The
// callbacks of C++ threads (Parallel above) do belong to the call that gave them.
{
    var failures = new ConcurrentQueue<int>();
    int Late(int value)
    {
        failures.Enqueue(value);
        throw new FormatException("late " + value);
    }
    string outcome = "";
    var storing = new Thread(() =>
    {
        try
        {
            F.StoreAndSleep(Late, 500);
            outcome = "returned";
        }
        catch (Exception error)
        {
            outcome = error.GetType().Name;
        }
    });
    storing.Start();
    var deadline = DateTime.UtcNow.AddSeconds(30);
    while (failures.IsEmpty)
    {
        Check(DateTime.UtcNow < deadline, "the callback was not stored");
        F.Fire(1);
        Thread.Sleep(5);
    }
    int count = failures.Count;
    F.Fire(2);  // not skipped, as it would be if the storing call had taken the error
    Check(failures.Count == count + 1, "the callback of the storing call goes on");
    storing.Join();
    Check(outcome == "returned", "the storing call returned normally: " + outcome);
    Check(unobserved.Count == failures.Count && unobserved.All(error => error is FormatException), "unobserved: " + unobserved.Count + " of " + failures.Count);
    unobserved.Clear();
    F.StoreAndSleep(null, 0);
    NoCallbacks("after the thread");
}

// A stored callback that fails in a call that is itself inside a call with callbacks: that call raises it.
F.SetLogHandler((level, text) =>
{
    if (text == "inner")
    {
        throw new FormatException("from the log");
    }
});
Expect<FormatException>(() => F.Sum(2, i => F.Log(Level.Info, "inner") ? 1 : 0), "from the log");
F.SetLogHandler(null);
NoCallbacks("after the stored callback");
Check(unobserved.IsEmpty, "it was raised, not reported");

// -- Many threads give callbacks at once ---------------------------------------------------------------

var errors = new ConcurrentBag<string>();
var workers = Enumerable.Range(0, 8).Select(number => new Thread(() =>
{
    for (int turn = 0; turn < 300; turn++)
    {
        int factor = number + 1;
        if (F.Sum(10, i => i * factor) != 45L * factor)
        {
            errors.Add($"sum {number} {turn}");
        }
        if (Timer.Apply(turn, value => value + number) != turn + number)
        {
            errors.Add($"apply {number} {turn}");
        }
    }
    try
    {
        F.Sum(3, i => throw new FormatException(number.ToString()));
        errors.Add($"no exception in thread {number}");
    }
    catch (FormatException error) when (error.Message == number.ToString())
    {
    }
})).ToList();
workers.ForEach(thread => thread.Start());
workers.ForEach(thread => thread.Join());
Check(errors.IsEmpty, "threads: " + string.Join("; ", errors.Take(3)));
NoCallbacks("after the threads");
for (int turn = 0; turn < 20000; turn++)
{
    F.Sum(1, i => 1);
}
NoCallbacks("after many calls");
Check(unobserved.IsEmpty, "nothing was left unobserved");

// -- At exit, a C++ global still holds a callback and a static destructor calls it: the bindings
// disable the callbacks first (see the top of the file), so it is neither called nor released.
F.SetLogHandler((level, text) =>
{
    if (text == "exit")
    {
        // The static destructor of C++ calls the handler with "exit": that must not happen.
        Environment.FailFast("a callback was called at exit");
    }
    log.Add((level, text));
});
F.LogAtExit(true);
Check(BridgefexCallbacks.Registered == 1, "the handler stays at exit");

Console.WriteLine("callbacks: C# round trip OK");
