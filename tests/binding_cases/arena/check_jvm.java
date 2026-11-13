// SPDX-License-Identifier: Apache-2.0
// The memory of a call of the JNI bindings, and what the bindings keep while they are in use. Each
// section is a failure that an adversarial review found.
//
// A request that does not fit the slab of the thread gets a buffer of its own, which only the
// segment referred to: the generated code passes the address (a long) to the native method, so the
// collector freed the buffer while C++ was still reading it (a wrong checksum, a SIGSEGV).
//
// The slab is the memory of the outermost arena of a thread and goes back to a pool when it ends:
// a thread that is not in a call has none, and one thread per call (virtual threads) does not
// allocate a block for each.
//
// tests/test_jvm_roundtrip.py builds this file together with the generated project and
// tests/support/jvm/Checks.java, and runs it with ARENA_LIBRARY pointing at the built library.

import static checks.Checks.check;
import static checks.Checks.collect;
import static checks.Checks.eventually;
import static checks.Checks.expect;

import arena.BridgefexArena;
import arena.BridgefexRuntime;
import arena.BridgefexSegment;
import arena.arena.Box;
import arena.arena.Functions;
import java.lang.ref.WeakReference;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.nio.ByteBuffer;
import java.util.HashSet;
import java.util.Set;

class Check {
    private static ByteBuffer bufferOf(BridgefexSegment segment) throws ReflectiveOperationException {
        Field field = BridgefexSegment.class.getDeclaredField("buffer");
        field.setAccessible(true);
        return (ByteBuffer) field.get(segment);
    }

    // Only the address is kept, as the generated code does: the segment is garbage from here on.
    static long allocateAndForget(BridgefexArena arena, int size, WeakReference<?>[] buffer)
            throws ReflectiveOperationException {
        BridgefexSegment segment = arena.allocate(size);
        buffer[0] = new WeakReference<Object>(bufferOf(segment));
        return segment.address();
    }

    // What the helpers of the glue do with what they are given wrongly: an exception, not a crash
    // of the JVM. (They are package private: this is reflection.)
    static Throwable helper(String name, Class<?>[] types, Object... arguments) throws Exception {
        Method method = Class.forName("arena.BridgefexNative").getDeclaredMethod(name, types);
        method.setAccessible(true);
        try {
            method.invoke(null, arguments);
        } catch (InvocationTargetException error) {
            return error.getCause();
        }
        throw new AssertionError("no exception");
    }

    // The memory of the arenas of a thread after another is the same: the block goes back to the pool.
    static void arenasOfOneThreadAreOneBlock() throws InterruptedException {
        Set<Long> blocks = new HashSet<Long>();
        for (int round = 0; round < 50; round++) {
            Thread thread = new Thread(() -> {
                BridgefexArena arena = BridgefexRuntime.arena();
                try {
                    long address = arena.allocate(8).address();
                    synchronized (blocks) {
                        blocks.add(address);
                    }
                } finally {
                    arena.close();
                }
            });
            thread.start();
            thread.join();
        }
        check(blocks.size() == 1, "50 threads, one after another, used " + blocks.size() + " blocks of memory");
        // Nested arenas share the block of their thread: the inner one is after the outer one.
        BridgefexArena outer = BridgefexRuntime.arena();
        long first = outer.allocate(8).address();
        BridgefexArena inner = BridgefexRuntime.arena();
        long second = inner.allocate(8).address();
        check(second >= first + 8, "an inner arena does not reuse the memory of the outer one");
        inner.close();
        check(outer.allocate(8).address() == first + 8 || outer.allocate(8).address() > first,
            "the outer arena goes on after the inner one is closed");
        outer.close();
    }

    static void arenasAreClosedInOrder() throws Exception {
        BridgefexArena outer = BridgefexRuntime.arena();
        BridgefexSegment kept = outer.allocate(16);
        kept.set(BridgefexRuntime.C_LONG_LONG, 0, 0x1122334455667788L);
        BridgefexArena inner = BridgefexRuntime.arena();
        inner.allocate(64);
        // The outer one cannot end first: its memory would be given again while the inner one uses it.
        expect(IllegalStateException.class, outer::close, "opposite order");
        inner.close();
        check(kept.get(BridgefexRuntime.C_LONG_LONG, 0) == 0x1122334455667788L, "the outer memory is intact");
        outer.close();
        outer.close(); // twice is nothing
        // An arena belongs to the thread that opened it.
        BridgefexArena mine = BridgefexRuntime.arena();
        Throwable[] failure = new Throwable[1];
        Thread other = new Thread(() -> {
            try {
                mine.close();
            } catch (Throwable error) {
                failure[0] = error;
            }
        });
        other.start();
        other.join();
        check(failure[0] instanceof IllegalStateException, "another thread cannot close the arena: " + failure[0]);
        mine.close();
    }

    // An offset or a length near the biggest long does not wrap around the size.
    static void segmentsRefuseWhatIsNotTheirs() {
        BridgefexArena arena = BridgefexRuntime.arena();
        try {
            BridgefexSegment segment = arena.allocate(16);
            expect(IndexOutOfBoundsException.class,
                () -> segment.get(BridgefexRuntime.C_INT, Long.MAX_VALUE - 3), "offset");
            expect(IndexOutOfBoundsException.class,
                () -> segment.set(BridgefexRuntime.C_LONG_LONG, Long.MAX_VALUE - 3, 1L), "offset");
            expect(IndexOutOfBoundsException.class, () -> segment.asSlice(Long.MAX_VALUE - 1, 4), "slice");
            expect(IndexOutOfBoundsException.class, () -> segment.asSlice(8, Long.MAX_VALUE), "slice");
            check(segment.asSlice(8, 8).byteSize() == 8, "a slice that fits");
            check(segment.get(BridgefexRuntime.C_LONG_LONG, 8) == 0L, "the end of the segment is readable");
            expect(IndexOutOfBoundsException.class,
                () -> segment.get(BridgefexRuntime.C_LONG_LONG, 9), "offset");
        } finally {
            arena.close();
        }
    }

    // The thread that destroys the objects that nobody closed is there while it has something to wait
    // for, and has no class loader of the application that happened to make the first one.
    static Thread cleaner() {
        for (Thread thread : Thread.getAllStackTraces().keySet()) {
            if (thread.getName().equals("bridgefex-cleaner") && thread.isAlive()) {
                return thread;
            }
        }
        return null;
    }

    static void forgetBoxes(int count) {
        for (int index = 0; index < count; index++) {
            check(new Box().value() == 42, "a box");
        }
    }

    static void theCleanerLeavesWhenThereIsNothingToWaitFor() {
        forgetBoxes(10);
        Thread thread = cleaner();
        // It may be gone already, if the collector was quick.
        check(thread == null || thread.getContextClassLoader() == null,
            "the cleaner has no context class loader of the application");
        collect();
        eventually(() -> {
            System.gc();
            return cleaner() == null;
        }, "the cleaner ends when every object that it waited for was collected");
        // ... and comes back for the next one.
        Box box = new Box();
        check(box.value() == 42, "a box after the cleaner ended");
        check(cleaner() != null, "the cleaner is back");
        box.close();
    }

    public static void main(String[] args) throws Exception {
        BridgefexRuntime.arena().close(); // loads the library
        check(helper("addressOf", new Class<?>[] {ByteBuffer.class}, ByteBuffer.allocate(8))
            instanceof IllegalArgumentException, "a buffer of the heap has no address");
        check(helper("addressOf", new Class<?>[] {ByteBuffer.class}, (Object) null)
            instanceof IllegalArgumentException, "no buffer, no address");
        check(helper("length", new Class<?>[] {long.class}, 0L) instanceof NullPointerException,
            "the length of the text at NULL");
        check(helper("copyOut", new Class<?>[] {long.class, int.class}, 0L, 8)
            instanceof NullPointerException, "a copy from NULL");
        expect(IllegalStateException.class, () -> BridgefexRuntime.takeBytes(BridgefexSegment.NULL, 8),
            "NULL pointer");
        check(BridgefexRuntime.takeBytes(BridgefexSegment.NULL, 0).length == 0, "nothing from NULL is nothing");

        // The buffer of a big allocation lives until the arena is closed, not until the segment is
        // unreachable: it is the memory of the call.
        WeakReference<?>[] buffer = new WeakReference<?>[1];
        BridgefexArena arena = BridgefexRuntime.arena();
        long address = allocateAndForget(arena, 1 << 20, buffer);
        check(address != 0L, "it has an address");
        collect();
        check(buffer[0].get() != null, "the buffer of a big allocation lives while the arena is open");
        arena.close();
        // ... and it is not kept longer than that.
        eventually(() -> {
            System.gc();
            return buffer[0].get() == null;
        }, "the buffer is released once the arena is closed");

        // The same with a size that fits the slab: it is the slab that the thread keeps.
        WeakReference<?>[] small = new WeakReference<?>[1];
        BridgefexArena second = BridgefexRuntime.arena();
        allocateAndForget(second, 64, small);
        collect();
        second.close();

        // A function that takes big text, called many times while the collector runs: the sum is
        // that of the text every time.
        StringBuilder builder = new StringBuilder();
        long expected = 0;
        for (int index = 0; index < 300_000; index++) {
            char letter = (char) ('a' + index % 26);
            builder.append(letter);
            expected += letter;
        }
        String text = builder.toString();
        Thread collector = new Thread(() -> {
            while (!Thread.currentThread().isInterrupted()) {
                System.gc();
                try {
                    Thread.sleep(1);
                } catch (InterruptedException error) {
                    return;
                }
            }
        });
        collector.setDaemon(true);
        collector.start();
        for (int round = 0; round < 200; round++) {
            check(Functions.arenaChecksum(text) == expected, "the sum of a big text, round " + round);
        }
        collector.interrupt();

        // Many strings, which take the slab and then a buffer each: all of them are read by C++, with
        // the collector running, and what C++ hashed is what Java sent.
        String[] words = new String[20_000];
        for (int index = 0; index < words.length; index++) {
            words[index] = "word-" + index;
        }
        long hash = 0xcbf29ce484222325L;
        for (String word : words) {
            for (byte item : word.getBytes("UTF-8")) {
                hash = (hash ^ (item & 0xFF)) * 0x100000001b3L;
            }
            hash = (hash ^ 0xFF) * 0x100000001b3L;
        }
        Thread churn = new Thread(() -> {
            while (!Thread.currentThread().isInterrupted()) {
                byte[] garbage = new byte[1 << 20];
                garbage[0] = 1;
                System.gc();
            }
        });
        churn.setDaemon(true);
        churn.start();
        for (int round = 0; round < 20; round++) {
            check(Functions.arenaHash(words) == hash, "the hash of 20000 strings, round " + round);
        }
        churn.interrupt();

        arenasOfOneThreadAreOneBlock();
        arenasAreClosedInOrder();
        segmentsRefuseWhatIsNotTheirs();
        theCleanerLeavesWhenThereIsNothingToWaitFor();

        System.out.println("round trip OK");
    }
}
