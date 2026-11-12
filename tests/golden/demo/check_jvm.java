// SPDX-License-Identifier: Apache-2.0
// Round trip through the generated Java bindings of the "demo" case.
//
// tests/test_jvm_roundtrip.py builds this file together with the generated project and
// tests/support/jvm/Checks.java, and runs it with DEMO_LIBRARY pointing at the built library.

import static checks.Checks.check;
import static checks.Checks.eventually;
import static checks.Checks.expect;

import demo.BridgefexException;
import demo.counter.Counter;
import demo.geometry.Circle;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ConcurrentLinkedQueue;

class Check {
    // The object is forgotten in another method: the unoptimized code of main would keep its
    // reference in a slot of the stack.
    static void forget() {
        Counter forgotten = new Counter(7);
        check(forgotten.value() == 7 && Counter.liveInstances() == 1, "forgotten");
    }

    public static void main(String[] args) throws Exception {
        // -- Counter -----------------------------------------------------------------------------
        check(Counter.liveInstances() == 0, "no counter yet");
        Counter first = new Counter();
        check(first.value() == 0 && first.isZero(), "a new counter is zero");
        Counter second = new Counter(41);
        second.increment();
        check(second.value() == 42 && Counter.liveInstances() == 2, "increment, two counters");

        // Overloads have the name of the method and the suffix of the C API.
        second.addInt32(-2);
        second.addDouble(0.6);
        check(second.value() == 41, "overloads");

        // Exceptions become BridgefexException, with the status and the message of C++.
        BridgefexException error = expect(BridgefexException.class, () -> second.setLimit(-1), "negative");
        check(error.status() == BridgefexException.EXCEPTION, "status of std::exception");
        check(error.statusName().equals("ERROR_EXCEPTION"), "name of the status");
        check(new BridgefexException(BridgefexException.NULL_ARGUMENT, "").statusName().equals("ERROR_NULL_ARGUMENT"), "status 3");
        check(new BridgefexException(BridgefexException.INVALID_ARGUMENT, "").statusName().equals("ERROR_INVALID_ARGUMENT"), "status 5");
        check(new BridgefexException(99, "").statusName().equals("UNKNOWN_STATUS_99"), "a status that is not known");
        second.setLimit(41);
        check(second.limit() == 41, "limit");
        expect(BridgefexException.class, second::increment, "limit reached");
        error = expect(BridgefexException.class, second::fail);
        check(error.status() == BridgefexException.UNKNOWN_EXCEPTION, "status of an unknown exception");
        check(error.statusName().equals("ERROR_UNKNOWN_EXCEPTION"), "name of the status 2");
        error = expect(BridgefexException.class, () -> second.reserve(42));
        check(error.status() == BridgefexException.OUT_OF_MEMORY, "status of a bad_alloc");
        check(error.statusName().equals("ERROR_OUT_OF_MEMORY"), "name of the status 4");
        second.reserve(41);

        // The extremes of the types work (and the C++ value stays in range).
        second.addInt32(Integer.MIN_VALUE + 1);
        second.addInt32(Integer.MAX_VALUE);
        check(second.value() == 41, "extremes");

        // Lifetime: close is idempotent, a closed object cannot be used, and the collector
        // destroys the objects that were forgotten.
        check(!second.isClosed(), "open");
        second.close();
        second.close();
        check(second.isClosed(), "closed");
        expect(IllegalStateException.class, second::value, "closed");
        check(Counter.liveInstances() == 1, "one counter left");
        first.close();
        forget();
        eventually(() -> Counter.liveInstances() == 0, "a collected object is destroyed");
        try (Counter scoped = new Counter(5)) {
            check(scoped.value() == 5 && Counter.liveInstances() == 1, "try-with-resources");
        }
        check(Counter.liveInstances() == 0, "try-with-resources closes");

        check(demo.counter.Functions.multiplyInt32Int32(-3, 1 << 30) == -3L * (1 << 30), "free function");

        // -- Geometry ----------------------------------------------------------------------------
        try (Circle circle = new Circle(2.0)) {
            check(circle.radius() == 2.0, "radius");
            check(Math.abs(circle.area() - Math.PI * 4.0) < 1e-12, "area");
            circle.scale(1.5);
            check(circle.radius() == 3.0, "scale");
            expect(BridgefexException.class, () -> circle.scale(0.0), "positive");
        }
        // A constructor that throws reports the error and creates nothing.
        expect(BridgefexException.class, () -> new Circle(-1.0), "radius");
        check(demo.geometry.Functions.distance(0.0, 0.0, 3.0, 4.0) == 5.0, "distance");
        check(demo.geometry.Functions.multiplyDoubleDouble(1.5, 4) == 6.0, "multiply");

        // -- Threads: each one reads the message of its own last failure -------------------------
        ConcurrentLinkedQueue<String> failures = new ConcurrentLinkedQueue<>();
        List<Thread> threads = new ArrayList<>();
        for (int number = 0; number < 8; number++) {
            int mine = number;
            Thread thread = new Thread(() -> {
                try (Circle circle = new Circle(1.0)) {
                    for (int turn = 0; turn < 200; turn++) {
                        try {
                            if (mine % 2 == 1) {
                                circle.scale(-1.0);
                            } else {
                                try (Counter counter = new Counter()) {
                                    counter.setLimit(-1);
                                }
                            }
                            failures.add("no error");
                        } catch (BridgefexException failure) {
                            String wanted = mine % 2 == 1 ? "positive" : "negative";
                            if (!failure.getMessage().contains(wanted)) {
                                failures.add(failure.getMessage());
                            }
                        }
                    }
                }
            });
            threads.add(thread);
            thread.start();
        }
        for (Thread thread : threads) {
            thread.join();
        }
        check(failures.isEmpty(), "threads: " + failures.stream().limit(3).toList());

        // Many threads use one object while another one closes it: each call either runs or says
        // the object is closed, and nothing is destroyed under a running call.
        for (int round = 0; round < 20; round++) {
            Counter shared = new Counter();
            List<Thread> users = new ArrayList<>();
            ConcurrentLinkedQueue<String> unexpected = new ConcurrentLinkedQueue<>();
            for (int user = 0; user < 4; user++) {
                Thread thread = new Thread(() -> {
                    for (int turn = 0; turn < 500; turn++) {
                        try {
                            shared.value();
                        } catch (IllegalStateException closed) {
                            return;
                        } catch (Throwable failure) {
                            unexpected.add(failure.toString());
                            return;
                        }
                    }
                });
                users.add(thread);
                thread.start();
            }
            shared.close();
            for (Thread thread : users) {
                thread.join();
            }
            check(unexpected.isEmpty(), "close while in use: " + unexpected.stream().limit(1).toList());
        }
        check(Counter.liveInstances() == 0, "nothing leaks");

        System.out.println("demo: Java round trip OK");
    }
}
