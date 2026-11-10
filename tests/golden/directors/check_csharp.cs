// SPDX-License-Identifier: Apache-2.0
// Round trip through the generated C# bindings of the "directors" case: C# classes that derive from
// the wrapped classes and override their virtual methods, which C++ calls back.
//
// tests/test_csharp_roundtrip.py builds this file together with the generated project and runs
// it with DRC_LIBRARY pointing at the built library.

using System.Collections.Concurrent;
using System.Runtime.CompilerServices;
using Drc;
using Drc.Shape;
using Beam = Drc.Shape.Beam;
using Circle = Drc.Shape.Circle;
using Holder = Drc.Shape.Holder;
using Mode = Drc.Shape.Mode;
using Point = Drc.Shape.Point;
using Shape = Drc.Shape.Shape;

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

static int Alive() => Shape.Alive();

var unobserved = new ConcurrentQueue<Exception>();
BridgefexCallbacks.Unobserved += unobserved.Enqueue;

// -- Classes that have a director work as before when nothing derives from them ----------------

using (var circle = new Circle(2.0))
{
    Check(circle.Area() == 12.0, "a circle");
    Check(circle.Name() == "round", "its name");
    Check(circle.Describe() == "round: 12 units", "describe");
}
using (var dot = new Dot())
{
    Check(dot.Area() == 0.0, "a dot");
}
using (var plain = new Plain())
{
    Check(plain.Value() == 5, "a class that asks for no director");
}
Check(Alive() == 0, "nothing is alive");

// -- A class derived from an abstract one: the pure virtual method has to be overridden -----------

using (var square = new Square())
{
    Check(Alive() == 1, "one object");
    Check(square.Area() == 4.0, "area");
    // C++ calls the overrides: Describe() uses Name() and Area().
    Check(square.Describe() == "sq:shape: 4 units", "describe: " + square.Describe());
    Check(square.Name() == "sq:shape", "name");
    Check(square.Sum(3, 4) == 12, "an overload");
    Check(square.SumDouble(5.0, 2.0) == 3.0, "the other overload");
    Check(square.HowQuiet() == 9, "a noexcept method");
    Check(square.TotalOfThree() == 6, "a method that has a container is the class's own");
    Check(square.Middle(new Point { X = 4.0, Y = 0.0 }).X == 3.0, "structs go both ways");
    Check(square.Choose(Mode.On) == Mode.Off && square.Choose(Mode.Off) == (Mode)5, "enums");
    Check(square.Welcome("Ann") == ">>|hello|Ann|>> hello Ann, I am sq:shape", "strings: " + square.Welcome("Ann"));
    Check(square.ScaledArea(3.0) == 4.0 && square.Scaled == 3.0, "void with an argument");
    square.Notify(5);  // a protected virtual method, overridden
    Check(square.Seen == 5 && square.LastReason() == 10, "protected");
    Check(square.Fixed() == 7, "final: never overridden");
}
Check(Alive() == 0, "closed");

// -- A class that does not override the pure virtual method cannot be instantiated -----------------

Expect<InvalidOperationException>(() => new Incomplete(), "Incomplete must override the pure virtual method Shape.area");
Check(Alive() == 0, "nothing was made");

// -- The constructors of the class are those of the C# class ---------------------------------------

using (var labelled = new Labelled("box", 3))
{
    Check(labelled.Name() == "box", "not overridden: the implementation of the class");
    Check(labelled.Sides() == 3 && labelled.Label() == "box", "the arguments of the constructor");
    Check(labelled.Describe() == "box: 1 units", "describe");
}
Check(Alive() == 0, "closed");

// -- A class derived from one that is not abstract, which has its own constructor ------------------

using (var disc = new Disc(1.0))
{
    Check(disc.Area() == 103.0, "the base area");
    Check(disc.Describe() == "disc: 103 units", "name() and area() are the overrides: " + disc.Describe());
    Check(disc.Radius() == 1.0, "a method of the class");
    // The overload that Circle hides still runs the one of Shape that the director overrides.
    Check(disc.SumDouble(1.5, 2.0) == 3.5, "an overload of the base");
}
Check(Alive() == 0, "closed");

// A class that derives and overrides nothing makes plain objects.

using (var quiet = new Quiet())
{
    Check(quiet.Describe() == "round: 3 units", "the implementation of the class");
}
Check(Alive() == 0, "closed");

// -- A C++ exception from a base call, and one from an override -----------------------------------------

using (var failing = new Failing())
{
    // The exception of an override is the one the call that runs it raises, as it was thrown.
    var error = Expect<FormatException>(() => failing.Describe(), "no area");
    Check(error.StackTrace!.Contains("Failing"), "the stack trace is that of the override");
    Expect<FormatException>(() => failing.ScaledArea(1.0).ToString(), "no area");
}
Check(unobserved.IsEmpty, "an exception that a call raised is not also unobserved");

// -- Given to C++: the object is kept alive by C++ ------------------------------------------------

[MethodImpl(MethodImplOptions.NoInlining)]
static WeakReference Give(Holder holder)
{
    var square = new Square();
    holder.Put(square);  // C++ owns the director now
    Check(square.IsClosed, "the object is closed for C#");
    return new WeakReference(square);
}

using (var holder = new Holder())
{
    WeakReference weak = Give(holder);
    GC.Collect();
    GC.WaitForPendingFinalizers();
    GC.Collect();
    Check(weak.IsAlive, "C++ keeps the object, the collector does not collect it");
    Check(holder.Describe() == "sq:shape: 4 units", "its overrides run: " + holder.Describe());
    Check(Alive() == 1, "C++ holds one");
    // Taking it back gives a plain wrapper of the same C++ object.
    using (Shape back = holder.Take()!)
    {
        Check(back.Describe() == "sq:shape: 4 units", "the same C++ object");
    }
}
GC.Collect();
GC.WaitForPendingFinalizers();
GC.Collect();
Check(Alive() == 0, "the holder destroyed it");

// -- A director that C# owns is destroyed by the collector --------------------------------------------

[MethodImpl(MethodImplOptions.NoInlining)]
static void Abandon()
{
    var square = new Square();
    Check(square.Describe() == "sq:shape: 4 units", "an object that is forgotten");
}

Abandon();
GC.Collect();
GC.WaitForPendingFinalizers();
Check(Alive() == 0, "the finalizer destroyed it");
Check(BridgefexCallbacks.Registered == 0, "and C++ released it");

// -- The base call of an object that is closed --------------------------------------------------------

var closed = new Square();
closed.Dispose();
Expect<ObjectDisposedException>(() => closed.Name());
Expect<ObjectDisposedException>(() => closed.Describe());
Check(Alive() == 0, "closed");

// -- Threads --------------------------------------------------------------------------------------------

using (var holder = new Holder())
{
    holder.Put(new Square());
    Check(holder.DescribeOnThread() == "sq:shape: 4 units", "C++ calls the overrides from a thread of its own");
}

// (The counter of live objects of the C++ test class is not atomic: the objects are made and
// destroyed one at a time, and the calls run together.)
var gate = new object();
var errors = new ConcurrentBag<string>();
var workers = Enumerable.Range(0, 6).Select(number => new Thread(() =>
{
    for (int turn = 0; turn < 100; turn++)
    {
        Square square;
        lock (gate)
        {
            square = new Square();
        }
        if (square.Sum(number, turn) != number * turn)
        {
            errors.Add($"sum {number} {turn}");
        }
        if (square.Describe() != "sq:shape: 4 units")
        {
            errors.Add($"describe {number} {turn}");
        }
        lock (gate)
        {
            square.Dispose();
        }
    }
})).ToList();
workers.ForEach(thread => thread.Start());
workers.ForEach(thread => thread.Join());
Check(errors.IsEmpty, "threads: " + string.Join("; ", errors.Take(3)));
Check(Alive() == 0, "all closed");
Check(BridgefexCallbacks.Registered == 0, "C++ released them all");

Console.WriteLine("directors: C# round trip OK");

// -- The classes of the user -----------------------------------------------------------------------

class Square : Shape
{
    public double Scaled;
    public int Seen;

    public override double Area() => 4.0;

    public override string Name() => "sq:" + base.Name();  // the implementation of C++, not this method

    public override int Sides() => 4;

    public override Point Center() => new Point { X = 2.0, Y = 4.0 };

    public override Mode Mode(Mode requested) => requested == Drc.Shape.Mode.On ? Drc.Shape.Mode.Off : (Drc.Shape.Mode)5;

    public override void Scale(double factor) => Scaled = factor;

    public override int AddInt32Int32(int a, int b) => a * b;

    public override double AddDoubleDouble(double a, double b) => a - b;

    public override string Greet(string who, string tag, string? mark) => $"{mark}|{tag}|{who}|" + base.Greet(who, tag, mark);

    public override int Quiet() => 9;

    protected override void Changed(int reason)
    {
        Seen = reason;
        base.Changed(reason * 2);  // a protected method of C++
    }
}

class Incomplete : Shape
{
    public override string Name() => "x";
}

class Labelled : Shape
{
    public Labelled(string label, int sides) : base(label, sides)
    {
    }

    public override double Area() => 1.0;
}

class Disc : Circle
{
    public Disc(double radius) : base(radius)
    {
    }

    public override double Area() => 100.0 + base.Area();  // the implementation of Circle: 3 * r * r

    public override string Name() => "disc";
}

class Quiet : Circle
{
    public Quiet() : base(1.0)
    {
    }
}

class Failing : Shape
{
    public override double Area() => throw new FormatException("no area");
}
