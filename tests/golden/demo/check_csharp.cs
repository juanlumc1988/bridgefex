// SPDX-License-Identifier: Apache-2.0
// Round trip through the generated C# bindings of the "demo" case.
//
// tests/test_csharp_roundtrip.py builds this file together with the generated project and runs
// it with DEMO_LIBRARY pointing at the built library.

using System.Collections.Concurrent;
using System.Runtime.CompilerServices;
using Demo;
using Counter = Demo.Counter.Counter;
using Circle = Demo.Geometry.Circle;

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

static void Collect()
{
    GC.Collect();
    GC.WaitForPendingFinalizers();
    GC.Collect();
}

// -- Counter -----------------------------------------------------------------------------------

Check(Counter.LiveInstances() == 0, "no counter yet");
var first = new Counter();
Check(first.Value() == 0 && first.IsZero(), "a new counter is zero");
var second = new Counter(41);
second.Increment();
Check(second.Value() == 42 && Counter.LiveInstances() == 2, "increment, two counters");

// Overloads have the name of the method and the suffix of the C API.
second.AddInt32(-2);
second.AddDouble(0.6);
Check(second.Value() == 41, "overloads");

// Exceptions become BridgefexException, with the status and the message of C++.
var error = Expect<BridgefexException>(() => second.SetLimit(-1), "negative");
Check(error.Status == BridgefexException.Exception, "status of std::exception");
Check(error.StatusName == "ERROR_EXCEPTION", "name of the status");
Check(new BridgefexException(BridgefexException.NullArgument, "").StatusName == "ERROR_NULL_ARGUMENT", "status 3");
Check(new BridgefexException(BridgefexException.InvalidArgument, "").StatusName == "ERROR_INVALID_ARGUMENT", "status 5");
Check(new BridgefexException(99, "").StatusName == "UNKNOWN_STATUS_99", "a status that is not known");
second.SetLimit(41);
Check(second.Limit() == 41, "limit");
Expect<BridgefexException>(() => second.Increment(), "limit reached");
error = Expect<BridgefexException>(() => second.Fail());
Check(error.Status == BridgefexException.UnknownException, "status of an unknown exception");
Check(error.StatusName == "ERROR_UNKNOWN_EXCEPTION", "name of the status 2");
error = Expect<BridgefexException>(() => second.Reserve(42));
Check(error.Status == BridgefexException.OutOfMemory, "status of a bad_alloc");
Check(error.StatusName == "ERROR_OUT_OF_MEMORY", "name of the status 4");
second.Reserve(41);

// The extremes of the types work (and the C++ value stays in range).
second.AddInt32(int.MinValue + 1);
second.AddInt32(int.MaxValue);
Check(second.Value() == 41, "extremes");

// Lifetime: Close is idempotent, a closed object cannot be used, and the collector destroys
// the objects that were forgotten.
Check(!second.IsClosed, "open");
second.Close();
second.Dispose();
Check(second.IsClosed, "closed");
Expect<ObjectDisposedException>(() => second.Value(), "closed");
Check(Counter.LiveInstances() == 1, "one counter left");
first.Close();
// The object is forgotten in another method: the unoptimized code of this one would keep
// its reference in a slot of the stack.
[MethodImpl(MethodImplOptions.NoInlining)]
static void Forget()
{
    var forgotten = new Counter(7);
    Check(forgotten.Value() == 7 && Counter.LiveInstances() == 1, "forgotten");
}
Forget();
Collect();
Check(Counter.LiveInstances() == 0, "a collected object is destroyed");
using (var scoped = new Counter(5))
{
    Check(scoped.Value() == 5 && Counter.LiveInstances() == 1, "using");
}
Check(Counter.LiveInstances() == 0, "using disposes");

Check(Demo.Counter.Functions.MultiplyInt32Int32(-3, 1 << 30) == -3L * (1 << 30), "free function");

// -- Geometry ----------------------------------------------------------------------------------

using (var circle = new Circle(2.0))
{
    Check(circle.Radius() == 2.0, "radius");
    Check(Math.Abs(circle.Area() - Math.PI * 4.0) < 1e-12, "area");
    circle.Scale(1.5);
    Check(circle.Radius() == 3.0, "scale");
    Expect<BridgefexException>(() => circle.Scale(0.0), "positive");
}
// A constructor that throws reports the error and creates nothing.
Expect<BridgefexException>(() => new Circle(-1.0), "radius");
Check(Demo.Geometry.Functions.Distance(0.0, 0.0, 3.0, 4.0) == 5.0, "distance");
Check(Demo.Geometry.Functions.MultiplyDoubleDouble(1.5, 4) == 6.0, "multiply");

// -- Threads: each one reads the message of its own last failure -------------------------------

var failures = new ConcurrentBag<string>();
var threads = new List<Thread>();
for (int number = 0; number < 8; number++)
{
    int mine = number;
    var thread = new Thread(() =>
    {
        using var circle = new Circle(1.0);
        for (int turn = 0; turn < 200; turn++)
        {
            try
            {
                if (mine % 2 == 1)
                {
                    circle.Scale(-1.0);
                }
                else
                {
                    using var counter = new Counter();
                    counter.SetLimit(-1);
                }
                failures.Add("no error");
            }
            catch (BridgefexException failure)
            {
                string wanted = mine % 2 == 1 ? "positive" : "negative";
                if (!failure.Message.Contains(wanted))
                {
                    failures.Add(failure.Message);
                }
            }
        }
    });
    threads.Add(thread);
    thread.Start();
}
foreach (var thread in threads)
{
    thread.Join();
}
Check(failures.IsEmpty, "threads: " + string.Join("; ", failures.Take(3)));

// Many threads use one object while another one closes it: each call either runs or says the
// object is closed, and nothing is destroyed under a running call.
for (int round = 0; round < 20; round++)
{
    var shared = new Counter();
    var users = new List<Thread>();
    var unexpected = new ConcurrentBag<string>();
    for (int user = 0; user < 4; user++)
    {
        var thread = new Thread(() =>
        {
            for (int turn = 0; turn < 500; turn++)
            {
                try
                {
                    shared.Value();
                }
                catch (ObjectDisposedException)
                {
                    return;
                }
                catch (Exception failure)
                {
                    unexpected.Add(failure.ToString());
                    return;
                }
            }
        });
        users.Add(thread);
        thread.Start();
    }
    shared.Close();
    foreach (var thread in users)
    {
        thread.Join();
    }
    Check(unexpected.IsEmpty, "close while in use: " + string.Join("; ", unexpected.Take(1)));
}
Check(Counter.LiveInstances() == 0, "nothing leaks");

Console.WriteLine("demo: C# round trip OK");
