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
import spn.views.Ledger;
import spn.views.Point;

class Check {
    static Ledger makeLedger() {
        // The arrays of the call are not referenced by anything when this returns.
        Ledger ledger = new Ledger(new int[] {1, 2, 3});
        ledger.add(new int[] {10, 20});
        ledger.addDoubles(new double[] {1.5, 2.5});
        ledger.addFlags(new boolean[] {true, false});
        ledger.addLongs(new long[] {7, 8, 9});
        return ledger;
    }

    static void churn() {
        System.gc();
        Ledger.scribble();
        System.gc();
    }

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

        // A span that C++ keeps a pointer to: the object that keeps it holds a copy.
        Ledger kept = makeLedger();
        churn();
        check(kept.intact(), "the numbers that C++ keeps are where they were");
        check(kept.total() == 36 && kept.kept() == 12, "the sum of the kept numbers");
        kept.addCopy(new int[] {4, 5});
        check(kept.kept() == 12, "a vector is a copy");
        kept.add(new int[0]);
        churn();
        check(kept.intact() && kept.total() == 6, "an empty span");
        kept.close();
        Ledger.pin(new int[] {5, 6, 7});
        churn();
        check(Ledger.keptIntact(), "a function keeps it for ever");
        makeLedger();  // abandoned: the cleaner destroys it after the C++ destructor read what it kept
        churn();
        check(Ledger.badDestructions() == 0, "no destructor found what it points to gone");

        System.out.println("spans: round trip OK");
    }
}
