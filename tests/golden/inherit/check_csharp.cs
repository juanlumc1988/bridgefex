// SPDX-License-Identifier: Apache-2.0
// Round trip through the generated C# bindings of the "inherit" case.
//
// tests/test_csharp_roundtrip.py builds this file together with the generated project and runs
// it with INH_LIBRARY pointing at the built library.

using System.Runtime.CompilerServices;
using Inh;
using Named = Inh.Shape.Named;
using Shape = Inh.Shape.Shape;
using Circle = Inh.Shapes.Circle;
using Square = Inh.Shapes.Square;
using Ring = Inh.Shapes.Ring;
using S = Inh.Shapes.Functions;
using Counter = Inh.Widget.Counter;
using Labeled = Inh.Widget.Labeled;
using Widget = Inh.Widget.Widget;
using Observer = Inh.Widget.Observer;
using Shelf = Inh.Widget.Shelf;
using Plain = Inh.Widget.Plain;
using PlainDerived = Inh.Widget.PlainDerived;
using W = Inh.Widget.Functions;

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

static int Alive()
{
    GC.Collect();
    GC.WaitForPendingFinalizers();
    GC.Collect();
    return S.ShapesAlive();
}

// The classes derive from each other as in C++, across headers.
Check(typeof(Shape).BaseType == typeof(Named), "Shape : Named");
Check(typeof(Ring).BaseType == typeof(Circle) && typeof(Circle).BaseType == typeof(Shape), "Ring : Circle : Shape");
Check(typeof(Widget).BaseType == typeof(Counter), "Widget : Counter (its first base)");
Check(typeof(Plain).BaseType == typeof(BridgefexObject), "a class without bases");

// Inherited methods, and virtual calls that run the override of the object's class.
using (var circle = new Circle(2.0))
{
    Check(circle.Area() == 12.0, "area");
    Check(circle.Name() == "circle", "name");
    Check(circle.Greeting() == "I am circle", "greeting, two levels up");
    Check(circle.Describe() == "circle: 12 units", "describe calls the overrides");
    Check(circle.Sides() == 0, "the default of a virtual method");
    Check(circle.Radius() == 2.0, "radius");
    Shape asShape = circle;
    Named asNamed = circle;
    Check(asShape.Name() == "circle" && asNamed.Name() == "circle", "through the base classes");
}
using (var square = new Square(3.0))
{
    Check(square.Sides() == 4, "overridden");
    Check(square.Describe() == "square: 9 units", "describe");
}

// A method that a derived class hides is the one of the class; the base's is still there.
using (var circle = new Circle(2.0))
{
    Check(circle.Scale(2.0) == 4.0, "Circle.Scale(double)");
    Check(circle.Radius() == 4.0, "radius after scale");
    Shape asShape = circle;
    Check(asShape.Scale(3) == 3, "Shape.Scale(int), on the Shape part");
    Check(circle.Scaled() == 3 && circle.Radius() == 4.0, "scaled");
}

// Three levels: Ring is a Circle, a Shape and a Named.
using (var ring = new Ring(10.0, 5.0))
{
    Check(ring.Area() == 225.0 && ring.Name() == "ring", "ring");
    Check(ring.Radius() == 10.0, "Circle.Radius on a Ring");
    Check(ring.Describe() == "ring: 225 units", "describe");
    Check(ring.Greeting() == "I am ring", "greeting");
    Check(S.CallName(ring) == "I am ring", "a Ring where a Named is expected");
    Check(S.TotalArea(ring, ring) == 450.0, "total_area");
    Check(S.DescribeFirst(ring) == "ring: 225 units", "describe_first");
    Check(S.Grow(ring, 2) == 450.0, "grow");
}

// A subclass in C# is the same C++ class.
using (var mine = new Mine(1.0))
{
    Check(mine.Twice() == 6.0, "twice");
    Check(S.TotalArea(mine, mine) == 6.0, "a subclass where a Shape is expected");
}

// A closed object fails like any other, through the base class too.
var dead = new Square(1.0);
dead.Close();
Expect<ObjectDisposedException>(() => S.TotalArea(dead, dead), "closed");
Expect<ObjectDisposedException>(() => dead.Describe(), "closed");
Expect<ArgumentNullException>(() => S.TotalArea(null!, dead), "first");
Expect<ArgumentNullException>(() => S.CallName(null!), "named");
Expect<ArgumentNullException>(() => S.DescribeFirst(null!), "shape");

// Objects that C++ returns as the base class: that is the class of the C# object, whatever
// the object is, and its virtual methods run the right code.
var made = Enumerable.Range(0, 3).Select(kind => S.MakeShape(kind, 3.0)!).ToList();
Check(made.All(item => item.GetType() == typeof(Shape)), "the class is the declared one");
Check(made.Select(item => item.Name()).SequenceEqual(new[] { "circle", "square", "ring" }), "names");
Check(made.Select(item => item.Sides()).SequenceEqual(new[] { 0, 4, 0 }), "sides");
Check(made.Select(item => item.Area()).SequenceEqual(new[] { 27.0, 9.0, 27.0 - 3.0 * 0.6 * 0.6 }), "areas");
Check(Alive() == 3, "three shapes");
foreach (var item in made)
{
    item.Close();
}
made.Clear();
Check(Alive() == 0, "closed");
Check(S.MakeShape(9, 1.0) is null, "an empty unique_ptr is null");

// A borrowed result is a Shape that belongs to its arguments.
using (var small = new Circle(1.0))
{
    var big = new Square(5.0);
    var chosen = S.Largest(small, big);
    Check(chosen.GetType() == typeof(Shape) && chosen.Name() == "square" && chosen.Sides() == 4, "largest");
    big.Close();
    Expect<ObjectDisposedException>(() => chosen.Name(), "closed");
}

// Giving an object up as the base class: the object is closed, C++ destroys it.
foreach (Func<Shape> make in new Func<Shape>[] { () => new Circle(1.0), () => new Square(1.0), () => new Ring(2.0, 1.0) })
{
    var kind = make();
    Check(Alive() == 1, "one shape");
    int expectedSides = kind is Square ? 4 : 0;
    Check(S.Consume(kind) == expectedSides, "consume");
    Expect<ObjectDisposedException>(() => kind.Area(), "closed");
    Check(Alive() == 0, "destroyed by C++");
}
var returned = S.MakeShape(1, 1.0)!;
Check(S.Consume(returned) == 4, "consume a returned shape");
Expect<ObjectDisposedException>(() => returned.Area(), "closed");
Expect<ObjectDisposedException>(() => S.Consume(returned), "closed");
Check(Alive() == 0, "nothing left");

// Several bases: each of them is a different part of the object.
using (var w = new Widget())
{
    Check(w.Next() == 1, "Counter.Next");
    Check(W.Bump(w) == 2, "a Widget where a Counter& is expected");
    Check(w.Count() == 2, "count");
    Check(W.Peek(w) == 2, "a copy of the Counter part");
    Check(w.Label() == "widget unnamed", "overridden Labeled.Label");
    w.SetLabel("ab");
    Check(W.NameOf(w) == "widget ab", "through Labeled*, virtual");
    Check(w.Both() == 2 * 100 + "widget ab".Length, "both");
    Labeled asLabeled = w;
    Check(asLabeled.Label() == "widget ab", "the second base, converted");
    asLabeled.SetLabel("cd");
    Check(w.Label() == "widget cd", "the view changes the object");
    Check(w.AsLabeled().Label() == "widget cd", "AsLabeled");
}
using (var counter = new Counter())
using (var labeled = new Labeled())
{
    Check(W.Bump(counter) == 1, "bump");
    Check(W.NameOf(labeled) == "unnamed", "name_of");
}
using (var labeled = new Labeled("given"))
{
    Check(labeled.Label() == "given", "a constructor with an argument");
}

// An object that is taken as a base class that is not at its address, and handed back as
// itself, still has what it retained: the Counter that the constructor kept.
[MethodImpl(MethodImplOptions.NoInlining)]
static Observer ObserverOfNewCounter(out Counter counter)
{
    counter = new Counter();
    counter.Next();
    return new Observer(counter);
}

var observer = ObserverOfNewCounter(out var watched);
Check(observer.Tag() == 41 && observer.Watched() == 1, "observer");
var shelf = new Shelf();
shelf.Put(observer);  // taken as its Labeled part: the conversion is a view of the object
Expect<ObjectDisposedException>(() => observer.Watched(), "closed");
watched = null!;
GC.Collect();
GC.WaitForPendingFinalizers();
GC.Collect();  // only what the object retained keeps the Counter now
var back = shelf.Pop()!;
Check(back.GetType() == typeof(Observer), "handed back as itself");
Check(back.Watched() == 1, "what it retained is still there");  // a use after free under AddressSanitizer if it was lost
Check(back.Label() == "unnamed", "label");
back.Close();
shelf.Close();

// A view of an object (a base that is not the first) gives the object, once.
using (var again = new Widget())
{
    var view = again.AsLabeled();
    var shelf2 = new Shelf();
    shelf2.Put(view);
    Expect<ObjectDisposedException>(() => again.Next(), "closed");
    Expect<ObjectDisposedException>(() => shelf2.Put(view), "closed");
    shelf2.Close();
}

// Without a virtual destructor, C++ would destroy a derived object through the base class:
// only an object of exactly that class (or a C# subclass of it) is accepted.
Check(W.TakePlain(new Plain()) == 5, "plain");
Check(W.TakePlain(new MyPlain()) == 5, "a C# subclass of Plain");
var derived = new PlainDerived();
Expect<ArgumentException>(() => W.TakePlain(derived), "no virtual destructor");
Check(derived.Extra() == 9 && derived.Value() == 5, "still usable: nothing was taken");
derived.Close();

Check(Alive() == 0, "no shape left");
Console.WriteLine("inherit: C# round trip OK");

class Mine : Circle
{
    public Mine(double radius) : base(radius)
    {
    }

    public double Twice() => 2 * Area();
}

class MyPlain : Plain
{
}
