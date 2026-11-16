// SPDX-License-Identifier: Apache-2.0
// The Java port of check_csharp.cs of this case (both Java bindings: tests/test_jvm_roundtrip.py).
import dob.dob.*;
import dob.BridgefexCallbacks;
import java.util.concurrent.ConcurrentLinkedQueue;

class Check {
    static int failures;
    static final ConcurrentLinkedQueue<Throwable> unobserved = new ConcurrentLinkedQueue<Throwable>();

    static void check(boolean c, String what) {
        if (!c) {
            failures++;
            System.out.println("FAIL " + what);
        }
    }

    static class MyNode extends Node {
        final int v;
        MyNode(int v) { super(v); this.v = v; }
        @Override public int value() { return v * 100; }
    }

    static Node stash;

    static class V extends Visitor {
        Node again;
        int mode;
        @Override public int seen(Node n) { return n.value(); }
        @Override public int touch(Node n) { n.set(77); return n.value(); }
        @Override public int pointer(Node n) { return n == null ? -1 : n.value(); }
        @Override public int take(Node n) { stash = n; return n == null ? -1 : n.value(); }
        @Override public Node make(int v) {
            switch (mode) {
                case 0: return new Node(v);
                case 1: if (again == null) again = new Node(v); return again;       // the same object twice
                case 2: { Node n = new Node(v); n.close(); return n; }              // closed
                case 3: return new MyNode(v);                                      // a director
                case 4: return null;
                default: return new Node(v);
            }
        }
        @Override public Node pass(Node n) { return n; }                            // the lent object given back
        @Override public int touchPointer(Node n) { n.close(); return 1; }          // closes what C++ lent
        @Override public int both(Node a, Node b, Node c) { throw new IllegalArgumentException("both"); }
        @Override public int marker() { throw new IllegalArgumentException("marker"); }
    }

    public static void main(String[] args) throws Exception {
        BridgefexCallbacks.setUnobserved(new java.util.function.Consumer<Throwable>() {
            public void accept(Throwable t) { unobserved.add(t); }
        });
        V v = new V();
        Node n7 = new Node(7);
        check(v.callSeen(n7) == 7, "seen");
        check(v.callTouch(n7) == 77, "touch");
        check(n7.callValue() == 77, "touched");
        MyNode mn = new MyNode(3);
        check(v.callSeen(mn) == 300, "seen of a director");

        // a node that C++ lends is given back as a unique_ptr: C++ must not delete what it does not own
        for (int i = 0; i < 3; i++) {
            try {
                Node got = v.callPass(n7);
                System.out.println("pass returned " + (got == null ? "null" : "value " + got.callValue()));
                if (got != null) got.close();
            } catch (RuntimeException e) {
                System.out.println("pass threw " + e);
            }
        }
        check(n7.callValue() == 77, "n7 alive after pass");
        Node n8 = new MyNode(8);
        try {
            Node got = v.callPass(n8);
            System.out.println("pass(director) returned " + (got == null ? "null" : "value " + got.callValue()));
        } catch (RuntimeException e) {
            System.out.println("pass(director) threw " + e);
        }
        check(n8.callValue() == 800, "n8 alive after pass");

        // the same object twice
        v.mode = 1;
        Node a = v.callMake(5);
        check(a != null && a.callValue() == 5, "make once");
        try {
            Node b = v.callMake(6);
            System.out.println("make twice returned " + (b == null ? "null" : "value " + b.callValue()));
        } catch (RuntimeException e) {
            System.out.println("make twice threw " + e);
        }
        a.close();
        // a closed object
        v.mode = 2;
        try {
            Node c = v.callMake(5);
            System.out.println("make closed returned " + c);
        } catch (RuntimeException e) {
            System.out.println("make closed threw " + e);
        }
        v.mode = 3;
        Node d = v.callMake(9);
        check(d != null && d.callValue() == 900, "make a director " + (d == null ? "null" : d.callValue()));
        d.close();
        v.mode = 4;
        check(v.callMake(1) == null, "make null");

        // closes what C++ lends
        Node lent = new MyNode(11);
        try {
            int r = v.callTouchPointer(lent);
            System.out.println("touchPointer returned " + r);
        } catch (RuntimeException e) {
            System.out.println("touchPointer threw " + e);
        }
        try {
            System.out.println("lent still works: " + lent.callValue());
        } catch (RuntimeException e) {
            System.out.println("lent after close threw " + e);
        }
        // C++ uses the node after the override returned: an override that closes it
        Node lent2 = new MyNode(12);
        try {
            int r = v.callSeenThenValue(lent2);
            System.out.println("seenThenValue " + r);
        } catch (RuntimeException e) {
            System.out.println("seenThenValue threw " + e);
        }

        // the first override fails: the second must not run, the object it would get is destroyed
        int alive0 = Node.alive();
        try {
            v.callMarkerThenTake(new Node(1));
            check(false, "markerThenTake no exception");
        } catch (IllegalArgumentException e) {
            check("marker".equals(e.getMessage()), "marker msg");
        }
        try {
            v.callBoth(new Node(1), n7, new Node(2));
            check(false, "both no exception");
        } catch (IllegalArgumentException e) {
        }
        for (int i = 0; i < 5; i++) { System.gc(); Thread.sleep(30); }
        System.out.println("alive " + alive0 + " -> " + Node.alive());

        // Keeper: C++ keeps the Visitor and asks
        Keeper k = new Keeper();
        k.put(new V());
        check(k.askSeen(n7) == 77, "keeper seen");
        for (int i = 0; i < 5; i++) { System.gc(); Thread.sleep(30); }
        check(k.askSeen(n7) == 77, "keeper seen after gc");
        Visitor back = k.release();
        check(back != null, "release");
        k.close();
        System.out.println(failures == 0 ? "round trip OK" : failures + " failures");
        if (failures != 0) System.exit(1);
    }
}
