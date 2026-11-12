// SPDX-License-Identifier: Apache-2.0
// Round trip through the generated Java bindings of the "spans" case: a std::span<const T> goes in
// as an array, and what C++ gives is an array too.
//
// tests/test_jvm_roundtrip.py builds this file together with the generated project and
// tests/support/jvm/Checks.java, and runs it with SPN_LIBRARY pointing at the built library.

import static checks.Checks.check;
import static checks.Checks.expect;

import java.util.Arrays;
import spn.views.Color;
import spn.views.Functions;
import spn.views.Point;

class Check {
    public static void main(String[] args) {
        check(Functions.sum(new short[] {1, 2, 3}) == 6, "numbers");
        check(Functions.sum(new short[0]) == 0, "empty");
        check(Functions.sum(new short[] {Short.MAX_VALUE, Short.MAX_VALUE, Short.MIN_VALUE})
            == Short.MAX_VALUE + Short.MAX_VALUE + Short.MIN_VALUE, "the limits");
        check(Arrays.equals(Functions.numbers(), new int[] {-1, 2, -3}), "a span as a result");
        check(Functions.countRed(new Color[] {Color.RED, Color.of(1), Color.of(0)}) == 2, "enums");
        check(Functions.sumX(new Point[] {new Point(1.5, 0), new Point(2.5, 0)}) == 4.0, "structs");
        check(Functions.total(new String[] {"ab", "c"}) == 3, "strings");
        check(Functions.total(new String[] {"é", "😀"}) == 2 + 4, "strings are counted in UTF-8 bytes");

        // What is not an array of what the span holds is an error that names the parameter.
        expect(NullPointerException.class, () -> Functions.sum(null), "values");
        expect(NullPointerException.class, () -> Functions.total(new String[] {"a", null}), "words[1]");
        expect(NullPointerException.class, () -> Functions.sumX(new Point[] {null}), "points[0]");
        expect(NullPointerException.class, () -> Functions.countRed(new Color[] {null}), "colors[0]");
        expect(IllegalArgumentException.class, () -> Functions.total(new String[] {"\ud800"}), "words[0]");

        System.out.println("spans: round trip OK");
    }
}
