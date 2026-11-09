// SPDX-License-Identifier: Apache-2.0
// Round trip through the generated C# bindings of the "ownership" case.
//
// tests/test_csharp_roundtrip.py builds this file together with the generated project and runs
// it with OWN_LIBRARY pointing at the built library.

using System.Runtime.CompilerServices;
using Own;
using Item = Own.Node.Item;
using Node = Own.Node.Node;
using Tree = Own.Tree.Tree;
using Driver = Own.Tree.Driver;
using Pair = Own.Tree.Pair;
using Peek = Own.Tree.Peek;
using Pipeline = Own.Tree.Pipeline;
using Box = Own.Tree.Box;
using F = Own.Tree.Functions;

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

static void Collect()
{
    GC.Collect();
    GC.WaitForPendingFinalizers();
    GC.Collect();
}

// The nodes that exist once the garbage is collected: a baseline that a collection cannot lower.
static nuint LiveNodes()
{
    Collect();
    return Node.Live();
}

// Objects that are made and forgotten in another method: the unoptimized code of the caller
// would keep their references in slots of the stack.
[MethodImpl(MethodImplOptions.NoInlining)]
static void ForgetNode(Tree tree) => tree.MakeNode(1);

[MethodImpl(MethodImplOptions.NoInlining)]
static Driver DriverOf(int value) => new Driver(new Node(value));

[MethodImpl(MethodImplOptions.NoInlining)]
static Pair PairOf() => new Pair(new Node(1), new Node(2));

[MethodImpl(MethodImplOptions.NoInlining)]
static Node BorrowedFromForgotten()
{
    var owner = new Tree();
    return owner.Root();
}

// The singleton and its root Node live until the program ends.
Tree.Global();
nuint singletonNodes = Node.Live();

var a = new Node(3);
var b = new Node(4);
var tree = new Tree();

// References and pointers.
Check(tree.Sum(a, b) == 7, "sum");
tree.Bump(a);
Check(a.Value() == 4, "bump");
Check(tree.Read(b) == 4, "read");
tree.Write(b, 40);
Check(b.Value() == 40, "write");

// A copy: the caller keeps its object.
nuint live = Node.Live();
tree.AddCopy(a);
Check(Node.Live() == live, "the copy is destroyed");
Check(a.Value() == 4 && tree.Copies() == 1004, "copies");

// Overloads that differ in the way they take the node.
Check(tree.WeightOwnNode(a) == 4, "weight(const Node&)");
Check(tree.WeightOwnNodeRef(a) == 8, "weight(Node&)");
Check(tree.WeightOwnNodeCptr(a) == 12, "weight(const Node*)");
Check(tree.WeightOwnNodePtr(a) == 16, "weight(Node*)");
Check(Tree.Twice(a) == 8, "twice");

// A class of another header, and a move-only one.
using (var item = new Item(10))
{
    Check(F.Total(a, item) == 14, "total");
    Check(F.IdOf(item) == 10, "idOf");
}

// Wrong arguments are refused before anything runs.
Expect<ArgumentNullException>(() => tree.Sum(a, null!), "b");
Expect<ArgumentNullException>(() => tree.Read(null!), "node");
Check(tree.Sum(a, b) == 44, "still works");

// A closed object cannot be used, as a parameter or as this.
var closed = new Node(1);
closed.Close();
Expect<ObjectDisposedException>(() => tree.Sum(a, closed), "Node object is closed");
Expect<ObjectDisposedException>(() => tree.Bump(closed), "Node object is closed");
Expect<ObjectDisposedException>(() => closed.Value(), "Node object is closed");
closed.Close();
var dead = new Tree();
dead.Dispose();
Expect<ObjectDisposedException>(() => dead.Sum(a, b), "Tree object is closed");

// Results by value and in a std::unique_ptr: new objects that C# owns.
nuint before = LiveNodes();
var made = tree.MakeNode(9);
Check(made.Value() == 9 && Node.Live() == before + 1, "makeNode");
made.Close();
Check(Node.Live() == before, "closing destroys it");
using (var madeItem = tree.MakeItem(3))
{
    Check(madeItem.Id() == 3, "makeItem");
}
using (var unique = tree.MakeUnique(4, true))
{
    Check(unique is not null && unique.Id() == 4, "makeUnique");
}
Check(tree.MakeUnique(4, false) is null, "an empty unique_ptr is null");
Expect<BridgefexException>(() => tree.MakeChecked(-1), "negative");
Check(tree.MakeChecked(2).Value() == 2, "makeChecked");
ForgetNode(tree);
Collect();
Check(Node.Live() == before, "a collected object is destroyed");

// Results by reference are borrowed: the C++ object belongs to its owner.
var root = tree.Root();
root.SetValue(50);
Check(tree.Croot().Value() == 50 && root.Value() == 50, "root");
var croot = tree.Croot();
Expect<InvalidOperationException>(() => croot.SetValue(1), "read-only");
Expect<InvalidOperationException>(() => tree.Bump(croot), "read-only");
Check(croot.Value() == 50, "const is not modified");
root.Close();
Check(tree.Root().Value() == 50, "closing a borrowed object destroys nothing");
Expect<ObjectDisposedException>(() => root.Value(), "closed");

// A borrowed object keeps its owners alive, and is invalid once one of them is closed.
var borrowed = BorrowedFromForgotten();
nuint otherLive = Node.Live();
Collect();
Check(Node.Live() == otherLive && borrowed.Value() == 0, "the owner is kept alive");
var other = new Tree();
borrowed = other.Root();
var chained = borrowed.Me();
Check(chained.Value() == 0 && chained.Cme().Value() == 0, "chained borrows");
other.Close();
Check(borrowed.IsClosed && chained.IsClosed, "borrowed from a closed object");
Expect<ObjectDisposedException>(() => borrowed.Value(), "closed");
Expect<ObjectDisposedException>(() => chained.Value(), "closed");

// A function returns what it borrows from its arguments.
var first = new Node(1);
var second = new Node(2);
Check(F.Pick(first, second, true).Value() == 2, "pick second");
var picked = F.Pick(first, second, false);
first.SetValue(11);
Check(picked.Value() == 11, "pick first");
second.Close();
Expect<ObjectDisposedException>(() => picked.Value(), "closed");  // borrowed from both
Check(F.Twin(first).Value() == 11, "twin");
Check(F.Same(first).Value() == 11, "same");
Expect<InvalidOperationException>(() => F.Same(first).SetValue(1), "read-only");
var globalTree = Tree.Global();
Check(globalTree.Root().Value() == globalTree.Croot().Value(), "global");
globalTree.Close();  // borrowed from nobody: nothing is destroyed
Check(Tree.Global().Croot().Value() == 0, "global is still there");

// Transfer: the C++ code takes the objects, and C# gives them up.
nuint itemsBefore = Item.Live();
var five = new Item(5);
tree.Adopt(five);
Expect<ObjectDisposedException>(() => five.Id(), "Item object is closed");
five.Close();
Check(tree.Items() == 1 && tree.ItemSum() == 5 && Item.Live() == itemsBefore + 1, "adopt");
Check(tree.First().Id() == 5, "first");
Expect<ObjectDisposedException>(() => tree.Adopt(five), "closed");
Expect<ArgumentNullException>(() => tree.Adopt(null!), "item");

// Everything is checked before anything is given up.
var x = new Item(7);
var y = new Item(8);
Expect<ArgumentException>(() => tree.AdoptBoth(x, 1, x), "given to C++");
Expect<ObjectDisposedException>(() => tree.AdoptBoth(x, 1, five), "closed");
Expect<InvalidOperationException>(() => tree.AdoptBoth(tree.First(), 1, y), "cannot be transferred");
Expect<ObjectDisposedException>(() => dead.AdoptBoth(x, 1, y), "Tree object is closed");
Check(x.Id() == 7 && y.Id() == 8, "still theirs");
Check(tree.Items() == 1, "nothing was taken");
Expect<InvalidOperationException>(() => tree.Adopt(tree.First()), "cannot be transferred");

// An exception after the objects were taken: they are destroyed anyway.
var error = Expect<BridgefexException>(() => tree.AdoptBoth(x, -1, y), "tag");
Check(error.Status == BridgefexException.Exception, "status");
Expect<ObjectDisposedException>(() => x.Id(), "closed");
Expect<ObjectDisposedException>(() => y.Id(), "closed");
Check(Item.Live() == itemsBefore + 1, "taken and destroyed");
tree.AdoptBoth(new Item(9), 1, new Item(10));
Check(tree.Items() == 3 && tree.ItemSum() == 24, "adoptBoth");

// Constructors and functions take them too.
var eleven = new Item(11);
var pipeline = new Pipeline(eleven);
Expect<ObjectDisposedException>(() => eleven.Id(), "closed");
Check(pipeline.FirstId() == 11 && Item.Live() == itemsBefore + 4, "pipeline");
pipeline.Close();
Check(Item.Live() == itemsBefore + 3, "pipeline destroys its item");
var failing = new Item(12);
Expect<BridgefexException>(() => new Pipeline(failing, 100), "below the minimum");
Expect<ObjectDisposedException>(() => failing.Id(), "closed");
Check(Item.Live() == itemsBefore + 3, "the item of a failed constructor is destroyed");
var last = new Item(14);
Check(F.Consume(last) == 14, "consume");
Expect<ObjectDisposedException>(() => last.Id(), "closed");
Check(Item.Live() == itemsBefore + 3, "consumed");

// What the tree owns is destroyed with it.
var treeWithItems = new Tree();
treeWithItems.Adopt(new Item(1));
Check(Item.Live() == itemsBefore + 4, "owned by the tree");
treeWithItems.Close();
Check(Item.Live() == itemsBefore + 3, "destroyed with the tree");

// Subclasses are accepted.
var special = new Special(100);
Check(tree.Sum(special, a) == 104, "a subclass");
special.Close();

// A constructor keeps what the C++ object keeps a pointer to, until it is closed.
nuint nodeLive = LiveNodes();
var driver = DriverOf(7);
Collect();
Check(Node.Live() == nodeLive + 1 && driver.Read() == 7, "the driver keeps its node");
driver.Close();
Check(LiveNodes() == nodeLive, "closing the driver releases its node");
var pair = PairOf();
Collect();
Check(pair.Sum() == 3 && Node.Live() == nodeLive + 2, "the pair keeps its nodes");
pair.Close();
Collect();
Check(Node.Live() == nodeLive, "closing the pair releases its nodes");

// A failed constructor retains nothing that matters, and leaks nothing.
Expect<ArgumentNullException>(() => new Driver(null!), "node");
Expect<ArgumentNullException>(() => new Pair(new Node(1), null!), "second");
Collect();
Check(Node.Live() == nodeLive, "no leak");

// Raw pointers: the annotations say who owns them.
nuint baseNodes = LiveNodes();
root = tree.Root();
root.SetValue(50);
var found = tree.Find(50);
Check(found is not null && found.Value() == 50, "find");
Check(tree.Find(51) is null, "find nothing");
var cfound = tree.Cfind(50);
Check(cfound is not null && cfound.Value() == 50, "cfind");
Expect<InvalidOperationException>(() => cfound!.SetValue(1), "read-only");
Check(tree.Cfind(51) is null, "cfind nothing");

// An owned pointer is C#'s: null when there is nothing, else to destroy.
while (tree.Items() > 0)
{
    tree.Detach()!.Close();
}
Check(tree.Detach() is null, "nothing to detach");
nuint baseItems = Item.Live();
tree.Adopt(new Item(31));
Check(Item.Live() == baseItems + 1, "adopted");
using (var detached = tree.Detach())
{
    Check(detached is not null && detached.Id() == 31, "detach");
}
Check(Item.Live() == baseItems, "detached and destroyed");
var raw = new Item(20);
tree.AdoptRaw(raw);
Expect<ObjectDisposedException>(() => raw.Id(), "closed");
Expect<ArgumentNullException>(() => tree.AdoptRaw(null!), "item");
Check(tree.Detach()!.Id() == 20, "adoptRaw");

// Pointers that may be null.
Check(tree.ValueOf(null) == -1 && tree.ValueOf(a) == a.Value(), "valueOf");
Check(tree.Both(null, b) == b.Value() && tree.Both(null, null) == 0, "both");
Expect<ArgumentNullException>(() => tree.Sum(null!, b), "a");
Check(Tree.LabelLength(null) == -1 && Tree.LabelLength("abc") == 3, "labelLength");
Expect<ArgumentException>(() => Tree.LabelLength("a\0b"), "NUL");

// A reference is borrowed from the arguments that are not null.
var only = new Node(61);
var pickedFirst = F.PickFirst(only, null);
Check(pickedFirst.Value() == 61, "pickFirst");
var otherNode = new Node(62);
Check(F.PickFirst(only, otherNode).Value() == 62, "pickFirst with two");
only.Close();
Expect<ObjectDisposedException>(() => pickedFirst.Value(), "closed");
otherNode.Close();

// Bytes, not text: the annotations say so for this result (the bytes 'a', 0, 0xff).
Check(tree.Signature().SequenceEqual(new byte[] { (byte)'a', 0, 0xff }), "signature");

// What the C++ object keeps pointers to is kept alive by it, until it is closed.
[MethodImpl(MethodImplOptions.NoInlining)]
static void SetCursorOf(Tree owner, int value) => owner.SetCursor(new Node(value));

SetCursorOf(tree, 77);
Collect();
Check(Node.Live() == baseNodes + 1 && tree.CursorValue() == 77, "the cursor is kept");
var holder = new Tree();
SetCursorOf(holder, 78);
Collect();
Check(holder.CursorValue() == 78 && Node.Live() == baseNodes + 3, "holder's cursor and root");
holder.Close();
Collect();
Check(Node.Live() == baseNodes + 1, "its root and its cursor went with it");
// A pointer that is replaced stays alive until the object is closed.
tree.SetCursor(a);
Collect();
Check(Node.Live() == baseNodes + 1 && tree.CursorValue() == a.Value(), "replaced cursor kept");

// A taken object keeps pointing to what it retained, also when it is given back.
nuint backBefore = LiveNodes();
var box = new Box();
var cursorHolder = new Tree();
SetCursorOf(cursorHolder, 88);
box.Put(cursorHolder);
Collect();
var back = box.Pop()!;
box.Close();
Collect();
Check(back.CursorValue() == 88 && Node.Live() == backBefore + 2, "kept after giving back");
back.Close();
Collect();
// Its root went with it; the node it kept is kept for ever (C++ may have used it elsewhere).
Check(Node.Live() == backBefore + 1, "what a given object retained is kept for ever");

// A constructor keeps what it gets, unless it only reads it.
[MethodImpl(MethodImplOptions.NoInlining)]
static Peek PeekOf(int value) => new Peek(new Node(value));

var peek = PeekOf(4);
Collect();
Check(peek.Seen() == 4 && Node.Live() == backBefore + 1, "peek keeps nothing");
peek.Close();

// Without an object to keep them, a function or a static method keeps them for ever.
[MethodImpl(MethodImplOptions.NoInlining)]
static void KeepForever()
{
    Tree.SetSharedCursor(new Node(79));
    F.SetGlobalNode(new Node(80));
}

nuint foreverBefore = LiveNodes();
KeepForever();
Collect();
Check(Tree.SharedCursorValue() == 79 && F.GlobalNodeValue() == 80, "kept for ever");
Check(Node.Live() == foreverBefore + 2, "two nodes kept for ever");

// Concurrency: two threads give the same object, and only one of them wins.
for (int round = 0; round < 50; round++)
{
    var contested = new Item(round);
    var barrier = new Barrier(2);
    int wins = 0;
    int refusals = 0;
    var contenders = new List<Thread>();
    for (int number = 0; number < 2; number++)
    {
        var contender = new Thread(() =>
        {
            barrier.SignalAndWait();
            try
            {
                F.Consume(contested);
                Interlocked.Increment(ref wins);
            }
            catch (Exception failure) when (failure is InvalidOperationException or ObjectDisposedException)
            {
                Interlocked.Increment(ref refusals);
            }
        });
        contenders.Add(contender);
        contender.Start();
    }
    foreach (var contender in contenders)
    {
        contender.Join();
    }
    Check(wins == 1 && refusals == 1, $"one winner, round {round}: {wins} wins, {refusals} refusals");
}

Console.WriteLine("ownership: C# round trip OK");

class Special : Node
{
    public Special(int value) : base(value)
    {
    }
}
