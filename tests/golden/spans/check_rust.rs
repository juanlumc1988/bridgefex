// SPDX-License-Identifier: Apache-2.0
// Round trip through the generated Rust bindings of the "spans" case: a `std::span<const T>` goes
// in as a slice, which is not copied if the elements are numbers, structs or enums.
//
// tests/test_rust_roundtrip.py builds this file as the main.rs of a crate that depends on the
// generated one, and runs it with the library of the case on the library path.

use spn::views::{count_red, numbers, sum, sum_x, total, Color, Ledger, Point};

fn check(condition: bool, what: &str) {
    assert!(condition, "check failed: {what}");
}

fn main() -> spn::Result<()> {
    check(sum(&[1, 2, 3])? == 6, "numbers");
    check(sum(&[])? == 0, "empty");
    check(sum(&[i16::MAX, i16::MAX, i16::MIN])? == 2 * i64::from(i16::MAX) + i64::from(i16::MIN), "the limits");
    check(sum(&[9, 4, 5, 6, 9][1..4])? == 15, "a slice of a slice");
    check(numbers()? == [-1, 2, -3], "a span as a result");
    check(count_red(&[Color::RED, Color::GREEN, Color(0)])? == 2, "enums");
    check(sum_x(&[Point { x: 1.5, y: 0.0 }, Point { x: 2.5, y: 0.0 }])? == 4.0, "structs");
    check(total(&["ab", "c"])? == 3, "strings");
    check(total(&["é", "😀"])? == 2 + 4, "strings are counted in UTF-8 bytes");

    // A span that C++ keeps a pointer to: the function is `unsafe` (the numbers have to stay where
    // they are for as long as the Ledger uses them), and what the caller declares first is
    // dropped last.
    let first = [1, 2, 3];
    let more = vec![10, 20];
    let doubles = [1.5, 2.5];
    let flags = [true, false];
    let longs = [7, 8, 9];
    let mut ledger = unsafe { Ledger::new(&first)? };
    unsafe {
        ledger.add(&more)?;
        ledger.add_doubles(&doubles)?;
        ledger.add_flags(&flags)?;
        ledger.add_longs(&longs)?;
    }
    Ledger::scribble()?;
    check(ledger.intact()?, "the numbers that C++ keeps are where they were");
    check(ledger.total()? == 36 && ledger.kept()? == 12, "the sum of the kept numbers");
    ledger.add_copy(&[4, 5])?;
    check(ledger.kept()? == 12, "a vector is a copy");
    drop(ledger);
    check(Ledger::bad_destructions()? == 0, "no destructor found what it points to gone");
    let for_ever: &'static [i32] = Box::leak(vec![5, 6, 7].into_boxed_slice());
    unsafe { Ledger::pin(for_ever)? };
    Ledger::scribble()?;
    check(Ledger::kept_intact()?, "a function keeps it for ever");

    println!("spans: Rust round trip OK");
    Ok(())
}
