// SPDX-License-Identifier: Apache-2.0
// Round trip through the generated Rust bindings of the "inherit" case.
//
// tests/test_rust_roundtrip.py builds this file as the main.rs of a crate that depends on the
// generated one, and runs it with the library of the case on the library path. A class gets the
// methods of its first base class through `Deref` (and `&Circle` is a `&Shape`, a `&Named`,
// where C++ expects one), the other bases through `as_<base>`, and gives itself up as a base class
// (`into_base`) only where C++ may destroy it through that class.

use inh::shape::{Named, Shape};
use inh::shapes::{self, Circle, Ring, Square};
use inh::widget::{self, Counter, Labeled, Observer, Plain, PlainDerived, Shelf, Widget};
use inh::Owned;

fn check(condition: bool, what: &str) {
    assert!(condition, "check failed: {what}");
}

fn close(a: f64, b: f64) -> bool {
    (a - b).abs() < 1e-9
}

fn main() -> inh::Result<()> {
    check(shapes::shapes_alive()? == 0, "no shape at the start");

    // Inherited methods, and virtual calls that run the override of the object's class.
    {
        let circle = Circle::new(2.0)?;
        check(circle.area()? == 12.0, "area");
        check(circle.name()? == "circle", "name");
        check(circle.greeting()? == "I am circle", "greeting, two levels up");
        check(circle.describe()? == "circle: 12 units", "describe calls the overrides");
        check(circle.sides()? == 0, "the default of a virtual method");
        check(circle.radius()? == 2.0, "radius");
        let as_shape: &Shape = &circle;
        let as_named: &Named = &circle;
        check(as_shape.name()? == "circle" && as_named.name()? == "circle", "through the bases");
    }
    {
        let square = Square::new(3.0)?;
        check(square.sides()? == 4, "overridden");
        check(square.describe()? == "square: 9 units", "describe");
    }

    // A method that a derived class hides is the one of the class; the base's is still there.
    {
        let mut circle = Circle::new(2.0)?;
        check(circle.scale(2.0)? == 4.0, "Circle::scale(f64)");
        check(circle.radius()? == 4.0, "radius after scale");
        let as_shape: &mut Shape = &mut circle;
        check(as_shape.scale(3)? == 3, "Shape::scale(i32), on the Shape part");
        check(circle.scaled()? == 3 && circle.radius()? == 4.0, "scaled");
    }

    // Three levels: Ring is a Circle, a Shape and a Named.
    {
        let mut ring = Ring::new(10.0, 5.0)?;
        check(ring.area()? == 225.0 && ring.name()? == "ring", "ring");
        check(ring.radius()? == 10.0, "Circle::radius on a Ring");
        check(ring.describe()? == "ring: 225 units", "describe");
        check(ring.greeting()? == "I am ring", "greeting");
        check(shapes::call_name(&ring)? == "I am ring", "a Ring where a Named is expected");
        check(shapes::total_area(&ring, &ring)? == 450.0, "total_area");
        check(shapes::describe_first(&ring)? == "ring: 225 units", "describe_first");
        check(shapes::grow(&mut ring, 2)? == 450.0, "grow");
    }

    // Objects that C++ returns as the base class: that is the class of the Rust object, whatever
    // the object is, and its virtual methods run the right code.
    {
        let mut made: Vec<Owned<Shape>> = Vec::new();
        for kind in 0..3 {
            made.push(shapes::make_shape(kind, 3.0)?.expect("a shape"));
        }
        let names: Vec<String> = made.iter().map(|shape| shape.name()).collect::<Result<_, _>>()?;
        check(names == ["circle", "square", "ring"], "names");
        let sides: Vec<i32> = made.iter().map(|shape| shape.sides()).collect::<Result<_, _>>()?;
        check(sides == [0, 4, 0], "sides");
        let areas: Vec<f64> = made.iter().map(|shape| shape.area()).collect::<Result<_, _>>()?;
        check(close(areas[0], 27.0) && close(areas[1], 9.0), "areas");
        check(close(areas[2], 27.0 - 3.0 * 0.6 * 0.6), "the area of the ring");
        check(shapes::shapes_alive()? == 3, "three shapes");
    }
    check(shapes::shapes_alive()? == 0, "dropped");
    check(shapes::make_shape(9, 1.0)?.is_none(), "an empty unique_ptr is None");

    // A borrowed result is a Shape that belongs to its arguments.
    {
        let mut small = Circle::new(1.0)?;
        let mut big = Square::new(5.0)?;
        let chosen = shapes::largest(&mut small, &mut big)?;
        check(chosen.name()? == "square" && chosen.sides()? == 4, "largest");
    }

    // Giving an object up as the base class: C++ destroys it.
    type Make = fn() -> inh::Result<Owned<Shape>>;
    let makers: [(Make, i32); 3] = [
        (|| Ok(Circle::new(1.0)?.into_base()), 0),
        (|| Ok(Square::new(1.0)?.into_base()), 4),
        (|| Ok(Ring::new(2.0, 1.0)?.into_base().into_base()), 0),
    ];
    for (make, sides) in makers {
        let shape = make()?;
        check(shapes::shapes_alive()? == 1, "one shape");
        check(shapes::consume(shape)? == sides, "consume");
        check(shapes::shapes_alive()? == 0, "destroyed by C++");
    }
    let returned = shapes::make_shape(1, 1.0)?.expect("a square");
    check(shapes::consume(returned)? == 4, "consume a returned shape");
    check(shapes::shapes_alive()? == 0, "nothing left");

    // Several bases: each of them is a different part of the object.
    {
        let mut w = Widget::new()?;
        check(w.next()? == 1, "Counter::next, through Deref");
        check(widget::bump(&mut w)? == 2, "a Widget where a Counter& is expected");
        check(w.count()? == 2, "count");
        check(widget::peek(&w)? == 2, "a copy of the Counter part");
        check(w.label()? == "widget unnamed", "overridden Labeled::label");
        w.as_labeled_mut().set_label("ab")?;
        check(widget::name_of(w.as_labeled())? == "widget ab", "through Labeled*, virtual");
        check(w.both()? == 2 * 100 + "widget ab".len() as i32, "both");
        check(w.as_labeled().label()? == "widget ab", "the second base");
    }
    {
        let mut counter = Counter::new()?;
        let labeled = Labeled::new_void()?;
        check(widget::bump(&mut counter)? == 1, "bump");
        check(widget::name_of(&labeled)? == "unnamed", "name_of");
    }
    check(Labeled::new_string("given")?.label()? == "given", "a constructor with an argument");

    // An object that is taken as a base class that is not at its address, and handed back as
    // itself, still has what it retained: the Counter that the constructor kept (it outlives the
    // observer, and the shelf: `retain` is `unsafe`, and this is why it is sound).
    let mut counter = Counter::new()?;
    counter.next()?;
    let mut shelf = Shelf::new()?;
    {
        let observer = unsafe { Observer::new(&mut counter)? };
        check(observer.tag()? == 41 && observer.watched()? == 1, "observer");
        shelf.put(observer.into_labeled())?;
    }
    let back = shelf.pop()?.expect("the observer");
    check(back.watched()? == 1, "what it retained is still there");
    check(back.as_labeled().label()? == "unnamed", "label");
    check(shelf.pop()?.is_none(), "the shelf is empty");

    // A base that is not the first gives the object, once: C++ destroys the whole Widget through
    // its Labeled part (a destructor called on a pointer that was not adjusted is what
    // AddressSanitizer sees).
    {
        let mut shelf = Shelf::new()?;
        shelf.put(Widget::new()?.into_labeled())?;
    }

    // A base class without a virtual destructor is taken only as exactly that class: a derived
    // object cannot be given as one (tests/test_rust_roundtrip.py checks that it does not compile).
    check(widget::take_plain(Plain::new()?)? == 5, "plain");
    let derived = PlainDerived::new()?;
    check(derived.extra()? == 9 && derived.value()? == 5, "the derived object is still usable");

    check(shapes::shapes_alive()? == 0, "no shape left");
    println!("round trip OK");
    Ok(())
}
