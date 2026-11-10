// SPDX-License-Identifier: Apache-2.0
// What the callbacks of the golden case "callbacks" do not reach: see case.toml.
//
// tests/test_csharp_roundtrip.py builds this file together with the generated project and runs
// it with CBK_LIBRARY pointing at the built library.

using System.Collections.Concurrent;
using System.Text;
using Cbk;
using F = Cbk.Cb.Functions;

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

var unobserved = new ConcurrentQueue<Exception>();
BridgefexCallbacks.Unobserved += unobserved.Enqueue;

// -- A call that fails before the C function is called drops what it registered -----------------

bool called = false;
Expect<ArgumentException>(() => F.Run(() => called = true, "\ud800"), "not valid Unicode");
Check(!called, "the callback was not called");
NoCallbacks("after an argument that failed after the callback was registered");
F.Run(() => called = true, "fine");
Check(called, "run");
Expect<BridgefexException>(() => F.Run(() => called = false, "fail"), "run failed");
NoCallbacks("after a C++ exception");
Check(called, "C++ failed before it called the callback");

// -- The exception of a callback wins over the failure of C++, which is kept with it ----------------

var winner = Expect<FormatException>(() => F.CallThenThrow(() => throw new FormatException("callback")), "callback");
var failure = winner.Data["Bridgefex.CallFailed"] as BridgefexException;
Check(failure is not null && failure.Message.Contains("after the callback"), "the failure of C++ goes with it");
Expect<BridgefexException>(() => F.CallThenThrow(() => { }), "after the callback");
var plain = Expect<FormatException>(() => F.Nest(1, i => throw new FormatException("alone")));
Check(!plain.Data.Contains("Bridgefex.CallFailed"), "nothing is added if C++ did not fail");
NoCallbacks("after the failures");

// -- C++ keeps the callback although the call fails -----------------------------------------------

Expect<BridgefexException>(() => F.KeepAndThrow(value => value + 1), "kept and failed");
Check(BridgefexCallbacks.Registered == 1, "C++ keeps the callback: " + BridgefexCallbacks.Registered);
Check(F.Kept(41) == 42, "and calls it");
F.DropKept();
NoCallbacks("after C++ lets it go");
Check(F.Kept(1) == -1, "nothing is kept");

// -- A C++ thread runs a callback after the call that gave it has returned ---------------------------

{
    int ran = 0;
    F.Start(value => { Interlocked.Add(ref ran, value); });
    Check(BridgefexCallbacks.Registered == 1, "C++ keeps it for its thread");
    F.Go();
    F.Join();
    Check(ran == 1 && unobserved.IsEmpty, "the thread ran the callback");
    NoCallbacks("after the thread ended");

    // Nobody can raise its exception: the call that gave it has returned.
    F.Start(value =>
    {
        Interlocked.Add(ref ran, 10);
        throw new FormatException("late " + value);
    });
    F.Go();
    F.Join();
    Check(ran == 11, "the thread ran the failing callback");
    Check(unobserved.Count == 1 && unobserved.TryDequeue(out var late) && late is FormatException && late.Message == "late 1", "it is reported");
    NoCallbacks("after the thread failed");
}

// -- Every kind of scalar, in both directions -----------------------------------------------------

Check(
    F.Scalars((a, b, c, d, e, f, g, h, i, j, k, l, m, n) =>
    {
        Check(a == -1 && b == 200 && c == -300 && d == 60000, "8 and 16 bits: " + (a, b, c, d));
        Check(e == -70000 && f == 4000000000u, "32 bits: " + (e, f));
        Check(g == -5000000000L && h == 18000000000000000000UL, "64 bits: " + (g, h));
        Check(i == 1.5f && j == 2.25, "floating point: " + (i, j));
        Check(k, "bool");
        Check(l == -7 && m == 7, "a C long and unsigned long: " + (l, m));
        Check(n == 123456, "size_t: " + n);
        return 42.5;
    }) == 42.5,
    "scalars");
Check(F.LongBack(value => value * 2, -5) == -10, "a C long goes and comes back");
Check(F.LongBack(value => value, int.MaxValue) == int.MaxValue, "the largest value of a 32-bit long");
Check(F.UlongBack(value => value + 1, 41) == 42, "a C unsigned long goes and comes back");
Check(!F.Flip(value => !value, true) && F.Flip(value => !value, false), "bool");
Check(F.Narrow(value => (byte)(value / 2), 500) == 250, "a narrow result");

// -- The widest delegate -----------------------------------------------------------------------

Check(
    F.Sixteen((a1, a2, a3, a4, a5, a6, a7, a8, a9, a10, a11, a12, a13, a14, a15, a16) =>
        a1 + a2 + a3 + a4 + a5 + a6 + a7 + a8 + a9 + a10 + a11 + a12 + a13 + a14 + a15 + a16
        + 1000 * (a16 - a1)) == 136 + 15000,
    "16 arguments, the last ones on the stack");

// -- Text that is not UTF-8 is an error of the callback, which the call raises ----------------------------

called = false;
Expect<DecoderFallbackException>(() => F.BadText(text => called = true));
Expect<DecoderFallbackException>(() => F.BadCText(text => called = true));
Check(!called, "the callback does not get text that is not valid");
NoCallbacks("after the invalid text");

// -- A callback that calls the function again -------------------------------------------------------------

int deepest = 0;
int Depth(int n) => F.Nest(n, m =>
{
    if (m == 0)
    {
        deepest = BridgefexCallbacks.Registered;
        return 0;
    }
    return 1 + Depth(m - 1);
});
Check(Depth(50) == 50, "recursion");
Check(deepest == 51, "a callback is held for each level: " + deepest);
NoCallbacks("after the recursion");
// An exception at the bottom crosses every level once.
int FailingDepth(int n) => F.Nest(n, m => m == 0 ? throw new FormatException("bottom") : 1 + FailingDepth(m - 1));
Expect<FormatException>(() => FailingDepth(20), "bottom");
NoCallbacks("after the exception crossed the levels");
Check(unobserved.IsEmpty, "nothing was left unobserved");

Console.WriteLine("callbacks (C# only): C# round trip OK");
