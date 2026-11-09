// SPDX-License-Identifier: Apache-2.0
// What is particular of the enums of the C# bindings: an enum without a fixed type refuses the
// values that its C++ type cannot hold (the shared scenarios do not say that).
//
// tests/test_csharp_roundtrip.py builds this file together with the generated project and runs
// it with ENM_LIBRARY pointing at the built library.

using Colors = Enm.Colors;
using F = Enm.Colors.Functions;

static void Check(bool condition, string what)
{
    if (!condition)
    {
        throw new Exception("check failed: " + what);
    }
}

static void Refuses(Action action, string what)
{
    try
    {
        action();
    }
    catch (ArgumentOutOfRangeException error)
    {
        Check(error.Message.Contains("not a value of the enum"), what + ": " + error.Message);
        return;
    }
    throw new Exception("not refused: " + what);
}

// Mode has the enumerators 0, 1 and 5: its C++ type holds 0..7 (the bits of the biggest).
Check(F.Toggle(Colors.Mode.On) == Colors.Mode.Off, "toggle");
Check(F.Toggle((Colors.Mode)7) == Colors.Mode.Off, "the biggest value that the type holds");
Refuses(() => F.Toggle((Colors.Mode)8), "one above");
Refuses(() => F.Toggle((Colors.Mode)(-1)), "one below");

// Signed has -3 and 2: it holds -4..3.
Check((int)F.Negate(Colors.Signed.First) == 3 && F.Negate(Colors.Signed.Last) == (Colors.Signed)(-2), "negate");
Check((int)F.Negate((Colors.Signed)(-4)) == 4, "the smallest value that the type holds");
Check((int)F.Negate((Colors.Signed)3) == -3, "the biggest");
Refuses(() => F.Negate((Colors.Signed)(-5)), "below the bits of the negative ones");
Refuses(() => F.Negate((Colors.Signed)4), "above");

// The members of a class and a nested enum.
using (var lamp = new Colors.Lamp(Colors.Color.Green))
{
    lamp.Set(Colors.Lamp.State.Lit, Colors.Lamp.Level.Bright);
    Check(lamp.State_() == Colors.Lamp.State.Lit && lamp.Level_() == Colors.Lamp.Level.Bright, "lamp");
    Refuses(() => lamp.Set(Colors.Lamp.State.Lit, (Colors.Lamp.Level)4), "a nested enum without a fixed type");
}

// Enums with a fixed type take any value of it: it is C++ that gives them a meaning.
Check(F.Mix(Colors.Color.Red, Colors.Color.Max) == (Colors.Color)127, "a fixed type takes any value of it");
Check(F.Mix((Colors.Color)250, (Colors.Color)254) == (Colors.Color)252, "also those that are no enumerator");
Check(F.Flip(Colors.Flags.All) == Colors.Flags.Nothing, "64 bits, unsigned");
Check((ulong)Colors.Flags.All == ulong.MaxValue, "the biggest value");
Check(F.Plain(Colors.Plain.Low) == Colors.Plain.Low && F.Plain(Colors.Plain.High) == Colors.Plain.High, "64 bits, signed");

Console.WriteLine("enums: C# round trip OK");
