// SPDX-License-Identifier: Apache-2.0
// Round trip through the generated Java bindings of the "callbacks" case: a std::function goes in as
// a functional interface (or null), and C++ calls it, on any thread, while the call runs or later.
// The same checks as those of the C# bindings (check_csharp.cs), for both Java flavors, in Java 8.
//
// tests/test_jvm_roundtrip.py builds this file together with the generated project and
// tests/support/jvm/Checks.java, and runs it with CBX_LIBRARY pointing at the built library.

import static checks.Checks.check;
import static checks.Checks.collect;
import static checks.Checks.eventually;
import static checks.Checks.expect;

import cbx.BridgefexCallbacks;
import cbx.BridgefexException;
import cbx.events.Functions;
import cbx.events.Level;
import cbx.events.Mode;
import cbx.events.Point;
import cbx.events.Timer;
import cbx.events.Watcher;
import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.lang.ref.WeakReference;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.Consumer;

class Check {
    static final ConcurrentLinkedQueue<Throwable> UNOBSERVED = new ConcurrentLinkedQueue<Throwable>();

    static void noCallbacks(String when) {
        check(BridgefexCallbacks.registered() == 0,
            "C++ holds no callback " + when + ", not " + BridgefexCallbacks.registered());
    }

    static class Point2 {
        final Point point;
        final String name;

        Point2(Point point, String name) {
            this.point = point;
            this.name = name;
        }
    }

    // A callback that is only reachable from C++ (the log handler): its object can be collected
    // once C++ lets the handler go.
    static WeakReference<Object> store(final List<String> sink) {
        final Object captured = new Object();
        Functions.setLogHandler((level, text) -> {
            if (captured == null) {
                throw new AssertionError();
            }
            sink.add(text);
        });
        return new WeakReference<Object>(captured);
    }

    static void abandon(List<Integer> sink) {
        Timer abandoned = new Timer(sink::add);
        abandoned.tick(1);
    }

    static int failing(List<Integer> calls, RuntimeException[] boom, int i) {
        calls.add(i);
        if (i == 2) {
            boom[0] = new IllegalStateException("boom"); // made here: the stack trace has this method
            throw boom[0];
        }
        return i;
    }

    public static void main(String[] args) throws Exception {
        BridgefexCallbacks.setUnobserved(UNOBSERVED::add);

        // -- Results, and the registry is empty after synchronous calls ------------------------------
        check(Functions.sum(5, i -> i * 2) == 20, "sum");
        check(Functions.countIf(6, i -> i % 3 == 0) == 2, "count_if: a bool result and a 64-bit argument");
        check(Functions.isEmpty(null), "null is an empty std::function");
        check(!Functions.isEmpty(() -> { }), "a Runnable without arguments");
        check(Functions.classify(1, value -> Level.ERROR) == Level.ERROR, "an enum result");
        check(Functions.pick(() -> Mode.AUTO) == Mode.AUTO, "an enum without a fixed type");
        check(Math.abs(Functions.integrate(x -> x, 0.0, 2.0, 4) - 2.0) < 1e-12, "a double");
        check(Timer.apply(21, value -> value * 2) == 42, "a static method");
        check(Functions.sum(4, Math::abs) == 6, "a method reference");
        noCallbacks("after synchronous calls");

        // -- Arguments: structs, const char* (null for NULL), the three string types, enums ----------
        final List<Point2> seen = new ArrayList<Point2>();
        check(Functions.walk((point, name) -> {
            seen.add(new Point2(point, name));
            return point.x;
        }) == 4.5, "walk");
        check(seen.size() == 2, "walk calls twice");
        check(seen.get(0).point.x == 1.5 && seen.get(0).point.y == 2.5 && "first".equals(seen.get(0).name),
            "a struct and a string");
        check(seen.get(1).point.x == 3.0 && seen.get(1).point.y == 4.0 && seen.get(1).name == null,
            "a NULL const char* is null");

        final List<Object> shifted = new ArrayList<Object>();
        Functions.shift((point, mode) -> {
            shifted.add(point);
            shifted.add(mode);
        });
        check(shifted.size() == 2 && ((Point) shifted.get(0)).x == -1.0 && ((Point) shifted.get(0)).y == 1.0
            && shifted.get(1) == Mode.AUTO, "shift");

        final List<String> texts = new ArrayList<String>();
        Functions.strings((a, b, c) -> {
            texts.add(a);
            texts.add(b);
            texts.add(c);
        });
        check(texts.equals(Arrays.asList("a\0b", "a\0b", "a\0b")),
            "string, std::string and std::string_view keep a NUL");
        noCallbacks("after the callbacks with arguments");

        // -- A result that C++ cannot hold is refused, and the call raises it ------------------------
        expect(IllegalStateException.class, () -> Functions.pick(() -> Mode.of(42)),
            "which is not a value of the enum (0..3)");
        expect(IllegalStateException.class, () -> Functions.pick(() -> Mode.of(4)), "returned 4");
        check(Functions.pick(() -> Mode.of(3)).equals(Mode.of(3)), "the last value of the enum is accepted");
        expect(IllegalStateException.class, () -> Functions.pick(() -> Mode.of(-1)), "returned -1");
        check(Functions.classify(1, value -> Level.of(200)).equals(Level.of(200)),
            "an enum with a fixed type holds every value of it");
        expect(NullPointerException.class, () -> Functions.pick(() -> null), "is null");
        noCallbacks("after the refused results");

        // -- The first exception of a callback is the one the call raises; C++ gets defaults ------------
        final List<Integer> calls = new ArrayList<Integer>();
        final RuntimeException[] boom = new RuntimeException[1];
        IllegalStateException raised = expect(IllegalStateException.class,
            () -> Functions.sum(1000, i -> failing(calls, boom, i)), "boom");
        check(raised == boom[0], "it is the exception of the callback, not a copy");
        check(calls.equals(Arrays.asList(0, 1, 2)), "the callbacks after the failure do not run: " + calls);
        boolean inFailing = false;
        for (StackTraceElement frame : raised.getStackTrace()) {
            inFailing |= frame.getMethodName().equals("failing");
        }
        check(inFailing, "the stack trace is that of the callback");
        noCallbacks("after a callback that throws");
        check(Functions.sum(3, i -> i) == 3, "the scope of the failed call is over");

        // Of two callbacks that fail, the call raises the first.
        IllegalArgumentException twice = expect(IllegalArgumentException.class, () -> Functions.sum(5, i -> {
            if (i == 0) {
                throw new IllegalArgumentException("first");
            }
            throw new IllegalStateException("second");
        }), "first");
        check("first".equals(twice.getMessage()), "the first one");

        // An exception in a call that a callback makes is raised by that call, and the callback may catch it.
        check(Functions.sum(2, i -> {
            try {
                Functions.sum(1, ignored -> {
                    throw new NumberFormatException("inner");
                });
                return -100;
            } catch (NumberFormatException expected) {
                return i;
            }
        }) == 1, "an inner call raises its own callbacks' exceptions");
        check((int) Functions.sum(3, i -> (int) Functions.sum(i, j -> j)) == 1, "a call inside a callback");
        // ... and if the callback does not catch it, it is the exception of the outer callback.
        expect(NumberFormatException.class, () -> Functions.sum(2, i -> (int) Functions.sum(1, ignored -> {
            throw new NumberFormatException("inner");
        })), "inner");
        noCallbacks("after nested calls");

        // A C++ exception: BridgefexException, and the callback is released.
        expect(BridgefexException.class, () -> Functions.integrate(x -> x, 0.0, 1.0, 0), "steps must be positive");
        noCallbacks("after a C++ exception");

        // An empty std::function called by C++: bad_function_call.
        expect(BridgefexException.class, () -> Functions.sum(1, null), "function");

        // -- Callbacks from C++ threads; an exception there is raised by the call that gave it ----------
        final ConcurrentLinkedQueue<Integer> hits = new ConcurrentLinkedQueue<Integer>();
        Functions.parallel(4, 25, hits::add);
        check(hits.size() == 100 && new HashSet<Integer>(hits).equals(new HashSet<Integer>(Arrays.asList(0, 1, 2, 3))),
            "parallel");
        expect(ArithmeticException.class, () -> Functions.parallel(3, 5, thread -> {
            throw new ArithmeticException("thread");
        }));
        final AtomicInteger threadCalls = new AtomicInteger();
        expect(ArithmeticException.class, () -> Functions.parallel(1, 25, thread -> {
            threadCalls.incrementAndGet();
            throw new ArithmeticException("thread");
        }));
        check(threadCalls.get() == 1, "the callbacks after the failure do not run, also on a C++ thread: " + threadCalls);
        noCallbacks("after the callbacks of C++ threads");
        check(UNOBSERVED.isEmpty(), "an exception of a callback that a call raised is not also unobserved");

        // -- Stored callbacks: kept while C++ keeps them, released on replacement --------------------
        final List<String> log = new ArrayList<String>();
        final List<Level> levels = new ArrayList<Level>();
        Functions.setLogHandler((level, text) -> {
            levels.add(level);
            log.add(text);
        });
        check(log.size() == 1 && levels.get(0) == Level.INFO && log.get(0).equals("set"),
            "the handler is called when it is stored");
        check(Functions.log(Level.DEBUG, "x\0y"), "log");
        check(log.get(log.size() - 1).equals("x\0y") && levels.get(levels.size() - 1) == Level.DEBUG,
            "a NUL in a string argument");
        check(BridgefexCallbacks.registered() == 1, "C++ holds the handler");
        Functions.setLogHandler(null);
        noCallbacks("after replacing the handler");
        check(!Functions.log(Level.DEBUG, "lost"), "no handler");

        // What C++ holds is alive for the garbage collector, and is free once C++ lets it go.
        final List<String> sink = new ArrayList<String>();
        final WeakReference<Object> weak = store(sink);
        collect();
        check(weak.get() != null, "C++ keeps the handler, which keeps what it captures");
        check(Functions.log(Level.INFO, "still"), "the handler survived a collection");
        check(sink.get(sink.size() - 1).equals("still"), "and runs");
        Functions.setLogHandler(null);
        eventually(() -> {
            System.gc();
            return weak.get() == null;
        }, "once released, what it captured can be collected");
        noCallbacks("after the collection");

        // -- An exception in a stored callback, called later, belongs to no call ----------------------
        Functions.setLogHandler((level, text) -> {
            if (text.equals("late")) {
                throw new IndexOutOfBoundsException("late");
            }
        });
        check(Functions.log(Level.INFO, "late"), "a call whose stored callback fails returns normally");
        Throwable late = UNOBSERVED.poll();
        check(UNOBSERVED.isEmpty() && late instanceof IndexOutOfBoundsException, "it is unobserved: " + late);
        check(Functions.log(Level.INFO, "fine"), "the handler goes on");
        check(UNOBSERVED.isEmpty(), "an exception is reported once");
        Functions.setLogHandler(null);

        // Without a handler of unobserved, the exception is written to the standard error; a handler
        // that throws does not reach C++.
        BridgefexCallbacks.setUnobserved(null);
        PrintStream standardError = System.err;
        ByteArrayOutputStream captured = new ByteArrayOutputStream();
        System.setErr(new PrintStream(captured, true, "UTF-8"));
        try {
            Functions.setLogHandler((level, text) -> {
                if (text.equals("x")) {
                    throw new IndexOutOfBoundsException("to stderr");
                }
            });
            check(Functions.log(Level.INFO, "x"), "the call returns");
        } finally {
            System.setErr(standardError);
        }
        String written = captured.toString("UTF-8");
        check(written.contains("Unobserved exception in a C++ callback") && written.contains("to stderr"),
            "stderr: " + written);
        BridgefexCallbacks.setUnobserved(error -> {
            throw new IllegalStateException("the handler fails");
        });
        check(Functions.log(Level.INFO, "x"), "a handler that throws does not reach C++");
        Functions.setLogHandler(null);
        BridgefexCallbacks.setUnobserved(UNOBSERVED::add);

        // -- Objects: the callback lives as long as the C++ object -------------------------------------
        final List<Integer> ticks = new ArrayList<Integer>();
        Timer timer = new Timer(ticks::add);
        timer.tick(3);
        check(ticks.equals(Arrays.asList(0, 1, 2)) && timer.ticks() == 3, "a timer");
        check(BridgefexCallbacks.registered() == 1, "the timer holds its callback");
        timer.setOnTick(null);
        noCallbacks("after setOnTick(null)");
        timer.setOnTick(ticks::add);
        timer.close();
        noCallbacks("after close");

        abandon(ticks);
        collect();
        eventually(() -> {
            System.gc();
            return BridgefexCallbacks.registered() == 0;
        }, "the cleaner of an object releases its callback");

        // The callback of a timer is stored: the call that runs it later is not the call that gave it,
        // so the exception of the callback belongs to no call (every tick is reported, and C++ goes on).
        timer = new Timer(value -> {
            throw new NumberFormatException("tick " + value);
        });
        timer.tick(5);
        check(timer.ticks() == 5, "the loop of C++ ran to its end: " + timer.ticks());
        List<String> messages = new ArrayList<String>();
        for (Throwable error : UNOBSERVED) {
            messages.add(error.getMessage());
        }
        check(messages.equals(Arrays.asList("tick 0", "tick 1", "tick 2", "tick 3", "tick 4")), "unobserved: " + messages);
        UNOBSERVED.clear();
        timer.setOnTick(null);
        timer.tick(2);
        check(timer.ticks() == 7 && UNOBSERVED.isEmpty(), "the object is usable after the exceptions");
        timer.close();

        // A callback may close the object whose method runs it: the object is destroyed when the call ends.
        final Timer[] closing = new Timer[1];
        final AtomicInteger closingTicks = new AtomicInteger();
        closing[0] = new Timer(null);
        closing[0].setOnTick(value -> {
            closingTicks.incrementAndGet();
            closing[0].close();
        });
        closing[0].tick(3);
        check(closingTicks.get() == 3, "C++ finishes its loop, with the object alive until the call ends: " + closingTicks);
        check(closing[0].isClosed(), "and it is closed");
        noCallbacks("after a callback closed its object");

        // A call that fails before the C call drops what it registered.
        final Timer dead = new Timer(null);
        dead.close();
        expect(IllegalStateException.class, () -> dead.setOnTick(ticks::add));
        noCallbacks("after a call that failed before the C function");

        // -- Beside by-reference parameters -------------------------------------------------------------
        check(Functions.prefixed("ab", text -> text.equals("ab!") ? text.length() : -1) == 3,
            "a std::string argument and a callback");
        final List<Integer> each = new ArrayList<Integer>();
        try (Timer plain = new Timer(null)) {
            check(plain.each(new int[] {4, 5, 6}, each::add) == 3 && each.equals(Arrays.asList(4, 5, 6)),
                "a span and a callback");
        }
        final List<String> notified = new ArrayList<String>();
        final List<Point> points = new ArrayList<Point>();
        try (Watcher watcher = new Watcher("w", new Point(1.0, 2.0), (name, at) -> {
            notified.add(name);
            points.add(at);
        })) {
            watcher.notify_();
            check(notified.equals(Arrays.asList("w")) && points.get(0).x == 1.0 && points.get(0).y == 2.0, "a watcher");
            check(BridgefexCallbacks.registered() == 1, "the watcher holds its callback");
        }
        noCallbacks("after the watcher");

        // -- A callback that C++ keeps, in a call that has returned -----------------------------------------
        Functions.storeAndSleep(value -> value * 2, 10);
        check(BridgefexCallbacks.registered() == 1, "C++ keeps the handler after the call");
        check(Functions.fire(21) == 42, "fire");
        Functions.storeAndSleep(null, 0);
        noCallbacks("after storing null");
        check(Functions.fire(21) == -1, "no handler");

        // A stored callback that an unrelated call runs on another thread, while the call that stored it
        // still runs: the exception is not the exception of that call (which returns normally and whose
        // callback goes on), and the call that ran it gave no callbacks, so it is unobserved.
        {
            final ConcurrentLinkedQueue<Integer> failures = new ConcurrentLinkedQueue<Integer>();
            final String[] outcome = {""};
            Thread storing = new Thread(() -> {
                try {
                    Functions.storeAndSleep(value -> {
                        failures.add(value);
                        throw new NumberFormatException("late " + value);
                    }, 500);
                    outcome[0] = "returned";
                } catch (Throwable error) {
                    outcome[0] = error.getClass().getSimpleName();
                }
            });
            storing.start();
            long deadline = System.nanoTime() + 30_000_000_000L;
            while (failures.isEmpty()) {
                check(System.nanoTime() < deadline, "the callback was not stored");
                Functions.fire(1);
                Thread.sleep(5);
            }
            int count = failures.size();
            Functions.fire(2); // not skipped, as it would be if the storing call had taken the error
            check(failures.size() == count + 1, "the callback of the storing call goes on");
            storing.join();
            check(outcome[0].equals("returned"), "the storing call returned normally: " + outcome[0]);
            check(UNOBSERVED.size() == failures.size(), "unobserved: " + UNOBSERVED.size() + " of " + failures.size());
            UNOBSERVED.clear();
            Functions.storeAndSleep(null, 0);
            noCallbacks("after the thread");
        }

        // A stored callback that fails in a call that is itself inside a call with callbacks: that call raises it.
        Functions.setLogHandler((level, text) -> {
            if (text.equals("inner")) {
                throw new NumberFormatException("from the log");
            }
        });
        expect(NumberFormatException.class, () -> Functions.sum(2, i -> Functions.log(Level.INFO, "inner") ? 1 : 0),
            "from the log");
        Functions.setLogHandler(null);
        noCallbacks("after the stored callback");
        check(UNOBSERVED.isEmpty(), "it was raised, not reported");

        // -- Many threads give callbacks at once ----------------------------------------------------------
        final ConcurrentLinkedQueue<String> errors = new ConcurrentLinkedQueue<String>();
        List<Thread> workers = new ArrayList<Thread>();
        for (int n = 0; n < 8; n++) {
            final int number = n;
            workers.add(new Thread(() -> {
                for (int turn = 0; turn < 300; turn++) {
                    final int factor = number + 1;
                    if (Functions.sum(10, i -> i * factor) != 45L * factor) {
                        errors.add("sum " + number + " " + turn);
                    }
                    final int t = turn;
                    if (Timer.apply(turn, value -> value + number) != t + number) {
                        errors.add("apply " + number + " " + turn);
                    }
                }
                try {
                    Functions.sum(3, i -> {
                        throw new NumberFormatException(Integer.toString(number));
                    });
                    errors.add("no exception in thread " + number);
                } catch (NumberFormatException error) {
                    if (!error.getMessage().equals(Integer.toString(number))) {
                        errors.add("wrong exception in thread " + number);
                    }
                }
            }));
        }
        for (Thread worker : workers) {
            worker.start();
        }
        for (Thread worker : workers) {
            worker.join();
        }
        check(errors.isEmpty(), "threads: " + errors);
        noCallbacks("after the threads");
        for (int turn = 0; turn < 20000; turn++) {
            Functions.sum(1, i -> 1);
        }
        noCallbacks("after many calls");
        check(UNOBSERVED.isEmpty(), "nothing was left unobserved");

        // -- At exit, a C++ global still holds a callback and a static destructor calls it: the bindings
        // disable the callbacks when the JVM exits, so it is neither called nor released.
        Functions.setLogHandler((level, text) -> {
            if (text.equals("exit")) {
                // The static destructor of C++ calls the handler with "exit": that must not happen.
                Runtime.getRuntime().halt(9);
            }
        });
        Functions.logAtExit(true);
        check(BridgefexCallbacks.registered() == 1, "the handler stays at exit");

        System.out.println("callbacks: Java round trip OK");
    }
}
