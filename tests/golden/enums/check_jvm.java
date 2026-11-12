// SPDX-License-Identifier: Apache-2.0
// What the conformance scenarios do not say about the enums of the Java bindings: they are classes,
// and any value of the underlying type is one (a flag, a strong integer type). Each section is a
// mutation of the generator that the tests did not catch.
//
// tests/test_jvm_roundtrip.py builds this file together with the generated project and
// tests/support/jvm/Checks.java, and runs it with ENM_LIBRARY pointing at the built library.

import static checks.Checks.check;
import static checks.Checks.expect;

import enm.colors.Color;
import enm.colors.Flags;
import enm.colors.Functions;
import enm.colors.Id;
import enm.colors.Lamp;
import enm.colors.Mode;
import enm.colors.Signed;
import java.util.HashMap;
import java.util.Map;

class Check {
    public static void main(String[] args) {
        // The constants have the values of C++ and are found again by value.
        check(Color.RED.value() == 0 && Color.GREEN.value() == 200 && Color.MAX.value() == 255, "values");
        check(Color.of(200) == Color.GREEN, "of() gives the constant");
        check("GREEN".equals(Color.GREEN.name()), "name");

        // A value that no enumerator has is an object of its own: a flag, a strong integer.
        Color other = Color.of(100);
        check(other.name() == null && other.value() == 100, "no name for a value that has no enumerator");
        check(other != Color.RED && other != Color.GREEN && other != Color.MAX, "and it is not a constant");
        check(other.equals(Color.of(100)) && other.hashCode() == Color.of(100).hashCode(), "equal by value");
        check(!other.equals(Color.of(101)) && !other.equals(Color.RED), "and different by value");
        check("Color(100)".equals(other.toString()) && "GREEN".equals(Color.GREEN.toString()), "toString");
        Map<Color, String> names = new HashMap<>();
        names.put(Color.of(7), "seven");
        check("seven".equals(names.get(Color.of(7))), "usable as a key");

        // The values of the underlying type, and no others.
        expect(IllegalArgumentException.class, () -> Color.of(256), "does not fit");
        expect(IllegalArgumentException.class, () -> Color.of(-1), "does not fit");
        check(Color.of(255) == Color.MAX && Color.of(0) == Color.RED, "the limits are values");
        expect(IllegalArgumentException.class, () -> Mode.of(1L << 31), "does not fit");

        // values() is a copy: the constants cannot be replaced through it.
        Color[] values = Color.values();
        check(values.length == 3 && values[0] == Color.RED, "values() in the order of the declaration");
        values[0] = null;
        check(Color.values()[0] == Color.RED, "values() gives a copy");

        // 64 bits unsigned: the bits, ordered as unsigned numbers.
        check(Flags.ALL.value() == -1L, "all the bits");
        check(Flags.of(-1L) == Flags.ALL, "found by its bits");
        check(Flags.ALL.compareTo(Flags.BIT31) > 0, "2^64 - 1 is more than 2^31 (unsigned order)");
        check(Flags.BIT31.compareTo(Flags.ALL) < 0 && Flags.ALL.compareTo(Flags.ALL) == 0, "and the reverse");
        check("Flags(9223372036854775808)".equals(Flags.of(Long.MIN_VALUE).toString()), "unsigned text");
        check(Functions.flip(Flags.NOTHING) == Flags.ALL, "a call with bits");

        // A strong integer type with no enumerators.
        check(Id.values().length == 0, "no enumerators");
        check(Functions.next(Id.of(41)).value() == 42, "any value is an Id");

        // An enum without a fixed type refuses what it cannot hold (0..7), and null is an error.
        check(Functions.toggle(Mode.OFF) == Mode.ON, "a call");
        expect(IllegalArgumentException.class, () -> Functions.toggle(Mode.of(100)), "m");
        expect(NullPointerException.class, () -> Functions.toggle(null), "m");
        expect(NullPointerException.class, () -> Functions.mix(null, Color.RED), "a");
        check(Functions.negate(Signed.FIRST).value() == 3, "negative values of an enum");
        expect(IllegalArgumentException.class, () -> Functions.negate(Signed.of(-5)), "s");

        // An enum inside a class is a class inside it.
        try (Lamp lamp = new Lamp(Color.GREEN)) {
            lamp.set(Lamp.State.LIT, Lamp.Level.BRIGHT);
            check(lamp.state() == Lamp.State.LIT && lamp.level() == Lamp.Level.BRIGHT, "nested enums");
            check(lamp.color() == Color.GREEN, "and an enum of the header");
            expect(NullPointerException.class, () -> lamp.set(null, Lamp.Level.DIM), "state");
        }

        System.out.println("round trip OK");
    }
}
