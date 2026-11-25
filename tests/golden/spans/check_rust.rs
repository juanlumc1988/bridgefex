// SPDX-License-Identifier: Apache-2.0
// Round trip through the generated Rust bindings of the "spans" case: a `std::span<const T>` goes
// in as a slice, which is not copied if the elements are numbers, structs or enums.
//
// tests/test_rust_roundtrip.py builds this file as the main.rs of a crate that depends on the
// generated one, and runs it with the library of the case on the library path.

use spn::views::{count_red, numbers, sum, sum_x, total, Color, Point};

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

    println!("spans: Rust round trip OK");
    Ok(())
}
