// SPDX-License-Identifier: Apache-2.0
// What is particular of the value structs of the C# bindings: what C++ gives a default-constructed
// object, the arrays that are checked when they are passed, and the enums of the members.
//
// tests/test_csharp_roundtrip.py builds this file together with the generated project and runs
// it with VALS_LIBRARY pointing at the built library.

using Geo = Vals.Geo;
using F = Vals.Geo.Functions;

static void Check(bool condition, string what)
{
    if (!condition)
    {
        throw new Exception("check failed: " + what);
    }
}

static void Refuses<T>(Action action, string what) where T : Exception
{
    try
    {
        action();
    }
    catch (T)
    {
        return;
    }
    throw new Exception("not refused: " + what);
}

// Default() is the object that C++ builds, with its default member initializers.
var config = Geo.Config.Default();
Check(config.Baud == 115200 && config.Parity && config.Kind == Geo.Kind.Marked, "default members");
var msg = Geo.Msg.Default();
Check(msg.Tag == 7, "default tag");
Check(msg.Samples.Length == 3 && msg.Path.Length == 2, "default arrays have their length");

// A value goes to C++ and comes back as it was.
msg.Where = new Geo.Point { X = 1.5, Y = -2 };
msg.Samples = new short[] { 1, -2, 3 };
msg.Ok = true;
msg.Path = new[] { new Geo.Point { X = 3, Y = 4 }, new Geo.Point { X = 5, Y = 6 } };
msg.Quality = Geo.Quality.Best;
var back = F.Echo(msg);
Check(back.Tag == 7 && back.Where.X == 1.5 && back.Where.Y == -2, "scalars and nested struct");
Check(back.Samples.SequenceEqual(new short[] { 1, -2, 3 }) && back.Ok, "array of scalars");
Check(back.Path[1].X == 5 && back.Path[1].Y == 6 && back.Quality == Geo.Quality.Best, "array of structs, enum");
Check(!ReferenceEquals(back.Samples, msg.Samples), "the result is a copy");

// The length of an array is checked: C++ would read what is not there, or ignore what is.
var wrong = msg;
wrong.Samples = new short[2];
Refuses<ArgumentException>(() => F.Echo(wrong), "an array that is too short");
wrong.Samples = new short[4];
Refuses<ArgumentException>(() => F.Echo(wrong), "an array that is too long");
wrong.Samples = null!;
Refuses<ArgumentException>(() => F.Echo(wrong), "an array that is null");
wrong = msg;
wrong.Path = new Geo.Point[1];
Refuses<ArgumentException>(() => F.Echo(wrong), "an array of structs that is too short");

// A member that is an enum without a fixed type holds 0..3.
wrong = msg;
wrong.Quality = (Geo.Quality)3;
F.Echo(wrong);
wrong.Quality = (Geo.Quality)4;
Refuses<ArgumentOutOfRangeException>(() => F.Echo(wrong), "an enum member above its range");
wrong.Quality = (Geo.Quality)(-1);
Refuses<ArgumentOutOfRangeException>(() => F.Echo(wrong), "an enum member below its range");

// Members of every size, in a struct with an alignment of 16: C++ doubles the big one.
var wide = new Geo.Wide
{
    F = 1.5f,
    Big = -(1L << 40),
    Size = nuint.MaxValue,
    L = -5,
    C = -3,
    Bytes = new byte[] { 1, 2, 3, 4 },
};
var widened = F.Widen(wide);
Check(widened.Big == -(1L << 41), "C++ got the member");
Check(widened.F == 1.5f && widened.Size == nuint.MaxValue && widened.L == -5 && widened.C == -3, "and the others survive");
Check(widened.Bytes.SequenceEqual(new byte[] { 1, 2, 3, 4 }), "and the array");

Console.WriteLine("structs: C# round trip OK");
