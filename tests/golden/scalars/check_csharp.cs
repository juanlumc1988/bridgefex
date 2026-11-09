// SPDX-License-Identifier: Apache-2.0
// What is particular of the scalars of the C# bindings: a C long is a C# long on every platform, and
// where it has 32 bits (Windows, 32-bit ARM) a value that does not fit is refused, not cut.
//
// tests/test_csharp_roundtrip.py builds this file together with the generated project and runs
// it with SCALARS_LIBRARY pointing at the built library.

using F = Scalars.Numbers.Functions;

static void Check(bool condition, string what)
{
    if (!condition)
    {
        throw new Exception("check failed: " + what);
    }
}

bool longIs32Bits = OperatingSystem.IsWindows() || IntPtr.Size == 4;

Check(F.EchoLong(-123456) == -123456, "a long");
Check(F.EchoLong(int.MinValue) == int.MinValue && F.EchoLong(int.MaxValue) == int.MaxValue, "the limits of 32 bits");
Check(F.EchoUlong(3000000000UL) == 3000000000UL, "an unsigned long above the signed 32 bits");
Check(F.EchoLlong(long.MinValue) == long.MinValue && F.EchoLlong(long.MaxValue) == long.MaxValue, "long long");
Check(F.EchoUllong(ulong.MaxValue) == ulong.MaxValue, "unsigned long long");

if (longIs32Bits)
{
    foreach (long value in new[] { (long)int.MaxValue + 1, (long)int.MinValue - 1, long.MaxValue })
    {
        try
        {
            F.EchoLong(value);
        }
        catch (OverflowException)
        {
            continue;
        }
        throw new Exception($"{value} was cut to the 32 bits of a long");
    }
    try
    {
        F.EchoUlong((ulong)uint.MaxValue + 1);
    }
    catch (OverflowException)
    {
        Console.WriteLine("scalars: C# round trip OK");
        return;
    }
    throw new Exception("an unsigned long was cut to 32 bits");
}
Check(F.EchoLong(long.MinValue) == long.MinValue && F.EchoLong(long.MaxValue) == long.MaxValue, "a long of 64 bits");
Check(F.EchoUlong(ulong.MaxValue) == ulong.MaxValue, "an unsigned long of 64 bits");

Console.WriteLine("scalars: C# round trip OK");
