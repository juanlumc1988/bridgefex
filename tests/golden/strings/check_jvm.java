// SPDX-License-Identifier: Apache-2.0
// What the conformance scenarios do not say about the strings of the Java bindings: the encoding is
// UTF-8 and strict in both directions, NULs and nulls, and what a very long string does. Each
// section is a mutation of the generator that the tests did not catch.
//
// tests/test_jvm_roundtrip.py builds this file together with the generated project and
// tests/support/jvm/Checks.java, and runs it with TXT_LIBRARY pointing at the built library.

import static checks.Checks.check;
import static checks.Checks.expect;

import txt.BridgefexException;
import txt.text.Functions;
import txt.text.Person;

class Check {
    public static void main(String[] args) {
        // Text of every script goes through UTF-8, also what needs a surrogate pair.
        for (String text : new String[] {"", "a", "café", "中文", "😀 emoji", "tab\tline\n"}) {
            check(text.equals(Functions.echo(text)), "round trip of '" + text + "'");
        }

        // An embedded NUL goes with the size; a const char* ends at it, so it is refused.
        String nul = "a\u0000b";
        check(nul.equals(Functions.echo(nul)), "a NUL in a std::string");
        check(Functions.countString(nul) == 3, "counted with its NUL");
        expect(IllegalArgumentException.class, () -> Functions.countCstr(nul), "NUL");

        // A lone surrogate is not text: it is refused, not replaced.
        for (String lone : new String[] {"\ud800", "a\udc00", "\ud83d", "x\ud83dy"}) {
            expect(IllegalArgumentException.class, () -> Functions.echo(lone), "lone surrogate");
        }

        // What C++ gives is checked the same way: invalid UTF-8 is not replaced either.
        expect(IllegalStateException.class, Functions::binary, "UTF-8");

        // null is an error that names the parameter, except where the annotations say so.
        expect(NullPointerException.class, () -> Functions.echo(null), "s");
        expect(NullPointerException.class, () -> Functions.countCstr(null), "s");

        // A const char* result that is NULL is null; one that is not NULL is a string.
        check(Functions.maybeNull(true) == null, "NULL is null");
        check(Functions.maybeNull(false) != null, "and not NULL is a string");

        // A very long string.
        StringBuilder text = new StringBuilder();
        for (int i = 0; i < (1 << 16); i++) {
            text.append("0123456789abcdef");
        }
        String big = text.toString();
        check(big.equals(Functions.echo(big)), "a megabyte");
        check(Functions.countString(big) == big.length(), "counted");

        // An error that C++ throws carries its message, whatever it has.
        BridgefexException error = expect(BridgefexException.class, () -> Functions.fail("boom"), "boom");
        check(error.status() != 0, "status");
        BridgefexException accent = expect(BridgefexException.class, () -> Functions.fail("café 中"));
        check(accent.getMessage().contains("café 中"), "a message that is not ASCII: " + accent.getMessage());

        // Strings in a class.
        try (Person person = new Person("Ann")) {
            check("Ann".equals(person.name()) && "Ann".equals(person.nameRef()), "a string result");
            check("Ann".equals(person.cName()), "a const char* result");
            person.setName("Boé");
            check("Boé".equals(person.name()), "a string parameter");
            expect(NullPointerException.class, () -> person.setName(null), "name");
            check(person.greet("you", "!").contains("you"), "several strings");
        }
        check(Person.species().length() > 0, "a static method");

        System.out.println("round trip OK");
    }
}
