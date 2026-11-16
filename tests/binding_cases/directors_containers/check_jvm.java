// SPDX-License-Identifier: Apache-2.0
// The Java port of check_csharp.cs of this case (both Java bindings: tests/test_jvm_roundtrip.py).
import dco.dco.*;
import dco.BridgefexCallbacks;
import java.util.Arrays;

class Check {
    static int failures;

    static void check(boolean c, String what) {
        if (!c) {
            failures++;
            System.out.println("FAIL " + what);
        }
    }

    static class Over extends Source {
        int wordsCalls;
        boolean totalSawEmpty, wordsSawEmpty;
        @Override public int total(int[] values) {
            if (values.length == 0) totalSawEmpty = true;
            return 1000 * values.length + super.total(values);
        }
        @Override public long spanTotal(int[] values) {
            long s = 0;
            for (int v : values) s += v;
            return s * 2;
        }
        @Override public int[] doubled(int[] values) {
            int[] r = new int[values.length];
            for (int i = 0; i < r.length; i++) r[i] = values[i] * 3;
            return r;
        }
        @Override public long[] longs(long[] values) {
            long[] r = values.clone();
            for (int i = 0; i < r.length; i++) r[i] += 10;
            return r;
        }
        @Override public long[] ulongs(long[] values) {
            long[] r = values.clone();
            for (int i = 0; i < r.length; i++) r[i] += 1;
            return r;
        }
        @Override public Color[] colors(Color[] values) { return values.clone(); }
        @Override public Mode[] modes(Mode[] values) {
            Mode[] r = new Mode[values.length];
            for (int i = 0; i < r.length; i++) r[i] = Mode.of(values[i].value() + 1);
            return r;
        }
        @Override public Point[] points(Point[] values) {
            Point[] r = new Point[values.length];
            for (int i = 0; i < r.length; i++) r[i] = new Point(values[i].x + 1, values[i].y + 2);
            return r;
        }
        @Override public String[] words(String[] values) {
            wordsCalls++;
            if (values.length == 0) wordsSawEmpty = true;
            String[] r = new String[values.length];
            for (int i = 0; i < r.length; i++) r[i] = values[i] + "?";
            return r;
        }
        @Override public boolean[] flags(boolean[] values) {
            boolean[] r = Arrays.copyOf(values, values.length + 1);
            r[values.length] = true;
            return r;
        }
        @Override public int[] pair(int[] values) { return new int[] {values[0] * 101, values[1] * 101}; }
        @Override public Point[] ends(Point[] values) { return new Point[] {values[0], values[0]}; }
        @Override public int[] nothing() { return new int[0]; }
        @Override public int mixed(int[] values, double[] weights, String label) {
            return values.length * 10000 + weights.length * 100 + label.length();
        }
    }

    static class BadNull extends Source {
        @Override public int[] doubled(int[] v) { return null; }
    }

    static class BadLength extends Source {
        @Override public int[] pair(int[] v) { return new int[3]; }
    }

    static class BadColor extends Source {
        @Override public Color[] colors(Color[] v) { return new Color[] {Color.of(0), Color.of(7)}; }
    }

    static class BadNullWord extends Source {
        @Override public String[] words(String[] v) { return new String[] {"a", null}; }
    }

    static class BadSurrogate extends Source {
        @Override public String[] words(String[] v) { return new String[] {"\ud800"}; }
    }

    static class Throwing extends Source {
        @Override public int total(int[] v) { throw new NumberFormatException("total"); }
    }

    public static void main(String[] args) throws Exception {
        Over over = new Over();
        check(over.callTotal(new int[] {1, 2, 3}) == 3006, "total " + over.callTotal(new int[] {1, 2, 3}));
        check(over.callSpanTotal(new int[] {1, 2, 3, 4}) == 20, "span total");
        check(Arrays.equals(over.callDoubled(new int[] {1, 2, 3}), new int[] {3, 6, 9}), "doubled");
        check(Arrays.equals(over.callLongs(new long[] {1, -2, Integer.MIN_VALUE}), new long[] {11, 8, Integer.MIN_VALUE + 10L}), "longs");
        check(Arrays.equals(over.callUlongs(new long[] {1, 2, 0xFFFFFFFEL}), new long[] {2, 3, 0xFFFFFFFFL}), "ulongs");
        check(Arrays.equals(over.callColors(new Color[] {Color.RED, Color.GREEN}), new Color[] {Color.RED, Color.GREEN}), "colors");
        check(Arrays.equals(over.callModes(new Mode[] {Mode.OFF, Mode.ON}), new Mode[] {Mode.ON, Mode.AUTO}), "modes");
        check(Arrays.equals(over.callModes(new Mode[] {Mode.AUTO}), new Mode[] {Mode.of(3)}), "modes out of range");
        Point[] pts = {new Point(1, 2), new Point(3, 4)};
        check(Arrays.equals(over.callPoints(pts), new Point[] {new Point(2, 4), new Point(4, 6)}), "points");
        String[] words = {"héllo", "", "日本語", new String(new char[10000]).replace('\0', 'x'), "emoji 😀"};
        String[] want = new String[words.length];
        for (int i = 0; i < words.length; i++) want[i] = words[i] + "?";
        check(Arrays.equals(over.callWords(words), want), "words");
        check(Arrays.equals(over.callFlags(new boolean[] {true, false, true}), new boolean[] {true, false, true, true}), "flags");
        check(Arrays.equals(over.callPair(new int[] {1, 2}), new int[] {101, 202}), "pair");
        Point[] ends = {new Point(1, 1), new Point(2, 2)};
        check(Arrays.equals(over.callEnds(ends), new Point[] {ends[0], ends[0]}), "ends");
        check(over.callNothing() == 0, "nothing");
        check(over.callMixed(3, "ab") == 30302, "mixed " + over.callMixed(3, "ab"));

        check(over.callTotalEmpty() == 0 && over.totalSawEmpty, "empty total");
        check(over.callWordsEmpty() == 0 && over.wordsSawEmpty, "empty words");
        long t0 = System.nanoTime();
        check(over.callTotalBig(1000000) == 2000000, "big " + over.callTotalBig(1000000));
        System.out.println("big in " + (System.nanoTime() - t0) / 1000000 + " ms");
        t0 = System.nanoTime();
        check(over.callWordsMany(50000) == 50000 * 5, "many");
        System.out.println("many in " + (System.nanoTime() - t0) / 1000000 + " ms");

        try { new BadNull().callDoubledSize(new int[] {1}); check(false, "bad null"); } catch (IllegalStateException e) { check(e.getMessage().contains("returned null for a std::vector"), "bad null msg " + e.getMessage()); }
        try { new BadLength().callPair(new int[] {1, 2}); check(false, "bad length"); } catch (IllegalStateException e) { check(e.getMessage().contains("returned 3 elements for a std::array of 2"), "bad length msg " + e.getMessage()); }
        try { new BadColor().callColors(new Color[] {Color.RED}); check(false, "bad color"); } catch (IllegalArgumentException | IllegalStateException e) { System.out.println("bad color: " + e.getMessage()); }
        try { new BadNullWord().callWords(new String[] {"x"}); check(false, "null word"); } catch (NullPointerException e) { System.out.println("null word: " + e.getMessage()); }
        try { new BadSurrogate().callWords(new String[] {"x"}); check(false, "surrogate"); } catch (IllegalArgumentException e) { System.out.println("surrogate: " + e.getMessage()); }

        int calls = over.wordsCalls;
        try { over.callWordsBad(); check(false, "words bad no exception"); } catch (IllegalStateException e) { System.out.println("bad utf8: " + e.getMessage()); }
        check(over.wordsCalls == calls, "override not called with bad text");
        check(Arrays.equals(over.callWords(new String[] {"ok"}), new String[] {"ok?"}), "after bad");

        Throwing th = new Throwing();
        try { th.callTotal(new int[] {1}); check(false, "throwing"); } catch (NumberFormatException e) { }
        check(Arrays.equals(th.callPair(new int[] {4, 5}), new int[] {5, 4}), "after throw");

        // threads
        final java.util.concurrent.atomic.AtomicInteger errors = new java.util.concurrent.atomic.AtomicInteger();
        Thread[] ts = new Thread[6];
        for (int n = 0; n < ts.length; n++) {
            final int number = n;
            ts[n] = new Thread(new Runnable() {
                public void run() {
                    Over o = new Over();
                    for (int turn = 0; turn < 200; turn++) {
                        int[] values = new int[20];
                        int sum = 0;
                        for (int i = 0; i < 20; i++) { values[i] = number + i; sum += values[i]; }
                        if (o.callTotal(values) != 20000 + sum) errors.incrementAndGet();
                        if (!Arrays.equals(o.callWords(new String[] {"a" + number, "b" + turn}), new String[] {"a" + number + "?", "b" + turn + "?"})) errors.incrementAndGet();
                    }
                }
            });
            ts[n].start();
        }
        for (Thread t : ts) t.join();
        check(errors.get() == 0, "threads " + errors.get());
        System.out.println(failures == 0 ? "round trip OK" : failures + " failures");
        if (failures != 0) System.exit(1);
    }
}
