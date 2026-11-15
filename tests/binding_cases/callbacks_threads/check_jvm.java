// SPDX-License-Identifier: Apache-2.0
// Callbacks that threads of C++ run after the call returned, what C++ gets from a callback that
// fails, and a callback beside the result that C++ allocates.

import static checks.Checks.check;
import static checks.Checks.eventually;
import static checks.Checks.expect;

import java.lang.management.ManagementFactory;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.atomic.AtomicInteger;
import thr.BridgefexCallbacks;
import thr.thr.Functions;

class Check {
    static final ConcurrentLinkedQueue<Throwable> UNOBSERVED = new ConcurrentLinkedQueue<Throwable>();

    static void failing() {
        throw new IllegalStateException("failed");
    }

    public static void main(String[] args) {
        BridgefexCallbacks.setUnobserved(UNOBSERVED::add);

        // -- A thread of C++ runs the callback after the call that gave it returned ---------------------
        final AtomicInteger got = new AtomicInteger();
        Functions.spawn(value -> {
            got.set(value);
            throw new IllegalStateException("late " + value);
        }, 7, 50);
        // The call returned normally: its callback belongs to no call, so the error is unobserved.
        eventually(() -> UNOBSERVED.size() == 1, "the exception of a callback run after its call");
        Throwable late = UNOBSERVED.poll();
        check(got.get() == 7 && late instanceof IllegalStateException && "late 7".equals(late.getMessage()),
            "unobserved: " + late);
        eventually(() -> Functions.running() == 0 && BridgefexCallbacks.registered() == 0,
            "C++ lets the callback go when its thread ends");
        check(UNOBSERVED.isEmpty(), "reported once");

        // -- A thread that C++ started is detached when it ends -----------------------------------------
        final int before = ManagementFactory.getThreadMXBean().getThreadCount();
        final AtomicInteger runs = new AtomicInteger();
        for (int n = 0; n < 20; n++) {
            Functions.spawn(value -> runs.incrementAndGet(), n, 20);
        }
        eventually(() -> runs.get() == 20 && Functions.running() == 0, "the threads ran");
        eventually(() -> ManagementFactory.getThreadMXBean().getThreadCount() <= before + 3,
            "the threads of C++ are detached when they end: " + before + " before, "
                + ManagementFactory.getThreadMXBean().getThreadCount() + " now");
        check(BridgefexCallbacks.registered() == 0, "nothing is left");

        // -- What C++ gets from a callback that failed is the default of its type ----------------------------
        Functions.runByte(value -> (byte) (value * 2));
        check(Functions.lastByte() == -10, "byte " + Functions.lastByte());
        Functions.runFloat(value -> value * 2);
        check(Functions.lastFloat() == 3.0F, "float " + Functions.lastFloat());
        Functions.runDouble(value -> value + 0.5);
        check(Functions.lastDouble() == 3.0, "double " + Functions.lastDouble());
        Functions.runLong(value -> value + 1);
        check(Functions.lastLong() == (1L << 40) + 1, "long " + Functions.lastLong());
        Functions.runBool(value -> !value);
        check(!Functions.lastBool(), "bool " + Functions.lastBool());

        expect(IllegalStateException.class, () -> Functions.runByte(value -> {
            failing();
            return value;
        }), "failed");
        check(Functions.lastByte() == 0, "C++ gets 0 as a byte: " + Functions.lastByte());
        expect(IllegalStateException.class, () -> Functions.runFloat(value -> {
            failing();
            return value;
        }), "failed");
        check(Functions.lastFloat() == 0.0F, "C++ gets 0 as a float: " + Functions.lastFloat());
        expect(IllegalStateException.class, () -> Functions.runDouble(value -> {
            failing();
            return value;
        }), "failed");
        check(Functions.lastDouble() == 0.0, "C++ gets 0 as a double: " + Functions.lastDouble());
        expect(IllegalStateException.class, () -> Functions.runLong(value -> {
            failing();
            return value;
        }), "failed");
        check(Functions.lastLong() == 0L, "C++ gets 0 as a long: " + Functions.lastLong());
        Functions.runBool(value -> true);
        expect(IllegalStateException.class, () -> Functions.runBool(value -> {
            failing();
            return value;
        }), "failed");
        check(!Functions.lastBool(), "C++ gets false: " + Functions.lastBool());
        check(BridgefexCallbacks.registered() == 0, "nothing is left");

        // -- A callback beside a result that C++ allocates -----------------------------------------------------
        check("value=42".equals(Functions.describe(() -> 42)), "describe");
        check(java.util.Arrays.equals(Functions.collect(4, i -> i * i), new int[] {0, 1, 4, 9}), "collect");
        for (int turn = 0; turn < 200; turn++) {
            expect(IllegalStateException.class, () -> Functions.describe(() -> {
                failing();
                return 0;
            }), "failed");
            expect(IllegalStateException.class, () -> Functions.collect(3, i -> {
                failing();
                return i;
            }), "failed");
        }
        check("value=1".equals(Functions.describe(() -> 1)), "the bindings go on");
        check(BridgefexCallbacks.registered() == 0, "nothing is left");
        check(UNOBSERVED.isEmpty(), "nothing is unobserved");

        System.out.println("callbacks threads: round trip OK");
    }
}
