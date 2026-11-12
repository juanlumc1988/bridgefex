// SPDX-License-Identifier: Apache-2.0
// What the conformance scenarios do not say about the value structs of the Java bindings: what
// C++ gives a default-constructed struct, equals, hashCode and toString, the checks of the arrays
// and the enums that a struct has, and that a struct is copied when it crosses. Each section is a
// mutation of the generator that the tests did not catch.
//
// tests/test_jvm_roundtrip.py builds this file together with the generated project and
// tests/support/jvm/Checks.java, and runs it with VALS_LIBRARY pointing at the built library.

import static checks.Checks.check;
import static checks.Checks.expect;

import vals.BridgefexException;
import vals.geo.Config;
import vals.geo.Functions;
import vals.geo.Kind;
import vals.geo.Msg;
import vals.geo.Path;
import vals.geo.Point;
import vals.geo.Quality;
import vals.geo.Wide;

class Check {
    public static void main(String[] args) {
        // A struct without arguments has every member zero; defaults() is what C++ gives.
        Config zero = new Config();
        check(zero.baud == 0 && !zero.parity && zero.kind == Kind.PLAIN, "every member zero");
        Config defaults = Config.defaults();
        check(defaults.baud == 115200 && defaults.parity && defaults.kind == Kind.MARKED,
            "defaults() has the default member initializers of C++");
        check(Msg.defaults().tag == 7, "and so does a struct with arrays");
        check(Msg.defaults().samples.length == 3 && Msg.defaults().path.length == 2, "with the arrays of their length");
        check(new Msg().path[1] != null && new Msg().where != null, "structs inside are never null");
        check(new Msg().path[0] != new Msg().path[1], "and each place has its own");

        // equals and hashCode are by value, also for the arrays and the floating point members.
        Point a = new Point(1.0, 2.0);
        check(a.equals(new Point(1.0, 2.0)) && a.hashCode() == new Point(1.0, 2.0).hashCode(), "equal by value");
        check(!a.equals(new Point(1.0, 3.0)) && !a.equals(new Point(2.0, 2.0)), "different by value");
        check(a.hashCode() != new Point(1.0, 3.0).hashCode(), "and by hash");
        check(!a.equals(null) && !a.equals("1,2"), "not equal to what is not a Point");
        check(new Point(Double.NaN, 0).equals(new Point(Double.NaN, 0)), "NaN equals NaN, as in Double.equals");
        check(!new Point(0.0, 0).equals(new Point(-0.0, 0)), "and 0 is not -0");
        Msg first = Msg.defaults();
        Msg second = Msg.defaults();
        check(first.equals(second) && first.hashCode() == second.hashCode(), "equal with arrays");
        second.samples[1] = 5;
        check(!first.equals(second), "an element of an array makes them different");
        check(first.hashCode() != second.hashCode(), "also for the hash");
        second.samples[1] = 0;
        second.path[1].y = 1.5;
        check(!first.equals(second), "an element of an array of structs too");
        check(first.toString().startsWith("Msg{tag=7") && first.toString().contains("samples=[0, 0, 0]"), "toString");

        // A 64-bit unsigned member is shown as the number it is.
        Wide wide = new Wide();
        wide.size = -1L;
        check(wide.toString().contains("size=18446744073709551615"), "unsigned text: " + wide);
        check(wide.toString().contains("big=0"), "and a signed one as it is");

        // The arrays have the length of their type, and an enum is in its range.
        Msg bad = Msg.defaults();
        bad.samples = new short[2];
        expect(IllegalArgumentException.class, () -> Functions.echo(bad), "samples must have 3");
        bad.samples = null;
        expect(NullPointerException.class, () -> Functions.echo(bad), "samples");
        Msg quality = Msg.defaults();
        quality.quality = Quality.of(9);
        expect(IllegalArgumentException.class, () -> Functions.echo(quality), "quality");
        quality.quality = null;
        expect(NullPointerException.class, () -> Functions.echo(quality), "quality");
        Msg nested = Msg.defaults();
        nested.where = null;
        expect(NullPointerException.class, () -> Functions.echo(nested), "where");

        // A struct is copied when it crosses: what C++ does with it does not change the Java object.
        Msg sent = Msg.defaults();
        sent.samples[0] = 10;
        Msg shifted = Functions.shift(sent, (short) 2);
        check(shifted.samples[0] == 12 && shifted.tag == 8, "C++ changed its copy");
        check(sent.samples[0] == 10 && sent.tag == 7, "and the object of Java is as it was");
        check(Functions.echo(sent).equals(sent), "a struct comes back as it went");

        // Members of every kind go through memory.
        Wide wide2 = new Wide(1.5f, 21L, -2L, -7L, (byte) -3, new short[] {1, 2, 3, 255});
        Wide doubled = Functions.widen(wide2);
        check(doubled.big == 42L && doubled.size == -2L && doubled.l == -7L && doubled.c == -3, "the members");
        check(doubled.f == 1.5f && java.util.Arrays.equals(doubled.bytes, new short[] {1, 2, 3, 255}), "and the array");
        check(Functions.mid(new Point(0, 0), new Point(2, 4)).equals(new Point(1, 2)), "a call with structs");
        expect(NullPointerException.class, () -> Functions.mid(null, new Point(0, 0)), "a");
        BridgefexException failed = expect(BridgefexException.class, () -> Functions.scale(a, -1), "negative scale");
        check(failed.status() != 0, "a C++ exception");

        // A class that takes and gives structs.
        try (Path path = new Path(new Point(1, 2))) {
            path.add(new Point(3, 4));
            check(path.last().equals(new Point(3, 4)), "a struct result");
            check(Path.origin().equals(new Point(0, 0)), "a static one");
        }

        System.out.println("round trip OK");
    }
}
