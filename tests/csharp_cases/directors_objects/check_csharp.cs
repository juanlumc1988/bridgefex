// SPDX-License-Identifier: Apache-2.0
// The objects that C++ lends to an override, gives to it and takes from it: see case.toml.
//
// tests/test_csharp_roundtrip.py builds this file together with the generated project and runs
// it with DOB_LIBRARY pointing at the built library.

using System.Collections.Concurrent;
using System.Runtime.CompilerServices;
using Dob;
using Derived = Dob.Dob.Derived;
using F = Dob.Dob.Functions;
using Keeper = Dob.Dob.Keeper;
using Node = Dob.Dob.Node;
using Plain = Dob.Dob.Plain;
using Visitor = Dob.Dob.Visitor;

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

static int Alive() => Node.Alive();

var unobserved = new ConcurrentQueue<Exception>();
BridgefexCallbacks.Unobserved += unobserved.Enqueue;

// -- A plain object does what C++ does ----------------------------------------------------------------------

using (var plain = new Visitor())
using (var node = new Node(7))
{
    Check(plain.CallSeen(node) == 7 && plain.CallPointer(node) == 7 && plain.CallPointer(null) == -1, "defaults");
    Check(plain.CallTouch(node) == 8 && node.CallValue() == 8, "a mutable reference");
}
Check(Alive() == 0, "nothing is alive");

// -- A reference or a pointer that C++ lends -----------------------------------------------------------------

using (var visitor = new Probe())
using (var node = new Node(5))
{
    Check(visitor.CallSeen(node) == 105, "a const reference: the override got a wrapper of the node");
    Check(visitor.Last is not null && !ReferenceEquals(visitor.Last, node), "a wrapper, not the object (C# does not derive from Node here)");
    Check(visitor.Last!.IsClosed, "the wrapper is closed when the override returns");
    Expect<ObjectDisposedException>(() => visitor.Last!.CallValue());
    Check(node.CallValue() == 5 && !node.IsClosed, "the object itself is not closed");
    Check(visitor.CallTouch(node) == 1005 && node.CallValue() == 6, "a reference that is not const: the override changed the object");
    Check(visitor.CallPointer(node) == 206 && visitor.CallPointer(null) == -2, "a pointer, and NULL is null");
    Check(visitor.PointerWasNull, "a NULL pointer is a null object");
}
using (var visitor = new ConstMutator())
using (var node = new Node(5))
{
    // A const reference or pointer is read-only: the wrapper refuses what would change the object.
    Expect<InvalidOperationException>(() => visitor.CallSeen(node), "read-only");
    Expect<InvalidOperationException>(() => visitor.CallPointer(node), "read-only");
    Check(node.CallValue() == 5, "the object was not changed");
    // A pointer that is not const can be changed.
    Check(visitor.CallTouchPointer(node) == 105 && node.CallValue() == 105, "a pointer that is not const");
}

// -- A C# object that C++ lends is the object itself --------------------------------------------------------

using (var mine = new MyNode(11))
using (var visitor = new Probe())
{
    Check(visitor.CallSeen(mine) == 111 && ReferenceEquals(visitor.Last, mine), "the object of the user, not a wrapper");
    Check(!mine.IsClosed, "and still open");
    Check(visitor.CallPointer(mine) == 211 && ReferenceEquals(visitor.Last, mine), "also through a pointer");
    Check(mine.Calls >= 1, "its own override runs: " + mine.Calls);
}
using (var one = new Self())
using (var two = new Self())
{
    Check(one.CallSame(one), "same() gets the object itself");
    Check(!one.CallSame(two), "another object");
    Check(!one.CallSame(null), "NULL");
    using var plain = new Visitor();
    Check(!one.CallSame(plain), "a plain object");
}

// -- What an override does with the object that is lent -----------------------------------------------------

using (var mine = new MyNode(3))
using (var closing = new Closing())
{
    Check(closing.CallSeenThenValue(mine) == 3, "the override closed the object, which C++ still uses: closing waits for the call");
    Check(mine.IsClosed, "and it is closed now");
}
Check(Alive() == 0, "the node was destroyed when the call ended");

// -- Objects that C++ gives to an override (std::unique_ptr) ------------------------------------------------

using (var visitor = new Taker())
{
    var node = new Node(40);
    Check(visitor.CallTake(node) == 140, "an owned object");
    Check(node.IsClosed, "the object was given up");
    Check(visitor.Kept is not null && !visitor.Kept.IsClosed, "the override owns it");
    Check(Alive() == 1, "C++ did not destroy it: the override has it");
    visitor.Kept!.Dispose();
    Check(Alive() == 0, "the override destroyed it");
    Check(visitor.CallTake(null) == -2 && visitor.TookNull, "an empty std::unique_ptr");
}
Check(Alive() == 0, "nothing is left");

using (var visitor = new Taker())
using (var lent = new Node(2))
{
    var a = new Node(1);
    var c = new Node(3);
    Check(visitor.CallBoth(a, lent, c) == 1 + 20 + 300 + 1000000, "two owned objects and a lent one");
    Check(Alive() == 1, "the owned ones were destroyed by the override, and the lent one is ours");
}
Check(Alive() == 0, "all destroyed");

// -- An override that does not run (an earlier one failed) does not leak what C++ gave up -----------------------

using (var visitor = new Failing())
{
    var node = new Node(9);
    Expect<FormatException>(() => visitor.CallMarkerThenTake(node), "marker");
    Check(visitor.TakeCalls == 0, "the second override did not run");
    Check(Alive() == 0, "and the node that it would have got was destroyed");
}

// -- Objects that an override gives to C++ ----------------------------------------------------------------------

using (var visitor = new Maker())
{
    using var made = visitor.CallMake(4)!;
    Check(made.CallValue() == 40 && Alive() == 1, "an object that the override made: " + made.CallValue());
    Check(visitor.Given is not null && visitor.Given.IsClosed, "C# gave it up");
    Expect<ObjectDisposedException>(() => visitor.Given!.CallValue());
}
Check(Alive() == 0, "C++ destroyed it");
using (var visitor = new Maker { Nothing = true })
{
    Check(visitor.CallMake(4) is null, "NULL when the override returns null");
}

// An object of a class of the user, with overrides of its own, that the override gives to C++.
using (var visitor = new Maker { Mine = true })
{
    var made = visitor.CallMake(4)!;
    Check(made.CallValue() == 4000 + 4, "C++ runs the override of the object that it was given: " + made.CallValue());
    Check(Alive() == 1, "one node");
    Check(BridgefexCallbacks.Registered >= 2, "C++ holds the directors");
    made.Dispose();
}
GC.Collect();
GC.WaitForPendingFinalizers();
GC.Collect();
Check(Alive() == 0, "destroyed");

// An object that cannot be given again: it is closed, or it belongs to someone else.
using (var visitor = new Maker { Reuse = true })
{
    using var first = visitor.CallMake(1)!;
    var error = Expect<InvalidOperationException>(() => visitor.CallMake(2), "the override Visitor.make returned an object that cannot be given to C++");
    Check(error.InnerException is ObjectDisposedException, "because the object is closed");
}
// The object that the override was lent is not its to give: C++ owns it, or another call uses it.
using (var visitor = new Passing())
using (var plain = new Node(1))
using (var mine = new MyNode(2))
{
    Expect<InvalidOperationException>(() => visitor.CallPass(plain), "the override Visitor.pass returned an object that cannot be given to C++");
    Expect<InvalidOperationException>(() => visitor.CallPass(mine), "cannot be transferred");
    Check(!plain.IsClosed && !mine.IsClosed && Alive() == 2, "nothing was given, nothing was destroyed");
}
Check(Alive() == 0, "the nodes are destroyed once");
Check(Alive() == 0, "nothing is left");

// C++ destroys through Plain, which has no virtual destructor: only exactly a Plain.
using (var visitor = new PlainMaker())
{
    using var made = visitor.CallMakePlain(6)!;
    Check(made.Value() == 6, "a Plain");
    visitor.Derived = true;
    Expect<InvalidOperationException>(() => visitor.CallMakePlain(7), "the override Visitor.make_plain returned Derived");
    Expect<InvalidOperationException>(() => visitor.CallMakePlain(7), "has no virtual destructor");
}

// An object that the override made and nobody else references: C++ owns it, and it is not collected.
using (var visitor = new Fleeting())
{
    using var made = visitor.CallMake(4)!;
    GC.Collect();
    GC.WaitForPendingFinalizers();
    GC.Collect();
    Check(made.CallValue() == 4004, "the object of the user that C++ owns is not collected: " + made.CallValue());
}
Check(Alive() == 0, "destroyed");

// -- The C++ object of a director is forgotten when C++ destroys it ----------------------------------------------
// The table that finds the object of the user by the address of its C++ object roots what it holds: if an
// entry were left, nothing that was given to C++ would ever be collected.

[MethodImpl(MethodImplOptions.NoInlining)]
static WeakReference[] GiveAndTakeBack()
{
    var weak = new List<WeakReference>();
    using var maker = new Maker { Mine = true };
    for (int turn = 0; turn < 20; turn++)
    {
        using (var made = maker.CallMake(turn)!)
        {
            Check(made.CallValue() == 4000 + turn, "an object of the user that C++ owns");
            weak.Add(new WeakReference(maker.Given));
            maker.Given = null;
        }
    }
    return weak.ToArray();
}

WeakReference[] given = GiveAndTakeBack();
GC.Collect();
GC.WaitForPendingFinalizers();
GC.Collect();
Check(given.All(reference => !reference.IsAlive), "the objects that C++ destroyed are collected");
Check(Alive() == 0, "and destroyed");

// -- A call whose scope was used before: it keeps nothing of that call -------------------------------------------
// The scope of a call that gives no callback is used again by the next call of the thread. One that gives a
// callback and fails before C++ runs must drop the callback, as if it were the first call.

Check(F.Run(size => size + 1, "ab") == 3, "a call with a callback");
for (int turn = 0; turn < 5; turn++)
{
    Alive();  // calls that give no callback: their scope is the one the next call gets
}
int registered = BridgefexCallbacks.Registered;
Expect<ArgumentException>(() => F.Run(size => size, "\ud800"), "Unicode");
Check(BridgefexCallbacks.Registered == registered, "a callback that no C function got is dropped");
Check(F.Run(size => size + 1, "abc") == 4, "and the next call is fine");

// -- The override that is called on a thread of C++ gets the lent object too ---------------------------------

using (var visitor = new Probe())
using (var node = new Node(8))
{
    Check(visitor.CallSeenOnThread(node) == 108, "on a thread of C++");
    Check(visitor.Last!.IsClosed, "closed when the override returned");
}

// -- An object that C++ owns: a wrapper whose virtual methods run the override --------------------------------------

using (var keeper = new Keeper())
{
    var mine = new Self();
    keeper.Put(mine);
    Check(mine.IsClosed, "given to C++");
    Check(!keeper.AskSame(), "same() gets a wrapper: the object is C++'s now");
    Check(mine.Asked >= 1, "the override of the object that C++ owns ran");
    Check(!mine.SameSawItself, "the object itself is not lent when C++ owns it");
    using var node = new Node(2);
    Check(keeper.AskSeen(node) == 2, "the override of the owned visitor");
}

GC.Collect();
GC.WaitForPendingFinalizers();
GC.Collect();
Check(Alive() == 0, "no node left");
Check(BridgefexCallbacks.Registered == 0, $"C++ holds no director, not {BridgefexCallbacks.Registered}");
Check(unobserved.IsEmpty, "nothing was left unobserved");

Console.WriteLine("directors objects (C# only): C# round trip OK");

// -- The classes of the user ----------------------------------------------------------------------------

class Probe : Visitor
{
    public Node? Last;
    public bool PointerWasNull;

    public override int Seen(Node node)
    {
        Last = node;
        return 100 + node.CallValue();
    }

    public override int Touch(Node node)
    {
        Last = node;
        node.Set(node.CallValue() + 1);
        return 1000 + node.CallValue() - 1;
    }

    public override int Pointer(Node? node)
    {
        Last = node;
        PointerWasNull = node is null;
        return node is null ? -2 : 200 + node.CallValue();
    }
}

class ConstMutator : Visitor
{
    public override int Seen(Node node)
    {
        node.Set(0);
        return 0;
    }

    public override int Pointer(Node? node)
    {
        node!.Set(0);
        return 0;
    }

    public override int TouchPointer(Node node)
    {
        node.Set(node.CallValue() + 100);
        return node.CallValue();
    }
}

class Passing : Visitor
{
    public override Node? Pass(Node node) => node;
}

class Fleeting : Visitor
{
    public override Node? Make(int value) => new Heavy(value);
}

class MyNode : Node
{
    public int Calls;

    public MyNode(int value) : base(value)
    {
    }

    public override int Value()
    {
        Calls++;
        return base.Value();
    }
}

class Self : Visitor
{
    public int Asked;
    public bool SameSawItself;

    public override bool Same(Visitor? other)
    {
        Asked++;
        SameSawItself = ReferenceEquals(other, this);
        return SameSawItself;
    }
}

class Closing : Visitor
{
    public override int Seen(Node node)
    {
        node.Dispose();
        return 0;
    }
}

class Taker : Visitor
{
    public Node? Kept;
    public bool TookNull;

    public override int Take(Node? node)
    {
        if (node is null)
        {
            TookNull = true;
            return -2;
        }
        Kept = node;
        return 100 + node.CallValue();
    }

    public override int Both(Node a, Node b, Node c)
    {
        int total = 1000000 + a.CallValue() + b.CallValue() * 10 + c.CallValue() * 100;
        a.Dispose();
        c.Dispose();
        return total;
    }
}

class Failing : Visitor
{
    public int TakeCalls;

    public override int Marker() => throw new FormatException("marker");

    public override int Take(Node? node)
    {
        TakeCalls++;
        return 0;
    }
}

class Maker : Visitor
{
    public Node? Given;
    public bool Nothing;
    public bool Mine;
    public bool Reuse;

    public override Node? Make(int value)
    {
        if (Nothing)
        {
            return null;
        }
        if (Mine)
        {
            Given = new Heavy(value);
            return Given;
        }
        if (Reuse && Given is not null)
        {
            return Given;
        }
        Given = new Node(value * 10);
        return Given;
    }
}

class Heavy : Node
{
    public Heavy(int value) : base(value)
    {
    }

    public override int Value() => 4000 + base.Value();
}

class PlainMaker : Visitor
{
    public bool Derived;

    public override Plain? MakePlain(int value) => Derived ? new Derived(value) : new Plain(value);
}
