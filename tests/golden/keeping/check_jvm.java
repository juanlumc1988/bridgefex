// SPDX-License-Identifier: Apache-2.0
// Round trip through the generated Java bindings of the "keeping" case.
//
// tests/test_jvm_roundtrip.py builds this file together with the generated project and
// tests/support/jvm/Checks.java, and runs it with KEEP_LIBRARY pointing at the built library. What
// C++ keeps a pointer to has to live as long as C++ may use it: the C++ code reads it later (the
// Latch reads its Gate, the Label adds up the bytes of its texts again), and a Gate or a text that
// the collector had freed shows.

import static checks.Checks.check;
import static checks.Checks.collect;
import static checks.Checks.eventually;
import static checks.Checks.expect;

import java.util.ArrayList;
import java.util.List;
import keep.BridgefexException;
import keep.kept.Gate;
import keep.kept.Label;
import keep.kept.Latch;
import keep.kept.Pen;
import keep.kept.Stamp;

class Check {
    // A text of {@code count} times {@code letter} (Java 8 has no String.repeat).
    static String rep(char letter, int count) {
        char[] letters = new char[count];
        java.util.Arrays.fill(letters, letter);
        return new String(letters);
    }

    // Memory that was freed is reused (and overwritten) before C++ reads it.
    static void churn() {
        List<byte[]> junk = new ArrayList<>();
        for (int size = 1; size < 400; size++) {
            for (int index = 0; index < 20; index++) {
                byte[] block = new byte[size];
                java.util.Arrays.fill(block, (byte) (index + 1));
                junk.add(block);
            }
        }
        junk.clear();
        collect();
        Label.scribble();
    }

    // Objects that are made and forgotten in another method: the unoptimized code of the caller
    // would keep their references in slots of the stack.
    static Latch latchOfForgottenGate() {
        return new Gate().latch();
    }

    static Latch[] twoLatchesOfForgottenGate() {
        Gate gate = new Gate();
        return new Latch[] {gate.latch(), gate.latch()};
    }

    static Latch constructedOfForgottenGate() {
        return new Latch(new Gate(), 5);
    }

    static void forgetGate() {
        new Gate();
    }

    static void pinForgottenGate() {
        new Gate().pin();
    }

    static void setTemporaryTexts(Label label) {
        label.setRaw("a raw text that is a temporary " + rep('r', 30));
        label.setView("a view that is a temporary " + rep('v', 40));
        label.setMaybe("maybe " + rep('m', 10));
        label.setCopy("a copy " + rep('c', 50));
    }

    static Label labelOfTemporaryTitle() {
        return Label.createCstr("a title that is a temporary " + rep('t', 20));
    }

    static class Mine extends Pen {
        @Override
        public Stamp stamp() {
            return super.stamp();
        }

        @Override
        public void note(String text) {
            super.note(text);
        }

        @Override
        public void noteView(String text) {
            super.noteView(text);
        }
    }

    static Stamp stampOfForgottenPen() {
        Mine pen = new Mine();
        pen.note("a note that is a temporary " + rep('n', 30));
        pen.noteView("a view that is a temporary " + rep('w', 40));
        churn();
        check(pen.notesIntact(), "the notes of the Pen");
        return pen.stamp();
    }

    public static void main(String[] args) throws Exception {
        // `this = "retain"`: the object that the method returns keeps the object it was called on.
        Latch latch = latchOfForgottenGate();
        churn();
        check(latch.open() && latch.number() == 1, "the Latch reads its Gate");
        check(Gate.live() == 1, "the Gate is still there: the Latch keeps it alive");
        latch.close();
        latch = null;
        eventually(() -> Latch.live() == 0 && Gate.live() == 0, "both went, the Latch first");

        // A Gate that nothing keeps is destroyed when it is dropped.
        forgetGate();
        eventually(() -> Gate.live() == 0, "a Gate that nothing keeps");

        // Several results keep the same object, and it goes with the last of them.
        Latch[] two = twoLatchesOfForgottenGate();
        churn();
        check(two[0].open() && two[1].open() && Gate.live() == 1, "two latches, one gate");
        two[0].close();
        churn();
        check(two[1].open() && Gate.live() == 1, "the second one still keeps it");
        two[1].close();
        two = null;
        eventually(() -> Gate.live() == 0, "gone with the last");

        // A constructor keeps the object that it is given (the default for an object).
        Latch constructed = constructedOfForgottenGate();
        churn();
        check(constructed.open() && constructed.number() == 5 && Gate.live() == 1,
            "the constructor keeps it");
        constructed.close();
        constructed = null;
        eventually(() -> Gate.live() == 0, "gone");

        // Without an object that keeps it, the object that C++ keeps is kept for ever.
        int baseGates = Gate.live();
        pinForgottenGate();
        churn();
        check(Gate.live() == baseGates + 1 && Gate.pinnedAlive(), "pinned for ever");

        // `retain` on a text: the text that C++ stores is kept as long as the object that stores it.
        Label label = labelOfTemporaryTitle();
        setTemporaryTexts(label);
        churn();
        check(label.intact(), "every text is intact");
        int expected = "a title that is a temporary ".length() + 20;
        expected += "a raw text that is a temporary ".length() + 30;
        expected += "a view that is a temporary ".length() + 40;
        expected += "maybe ".length() + 10;
        check(label.kept() == expected, "the length of what it keeps");
        label.setMaybe(null);
        check(label.kept() == expected - "maybe ".length() - 10, "NULL is no text");
        churn();
        check(label.intact(), "still intact");

        // A text that is replaced is still kept: C++ may use the old one until the object is closed.
        for (int number = 0; number < 100; number++) {
            label.setView("text number " + number + " " + rep('w', number % 17));
        }
        churn();
        check(label.intact(), "the replaced texts are intact");

        // Closing the object lets go of its texts, and the C++ object does not read them again.
        label.close();
        churn();

        // A constructor that fails keeps nothing, and the memory of the text is the collector's.
        expect(IllegalArgumentException.class, () -> Label.createCstr("a title with a NUL \0 inside"), "NUL");

        // Without an object, for ever (a function or a static method).
        Label.keepView("a text for ever " + rep('f', 30));
        churn();
        check(Label.keptIntact(), "the text for ever");

        // What is not annotated stays as it was: a std::string is copied for the call.
        try (Label other = new Label()) {
            other.setCopy("copied");
            check(other.intact(), "a copy is intact");
        }

        // A call that stores the pointer and then throws: what it stored is kept all the same.
        try (Label thrower = new Label()) {
            BridgefexException error = expect(BridgefexException.class,
                () -> thrower.setRawThrow("a text that is stored and then C++ throws " + rep('z', 20)),
                "stored, then failed");
            churn();
            check(thrower.intact(), "kept although the call threw");
        }

        // Two constructors that Java cannot tell by their arguments are told by name: each keeps its text.
        try (Label titled = Label.createStringView("a title that is a view and a temporary " + rep('q', 20))) {
            churn();
            check(titled.intact() && titled.kept() == "a title that is a view and a temporary ".length() + 20,
                "a view title");
        }

        // An override that calls the implementation of the class keeps what the class keeps too.
        Stamp stamp = stampOfForgottenPen();
        churn();
        check(stamp.open() && Pen.live() == 1, "the Stamp keeps its Pen alive");
        stamp.close();
        stamp = null;
        eventually(() -> Stamp.live() == 0 && Pen.live() == 0, "both went");

        // What the destructors of the objects read was still there when they ran.
        collect();
        check(Label.badDestructions() == 0, "no destructor found what it points to gone");

        System.out.println("keeping: Java round trip OK");
    }
}
