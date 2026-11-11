// SPDX-License-Identifier: Apache-2.0
// The containers that C++ gives to an override and takes from it: see case.toml.
//
// tests/test_csharp_roundtrip.py builds this file together with the generated project and runs
// it with DCO_LIBRARY pointing at the built library.

using System.Collections.Concurrent;
using System.Text;
using Dco;
using Color = Dco.Dco.Color;
using Mode = Dco.Dco.Mode;
using Point = Dco.Dco.Point;
using Source = Dco.Dco.Source;

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

static long Growth(Action action, int turns)
{
    GC.Collect();
    long before = Environment.WorkingSet;
    for (int turn = 0; turn < turns; turn++)
    {
        action();
    }
    GC.Collect();
    return (Environment.WorkingSet - before) >> 20;
}

var unobserved = new ConcurrentQueue<Exception>();
BridgefexCallbacks.Unobserved += unobserved.Enqueue;

// -- Without overrides, C++ does it all --------------------------------------------------------------

using (var plain = new Source())
{
    Check(plain.CallTotal(new[] { 1, 2, 3 }) == 6, "total");
    Check(plain.CallDoubled(new[] { 1, 2, 3 }).SequenceEqual(new[] { 2, 4, 6 }), "doubled");
    Check(plain.CallWords(new[] { "a", "b" }).SequenceEqual(new[] { "a!", "b!" }), "words");
    Check(plain.CallPair(new[] { 1, 2 }).SequenceEqual(new[] { 2, 1 }), "pair");
}
Check(BridgefexCallbacks.Registered == 0, "no director for plain objects");

// -- Every kind of element, in both directions ----------------------------------------------------------

using (var over = new Over())
{
    Check(over.CallTotal(new[] { 1, 2, 3 }) == 3006, "a span of int, and the base call with it: " + over.CallTotal(new[] { 1, 2, 3 }));
    Check(over.CallSpanTotal(new[] { 1, 2, 3, 4 }) == 20, "a std::span");
    Check(over.CallDoubled(new[] { 1, 2, 3 }).SequenceEqual(new[] { 3, 6, 9 }), "an array of int");
    Check(
        over.CallLongs(new long[] { 1, -2, int.MinValue }).SequenceEqual(new long[] { 11, 8, int.MinValue + 10L }),
        "C longs");
    Check(
        over.CallUlongs(new ulong[] { 1, 2, uint.MaxValue - 1 }).SequenceEqual(new ulong[] { 2, 3, uint.MaxValue }),
        "C unsigned longs");
    Check(
        over.CallColors(new[] { Color.Red, Color.Green }).SequenceEqual(new[] { Color.Red, Color.Green }),
        "enums without a fixed type: kept, where C++ swaps them");
    Check(
        over.CallModes(new[] { Mode.Off, Mode.On }).SequenceEqual(new[] { Mode.On, Mode.Auto }),
        "enums with a fixed type");
    Check(
        over.CallModes(new[] { Mode.Auto }).SequenceEqual(new[] { (Mode)3 }),
        "a value that is not one of the enum: C++ has no range for a fixed type");
    Check(
        over.CallPoints(new[] { new Point { X = 1, Y = 2 }, new Point { X = 3, Y = 4 } })
            .SequenceEqual(new[] { new Point { X = 2, Y = 4 }, new Point { X = 4, Y = 6 } }),
        "structs");
    string[] words = { "héllo", "", "日本語", new string('x', 10000), "emoji 😀" };
    Check(over.CallWords(words).SequenceEqual(words.Select(word => word + "?")), "strings, Unicode and an empty one");
    Check(
        over.CallFlags(new[] { true, false, true }).SequenceEqual(new[] { true, false, true, true }),
        "bool");
    Check(over.CallPair(new[] { 1, 2 }).SequenceEqual(new[] { 101, 202 }), "std::array of int");
    var ends = new[] { new Point { X = 1, Y = 1 }, new Point { X = 2, Y = 2 } };
    Check(over.CallEnds(ends).SequenceEqual(new[] { ends[0], ends[0] }), "std::array of structs");
    Check(over.CallNothing() == 0, "an empty result");
    Check(over.CallMixed(3, "ab") == 30302, "a vector, a span and a string: " + over.CallMixed(3, "ab"));
}

// -- Empty containers, and big ones ---------------------------------------------------------------------

using (var over = new Over())
{
    Check(over.CallTotalEmpty() == 0 && over.TotalSawEmpty, "an empty vector gives an empty span");
    Check(over.CallWordsEmpty() == 0 && over.WordsSawEmpty, "an empty vector of strings");
    Check(over.CallTotalBig(1_000_000) == 2_000_000, "a span of a million elements, not copied");
    Check(over.CallWordsMany(50_000) == 50_000 * 5, "fifty thousand strings");
}

// -- What an override must not give back ----------------------------------------------------------------

using (var bad = new BadNull())
{
    Expect<InvalidOperationException>(() => bad.CallDoubledSize(new[] { 1 }), "the override Source.doubled returned null for a std::vector");
}
using (var bad = new BadLength())
{
    Expect<InvalidOperationException>(() => bad.CallPair(new[] { 1, 2 }), "returned 3 elements for a std::array of 2");
}
using (var bad = new BadColor())
{
    Expect<InvalidOperationException>(() => bad.CallColors(new[] { Color.Red }), "the override Source.colors[1] returned 7");
}
using (var bad = new BadNullWord())
{
    Expect<ArgumentNullException>(() => bad.CallWords(new[] { "x" }), "result[1]");
}
using (var bad = new BadSurrogate())
{
    Expect<ArgumentException>(() => bad.CallWords(new[] { "x" }), "not valid Unicode");
}

// -- Text that C++ gives and is not UTF-8 is an error of the call, and the override does not run ---------

using (var over = new Over())
{
    int calls = over.WordsCalls;
    Expect<DecoderFallbackException>(() => over.CallWordsBad());
    Check(over.WordsCalls == calls, "the override is not called with text that is not valid");
    Check(over.CallWords(new[] { "ok" }).SequenceEqual(new[] { "ok?" }), "and the next call is fine");
}

// -- The exception of an override that has a container goes with the call -------------------------------

using (var throwing = new Throwing())
{
    Expect<FormatException>(() => throwing.CallTotal(new[] { 1 }), "total");
    Expect<FormatException>(() => throwing.CallWords(new[] { "a", "b" }), "words");
    Check(throwing.CallPair(new[] { 4, 5 }).SequenceEqual(new[] { 5, 4 }), "the other methods are C++'s: the call after it is fine");
}
Check(unobserved.IsEmpty, "nothing was left unobserved");

// -- Memory: what is made for a result is released, also when the result is refused ----------------------

using (var big = new BigResults())
{
    long grown = Growth(() => big.CallPoints(new[] { new Point { X = 1, Y = 1 } }), 200);
    Check(grown < 100, $"the structs of a result are released (grew {grown} MiB)");
    grown = Growth(() => big.CallWords(new[] { "x" }), 100);
    Check(grown < 100, $"the strings of a result are released (grew {grown} MiB)");
}
using (var bad = new BadSurrogate())
{
    long grown = Growth(() => Expect<ArgumentException>(() => bad.CallWords(new[] { "x" })), 2000);
    Check(grown < 100, $"a result that is refused is released (grew {grown} MiB)");
}

// -- Threads: C++ runs the overrides with containers on the thread of the call --------------------------

{
    var errors = new ConcurrentBag<string>();
    var workers = Enumerable.Range(0, 6).Select(number => new Thread(() =>
    {
        using var over = new Over();
        for (int turn = 0; turn < 200; turn++)
        {
            int[] values = Enumerable.Range(number, 20).ToArray();
            if (over.CallTotal(values) != 20000 + values.Sum())
            {
                errors.Add($"total {number} {turn}");
            }
            if (!over.CallWords(new[] { "a" + number, "b" + turn }).SequenceEqual(new[] { "a" + number + "?", "b" + turn + "?" }))
            {
                errors.Add($"words {number} {turn}");
            }
        }
    })).ToList();
    workers.ForEach(thread => thread.Start());
    workers.ForEach(thread => thread.Join());
    Check(errors.IsEmpty, "threads: " + string.Join("; ", errors.Take(3)));
}

GC.Collect();
GC.WaitForPendingFinalizers();
GC.Collect();
Check(BridgefexCallbacks.Registered == 0, $"C++ holds no director at the end, not {BridgefexCallbacks.Registered}");
Check(unobserved.IsEmpty, "nothing was left unobserved");

Console.WriteLine("directors containers (C# only): C# round trip OK");

// -- The classes of the user ----------------------------------------------------------------------------

class Over : Source
{
    public int WordsCalls;
    public bool TotalSawEmpty;
    public bool WordsSawEmpty;

    public override int Total(ReadOnlySpan<int> values)
    {
        if (values.IsEmpty)
        {
            TotalSawEmpty = true;
        }
        return 1000 * values.Length + base.Total(values);
    }

    public override long SpanTotal(ReadOnlySpan<int> values)
    {
        long sum = 0;
        foreach (int value in values)
        {
            sum += value;
        }
        return sum * 2;
    }

    public override int[] Doubled(ReadOnlySpan<int> values)
    {
        var result = new int[values.Length];
        for (int i = 0; i < values.Length; i++)
        {
            result[i] = values[i] * 3;
        }
        return result;
    }

    public override long[] Longs(ReadOnlySpan<long> values)
    {
        long[] result = values.ToArray();
        for (int i = 0; i < result.Length; i++)
        {
            result[i] += 10;
        }
        return result;
    }

    public override ulong[] Ulongs(ReadOnlySpan<ulong> values) => values.ToArray().Select(value => value + 1).ToArray();

    public override Color[] Colors(ReadOnlySpan<Color> values) => values.ToArray();

    public override Mode[] Modes(ReadOnlySpan<Mode> values) => values.ToArray().Select(mode => (Mode)((int)mode + 1)).ToArray();

    public override Point[] Points(ReadOnlySpan<Point> values) =>
        values.ToArray().Select(point => new Point { X = point.X + 1, Y = point.Y + 2 }).ToArray();

    public override string[] Words(ReadOnlySpan<string> values)
    {
        WordsCalls++;
        if (values.IsEmpty)
        {
            WordsSawEmpty = true;
        }
        return values.ToArray().Select(word => word + "?").ToArray();
    }

    public override bool[] Flags(ReadOnlySpan<bool> values) => [.. values.ToArray(), true];

    public override int[] Pair(ReadOnlySpan<int> values) => new[] { values[0] + 100, values[1] + 200 };

    public override Point[] Ends(ReadOnlySpan<Point> values) => new[] { values[0], values[0] };

    public override int[] Nothing() => Array.Empty<int>();

    public override int Mixed(ReadOnlySpan<int> values, ReadOnlySpan<double> weights, string label) =>
        values.Length * 10000 + weights.Length * 100 + label.Length;
}

class BadNull : Source
{
    public override int[] Doubled(ReadOnlySpan<int> values) => null!;
}

class BadLength : Source
{
    public override int[] Pair(ReadOnlySpan<int> values) => new[] { 1, 2, 3 };
}

class BadColor : Source
{
    public override Color[] Colors(ReadOnlySpan<Color> values) => new[] { Color.Red, (Color)7 };
}

class BadNullWord : Source
{
    public override string[] Words(ReadOnlySpan<string> values) => new[] { "a", null! };
}

class BadSurrogate : Source
{
    public override string[] Words(ReadOnlySpan<string> values) => new[] { "a", "\ud800" };
}

class Throwing : Source
{
    public override int Total(ReadOnlySpan<int> values) => throw new FormatException("total");

    public override string[] Words(ReadOnlySpan<string> values) => throw new FormatException("words");
}

class BigResults : Source
{
    public override Point[] Points(ReadOnlySpan<Point> values) => new Point[100_000];

    public override string[] Words(ReadOnlySpan<string> values) => Enumerable.Repeat("a string of some length", 20_000).ToArray();
}
