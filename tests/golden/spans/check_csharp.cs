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

// -- A span that C++ keeps a pointer to: the object that keeps it holds a copy ----------------------

{
    static void Churn()
    {
        GC.Collect();
        GC.WaitForPendingFinalizers();
        GC.Collect();
        V.Ledger.Scribble();
    }

    static V.Ledger Make()
    {
        // The arrays of the call are not referenced by anything when this returns.
        var ledger = new V.Ledger(new[] { 1, 2, 3 });
        ledger.Add(new[] { 10, 20 });
        ledger.AddDoubles(new[] { 1.5, 2.5 });
        ledger.AddFlags(new[] { true, false });
        ledger.AddLongs(new long[] { 7, 8, 9 });
        return ledger;
    }

    var kept = Make();
    Churn();
    Check(kept.Intact(), "the numbers that C++ keeps are where they were");
    Check(kept.Total() == 36 && kept.Kept() == 12, "the sum of the kept numbers");
    kept.AddCopy(new[] { 4, 5 });
    Check(kept.Kept() == 12, "a vector is a copy");
    kept.Add(ReadOnlySpan<int>.Empty);
    Churn();
    Check(kept.Intact() && kept.Total() == 6, "an empty span");
    kept.Dispose();
    V.Ledger.Pin(new[] { 5, 6, 7 });
    Churn();
    Check(V.Ledger.KeptIntact(), "a function keeps it for ever");

    // An object that is abandoned: the finalizer destroys it after the C++ destructor read what it kept.
    [System.Runtime.CompilerServices.MethodImpl(System.Runtime.CompilerServices.MethodImplOptions.NoInlining)]
    static void Abandon() => Make();
    Abandon();
    Churn();
    Check(V.Ledger.BadDestructions() == 0, "no destructor found what it points to gone");
}

Console.WriteLine("spans: C# round trip OK");
