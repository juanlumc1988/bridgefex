// SPDX-License-Identifier: Apache-2.0
// Round trip through the generated Java bindings of the "directors" case: Java classes that derive
// from the wrapped classes and override their virtual methods, which C++ calls back. The same
// scenarios as the C# check (check_csharp.cs), for the types that an override can have in Java
// (scalars, enums, strings and value structs).
//
// tests/test_jvm_roundtrip.py builds this file together with the generated project and
// tests/support/jvm/Checks.java, and runs it with DRC_LIBRARY pointing at the built library.

import static checks.Checks.check;
import static checks.Checks.collect;
import static checks.Checks.eventually;
import static checks.Checks.expect;

import drc.BridgefexCallbacks;
import drc.shape.Beam;
import drc.shape.Circle;
import drc.shape.Dot;
import drc.shape.Holder;
import drc.shape.Mode;
import drc.shape.Plain;
import drc.shape.Point;
import drc.shape.Shape;
import drc.stage.Stage;
import java.lang.ref.WeakReference;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.atomic.AtomicInteger;

class Check {
    static final ConcurrentLinkedQueue<Throwable> UNOBSERVED = new ConcurrentLinkedQueue<Throwable>();

    static WeakReference<Square> give(Holder holder) {
        Square square = new Square();
        holder.put(square); // C++ owns the director now
        check(square.isClosed(), "the object is closed for Java");
        return new WeakReference<Square>(square);
    }

    static void abandon() {
        Square square = new Square();
        check(square.describe().equals("sq:shape: 4 units"), "an object that is forgotten");
    }

    public static void main(String[] args) throws Exception {
        BridgefexCallbacks.setUnobserved(UNOBSERVED::add);

        // -- Classes that have a director work as before when nothing derives from them --------------
        try (Circle circle = new Circle(2.0)) {
            check(circle.area() == 12.0, "a circle");
            check(circle.name().equals("round"), "its name");
            check(circle.describe().equals("round: 12 units"), "describe");
        }
        try (Dot dot = new Dot()) {
            check(dot.area() == 0.0, "a dot");
        }
        try (Plain plain = new Plain()) {
            check(plain.value() == 5, "a class that asks for no director");
        }
        check(Shape.alive() == 0, "nothing is alive");

        // -- A class derived from an abstract one: the pure virtual method has to be overridden ----------
        try (Square square = new Square()) {
            check(Shape.alive() == 1, "one object");
            check(square.area() == 4.0, "area");
            // C++ calls the overrides: describe() uses name() and area().
            check(square.describe().equals("sq:shape: 4 units"), "describe: " + square.describe());
            check(square.name().equals("sq:shape"), "name");
            check(square.sum(3, 4) == 12, "an overload");
            check(square.sumDouble(5.0, 2.0) == 3.0, "the other overload");
            check(square.howQuiet() == 9, "a noexcept method");
            // Containers, in both directions: an array that C++ copies, and an array that C++ copies back.
            check(square.totalOfThree() == 12,
                "total() is the override, which calls the base with the array: " + square.totalOfThree());
            check(square.callTotal(new int[] {5, 6}) == 22, "a vector of int");
            check(Arrays.equals(square.callNumbers(new int[] {1, 2, 3}), new int[] {3, 2, 1, 99}),
                "the base result, and one more");
            check(Arrays.equals(square.callWords(new String[] {"a", "b\u00e9"}), new String[] {"a?", "b\u00e9?"}),
                "strings");
            check(Arrays.equals(square.callModes(new Mode[] {Mode.ON, Mode.OFF}), new Mode[] {Mode.OFF, Mode.ON}),
                "enums");
            Point[] moved = square.callPoints(new Point[] {new Point(1.0, 2.0)});
            check(moved.length == 1 && moved[0].x == 3.0 && moved[0].y == 2.0, "structs");
            check(Arrays.equals(square.callFlags(new boolean[] {true, false}), new boolean[] {false, true}), "bool");
            check(Arrays.equals(square.callTriple(new double[] {1.0, 2.0, 3.0}), new double[] {1.5, 3.0, 4.5}),
                "std::array");
            check(square.callTotal(new int[0]) == 0, "an empty vector");
            // Objects: lent to the override during the call (a reference or a pointer), given to it,
            // and given by it.
            try (Holder holder = new Holder()) {
                check(square.callPeek(holder) == 10, "a const reference to an object of C++");
                holder.put(new Square());
                check(square.callPeek(holder) == 20, "the override sees what the holder holds");
            }
            try (Circle circle = new Circle(1.0)) {
                check(square.callCompare(circle) == 4.0 - 3.0 + 100.0,
                    "an object of C++, whose methods the override calls");
                check(!square.callSame(circle), "another object is not this one");
            }
            check(square.callCompare(square) == 100.0, "the object of Java is itself lent: its override runs");
            check(square.callSame(square), "and it is the same object");
            check(!square.callSame(null), "NULL is null");
            try (Beam beam = new Beam(7)) {
                check(square.callGrow(beam) == 107, "a reference that is not const");
            }
            Beam given = new Beam(3);
            check(square.callAdopt(given) == 103, "an object that C++ gives up to the override");
            check(given.isClosed(), "and the object of Java is closed");
            try (Beam made = square.callMakeBeam(5)) {
                check(made.callLength() == 10, "an object that the override gives to C++");
            }
            check(square.middle(new Point(4.0, 0.0)).x == 3.0, "structs go both ways");
            check(square.choose(Mode.ON) == Mode.OFF && square.choose(Mode.OFF) == Mode.of(5), "enums");
            check(square.welcome("Ann").equals(">>|hello|Ann|>> hello Ann, I am sq:shape"),
                "strings: " + square.welcome("Ann"));
            check(square.scaledArea(3.0) == 4.0 && square.scaled == 3.0, "void with an argument");
            square.notify_(5); // a protected virtual method, overridden
            check(square.seen == 5 && square.lastReason() == 10, "protected");
            check(square.fixed() == 7, "final: never overridden");
        }
        check(Shape.alive() == 0, "closed");

        // -- A class that does not override the pure virtual method cannot be instantiated -----------------
        expect(IllegalStateException.class, () -> new Incomplete(),
            "Incomplete must override the pure virtual method Shape.area");
        check(Shape.alive() == 0, "nothing was made");

        // -- The constructors of the class are those of the Java class -----------------------------------------
        try (Labelled labelled = new Labelled("box", 3)) {
            check(labelled.name().equals("box"), "not overridden: the implementation of the class");
            check(labelled.sides() == 3 && labelled.label().equals("box"), "the arguments of the constructor");
            check(labelled.describe().equals("box: 1 units"), "describe");
        }
        check(Shape.alive() == 0, "closed");

        // -- A class derived from one that is not abstract, which has its own constructor ---------------------
        try (Disc disc = new Disc(1.0)) {
            check(disc.area() == 103.0, "the base area");
            check(disc.describe().equals("disc: 103 units"), "name() and area() are the overrides: " + disc.describe());
            check(disc.radius() == 1.0, "a method of the class");
            // The overload that Circle hides still runs the one of Shape that the director overrides.
            check(disc.sumDouble(1.5, 2.0) == 3.5, "an overload of the base");
        }
        check(Shape.alive() == 0, "closed");

        // A class that derives and overrides nothing makes plain objects.
        try (Quiet quiet = new Quiet()) {
            check(quiet.describe().equals("round: 3 units"), "the implementation of the class");
        }
        check(Shape.alive() == 0, "closed");

        // -- Protected constructors, and a protected pure virtual method -----------------------------------------
        try (Steps steps = new Steps(3)) {
            check(steps.run(Mode.AUTO).equals("AUTO1,AUTO2,AUTO3,@7"), "run: " + steps.run(Mode.AUTO));
            check(steps.steps() == 3, "the protected constructor with an argument");
        }
        try (Steps steps = new Steps()) {
            check(steps.run(Mode.ON).equals("ON1,ON2,@7"), "the protected default constructor");
        }

        // A class with public constructors and protected ones: the protected ones make the objects of
        // its subclasses only.
        try (Short5 short5 = new Short5(7)) {
            check(short5.callLength() == 5 && short5.madeBy() == 1, "the public constructor, with an override");
        }
        try (Short5 short5 = new Short5()) {
            check(short5.callLength() == 5 && short5.madeBy() == 2, "the protected default constructor");
        }
        try (Short5 short5 = new Short5("four")) {
            check(short5.callLength() == 5 && short5.madeBy() == 3, "the protected constructor of a label");
        }
        try (PlainBeam beam = new PlainBeam("four")) {
            check(beam.callLength() == 4 && beam.madeBy() == 3, "a subclass that overrides nothing: a plain object");
        }
        try (Beam beam = new Beam(2)) {
            check(beam.callLength() == 2 && beam.madeBy() == 1, "the class itself: the public constructor");
        }

        // -- Objects that C++ lends are valid while the override runs ----------------------------------------------
        try (Keeper keeper = new Keeper(); Holder holder = new Holder(); Circle circle = new Circle(2.0)) {
            check(keeper.callPeek(holder) == 1, "the override kept the object");
            // A wrapper that is kept fails instead of using what C++ may have freed.
            expect(IllegalStateException.class, () -> keeper.kept.empty(), "closed");
            // A const reference is read-only: what would change the object fails.
            check(keeper.callCompare(circle) == 12.0, "a const reference");
            expect(IllegalStateException.class, () -> keeper.keptShape.area(), "closed");
            check(keeper.sawReadOnly, "the const reference is read-only: " + keeper.sawReadOnly);
            // An object that the override gives and that is closed cannot be given.
            keeper.give = new Beam(1);
            keeper.give.close();
            expect(IllegalStateException.class, () -> keeper.callMakeBeam(1), "closed");
        }
        check(Shape.alive() == 0, "closed");

        // -- An exception from an override reaches the call that made C++ run it ------------------------------------
        try (Failing failing = new Failing()) {
            // The exception of an override is the one the call that runs it raises, as it was thrown.
            IllegalArgumentException error = expect(IllegalArgumentException.class,
                () -> failing.describe(), "no area");
            boolean inOverride = false;
            for (StackTraceElement frame : error.getStackTrace()) {
                inOverride |= frame.getClassName().equals("Failing");
            }
            check(inOverride, "the stack trace is that of the override");
            expect(IllegalArgumentException.class, () -> failing.scaledArea(1.0), "no area");
            // The next call is not poisoned.
            check(failing.sum(1, 2) == 3, "an override that is not there");
        }
        check(UNOBSERVED.isEmpty(), "an exception that a call raised is not also unobserved");
        check(Shape.alive() == 0, "closed");

        // -- Given to C++: the object is kept alive by C++ ------------------------------------------------------------
        try (Holder holder = new Holder()) {
            WeakReference<Square> weak = give(holder);
            collect();
            check(weak.get() != null, "C++ keeps the object, the collector does not collect it");
            check(holder.describe().equals("sq:shape: 4 units"), "its overrides run: " + holder.describe());
            check(Shape.alive() == 1, "C++ holds one");
            // Taking it back gives a plain wrapper of the same C++ object.
            try (Shape back = holder.take()) {
                check(back.describe().equals("sq:shape: 4 units"), "the same C++ object");
            }
        }
        collect();
        eventually(() -> Shape.alive() == 0, "the holder destroyed it");

        // -- A director that Java owns is destroyed by the collector ------------------------------------------------
        abandon();
        eventually(() -> {
            System.gc();
            return Shape.alive() == 0 && BridgefexCallbacks.registered() == 0;
        }, "the cleaner destroyed it, and C++ released it");

        // -- The base call of an object that is closed ------------------------------------------------------------------
        Square closed = new Square();
        closed.close();
        expect(IllegalStateException.class, () -> closed.name(), "closed");
        expect(IllegalStateException.class, () -> closed.describe(), "closed");
        check(Shape.alive() == 0, "closed");

        // -- Threads -------------------------------------------------------------------------------------------------------
        try (Holder holder = new Holder()) {
            holder.put(new Square());
            check(holder.describeOnThread().equals("sq:shape: 4 units"),
                "C++ calls the overrides from a thread of its own");
        }
        eventually(() -> Shape.alive() == 0, "the holder destroyed it");

        // (The counter of live objects of the C++ test class is not atomic: the objects are made and
        // destroyed one at a time, and the calls run together.)
        final Object gate = new Object();
        final ConcurrentLinkedQueue<String> errors = new ConcurrentLinkedQueue<String>();
        List<Thread> workers = new ArrayList<Thread>();
        for (int n = 0; n < 6; n++) {
            final int number = n;
            workers.add(new Thread(() -> {
                for (int turn = 0; turn < 100; turn++) {
                    Square square;
                    synchronized (gate) {
                        square = new Square();
                    }
                    if (square.sum(number, turn) != number * turn) {
                        errors.add("sum " + number + " " + turn);
                    }
                    if (!square.describe().equals("sq:shape: 4 units")) {
                        errors.add("describe " + number + " " + turn);
                    }
                    synchronized (gate) {
                        square.close();
                    }
                }
            }));
        }
        for (Thread worker : workers) {
            worker.start();
        }
        for (Thread worker : workers) {
            worker.join();
        }
        check(errors.isEmpty(), "threads: " + errors);
        check(Shape.alive() == 0, "all closed");
        check(BridgefexCallbacks.registered() == 0, "C++ released them all");

        System.out.println("directors: Java round trip OK");
    }
}

// -- The classes of the user ---------------------------------------------------------------------------------------

class Square extends Shape {
    double scaled;
    int seen;

    @Override
    public double area() {
        return 4.0;
    }

    @Override
    public String name() {
        return "sq:" + super.name(); // the implementation of C++, not this method
    }

    @Override
    public int sides() {
        return 4;
    }

    @Override
    public Point center() {
        return new Point(2.0, 4.0);
    }

    @Override
    public Mode mode(Mode requested) {
        return requested == Mode.ON ? Mode.OFF : Mode.of(5);
    }

    @Override
    public void scale(double factor) {
        scaled = factor;
    }

    @Override
    public int addInt32Int32(int a, int b) {
        return a * b;
    }

    @Override
    public double addDoubleDouble(double a, double b) {
        return a - b;
    }

    @Override
    public String greet(String who, String tag, String mark) {
        return mark + "|" + tag + "|" + who + "|" + super.greet(who, tag, mark);
    }

    @Override
    public int quiet() {
        return 9;
    }

    @Override
    public int peek(Holder holder) {
        return holder.empty() ? 10 : 20;
    }

    @Override
    public double compare(Shape other) {
        return area() - other.area() + 100.0;
    }

    @Override
    public boolean same(Shape other) {
        return other == this;
    }

    @Override
    public int grow(Beam beam) {
        return beam.callLength() + 100;
    }

    @Override
    public int adopt(Beam beam) {
        int length = beam.callLength() + 100;
        beam.close();
        return length;
    }

    @Override
    public Beam makeBeam(int length) {
        return new Beam(length * 2);
    }

    @Override
    public int total(int[] values) {
        return super.total(values) * 2;
    }

    @Override
    public int[] numbers(int[] values) {
        int[] base = super.numbers(values);
        int[] more = Arrays.copyOf(base, base.length + 1);
        more[base.length] = 99;
        return more;
    }

    @Override
    public String[] words(String[] input) {
        String[] result = new String[input.length];
        for (int i = 0; i < input.length; i++) {
            result[i] = input[i] + "?";
        }
        return result;
    }

    @Override
    public Mode[] modes(Mode[] wanted) {
        Mode[] result = new Mode[wanted.length];
        for (int i = 0; i < wanted.length; i++) {
            result[i] = wanted[wanted.length - 1 - i];
        }
        return result;
    }

    @Override
    public Point[] points(Point[] list) {
        Point[] result = new Point[list.length];
        for (int i = 0; i < list.length; i++) {
            result[i] = new Point(list[i].x + 2.0, list[i].y);
        }
        return result;
    }

    @Override
    public boolean[] flags(boolean[] input) {
        boolean[] result = new boolean[input.length];
        for (int i = 0; i < input.length; i++) {
            result[i] = !input[i];
        }
        return result;
    }

    @Override
    public double[] triple(double[] values) {
        double[] result = new double[values.length];
        for (int i = 0; i < values.length; i++) {
            result[i] = values[i] * 1.5;
        }
        return result;
    }

    @Override
    protected void changed(int reason) {
        seen = reason;
        super.changed(reason * 2); // a protected method of C++
    }
}

class Keeper extends Shape {
    Holder kept;
    Shape keptShape;
    boolean sawReadOnly;
    Beam give;

    @Override
    public double area() {
        return 1.0;
    }

    @Override
    public int peek(Holder holder) {
        kept = holder;
        return 1;
    }

    @Override
    public double compare(Shape other) {
        keptShape = other;
        try {
            other.scale(2.0); // C++ lends a const reference: the object is read-only
        } catch (IllegalStateException error) {
            sawReadOnly = error.getMessage().contains("read-only");
        }
        return other.area();
    }

    @Override
    public Beam makeBeam(int length) {
        return give;
    }
}

class Incomplete extends Shape {
    @Override
    public String name() {
        return "x";
    }
}

class Labelled extends Shape {
    Labelled(String label, int sides) {
        super(label, sides);
    }

    @Override
    public double area() {
        return 1.0;
    }
}

class Disc extends Circle {
    Disc(double radius) {
        super(radius);
    }

    @Override
    public double area() {
        return 100.0 + super.area(); // the implementation of Circle: 3 * r * r
    }

    @Override
    public String name() {
        return "disc";
    }
}

class Quiet extends Circle {
    Quiet() {
        super(1.0);
    }
}

class Failing extends Shape {
    @Override
    public double area() {
        throw new IllegalArgumentException("no area");
    }
}

class Steps extends Stage {
    Steps() {
        super();
    }

    Steps(int steps) {
        super(steps);
    }

    @Override
    protected String step(Mode mode, int number) {
        return mode + "" + number + ",";
    }

    @Override
    protected Point origin() {
        return new Point(7.0, 0.0);
    }
}

class Short5 extends Beam {
    Short5(int length) {
        super(length);
    }

    Short5() {
        super();
    }

    Short5(String label) {
        super(label);
    }

    @Override
    public int length() {
        return 5;
    }
}

class PlainBeam extends Beam {
    PlainBeam(String label) {
        super(label);
    }
}
