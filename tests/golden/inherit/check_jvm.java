// SPDX-License-Identifier: Apache-2.0
// Round trip through the generated Java bindings of the "inherit" case.
//
// tests/test_jvm_roundtrip.py builds this file together with the generated project and
// tests/support/jvm/Checks.java, and runs it with INH_LIBRARY pointing at the built library.

import static checks.Checks.check;
import static checks.Checks.eventually;
import static checks.Checks.expect;

import inh.shape.Named;
import inh.shape.Shape;
import inh.shapes.Circle;
import inh.shapes.Ring;
import inh.shapes.Square;
import inh.widget.Counter;
import inh.widget.Labeled;
import inh.widget.Observer;
import inh.widget.Plain;
import inh.widget.PlainDerived;
import inh.widget.Shelf;
import inh.widget.Widget;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.function.Supplier;
import java.util.stream.Collectors;

class Check {
    static int alive() {
        return inh.shapes.Functions.shapesAlive();
    }

    static class Mine extends Circle {
        Mine(double radius) {
            super(radius);
        }

        double twice() {
            return 2 * area();
        }
    }

    static class MyPlain extends Plain {
    }

    // The counter is only referenced by the observer (and by the C++ object): the collector may
    // take its wrapper.
    static Observer observerOfNewCounter() {
        Counter counter = new Counter();
        counter.next();
        return new Observer(counter);
    }

    public static void main(String[] args) {
        // The classes derive from each other as in C++, across headers.
        check(Shape.class.getSuperclass() == Named.class, "Shape : Named");
        check(Ring.class.getSuperclass() == Circle.class && Circle.class.getSuperclass() == Shape.class,
            "Ring : Circle : Shape");
        check(Widget.class.getSuperclass() == Counter.class, "Widget : Counter (its first base)");
        check(Plain.class.getSuperclass() == inh.BridgefexObject.class, "a class without bases");

        // Inherited methods, and virtual calls that run the override of the object's class.
        try (Circle circle = new Circle(2.0)) {
            check(circle.area() == 12.0, "area");
            check(circle.name().equals("circle"), "name");
            check(circle.greeting().equals("I am circle"), "greeting, two levels up");
            check(circle.describe().equals("circle: 12 units"), "describe calls the overrides");
            check(circle.sides() == 0, "the default of a virtual method");
            check(circle.radius() == 2.0, "radius");
            Shape asShape = circle;
            Named asNamed = circle;
            check(asShape.name().equals("circle") && asNamed.name().equals("circle"), "through the base classes");
        }
        try (Square square = new Square(3.0)) {
            check(square.sides() == 4, "overridden");
            check(square.describe().equals("square: 9 units"), "describe");
        }

        // A method that a derived class hides is the one of the class; the base's is still there.
        try (Circle circle = new Circle(2.0)) {
            check(circle.scale(2.0) == 4.0, "Circle.scale(double)");
            check(circle.radius() == 4.0, "radius after scale");
            Shape asShape = circle;
            check(asShape.scale(3) == 3, "Shape.scale(int), on the Shape part");
            check(circle.scaled() == 3 && circle.radius() == 4.0, "scaled");
        }

        // Three levels: Ring is a Circle, a Shape and a Named.
        try (Ring ring = new Ring(10.0, 5.0)) {
            check(ring.area() == 225.0 && ring.name().equals("ring"), "ring");
            check(ring.radius() == 10.0, "Circle.radius on a Ring");
            check(ring.describe().equals("ring: 225 units"), "describe");
            check(ring.greeting().equals("I am ring"), "greeting");
            check(inh.shapes.Functions.callName(ring).equals("I am ring"), "a Ring where a Named is expected");
            check(inh.shapes.Functions.totalArea(ring, ring) == 450.0, "total_area");
            check(inh.shapes.Functions.describeFirst(ring).equals("ring: 225 units"), "describe_first");
            check(inh.shapes.Functions.grow(ring, 2) == 450.0, "grow");
        }

        // A subclass in Java is the same C++ class.
        try (Mine mine = new Mine(1.0)) {
            check(mine.twice() == 6.0, "twice");
            check(inh.shapes.Functions.totalArea(mine, mine) == 6.0, "a subclass where a Shape is expected");
        }

        // A closed object fails like any other, through the base class too.
        Square dead = new Square(1.0);
        dead.close();
        expect(IllegalStateException.class, () -> inh.shapes.Functions.totalArea(dead, dead), "closed");
        expect(IllegalStateException.class, dead::describe, "closed");
        expect(NullPointerException.class, () -> inh.shapes.Functions.totalArea(null, dead), "first");
        expect(NullPointerException.class, () -> inh.shapes.Functions.callName(null), "named");
        expect(NullPointerException.class, () -> inh.shapes.Functions.describeFirst(null), "shape");

        // Objects that C++ returns as the base class: that is the class of the Java object,
        // whatever the object is, and its virtual methods run the right code.
        List<Shape> made = new ArrayList<>();
        for (int kind = 0; kind < 3; kind++) {
            made.add(inh.shapes.Functions.makeShape(kind, 3.0));
        }
        check(made.stream().allMatch(item -> item.getClass() == Shape.class), "the class is the declared one");
        check(made.stream().map(Named::name).collect(Collectors.toList()).equals(Arrays.asList("circle", "square", "ring")), "names");
        check(made.stream().map(Shape::sides).collect(Collectors.toList()).equals(Arrays.asList(0, 4, 0)), "sides");
        check(made.stream().map(Shape::area).collect(Collectors.toList()).equals(Arrays.asList(27.0, 9.0, 27.0 - 3.0 * 0.6 * 0.6)), "areas");
        check(alive() == 3, "three shapes");
        for (Shape item : made) {
            item.close();
        }
        made.clear();
        check(alive() == 0, "closed");
        check(inh.shapes.Functions.makeShape(9, 1.0) == null, "an empty unique_ptr is null");

        // A borrowed result is a Shape that belongs to its arguments.
        try (Circle small = new Circle(1.0)) {
            Square big = new Square(5.0);
            Shape chosen = inh.shapes.Functions.largest(small, big);
            check(chosen.getClass() == Shape.class && chosen.name().equals("square") && chosen.sides() == 4, "largest");
            big.close();
            expect(IllegalStateException.class, chosen::name, "closed");
        }

        // Giving an object up as the base class: the object is closed, C++ destroys it.
        List<Supplier<Shape>> makers = Arrays.<Supplier<Shape>>asList(
            () -> new Circle(1.0), () -> new Square(1.0), () -> new Ring(2.0, 1.0));
        for (Supplier<Shape> make : makers) {
            Shape kind = make.get();
            check(alive() == 1, "one shape");
            int expectedSides = kind instanceof Square ? 4 : 0;
            check(inh.shapes.Functions.consume(kind) == expectedSides, "consume");
            expect(IllegalStateException.class, kind::area, "closed");
            check(alive() == 0, "destroyed by C++");
        }
        Shape returned = inh.shapes.Functions.makeShape(1, 1.0);
        check(inh.shapes.Functions.consume(returned) == 4, "consume a returned shape");
        expect(IllegalStateException.class, returned::area, "closed");
        expect(IllegalStateException.class, () -> inh.shapes.Functions.consume(returned), "closed");
        check(alive() == 0, "nothing left");

        // Several bases: each of them is a different part of the object.
        try (Widget w = new Widget()) {
            check(w.next() == 1, "Counter.next");
            check(inh.widget.Functions.bump(w) == 2, "a Widget where a Counter is expected");
            check(w.count() == 2, "count");
            check(inh.widget.Functions.peek(w) == 2, "a copy of the Counter part");
            check(w.label().equals("widget unnamed"), "overridden Labeled.label");
            w.setLabel("ab");
            check(inh.widget.Functions.nameOf(w.asLabeled()).equals("widget ab"), "through Labeled*, virtual");
            check(w.both() == 2 * 100 + "widget ab".length(), "both");
            Labeled asLabeled = w.asLabeled();
            check(asLabeled.label().equals("widget ab"), "the second base, converted");
            asLabeled.setLabel("cd");
            check(w.label().equals("widget cd"), "the view changes the object");
        }
        try (Counter counter = new Counter(); Labeled labeled = new Labeled()) {
            check(inh.widget.Functions.bump(counter) == 1, "bump");
            check(inh.widget.Functions.nameOf(labeled).equals("unnamed"), "name_of");
        }
        try (Labeled labeled = new Labeled("given")) {
            check(labeled.label().equals("given"), "a constructor with an argument");
        }

        // An object that is taken as a base class that is not at its address, and handed back as
        // itself, still has what it retained: the Counter that the constructor kept.
        Observer observer = observerOfNewCounter();
        check(observer.tag() == 41 && observer.watched() == 1, "observer");
        Shelf shelf = new Shelf();
        shelf.put(observer.asLabeled()); // taken as its Labeled part: the conversion is a view of the object
        expect(IllegalStateException.class, observer::watched, "closed");
        eventually(() -> true, "the collector has run");
        Observer back = shelf.pop();
        check(back.getClass() == Observer.class, "handed back as itself");
        check(back.watched() == 1, "what it retained is still there"); // a use after free under AddressSanitizer if it was lost
        check(back.label().equals("unnamed"), "label");
        back.close();
        shelf.close();

        // A view of an object (a base that is not the first) gives the object, once.
        try (Widget again = new Widget()) {
            Labeled view = again.asLabeled();
            Shelf shelf2 = new Shelf();
            shelf2.put(view);
            expect(IllegalStateException.class, again::next, "closed");
            expect(IllegalStateException.class, () -> shelf2.put(view), "closed");
            shelf2.close();
        }

        // Without a virtual destructor, C++ would destroy a derived object through the base class:
        // only an object of exactly that class (or a Java subclass of it) is accepted.
        check(inh.widget.Functions.takePlain(new Plain()) == 5, "plain");
        check(inh.widget.Functions.takePlain(new MyPlain()) == 5, "a Java subclass of Plain");
        PlainDerived derived = new PlainDerived();
        expect(IllegalArgumentException.class, () -> inh.widget.Functions.takePlain(derived), "no virtual destructor");
        check(derived.extra() == 9 && derived.value() == 5, "still usable: nothing was taken");
        derived.close();

        check(alive() == 0, "no shape left");
        System.out.println("inherit: Java round trip OK");
    }
}
