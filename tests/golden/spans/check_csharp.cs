// SPDX-License-Identifier: Apache-2.0
// Round trip through the generated C# bindings of the "spans" case: a std::span<const T> goes in as a
// ReadOnlySpan<T>, which is not copied if the elements are numbers.
//
// tests/test_csharp_roundtrip.py builds this file together with the generated project and runs
// it with SPN_LIBRARY pointing at the built library.

using Spn;
using V = Spn.Views;
using F = Spn.Views.Functions;

static void Check(bool condition, string what)
{
    if (!condition)
    {
        throw new Exception("check failed: " + what);
    }
}

Check(F.Sum(new short[] { 1, 2, 3 }) == 6, "numbers");
Check(F.Sum(default) == 0 && F.Sum(Array.Empty<short>()) == 0, "empty");
Check(F.Sum(new short[] { short.MaxValue, short.MaxValue, short.MinValue }) == short.MaxValue + short.MaxValue + short.MinValue, "the limits");
Span<short> onStack = stackalloc short[] { 4, 5, 6 };
Check(F.Sum(onStack) == 15, "memory of the stack");
Check(F.Numbers().SequenceEqual(new[] { -1, 2, -3 }), "a span as a result");
Check(F.CountRed(new[] { V.Color.Red, (V.Color)1, (V.Color)0 }) == 2, "enums");
Check(F.SumX(new[] { new V.Point { X = 1.5, Y = 0 }, new V.Point { X = 2.5, Y = 0 } }) == 4.0, "structs");
Check(F.Total(new[] { "ab", "c" }) == 3, "strings");
Check(F.Total(new[] { "é", "😀" }) == 2 + 4, "strings are counted in UTF-8 bytes");

Console.WriteLine("spans: C# round trip OK");
