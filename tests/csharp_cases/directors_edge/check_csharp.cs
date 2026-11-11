// SPDX-License-Identifier: Apache-2.0
// What the directors of the golden case "directors" do not reach: see case.toml.
//
// tests/test_csharp_roundtrip.py builds this file together with the generated project and runs
// it with DE_LIBRARY pointing at the built library.

using System.Collections.Concurrent;
using System.Runtime.CompilerServices;
using De;
using Base = De.De.Base;
using ColorEnum = De.De.Color;
using Leaf = De.De.Leaf;
using Registry = De.De.Registry;
using Sub = De.De.Sub;
using Thrower = De.De.Thrower;

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

static void NoDirectors(string when)
{
    Check(BridgefexCallbacks.Registered == 0, $"C++ holds no director {when}, not {BridgefexCallbacks.Registered}");
}

var unobserved = new ConcurrentQueue<Exception>();
BridgefexCallbacks.Unobserved += unobserved.Enqueue;

// -- Plain objects: nothing derives from them, or what derives overrides nothing ----------------------------

using (var plain = new Base())
{
    Check(plain.CallValue() == 1 && plain.CallNamed() == "base" && plain.Made() == 2, "a plain object");
}
using (var sub = new Sub())
{
    Check(sub.CallAdd(3, 4) == 12, "a class that redeclares one overload");
    Check(sub.CallAddDouble(1.5) == 3.0, "and the other one");
}
using (var quiet = new Quiet())
{
    Check(quiet.CallValue() == 1, "a class of the user that overrides nothing");
}
NoDirectors("for plain objects");

// -- Every kind of type in an override ------------------------------------------------------------------------

using (var full = new Full())
{
    Check(full.CallValue() == 10, "int");
    Check(!full.CallFlag(true) && full.CallFlag(false), "bool");
    Check(full.CallWide(21) == 42, "long");
    Check(full.CallColor() == ColorEnum.Green, "an enum without a fixed type");
    Check(full.CallTextNull() == "NULL!", "a NULL const char* is null");
    Check(full.CallText("x") == "Mx", "a const char*");
    Check(full.CallNamed() == "full", "a std::string result");
    Check(full.CallAdd(5, 3) == 2, "an overload");
    Check(full.CallAddDouble(1.0) == 1.5, "the other overload");
    Check(full.Pair() == "10:full", "two overrides in a call");
}
using (var bad = new BadColor(7))
{
    Expect<InvalidOperationException>(() => bad.CallColor(), "the override Base.color returned 7");
}
using (var edge = new BadColor(1))
{
    Check(edge.CallColor() == ColorEnum.Green, "the last value of the enum");
}
using (var empty = new NullName())
{
    Expect<InvalidOperationException>(() => empty.CallNamed(), "returned null");
}
NoDirectors("after the overrides");

// -- After the first exception of a call, the other overrides do not run -------------------------------------

using (var first = new FailFirst())
{
    Expect<FormatException>(() => first.Pair(), "value");
    Check(first.NamedCalls == 0, "named() is not called after value() failed");
}

// -- A method that hides an override with `new` is not an override --------------------------------------------

using (var hidden = new HiddenB())
{
    Check(hidden.CallValue() == 4, "C++ runs the override of A, not the method that hides it");
}
using (var generic = new WithGeneric())
{
    Check(generic.CallValue() == 7, "a generic method with the same name is not an overload");
}
using (var deeper = new HidingNamed2())
{
    Check(deeper.CallNamed() == "A:base", "an override above a method that hides it: " + deeper.CallNamed());
}

// -- The constructors of the class ----------------------------------------------------------------------------

using (var tagged = new Tagged("t"))
{
    Check(tagged.Made() == 3 && tagged.CallValue() == 1, "a protected constructor");
}
using (var thrown = new MyThrower(3))
{
    Check(thrown.CallGet() == 33, "a director of another class");
}
int before = BridgefexCallbacks.Registered;
Expect<BridgefexException>(() => new MyThrower(-1), "negative");
Check(BridgefexCallbacks.Registered == before, "C++ released the director of a constructor that failed");
Expect<ArgumentNullException>(() => new Tagged(null!));
Check(BridgefexCallbacks.Registered == before, "an argument that fails before the C function drops the director");

// -- A class that derives from one with a director but asks for none ------------------------------------------

Expect<NotSupportedException>(() => new MyLeaf(), "no director");
using (var leaf = new Leaf())
{
    Check(leaf.CallValue() == 1, "the class itself works");
}

// -- A director that C++ points to, and an object that is an argument, are not collected -----------------------

[MethodImpl(MethodImplOptions.NoInlining)]
static void KeepIt(Registry registry)
{
    registry.Keep_(new Full());
}

using (var registry = new Registry())
{
    KeepIt(registry);
    Check(registry.Sequence() == "full|10", "a retained object");
    GC.Collect();
    GC.WaitForPendingFinalizers();
    GC.Collect();
    Check(registry.Sequence() == "full|10", "a retained object is alive after the collector ran: " + registry.Sequence());
}

[MethodImpl(MethodImplOptions.NoInlining)]
static string Slow()
{
    var argument = new Full();
    return Registry.SequenceOf(argument, 5);
}

{
    bool stop = false;
    var collector = new Thread(() =>
    {
        while (!Volatile.Read(ref stop))
        {
            GC.Collect();
            Thread.Sleep(2);
        }
    });
    collector.Start();
    for (int turn = 0; turn < 20; turn++)
    {
        string result = Slow();
        Check(result == "full|10|full", "an argument that the collector could collect while C++ uses it: " + result);
    }
    Volatile.Write(ref stop, true);
    collector.Join();
}

// -- An override that throws on a thread of C++ ---------------------------------------------------------------

using (var registry = new Registry())
using (var failing = new ThreadFail())
{
    registry.Keep_(failing);
    Check(unobserved.IsEmpty, "nothing yet");
    Check(registry.ValueOnThread() == 0, "C++ gets the default result");
    Check(unobserved.Count == 1 && unobserved.TryDequeue(out var error) && error is FormatException, "the exception is unobserved");
}

// -- The scope of a call is reused by the next one on the thread: nothing of a call is left to it -----------

using (var failing = new FailFirst())
using (var fine = new Full())
{
    for (int turn = 0; turn < 50; turn++)
    {
        Expect<FormatException>(() => failing.Pair(), "value");
        Check(fine.CallValue() == 10 && fine.Pair() == "10:full", "a call after one that failed: " + turn);
    }
}

// A call inside an override has a scope of its own: its exception is that call's, and the outer call goes on.
using (var inner = new FailFirst())
using (var nesting = new Nesting(inner))
{
    for (int turn = 0; turn < 20; turn++)
    {
        Check(nesting.CallValue() == -1, "the override caught the exception of the call inside it");
        Check(inner.NamedCalls == 0, "and the override of the inner call did not run twice");
    }
}
using (var inner = new FailFirst())
using (var through = new Through(inner))
{
    Expect<FormatException>(() => through.CallValue(), "value");
    Check(through.CallValue2() == 1, "the thread is not left in a failed call: " + through.CallValue2());
}

// A call that C++ runs without overrides makes no garbage: the scope of the thread is reused.
using (var counted = new Base())
{
    for (int warm = 0; warm < 200; warm++)
    {
        counted.CallValue();
    }
    long start = GC.GetAllocatedBytesForCurrentThread();
    for (int turn = 0; turn < 1000; turn++)
    {
        counted.CallValue();
    }
    long allocated = GC.GetAllocatedBytesForCurrentThread() - start;
    Check(allocated < 4096, "a call allocates nothing, 1000 calls allocated " + allocated + " bytes");
}

// -- A base call after the object ran an override and was closed ---------------------------------------------

var caller = new Caller();
Check(caller.CallNamed() == "c:base", "the override calls the base");
caller.Dispose();
Expect<ObjectDisposedException>(() => caller.Named());

GC.Collect();
GC.WaitForPendingFinalizers();
GC.Collect();
NoDirectors("at the end");
Check(unobserved.IsEmpty, "nothing was left unobserved");

Console.WriteLine("directors (C# only): C# round trip OK");

// -- The classes of the user ----------------------------------------------------------------------------------

class Quiet : Sub
{
}

class Full : Base
{
    public override int Value() => 10;

    public override bool Flag(bool on) => !on;

    public override long Wide(long x) => x * 2;

    public override ColorEnum Color() => ColorEnum.Green;

    public override string Text(string? mark) => mark is null ? "NULL!" : "M" + mark;

    public override string Named() => "full";

    public override int AddInt32Int32(int a, int b) => a - b;

    public override double AddDouble(double a) => a + 0.5;
}

class BadColor : Base
{
    private readonly int _value;

    public BadColor(int value)
    {
        _value = value;
    }

    public override ColorEnum Color() => (ColorEnum)_value;
}

class NullName : Base
{
    public override string Named() => null!;
}

class FailFirst : Base
{
    public int NamedCalls;

    public override int Value() => throw new FormatException("value");

    public override string Named()
    {
        NamedCalls++;
        return "x";
    }
}

class A : Base
{
    public override int Value() => 4;
}

class HiddenB : A
{
    public new int Value() => 5;
}

class WithGeneric : Base
{
    public override int Value() => 7;

    public int Value<T>() => 1;
}

class HidingNamed : Base
{
    public override string Named() => "A:" + base.Named();
}

class HidingNamed2 : HidingNamed
{
    public new string Named() => "B";
}

class Tagged : Base
{
    public Tagged(string tag) : base(tag)
    {
    }
}

class MyThrower : Thrower
{
    private readonly int _value;

    public MyThrower(int value) : base(value)
    {
        _value = value;
    }

    public override int Get() => _value * 11;
}

class MyLeaf : Leaf
{
    public override int Value() => 3;
}

class ThreadFail : Base
{
    public override int Value() => throw new FormatException("on a thread of C++");
}

class Caller : Base
{
    public override string Named() => "c:" + base.Named();
}

class Nesting : Base
{
    private readonly Base _other;

    public Nesting(Base other)
    {
        _other = other;
    }

    public override int Value()
    {
        try
        {
            return _other.CallValue();
        }
        catch (FormatException)
        {
            return -1;
        }
    }
}

class Through : Base
{
    private readonly Base _other;

    public Through(Base other)
    {
        _other = other;
    }

    public override int Value() => _other.CallValue();

    public int CallValue2() => Quiet2();

    private static int Quiet2()
    {
        using var plain = new Base();
        return plain.CallValue();
    }
}
