// SPDX-License-Identifier: Apache-2.0
// What the conformance scenarios do not say about the containers of the Java bindings: the arrays
// are copies, what is not valid is refused naming the element, and a lot of elements cross.
//
// tests/test_jvm_roundtrip.py builds this file together with the generated project and
// tests/support/jvm/Checks.java, and runs it with CONT_LIBRARY pointing at the built library.

import static checks.Checks.check;
import static checks.Checks.expect;

import cont.BridgefexException;
import cont.vectors.Bag;
import cont.vectors.Color;
import cont.vectors.Functions;
import cont.vectors.Level;
import cont.vectors.Point;
import java.util.Arrays;

class Check {
    public static void main(String[] args) {
        // Numbers, in both directions, also when there are none.
        check(Arrays.equals(Functions.scale(new long[] {1, -2, 3}, 10), new long[] {10, -20, 30}), "numbers");
        check(Functions.scale(new long[0], 10).length == 0, "no numbers");
        check(Arrays.equals(Functions.halves(new float[] {1f, 3f}), new float[] {0.5f, 1.5f}), "floats");
        check(Arrays.equals(Functions.invert(new boolean[] {true, false, true}), new boolean[] {false, true, false}), "bools");
        check(Functions.invert(new boolean[0]).length == 0, "no bools");
        // uint8_t is a short: all the values that a byte holds, as unsigned.
        short[] bytes = new short[256];
        for (int i = 0; i < 256; i++) {
            bytes[i] = (short) i;
        }
        check(Arrays.equals(Functions.echoBytes(bytes), bytes), "every byte");
        expect(IllegalArgumentException.class, () -> Functions.echoBytes(new short[] {1, 256}), "data[1]");

        // The array that goes in is not changed, and the one that comes out is the caller's.
        long[] values = {1, 2};
        long[] scaled = Functions.scale(values, 3);
        scaled[0] = 99;
        check(values[0] == 1 && values[1] == 2, "the input is not changed");
        check(Functions.scale(values, 3)[0] == 3, "and a result is a copy");

        // Strings, with what a string can hold.
        check(Arrays.equals(Functions.upper(new String[] {"ab", "cé", ""}), new String[] {"AB", "Cé", ""}),
            "strings");
        check(Functions.totalSize(new String[] {"a\u0000b", "😀"}) == 3 + 4, "a NUL, and a surrogate pair");
        check(Functions.emptyStrings().length > 0, "empty strings");
        expect(IllegalArgumentException.class, () -> Functions.upper(new String[] {"ok", "\udc00"}), "words[1]");
        expect(NullPointerException.class, () -> Functions.upper(new String[] {null}), "words[0]");

        // Enums and structs.
        check(Arrays.equals(Functions.rotate(new Color[] {Color.RED, Color.GREEN}).length == 2
            ? new int[] {1} : new int[] {0}, new int[] {1}), "enums");
        check(Functions.countHigh(new Level[] {Level.LOW, Level.HIGH, Level.HIGH}) == 2, "a plain enum");
        expect(IllegalArgumentException.class, () -> Functions.countHigh(new Level[] {Level.LOW, Level.of(7)}), "levels[1]");
        expect(NullPointerException.class, () -> Functions.rotate(new Color[] {null}), "colors[0]");
        Point[] shifted = Functions.shift(new Point[] {new Point(1, 2), new Point(3, 4)}, 0.5);
        check(shifted.length == 2 && shifted[0].x == 1.5 && shifted[1].y == 4.0, "structs");
        expect(NullPointerException.class, () -> Functions.shift(new Point[] {null}, 1), "points[0]");

        // An array that is passed is not null, and std::array has its length.
        expect(NullPointerException.class, () -> Functions.scale(null, 1), "values");
        expect(IllegalArgumentException.class, () -> Functions.twice(new float[3]), "must have 4 elements, got 3");
        expect(IllegalArgumentException.class, () -> Functions.twice(new float[5]), "must have 4 elements, got 5");
        check(Arrays.equals(Functions.twice(new float[] {1, 2, 3, 4}), new float[] {2, 4, 6, 8}), "std::array");
        expect(NullPointerException.class, () -> Functions.twice(null), "m");
        Point[] swapped = Functions.swap(new Point[] {new Point(1, 2), new Point(3, 4)});
        check(swapped[0].x == 3 && swapped[1].x == 1, "std::array of structs");
        check(Functions.colors().length == 3, "std::array as a result");

        // What C++ throws is an exception, and nothing leaks.
        BridgefexException failed = expect(BridgefexException.class, Functions::tooBig);
        check(failed.status() != 0, "std::length_error");
        // The size parameter of the array that C++ names like it.
        check(Functions.sum(new int[] {1, 2, 3}, 100) == 6100, "a parameter named like the size");

        // A lot of elements.
        int count = 1_000_000;
        long[] big = new long[count];
        for (int i = 0; i < count; i++) {
            big[i] = i;
        }
        long[] doubled = Functions.scale(big, 2);
        check(doubled.length == count && doubled[count - 1] == 2L * (count - 1), "a million numbers");
        String[] words = new String[10_000];
        Arrays.fill(words, "word");
        check(Functions.upper(words).length == 10_000, "ten thousand strings");

        // Containers in a class.
        try (Bag bag = new Bag(new String[] {"x", "y"})) {
            check(Arrays.equals(bag.items(), new String[] {"x", "y"}), "a container in a constructor");
            bag.add(new int[] {4, 5});
            check(Arrays.equals(bag.counts(), new int[] {4, 5}), "in a method");
            expect(NullPointerException.class, () -> bag.add(null), "counts");
        }

        System.out.println("containers: round trip OK");
    }
}
