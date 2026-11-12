// SPDX-License-Identifier: Apache-2.0
// The lifetime of the objects of the Java bindings under threads and under the collector. Each
// section is a failure that an adversarial review of the C# bindings found (a use after free of
// the C++ object, a leak, or an object that did not look closed once it was): the Java bindings
// have their own implementation of it, over a Cleaner.
//
// tests/test_jvm_roundtrip.py builds this file together with the generated project and
// tests/support/jvm/Checks.java, and runs it with LIFE_LIBRARY pointing at the built library.

import static checks.Checks.check;
import static checks.Checks.collect;
import static checks.Checks.eventually;
import static checks.Checks.expect;

import java.lang.foreign.MemorySegment;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CyclicBarrier;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.atomic.AtomicInteger;
import life.BridgefexException;
import life.BridgefexHandle;
import life.life.Functions;
import life.life.Holder;
import life.life.M;
import life.life.N;
import life.life.P;
import life.life.R;
import life.life.Res;

class Check {
    // An object is in a call of another thread for a while.
    static Thread inCall(Res res, int milliseconds, int[] seen) throws InterruptedException {
        Thread thread = new Thread(() -> seen[0] = res.slowId(milliseconds));
        thread.start();
        Thread.sleep(100); // it is inside the C++ method now
        return thread;
    }

    static void storeAndFail(Holder holder, int id) {
        expect(BridgefexException.class, () -> holder.setThenThrow(new Res(id)), "stored, then failed");
    }

    static void forgetPairs(int count) {
        for (int i = 0; i < count; i++) {
            Holder holder = new Holder();
            holder.set(new Res(i));
            Holder borrowed = new Holder();
            borrowed.set(holder.inner());
        }
    }

    // A handle over a destroyer that counts, without a C++ object: the state that the calls, the
    // user and the collector share, alone.
    static BridgefexHandle counting(AtomicInteger destroyed, boolean owns) {
        BridgefexHandle handle = new BridgefexHandle(pointer -> destroyed.incrementAndGet(), owns);
        handle.assign(MemorySegment.ofAddress(0x1000));
        return handle;
    }

    static void theHandle() throws Exception {
        // The collector (run) does not destroy an object under a call: the compiler may take the
        // reference of a wrapper for dead once the call has started.
        AtomicInteger destroyed = new AtomicInteger();
        BridgefexHandle handle = counting(destroyed, true);
        check(handle.tryLease() == BridgefexHandle.LeaseResult.OK, "a call starts");
        handle.run();
        check(destroyed.get() == 0, "not destroyed under a call, by the collector");
        handle.releaseLease();
        check(destroyed.get() == 1, "destroyed when the call ends");
        handle.run();
        handle.closeObject();
        check(destroyed.get() == 1, "once");
        check(handle.tryLease() == BridgefexHandle.LeaseResult.CLOSED, "closed");

        // Closing does the same, and a lease that is not the last one does not destroy.
        destroyed.set(0);
        handle = counting(destroyed, true);
        handle.tryLease();
        handle.tryLease();
        handle.closeObject();
        handle.releaseLease();
        check(destroyed.get() == 0, "one call is still running");
        handle.releaseLease();
        check(destroyed.get() == 1, "the last one destroys");

        // A claim is for one call alone, and a lease cannot start under it.
        destroyed.set(0);
        handle = counting(destroyed, true);
        check(handle.tryLease() == BridgefexHandle.LeaseResult.OK, "lease");
        check(handle.tryClaim() == BridgefexHandle.LeaseResult.BUSY, "no claim under a call");
        handle.releaseLease();
        check(handle.tryClaim() == BridgefexHandle.LeaseResult.OK, "claim");
        check(handle.tryLease() == BridgefexHandle.LeaseResult.BUSY, "no call under a claim");
        check(handle.tryClaim() == BridgefexHandle.LeaseResult.BUSY, "one claim");
        handle.releaseLease(); // the claim that gave nothing ends with its call
        check(handle.tryLease() == BridgefexHandle.LeaseResult.OK, "the claim ended");
        handle.releaseLease();

        // An object that was given is C++'s: neither the collector nor close destroy it.
        check(handle.tryClaim() == BridgefexHandle.LeaseResult.OK, "claim to give");
        handle.giveUp();
        handle.releaseLease();
        handle.run();
        handle.closeObject();
        check(destroyed.get() == 0, "given objects are not destroyed by Java");
        check(handle.tryLease() == BridgefexHandle.LeaseResult.CLOSED, "and it is closed");

        // An object that C++ owns is never destroyed by Java.
        BridgefexHandle borrowed = counting(destroyed, false);
        borrowed.run();
        borrowed.closeObject();
        check(destroyed.get() == 0, "borrowed");

        // Threads that call, claim, close and collect: destroyed once, and never under a call.
        for (int round = 0; round < 300; round++) {
            AtomicInteger count = new AtomicInteger();
            AtomicInteger running = new AtomicInteger();
            AtomicInteger under = new AtomicInteger();
            BridgefexHandle shared = new BridgefexHandle(pointer -> {
                count.incrementAndGet();
                if (running.get() != 0) {
                    under.incrementAndGet();
                }
            }, true);
            shared.assign(MemorySegment.ofAddress(0x2000));
            CyclicBarrier barrier = new CyclicBarrier(6);
            List<Thread> threads = new ArrayList<>();
            for (int number = 0; number < 6; number++) {
                int role = number;
                threads.add(new Thread(() -> {
                    try {
                        barrier.await();
                        for (int turn = 0; turn < 20; turn++) {
                            switch (role % 3) {
                                case 0 -> {
                                    if (shared.tryLease() == BridgefexHandle.LeaseResult.OK) {
                                        running.incrementAndGet();
                                        Thread.onSpinWait();
                                        running.decrementAndGet();
                                        shared.releaseLease();
                                    }
                                }
                                case 1 -> {
                                    if (shared.tryClaim() == BridgefexHandle.LeaseResult.OK) {
                                        running.incrementAndGet();
                                        if (ThreadLocalRandom.current().nextBoolean()) {
                                            shared.giveUp();
                                        }
                                        running.decrementAndGet();
                                        shared.releaseLease();
                                    }
                                }
                                default -> {
                                    if (role == 2 && turn == 10) {
                                        shared.closeObject();
                                    } else {
                                        shared.run();
                                    }
                                }
                            }
                        }
                    } catch (Exception failure) {
                        throw new AssertionError(failure);
                    }
                }));
            }
            for (Thread thread : threads) {
                thread.start();
            }
            for (Thread thread : threads) {
                thread.join();
            }
            check(count.get() <= 1, "destroyed at most once, round " + round);
            check(under.get() == 0, "destroyed under a call, round " + round);
        }
    }

    public static void main(String[] args) throws Exception {
        theHandle();

        // 1. An object that another thread is using is not given to C++ (which would destroy it under that call).
        {
            Res res = new Res(5);
            int[] seen = {-1};
            Thread thread = inCall(res, 400, seen);
            expect(IllegalStateException.class, () -> Functions.consume(res), "another call uses it");
            check(!res.isClosed(), "the object is still open after the refusal");
            thread.join();
            check(seen[0] == 5, "the call that was running finished");
            check(Functions.consume(res) == 5, "and now it can be given");
        }

        // 1b. The same when the object the other thread uses is the owner of a borrowed one.
        {
            Holder holder = new Holder();
            Res inner = holder.inner();
            Thread thread = new Thread(() -> inner.slowId(400));
            thread.start();
            Thread.sleep(100);
            holder.close();
            check(holder.isClosed() && inner.isClosed(), "closed, although a call is running");
            check(Holder.live() == 1, "and alive until the call ends");
            expect(IllegalStateException.class, inner::id, "closed");
            thread.join(); // the holder is destroyed when that call ends, not under it
            check(Holder.live() == 0, "destroyed after the call");
        }

        // 5. A closed object looks closed, also while a call of another thread is using it.
        {
            Res res = new Res(6);
            int[] seen = {-1};
            Thread thread = inCall(res, 300, seen);
            res.close();
            check(res.isClosed(), "isClosed after close");
            check(Res.live() == 1, "the object lives while a call uses it");
            expect(IllegalStateException.class, res::id, "closed");
            expect(IllegalStateException.class, () -> res.set(1), "closed");
            expect(IllegalStateException.class, () -> Functions.consume(res), "closed");
            thread.join();
            check(seen[0] == 6, "the running call saw its object");
            check(Res.live() == 0, "destroyed when the call ended");
        }

        // 2. An object that the C++ object of another one points to is not given either.
        {
            Holder holder = new Holder();
            Res res = new Res(7);
            holder.set(res);
            expect(IllegalStateException.class, () -> Functions.consume(res), "points to it");
            check(holder.peek() == 7 && !res.isClosed(), "the pointer is still good");
            holder.close();
            check(Functions.consume(res) == 7, "given once nothing points to it");
        }

        // 3. A call that stores a pointer and then throws has kept it.
        {
            Holder holder = new Holder();
            storeAndFail(holder, 8);
            collect();
            check(holder.peek() == 8 && Res.live() == 2, "what the failed call stored is alive (and the holder's own Res)");
            holder.close();
            eventually(() -> Res.live() == 0 && Holder.badDestructions() == 0, "and is released with the holder");
        }

        // 4. An object that points to a part of itself, or two that point to each other, are destroyed when they are closed.
        {
            Holder holder = new Holder();
            holder.set(holder.inner());
            holder.close();
            eventually(() -> Holder.live() == 0 && Res.live() == 0, "a self-reference is destroyed");

            Holder first = new Holder();
            Holder second = new Holder();
            first.set(second.inner());
            second.set(first.inner());
            first.close();
            second.close();
            eventually(() -> Holder.live() == 0 && Res.live() == 0, "two that point to each other are destroyed when closed");
        }

        // The collector destroys what points before what is pointed to, whatever order it finds them in.
        {
            int badBefore = Holder.badDestructions(); // two holders that point to each other cannot both be first
            forgetPairs(300);
            eventually(() -> Holder.live() == 0 && Res.live() == 0, "everything was collected");
            check(Holder.badDestructions() == badBefore, "no destructor found what it points to destroyed");
        }

        // Closing an object that is pointed to destroys it all the same: that is up to the user.
        {
            Holder holder = new Holder();
            Res res = new Res(9);
            holder.set(res);
            res.close();
            check(res.isClosed(), "closed although it is pointed to");
            expect(IllegalStateException.class, res::id, "closed");
            holder.set(holder.inner()); // do not leave the holder pointing to it
            holder.close();
        }

        // Many threads giving, using and closing the same object: it is destroyed exactly once, and
        // no call runs on a destroyed object.
        for (int round = 0; round < 200; round++) {
            Res res = new Res(round);
            CyclicBarrier barrier = new CyclicBarrier(3);
            AtomicInteger given = new AtomicInteger();
            AtomicInteger refused = new AtomicInteger();
            List<Thread> threads = List.of(
                new Thread(() -> {
                    try {
                        barrier.await();
                        Functions.consume(res);
                        given.incrementAndGet();
                    } catch (IllegalStateException failure) {
                        refused.incrementAndGet();
                    } catch (Exception failure) {
                        throw new AssertionError(failure);
                    }
                }),
                new Thread(() -> {
                    try {
                        barrier.await();
                        res.id();
                    } catch (IllegalStateException failure) {
                        // closed or given meanwhile
                    } catch (Exception failure) {
                        throw new AssertionError(failure);
                    }
                }),
                new Thread(() -> {
                    try {
                        barrier.await();
                        res.close();
                    } catch (Exception failure) {
                        throw new AssertionError(failure);
                    }
                }));
            for (Thread thread : threads) {
                thread.start();
            }
            for (Thread thread : threads) {
                thread.join();
            }
            check(given.get() + refused.get() == 1, "one attempt to give, round " + round);
        }
        check(Res.live() == 0, "every object of the race was destroyed once");

        // -- Several bases -------------------------------------------------------------------------

        // A class without a virtual destructor is taken as that class only: the message names the
        // object, also when it comes through a view.
        try (M mixed = new M()) {
            IllegalArgumentException error = expect(IllegalArgumentException.class,
                () -> Functions.takeP(mixed.asP()), "no virtual destructor");
            check(error.getMessage().contains("M cannot be given as P"), "the message names the object: " + error.getMessage());
            check(!mixed.isClosed(), "and the object is still there");
            check(Functions.takeP(new P()) == 3, "an object of exactly that class is taken");
        }

        // An object cannot be given twice in one call, also if one of the arguments is a view of it:
        // the second claim is refused, and nothing was given.
        {
            N both = new N();
            expect(IllegalStateException.class, () -> Functions.takeBoth(both, both.asR()),
                "another call uses it, or is giving it");
            check(!both.isClosed() && both.q() == 1 && both.r() == 2, "nothing was given");
            check(Functions.takeBoth(both, new N().asR()) == 12, "and it can be given once");
            check(both.isClosed(), "given");
        }

        // A view of an object obtained through a const reference is read-only too, and a read-only
        // object goes where C++ does not change it.
        {
            N constant = Functions.constN();
            check(constant.r() == 2, "const read");
            R view = constant.asR();
            expect(IllegalStateException.class, () -> view.setR(5), "read-only");
            expect(IllegalStateException.class, () -> Functions.touch(view), "read-only");
            check(Functions.look(constant.asR()) == 2 && Functions.look(view) == 2, "to a const reference");
            N mutable = new N();
            check(Functions.touch(mutable.asR()) == 3 && Functions.look(mutable.asR()) == 3, "a mutable object to both");
        }

        System.out.println("lifetime: Java round trip OK");
    }
}
