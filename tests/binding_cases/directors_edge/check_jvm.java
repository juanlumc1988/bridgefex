// SPDX-License-Identifier: Apache-2.0
// The Java port of check_csharp.cs of this case (both Java bindings: tests/test_jvm_roundtrip.py).
import de.de.*;
import de.BridgefexCallbacks;
import de.BridgefexException;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.atomic.AtomicBoolean;

class Check {
    static int failures;
    static final ConcurrentLinkedQueue<Throwable> unobserved = new ConcurrentLinkedQueue<Throwable>();

    static void check(boolean c, String what) {
        if (!c) {
            failures++;
            System.out.println("FAIL " + what);
        }
    }

    static class Full extends Base {
        @Override public int value() { return 10; }
        @Override public boolean flag(boolean on) { return !on; }
        @Override public long wide(long x) { return x * 2; }
        @Override public Color color() { return Color.of(1); }
        @Override public String text(String mark) { return mark == null ? "NULL!" : "M" + mark; }
        @Override public String named() { return "full"; }
        @Override public int addInt32Int32(int a, int b) { return a - b; }
        @Override public double addDouble(double a) { return a + 0.5; }
    }

    static class BadColor extends Base {
        final int v;
        BadColor(int v) { this.v = v; }
        @Override public Color color() { return Color.of(v); }
    }

    static class NullName extends Base {
        @Override public String named() { return null; }
    }

    static class FailFirst extends Base {
        int namedCalls;
        @Override public int value() { throw new NumberFormatException("value"); }
        @Override public String named() { namedCalls++; return "x"; }
    }

    static class MyThrower extends Thrower {
        final int v;
        MyThrower(int v) { super(v); this.v = v; }
        @Override public int get() { return v * 11; }
    }

    static class ThreadFail extends Base {
        @Override public int value() { throw new NumberFormatException("on a thread of C++"); }
    }

    static class Caller extends Base {
        @Override public String named() { return "c:" + super.named(); }
    }

    static class Nesting extends Base {
        final Base other;
        Nesting(Base other) { this.other = other; }
        @Override public int value() {
            try {
                return other.callValue();
            } catch (NumberFormatException e) {
                return -1;
            }
        }
    }

    static class Through extends Base {
        final Base other;
        Through(Base other) { this.other = other; }
        @Override public int value() { return other.callValue(); }
        int callValue2() {
            Base plain = new Base();
            try {
                return plain.callValue();
            } finally {
                plain.close();
            }
        }
    }

    static void keepIt(Registry registry) {
        registry.keep(new Full());
    }

    static String slow() {
        Full argument = new Full();
        return Registry.sequenceOf(argument, 5);
    }

    static void gc() throws Exception {
        for (int i = 0; i < 3; i++) {
            System.gc();
            Thread.sleep(30);
        }
    }

    public static void main(String[] args) throws Exception {
        BridgefexCallbacks.setUnobserved(new java.util.function.Consumer<Throwable>() {
            public void accept(Throwable t) { unobserved.add(t); }
        });
        Base plain = new Base();
        check(plain.callValue() == 1 && plain.callNamed().equals("base") && plain.made() == 2, "plain");
        plain.close();
        Full full = new Full();
        check(full.callValue() == 10, "int");
        check(!full.callFlag(true) && full.callFlag(false), "bool");
        check(full.callWide(21) == 42, "long");
        check(full.callColor().value() == 1, "enum");
        check("NULL!".equals(full.callTextNull()), "NULL const char* is null");
        check("Mx".equals(full.callText("x")), "const char*");
        check("full".equals(full.callNamed()), "std::string");
        check(full.callAdd(5, 3) == 2, "overload");
        check(full.callAddDouble(1.0) == 1.5, "other overload");
        check("10:full".equals(full.pair()), "pair");
        full.close();

        BadColor bad = new BadColor(7);
        try {
            bad.callColor();
            check(false, "bad color no exception");
        } catch (IllegalStateException e) {
            check(e.getMessage().contains("returned 7"), "bad color message " + e.getMessage());
        }
        NullName nn = new NullName();
        try {
            nn.callNamed();
            check(false, "null name no exception");
        } catch (NullPointerException | IllegalStateException e) {
            // ok
        }

        FailFirst first = new FailFirst();
        try {
            first.pair();
            check(false, "failfirst no exception");
        } catch (NumberFormatException e) {
            check(e.getMessage().equals("value"), "failfirst");
        }
        check(first.namedCalls == 0, "named() not called after value() failed");

        MyThrower t = new MyThrower(3);
        check(t.callGet() == 33, "thrower");
        int before = BridgefexCallbacks.registered();
        try {
            new MyThrower(-1);
            check(false, "thrower(-1) no exception");
        } catch (BridgefexException e) {
            check(e.getMessage().contains("negative"), "negative");
        }
        check(BridgefexCallbacks.registered() == before, "registered after failed ctor " + BridgefexCallbacks.registered() + " vs " + before);

        // a director C++ points to is not collected
        Registry registry = new Registry();
        keepIt(registry);
        check("full|10".equals(registry.sequence()), "retained");
        gc();
        check("full|10".equals(registry.sequence()), "retained after gc");

        final AtomicBoolean stop = new AtomicBoolean();
        Thread collector = new Thread(new Runnable() {
            public void run() {
                while (!stop.get()) {
                    System.gc();
                    try { Thread.sleep(2); } catch (InterruptedException e) { return; }
                }
            }
        });
        collector.start();
        for (int turn = 0; turn < 20; turn++) {
            String r = slow();
            check("full|10|full".equals(r), "argument not collected while C++ uses it: " + r);
        }
        stop.set(true);
        collector.join();

        // throw on a thread of C++
        {
            Registry r2 = new Registry();
            ThreadFail failing = new ThreadFail();
            r2.keep(failing);
            check(unobserved.isEmpty(), "nothing yet");
            check(r2.valueOnThread() == 0, "default result on a thread of C++");
            check(unobserved.size() == 1 && unobserved.poll() instanceof NumberFormatException, "unobserved exception");
            r2.close();
        }

        FailFirst failing = new FailFirst();
        Full fine = new Full();
        for (int turn = 0; turn < 50; turn++) {
            try {
                failing.pair();
                check(false, "no exception");
            } catch (NumberFormatException e) {
            }
            check(fine.callValue() == 10 && "10:full".equals(fine.pair()), "call after one that failed " + turn);
        }
        FailFirst inner = new FailFirst();
        Nesting nesting = new Nesting(inner);
        for (int turn = 0; turn < 20; turn++) {
            check(nesting.callValue() == -1, "nesting caught");
            check(inner.namedCalls == 0, "inner named not run");
        }
        FailFirst inner2 = new FailFirst();
        Through through = new Through(inner2);
        try {
            through.callValue();
            check(false, "through no exception");
        } catch (NumberFormatException e) {
        }
        check(through.callValue2() == 1, "thread not left in failed call");

        Caller caller = new Caller();
        check("c:base".equals(caller.callNamed()), "base call");
        caller.close();
        try {
            caller.named();
            check(false, "closed no exception");
        } catch (IllegalStateException e) {
        }
        registry.close();
        full = null; plain = null;
        gc();
        System.out.println("alive=" + Base.alive() + " registered=" + BridgefexCallbacks.registered());
        System.out.println(failures == 0 ? "round trip OK" : failures + " failures");
        if (failures != 0) System.exit(1);
    }
}
