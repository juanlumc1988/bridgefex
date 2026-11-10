// SPDX-License-Identifier: Apache-2.0
// Round trip through the generated C# bindings of the "containers" case: a vector, an array or a
// span goes in as a ReadOnlySpan, and a result comes back as an array.
//
// tests/test_csharp_roundtrip.py builds this file together with the generated project and runs
// it with CONT_LIBRARY pointing at the built library.

using System.Runtime.InteropServices;
using Cont;
using V = Cont.Vectors;
using F = Cont.Vectors.Functions;

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

// -- A span is made from anything that has the elements together ------------------------------

long[] expected = { 10, -20, 30 };
Check(F.Scale(new long[] { 1, -2, 3 }, 10).SequenceEqual(expected), "an array");
Check(F.Scale(new List<long> { 1, -2, 3 }.ToArray(), 10).SequenceEqual(expected), "a list");
Check(F.Scale(CollectionsMarshal.AsSpan(new List<long> { 1, -2, 3 }), 10).SequenceEqual(expected), "the span of a list");
Span<long> onStack = stackalloc long[] { 1, -2, 3 };
Check(F.Scale(onStack, 10).SequenceEqual(expected), "memory of the stack");
Check(F.Scale(new long[] { 9, 1, -2, 3, 9 }.AsSpan(1, 3), 10).SequenceEqual(expected), "a slice");
Check(F.Scale(new long[] { 9, 1, -2, 3, 9 }[1..4], 10).SequenceEqual(expected), "a range");
Check(F.Scale(default, 10).Length == 0, "the default span is empty");
Check(F.Scale(Array.Empty<long>(), 10).Length == 0, "an empty array");
Check(F.Scale(ReadOnlySpan<long>.Empty, 10).Length == 0, "an empty span");

// A result is an array of its own: nothing is shared with C++.
long[] first = F.Scale(new long[] { 1 }, 2);
long[] second = F.Scale(new long[] { 1 }, 2);
first[0] = 99;
Check(second[0] == 2 && F.Scale(new long[] { 1 }, 2)[0] == 2, "a result is a copy");

// -- Elements -----------------------------------------------------------------------------------

Check(F.Invert(new[] { true, false }).SequenceEqual(new[] { false, true }), "bool");
Check(F.EchoBytes(new byte[] { 0, 255 }).SequenceEqual(new byte[] { 0, 255 }), "bytes");
Check(F.Halves(new[] { 1.0f, 3.0f }).SequenceEqual(new[] { 0.5f, 1.5f }), "float");
Check(F.TotalSize(new[] { "ab", "c\0d", "" }) == 5, "strings keep every character");
Check(F.Upper(new[] { "ab\0c", "", "xyz" }).SequenceEqual(new[] { "AB\0C", "", "XYZ" }), "strings go and come back");
Check(F.EmptyStrings().SequenceEqual(new[] { "", "" }), "empty strings come back");
Check(F.Upper(new[] { "héllo 😀" })[0] == "HéLLO 😀", "UTF-8 goes through (C++ changes the ASCII letters)");
Check(F.Sum(new[] { 1, 2 }, 7) == 3007, "a parameter named like the size of the vector");
Check(F.Primes().SequenceEqual(new[] { 2, 3, 5, 7 }), "a result by reference");

var red = V.Color.Red;
var green = V.Color.Green;
var blue = V.Color.Blue;
Check(F.Rotate(new[] { red, blue, (V.Color)2 }).SequenceEqual(new[] { green, red, red }), "enums");
Check(F.CountHigh(new[] { V.Level.High, V.Level.Low, V.Level.High }) == 2, "enums without a fixed type");
Check(F.Levels(3).SequenceEqual(new[] { V.Level.Low, V.Level.High, V.Level.Low }), "enums come back");
var shifted = F.Shift(new[] { new V.Point { X = 1, Y = 2 }, new V.Point { X = 3, Y = 4 } }, 0.5);
Check(shifted.Length == 2 && shifted[0].X == 1.5 && shifted[0].Y == 2 && shifted[1].X == 3.5 && shifted[1].Y == 4, "structs");

// -- Arrays have a length -----------------------------------------------------------------------

Check(F.Twice(new[] { 1f, 2f, 3.5f, 4f }).SequenceEqual(new[] { 2f, 4f, 7f, 8f }), "std::array");
var wrongLength = Expect<ArgumentException>(() => F.Twice(new[] { 1f, 2f }), "argument 'm' must have 4 elements, got 2");
Check(wrongLength.ParamName == "m", "the parameter is named: " + wrongLength.ParamName);
Expect<ArgumentException>(() => F.Twice(new float[5]), "must have 4 elements, got 5");
Expect<ArgumentException>(() => F.Twice(default), "must have 4 elements, got 0");
var swapped = F.Swap(new[] { new V.Point { X = 1, Y = 2 }, new V.Point { X = 3, Y = 4 } });
Check(swapped[0].X == 3 && swapped[1].X == 1, "an array of structs");
Check(F.Colors().SequenceEqual(new[] { blue, green, red }), "an array of enums comes back");
Check(F.SumLevels(new[] { V.Level.High, V.Level.High }) == 2, "an array of enums goes in");

// -- Refusals -----------------------------------------------------------------------------------

Expect<ArgumentOutOfRangeException>(() => F.CountHigh(new[] { V.Level.High, (V.Level)2 }), "not a value of the enum");
var range = Expect<ArgumentOutOfRangeException>(() => F.CountHigh(new[] { (V.Level)(-1) }));
Check(range.ParamName == "levels[0]", "the element is named: " + range.ParamName);
Expect<ArgumentOutOfRangeException>(() => F.SumLevels(new[] { V.Level.High, (V.Level)7 }));
var nullElement = Expect<ArgumentNullException>(() => F.TotalSize(new[] { "a", null! }));
Check(nullElement.ParamName == "words[1]", "the string is named: " + nullElement.ParamName);
Expect<ArgumentException>(() => F.TotalSize(new[] { "a", "\ud800" }), "not valid Unicode");
Expect<BridgefexException>(() => F.TooBig(), "max_size");

// -- A class that takes and returns vectors -----------------------------------------------------

using (var bag = new V.Bag(new[] { "a", "b" }))
{
    bag.Add(new[] { 1, 2 });
    bag.Add(new[] { 3 });
    Check(bag.Counts().SequenceEqual(new[] { 1, 2, 3 }), "counts");
    Check(bag.Items().SequenceEqual(new[] { "a", "b" }), "items");
}

// -- What C++ allocated is freed, also when the conversion fails and with many threads --------------

{
    var many = new string('x', 1000);
    var words = Enumerable.Repeat(many, 1000).ToArray();  // 1 MB of text
    F.Upper(words);
    long before = Environment.WorkingSet;
    for (int turn = 0; turn < 300; turn++)
    {
        Check(F.Upper(words).Length == 1000, "a big vector of strings");
        Check(F.Scale(new long[100000], 1).Length == 100000, "a big vector of numbers");
    }
    GC.Collect();
    long grown = Environment.WorkingSet - before;
    Check(grown < 200L * 1024 * 1024, $"the results are freed (the process grew by {grown >> 20} MiB)");
}

var failures = new System.Collections.Concurrent.ConcurrentBag<string>();
var threads = Enumerable.Range(0, 8).Select(number => new Thread(() =>
{
    for (int turn = 0; turn < 500; turn++)
    {
        long[] given = { number, turn, -number };
        if (!F.Scale(given, 3).SequenceEqual(new long[] { 3L * number, 3L * turn, -3L * number }))
        {
            failures.Add($"scale {number} {turn}");
        }
        string text = $"thread {number} turn {turn}";
        if (F.Upper(new[] { text, "é" })[0] != text.ToUpperInvariant())
        {
            failures.Add($"upper {number} {turn}");
        }
    }
})).ToList();
threads.ForEach(thread => thread.Start());
threads.ForEach(thread => thread.Join());
Check(failures.IsEmpty, "threads: " + string.Join("; ", failures.Take(3)));

Console.WriteLine("containers: C# round trip OK");
