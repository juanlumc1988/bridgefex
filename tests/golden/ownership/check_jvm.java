// SPDX-License-Identifier: Apache-2.0
// Round trip through the generated Java bindings of the "ownership" case.
//
// tests/test_jvm_roundtrip.py builds this file together with the generated project and
// tests/support/jvm/Checks.java, and runs it with OWN_LIBRARY pointing at the built library.

import static checks.Checks.check;
import static checks.Checks.collect;
import static checks.Checks.eventually;
import static checks.Checks.expect;
import static checks.Checks.settled;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CyclicBarrier;
import java.util.concurrent.atomic.AtomicInteger;
import own.BridgefexException;
import own.node.Item;
import own.node.Node;
import own.tree.Box;
import own.tree.Driver;
import own.tree.Functions;
import own.tree.Pair;
import own.tree.Peek;
import own.tree.Pipeline;
import own.tree.Tree;

class Check {
    // The nodes that exist once the garbage is collected: a baseline that a collection cannot lower.
    static long liveNodes() {
        return settled(Node::live);
    }

    // Objects that are made and forgotten in another method: the unoptimized code of the caller
    // would keep their references in slots of the stack.
    static void forgetNode(Tree tree) {
        tree.makeNode(1);
    }

    static Driver driverOf(int value) {
        return new Driver(new Node(value));
    }

    static Pair pairOf() {
        return new Pair(new Node(1), new Node(2));
    }

    static Node borrowedFromForgotten() {
        Tree owner = new Tree();
        return owner.root();
    }

    static void setCursorOf(Tree owner, int value) {
        owner.setCursor(new Node(value));
    }

    static Peek peekOf(int value) {
        return new Peek(new Node(value));
    }

    static void keepForever() {
        Tree.setSharedCursor(new Node(79));
        Functions.setGlobalNode(new Node(80));
    }

    static class Special extends Node {
        Special(int value) {
            super(value);
        }
    }

    public static void main(String[] args) throws Exception {
        // The singleton and its root Node live until the program ends.
        Tree.global();

        Node a = new Node(3);
        Node b = new Node(4);
        Tree tree = new Tree();

        // References and pointers.
        check(tree.sum(a, b) == 7, "sum");
        tree.bump(a);
        check(a.value() == 4, "bump");
        check(tree.read(b) == 4, "read");
        tree.write(b, 40);
        check(b.value() == 40, "write");

        // A copy: the caller keeps its object.
        long live = Node.live();
        tree.addCopy(a);
        check(Node.live() == live, "the copy is destroyed");
        check(a.value() == 4 && tree.copies() == 1004, "copies");

        // Overloads that differ in the way they take the node.
        check(tree.weightOwnNode(a) == 4, "weight(const Node&)");
        check(tree.weightOwnNodeRef(a) == 8, "weight(Node&)");
        check(tree.weightOwnNodeCptr(a) == 12, "weight(const Node*)");
        check(tree.weightOwnNodePtr(a) == 16, "weight(Node*)");
        check(Tree.twice(a) == 8, "twice");

        // A class of another header, and a move-only one.
        try (Item item = new Item(10)) {
            check(Functions.total(a, item) == 14, "total");
            check(Functions.idOf(item) == 10, "idOf");
        }

        // Wrong arguments are refused before anything runs.
        expect(NullPointerException.class, () -> tree.sum(a, null), "b");
        expect(NullPointerException.class, () -> tree.read(null), "node");
        check(tree.sum(a, b) == 44, "still works");

        // A closed object cannot be used, as a parameter or as this.
        Node closed = new Node(1);
        closed.close();
        expect(IllegalStateException.class, () -> tree.sum(a, closed), "Node object is closed");
        expect(IllegalStateException.class, () -> tree.bump(closed), "Node object is closed");
        expect(IllegalStateException.class, closed::value, "Node object is closed");
        closed.close();
        Tree dead = new Tree();
        dead.close();
        expect(IllegalStateException.class, () -> dead.sum(a, b), "Tree object is closed");

        // Results by value and in a std::unique_ptr: new objects that Java owns.
        long before = liveNodes();
        Node made = tree.makeNode(9);
        check(made.value() == 9 && Node.live() == before + 1, "makeNode");
        made.close();
        check(Node.live() == before, "closing destroys it");
        try (Item madeItem = tree.makeItem(3)) {
            check(madeItem.id() == 3, "makeItem");
        }
        try (Item unique = tree.makeUnique(4, true)) {
            check(unique != null && unique.id() == 4, "makeUnique");
        }
        check(tree.makeUnique(4, false) == null, "an empty unique_ptr is null");
        expect(BridgefexException.class, () -> tree.makeChecked(-1), "negative");
        check(tree.makeChecked(2).value() == 2, "makeChecked");
        forgetNode(tree);
        eventually(() -> Node.live() == before, "a collected object is destroyed");

        // Results by reference are borrowed: the C++ object belongs to its owner.
        Node root = tree.root();
        root.setValue(50);
        check(tree.croot().value() == 50 && root.value() == 50, "root");
        Node croot = tree.croot();
        expect(IllegalStateException.class, () -> croot.setValue(1), "read-only");
        expect(IllegalStateException.class, () -> tree.bump(croot), "read-only");
        check(croot.value() == 50, "const is not modified");
        check(croot.isReadOnly() && !root.isReadOnly(), "isReadOnly");
        // A read-only object goes where C++ does not change it: a const reference or pointer, and a copy.
        check(tree.sum(croot, b) == 50 + b.value(), "a read-only object as a const reference");
        check(Functions.twin(croot).value() == 50, "a read-only object is copied");
        check(tree.valueOf(tree.cfind(50)) == 50, "a read-only pointer as a const pointer");
        check(Functions.same(croot).value() == 50, "and as the argument of a function");
        root.close();
        check(tree.root().value() == 50, "closing a borrowed object destroys nothing");
        expect(IllegalStateException.class, root::value, "closed");

        // A borrowed object keeps its owners alive, and is invalid once one of them is closed.
        Node borrowed = borrowedFromForgotten();
        long otherLive = liveNodes();
        collect();
        check(Node.live() == otherLive && borrowed.value() == 0, "the owner is kept alive");
        Tree other = new Tree();
        Node borrowedFromOther = other.root();
        Node chained = borrowedFromOther.me();
        check(chained.value() == 0 && chained.cme().value() == 0, "chained borrows");
        other.close();
        check(borrowedFromOther.isClosed() && chained.isClosed(), "borrowed from a closed object");
        expect(IllegalStateException.class, borrowedFromOther::value, "closed");
        expect(IllegalStateException.class, chained::value, "closed");

        // A function returns what it borrows from its arguments.
        Node first = new Node(1);
        Node second = new Node(2);
        check(Functions.pick(first, second, true).value() == 2, "pick second");
        Node picked = Functions.pick(first, second, false);
        first.setValue(11);
        check(picked.value() == 11, "pick first");
        second.close();
        expect(IllegalStateException.class, picked::value, "closed"); // borrowed from both
        check(Functions.twin(first).value() == 11, "twin");
        check(Functions.same(first).value() == 11, "same");
        expect(IllegalStateException.class, () -> Functions.same(first).setValue(1), "read-only");
        Tree globalTree = Tree.global();
        check(globalTree.root().value() == globalTree.croot().value(), "global");
        globalTree.close(); // borrowed from nobody: nothing is destroyed
        check(Tree.global().croot().value() == 0, "global is still there");

        // Transfer: the C++ code takes the objects, and Java gives them up.
        long itemsBefore = Item.live();
        Item five = new Item(5);
        tree.adopt(five);
        expect(IllegalStateException.class, five::id, "Item object is closed");
        five.close();
        check(tree.items() == 1 && tree.itemSum() == 5 && Item.live() == itemsBefore + 1, "adopt");
        check(tree.first().id() == 5, "first");
        expect(IllegalStateException.class, () -> tree.adopt(five), "closed");
        expect(NullPointerException.class, () -> tree.adopt(null), "item");

        // Everything is checked before anything is given up.
        Item x = new Item(7);
        Item y = new Item(8);
        expect(IllegalArgumentException.class, () -> tree.adoptBoth(x, 1, x), "given to C++");
        expect(IllegalStateException.class, () -> tree.adoptBoth(x, 1, five), "closed");
        expect(IllegalStateException.class, () -> tree.adoptBoth(tree.first(), 1, y), "cannot be transferred");
        expect(IllegalStateException.class, () -> dead.adoptBoth(x, 1, y), "Tree object is closed");
        check(x.id() == 7 && y.id() == 8, "still theirs");
        check(tree.items() == 1, "nothing was taken");
        expect(IllegalStateException.class, () -> tree.adopt(tree.first()), "cannot be transferred");

        // An exception after the objects were taken: they are destroyed anyway.
        BridgefexException error = expect(BridgefexException.class, () -> tree.adoptBoth(x, -1, y), "tag");
        check(error.status() == BridgefexException.EXCEPTION, "status");
        expect(IllegalStateException.class, x::id, "closed");
        expect(IllegalStateException.class, y::id, "closed");
        check(Item.live() == itemsBefore + 1, "taken and destroyed");
        tree.adoptBoth(new Item(9), 1, new Item(10));
        check(tree.items() == 3 && tree.itemSum() == 24, "adoptBoth");

        // Constructors and functions take them too.
        Item eleven = new Item(11);
        Pipeline pipeline = new Pipeline(eleven);
        expect(IllegalStateException.class, eleven::id, "closed");
        check(pipeline.firstId() == 11 && Item.live() == itemsBefore + 4, "pipeline");
        pipeline.close();
        check(Item.live() == itemsBefore + 3, "pipeline destroys its item");
        Item failing = new Item(12);
        expect(BridgefexException.class, () -> new Pipeline(failing, 100), "below the minimum");
        expect(IllegalStateException.class, failing::id, "closed");
        check(Item.live() == itemsBefore + 3, "the item of a failed constructor is destroyed");
        Item last = new Item(14);
        check(Functions.consume(last) == 14, "consume");
        expect(IllegalStateException.class, last::id, "closed");
        check(Item.live() == itemsBefore + 3, "consumed");

        // What the tree owns is destroyed with it.
        Tree treeWithItems = new Tree();
        treeWithItems.adopt(new Item(1));
        check(Item.live() == itemsBefore + 4, "owned by the tree");
        treeWithItems.close();
        check(Item.live() == itemsBefore + 3, "destroyed with the tree");

        // Subclasses are accepted.
        Special special = new Special(100);
        check(tree.sum(special, a) == 104, "a subclass");
        special.close();

        // A constructor keeps what the C++ object keeps a pointer to, until it is closed.
        long nodeLive = liveNodes();
        Driver driver = driverOf(7);
        collect();
        check(Node.live() == nodeLive + 1 && driver.read() == 7, "the driver keeps its node");
        driver.close();
        eventually(() -> Node.live() == nodeLive, "closing the driver releases its node");
        Pair pair = pairOf();
        collect();
        check(pair.sum() == 3 && Node.live() == nodeLive + 2, "the pair keeps its nodes");
        pair.close();
        eventually(() -> Node.live() == nodeLive, "closing the pair releases its nodes");

        // A failed constructor retains nothing that matters, and leaks nothing.
        expect(NullPointerException.class, () -> new Driver(null), "node");
        expect(NullPointerException.class, () -> new Pair(new Node(1), null), "second");
        eventually(() -> Node.live() == nodeLive, "no leak");

        // Raw pointers: the annotations say who owns them.
        long baseNodes = liveNodes();
        root = tree.root();
        root.setValue(50);
        Node found = tree.find(50);
        check(found != null && found.value() == 50, "find");
        check(tree.find(51) == null, "find nothing");
        Node cfound = tree.cfind(50);
        check(cfound != null && cfound.value() == 50, "cfind");
        expect(IllegalStateException.class, () -> cfound.setValue(1), "read-only");
        check(tree.cfind(51) == null, "cfind nothing");

        // An owned pointer is Java's: null when there is nothing, else to destroy.
        while (tree.items() > 0) {
            tree.detach().close();
        }
        check(tree.detach() == null, "nothing to detach");
        long baseItems = Item.live();
        tree.adopt(new Item(31));
        check(Item.live() == baseItems + 1, "adopted");
        try (Item detached = tree.detach()) {
            check(detached != null && detached.id() == 31, "detach");
        }
        check(Item.live() == baseItems, "detached and destroyed");
        Item raw = new Item(20);
        tree.adoptRaw(raw);
        expect(IllegalStateException.class, raw::id, "closed");
        expect(NullPointerException.class, () -> tree.adoptRaw(null), "item");
        check(tree.detach().id() == 20, "adoptRaw");

        // Pointers that may be null.
        check(tree.valueOf(null) == -1 && tree.valueOf(a) == a.value(), "valueOf");
        check(tree.both(null, b) == b.value() && tree.both(null, null) == 0, "both");
        expect(NullPointerException.class, () -> tree.sum(null, b), "a");
        check(Tree.labelLength(null) == -1 && Tree.labelLength("abc") == 3, "labelLength");
        expect(IllegalArgumentException.class, () -> Tree.labelLength("a\0b"), "NUL");

        // A reference is borrowed from the arguments that are not null.
        Node only = new Node(61);
        Node pickedFirst = Functions.pickFirst(only, null);
        check(pickedFirst.value() == 61, "pickFirst");
        Node otherNode = new Node(62);
        check(Functions.pickFirst(only, otherNode).value() == 62, "pickFirst with two");
        only.close();
        expect(IllegalStateException.class, pickedFirst::value, "closed");
        otherNode.close();

        // Bytes, not text: the annotations say so for this result (the bytes 'a', 0, 0xff).
        check(java.util.Arrays.equals(tree.signature(), new byte[] {'a', 0, (byte) 0xff}), "signature");

        // What the C++ object keeps pointers to is kept alive by it, until it is closed.
        setCursorOf(tree, 77);
        collect();
        check(Node.live() == baseNodes + 1 && tree.cursorValue() == 77, "the cursor is kept");
        Tree holder = new Tree();
        setCursorOf(holder, 78);
        collect();
        check(holder.cursorValue() == 78 && Node.live() == baseNodes + 3, "holder's cursor and root");
        holder.close();
        eventually(() -> Node.live() == baseNodes + 1, "its root and its cursor went with it");
        // A pointer that is replaced stays alive until the object is closed.
        tree.setCursor(a);
        collect();
        check(Node.live() == baseNodes + 1 && tree.cursorValue() == a.value(), "replaced cursor kept");

        // A taken object keeps pointing to what it retained, also when it is given back.
        long backBefore = liveNodes();
        Box box = new Box();
        Tree cursorHolder = new Tree();
        setCursorOf(cursorHolder, 88);
        box.put(cursorHolder);
        collect();
        Tree back = box.pop();
        box.close();
        collect();
        check(back.cursorValue() == 88 && Node.live() == backBefore + 2, "kept after giving back");
        back.close();
        collect();
        // Its root went with it; the node it kept is kept for ever (C++ may have used it elsewhere).
        check(Node.live() == backBefore + 1, "what a given object retained is kept for ever");

        // A constructor keeps what it gets, unless it only reads it.
        Peek peek = peekOf(4);
        collect();
        check(peek.seen() == 4 && Node.live() == backBefore + 1, "peek keeps nothing");
        peek.close();

        // Without an object to keep them, a function or a static method keeps them for ever.
        long foreverBefore = liveNodes();
        keepForever();
        collect();
        check(Tree.sharedCursorValue() == 79 && Functions.globalNodeValue() == 80, "kept for ever");
        check(Node.live() == foreverBefore + 2, "two nodes kept for ever");

        // Concurrency: two threads give the same object, and only one of them wins.
        for (int round = 0; round < 50; round++) {
            Item contested = new Item(round);
            CyclicBarrier barrier = new CyclicBarrier(2);
            AtomicInteger wins = new AtomicInteger();
            AtomicInteger refusals = new AtomicInteger();
            List<Thread> contenders = new ArrayList<>();
            for (int number = 0; number < 2; number++) {
                Thread contender = new Thread(() -> {
                    try {
                        barrier.await();
                        Functions.consume(contested);
                        wins.incrementAndGet();
                    } catch (IllegalStateException refused) {
                        refusals.incrementAndGet();
                    } catch (Exception failure) {
                        throw new AssertionError(failure);
                    }
                });
                contenders.add(contender);
                contender.start();
            }
            for (Thread contender : contenders) {
                contender.join();
            }
            check(wins.get() == 1 && refusals.get() == 1,
                "one winner, round " + round + ": " + wins + " wins, " + refusals + " refusals");
        }

        System.out.println("ownership: Java round trip OK");
    }
}
