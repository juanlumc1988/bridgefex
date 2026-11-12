// SPDX-License-Identifier: Apache-2.0
// What the check programs of the Java bindings share (tests/golden/*/check_jvm.java): assertions
// and the garbage collector. It is compiled with each of them (as `import static checks.Checks.*`).

package checks;

import java.util.function.BooleanSupplier;
import java.util.function.LongSupplier;

public final class Checks {
    private Checks() {
    }

    public static void check(boolean condition, String what) {
        if (!condition) {
            throw new AssertionError("check failed: " + what);
        }
    }

    /** Runs {@code action}, which must throw a {@code type} whose message has {@code message}. */
    public static <T extends Throwable> T expect(Class<T> type, Runnable action, String message) {
        try {
            action.run();
        } catch (Throwable error) {
            if (!type.isInstance(error)) {
                throw new AssertionError(error + " is not a " + type.getSimpleName(), error);
            }
            if (message != null && (error.getMessage() == null || !error.getMessage().contains(message))) {
                throw new AssertionError("'" + error.getMessage() + "' lacks '" + message + "'", error);
            }
            return type.cast(error);
        }
        throw new AssertionError("no " + type.getSimpleName());
    }

    public static <T extends Throwable> T expect(Class<T> type, Runnable action) {
        return expect(type, action, null);
    }

    private static void pause(long milliseconds) {
        try {
            Thread.sleep(milliseconds);
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            throw new AssertionError(error);
        }
    }

    /** Collects what is unreachable and gives the cleaner time to destroy it. */
    public static void collect() {
        for (int round = 0; round < 3; round++) {
            System.gc();
            pause(50);
        }
    }

    /** Collects until {@code condition} holds: what the collector destroys, it does when it can. */
    public static void eventually(BooleanSupplier condition, String what) {
        long deadline = System.nanoTime() + 20_000_000_000L;
        while (!condition.getAsBoolean()) {
            if (System.nanoTime() > deadline) {
                throw new AssertionError("check failed (after collecting for 20 s): " + what);
            }
            System.gc();
            pause(20);
        }
    }

    /** A value that a collection cannot lower: it has not changed in four rounds of collecting. */
    public static long settled(LongSupplier value) {
        long last = value.getAsLong();
        int same = 0;
        long deadline = System.nanoTime() + 20_000_000_000L;
        while (same < 4) {
            if (System.nanoTime() > deadline) {
                throw new AssertionError("a value that does not settle");
            }
            System.gc();
            pause(30);
            long now = value.getAsLong();
            same = now == last ? same + 1 : 0;
            last = now;
        }
        return last;
    }
}
