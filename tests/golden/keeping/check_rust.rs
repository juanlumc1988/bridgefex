// SPDX-License-Identifier: Apache-2.0
// Round trip through the generated Rust bindings of the "keeping" case.
//
// tests/test_rust_roundtrip.py builds this file as the main.rs of a crate that depends on the
// generated one, and runs it with the library of the case on the library path. What C++ keeps a
// pointer to is something that Rust cannot see how long: the functions that do are `unsafe`, and
// the caller says why it is sound (it declares the object before what keeps it, or leaks it).

use keep::keep::{Gate, Label, Latch};
use keep::Owned;
use std::ffi::CStr;

fn check(condition: bool, what: &str) {
    assert!(condition, "check failed: {what}");
}

fn main() -> keep::Result<()> {
    // `this = "retain"`: what the method returns keeps a pointer to the object. The Gate is
    // declared first, so that it is dropped after the Latch that reads it.
    {
        let mut gate = Gate::new()?;
        let latch = unsafe { gate.latch()? }.expect("a latch");
        check(latch.open()? && latch.number()? == 1, "the latch reads its gate");
        check(Gate::live()? == 1 && Latch::live()? == 1, "one gate, one latch");
        let second = unsafe { gate.latch()? }.expect("a latch");
        check(second.number()? == 2 && gate.latches()? == 2, "two latches");
    }
    check(Gate::live()? == 0 && Latch::live()? == 0, "both destroyed");

    // A constructor keeps the object that it is given, unless it says it only reads it.
    {
        let gate = Gate::new()?;
        let latch = unsafe { Latch::new(&gate, 5)? };
        check(latch.open()? && latch.number()? == 5, "the constructor keeps the gate");
    }
    check(Gate::live()? == 0, "destroyed");

    // The library keeps it for ever: the caller gives it up for good.
    let pinned = Owned::leak(Gate::new()?);
    unsafe { pinned.pin()? };
    check(Gate::live()? == 1 && Gate::pinned_alive()?, "pinned for ever");

    // `retain` on a text: a `const char*` is the caller's C string, a `std::string_view` its str.
    // They live as long as the program here, so what C++ reads is what it was given.
    let title = CStr::from_bytes_with_nul(b"a title that C++ keeps\0").unwrap();
    let raw = CStr::from_bytes_with_nul(b"a raw text that C++ keeps\0").unwrap();
    let view = "a view that C++ keeps";
    let maybe = CStr::from_bytes_with_nul(b"maybe\0").unwrap();
    let mut label = unsafe { Label::new_cstr(title)? };
    unsafe {
        label.set_raw(raw)?;
        label.set_view(view)?;
        label.set_maybe(Some(maybe))?;
    }
    label.set_copy("a copy: nothing to keep")?;
    check(label.intact()?, "every text is intact");
    let expected = title.to_bytes().len() + raw.to_bytes().len() + view.len() + maybe.to_bytes().len();
    check(label.kept()? == expected as i32, "the length of what it keeps");
    unsafe { label.set_maybe(None)? };
    check(label.kept()? == (expected - maybe.to_bytes().len()) as i32, "NULL is no text");
    check(label.intact()?, "still intact");

    // A text that nobody owns: for ever.
    unsafe { Label::keep_view("a text for ever")? };
    check(Label::kept_intact()?, "the text for ever");

    // What is not annotated stays safe: a `std::string` is copied for the call.
    let mut other = Label::new_void()?;
    other.set_copy("copied")?;
    check(other.intact()?, "a copy is intact");

    println!("round trip OK");
    Ok(())
}
