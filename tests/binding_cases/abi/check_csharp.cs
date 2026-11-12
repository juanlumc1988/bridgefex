// SPDX-License-Identifier: Apache-2.0
// Conversions that are not the identity, and structs that are too big for the stack.
//
// tests/test_csharp_roundtrip.py builds this file together with the generated project and runs
// it with ABI_LIBRARY pointing at the built library.

using Abi;
using F = Abi.Abi.Functions;

static void Check(bool condition, string what)
{
    if (!condition)
    {
        throw new Exception("check failed: " + what);
    }
}

// -- Enums over a C long: negative values, and values that need the 32 bits of an unsigned long.
Check(F.Flip(Abi.Abi.Signed.Low) == Abi.Abi.Signed.High, "signed long enum");
Check(F.Flip(Abi.Abi.Signed.High) == Abi.Abi.Signed.Low, "and back");
Check(F.FlipUnsigned(Abi.Abi.Unsigned.Low) == Abi.Abi.Unsigned.High, "unsigned long enum");
Check((ulong)Abi.Abi.Unsigned.High == 3000000000UL, "its value");

var mixed = new Abi.Abi.Mixed
{
    SignedValue = Abi.Abi.Signed.Low,
    UnsignedValue = Abi.Abi.Unsigned.High,
    Several = new[] { Abi.Abi.Signed.High, Abi.Abi.Signed.Low },
    Number = -1234567,
    UnsignedNumber = 3000000000UL,
};
var echoed = F.Echo(mixed);
Check(echoed.SignedValue == Abi.Abi.Signed.Low && echoed.UnsignedValue == Abi.Abi.Unsigned.High, "enum members");
Check(echoed.Several[0] == Abi.Abi.Signed.High && echoed.Several[1] == Abi.Abi.Signed.Low, "enum array");
Check(echoed.Number == -1234567 && echoed.UnsignedNumber == 3000000000UL, "long members");

// -- Big structs, in a thread with 1 MiB of stack. The C layer builds its own copies of a struct on
// the stack (MSVC more than GCC), so Windows gets more room: what is tested here is that the
// bindings add none, which the 1 MiB shows on the platforms where it fits.
Exception? failure = null;
var thread = new Thread(() =>
{
    try
    {
        var big = Abi.Abi.Big.Default();
        Check(big.Data.Length == 300000, "default array");
        for (int i = 0; i < big.Data.Length; i++)
        {
            big.Data[i] = (byte)(i % 251);
        }
        big.Tail = 77;
        long expected = 77;
        foreach (byte value in big.Data)
        {
            expected += value;
        }
        Check(F.Sum(big) == expected, "a big struct as a parameter");
        var copy = F.EchoBig(big);
        Check(copy.Tail == 77 && copy.Data.AsSpan().SequenceEqual(big.Data), "a big struct as a result");

        var outer = F.MakeOuter(5);
        Check(outer.Count == 5 && outer.Big.Tail == 5 && outer.Big.Data[0] == 1 && outer.Big.Data[299999] == 2, "a nested big struct");
        Check(F.CountOf(outer) == 10, "a nested big struct as a parameter");
        Check(Abi.Abi.Outer.Default().Big.Data.Length == 300000, "default of a nested big struct");
        big.Data = new byte[3];
        try
        {
            F.Sum(big);
            Check(false, "a wrong length is refused");
        }
        catch (ArgumentException)
        {
        }
    }
    catch (Exception error)
    {
        failure = error;
    }
}, OperatingSystem.IsWindows() ? 4 << 20 : 1 << 20);
thread.Start();
thread.Join();
if (failure is not null)
{
    throw failure;
}

Console.WriteLine("abi: C# round trip OK");
