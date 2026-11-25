// SPDX-License-Identifier: Apache-2.0
// Round trip through the generated Rust bindings of the "containers" case: a vector, an array or a
// span goes in as a slice (a reference to an array for a `std::array`), and a result comes back as
// a `Vec` (an array for a `std::array`).
//
// tests/test_rust_roundtrip.py builds this file as the main.rs of a crate that depends on the
// generated one, and runs it with the library of the case on the library path.

use cont::vectors::{
    colors, count_high, echo_bytes, empty_strings, halves, invert, levels, primes, rotate, scale,
    shift, sum, sum_levels, swap, too_big, total_size, twice, upper, Bag, Color, Level, Point,
};
use cont::ErrorKind;

fn check(condition: bool, what: &str) {
    assert!(condition, "check failed: {what}");
}

fn main() -> cont::Result<()> {
    // (The phases say where a run that is stuck or slow (under a sanitizer, on a small machine)
    // was.)
    eprintln!("containers: slices");

    // -- Anything that has its elements together is a slice ----------------------------------

    let expected: Vec<i64> = vec![10, -20, 30];
    check(scale(&[1, -2, 3], 10)? == expected, "an array");
    check(scale(&vec![1, -2, 3], 10)? == expected, "a vector");
    check(scale(&[9, 1, -2, 3, 9][1..4], 10)? == expected, "a slice of a slice");
    check(scale(&[], 10)?.is_empty(), "an empty slice");
    check(scale(&Vec::new(), 10)?.is_empty(), "an empty vector");

    // A result is a vector of its own: nothing is shared with C++.
    let mut first = scale(&[1], 2)?;
    let second = scale(&[1], 2)?;
    first[0] = 99;
    check(second[0] == 2 && scale(&[1], 2)?[0] == 2, "a result is a copy");

    // -- Elements ------------------------------------------------------------------------------

    check(invert(&[true, false])? == [false, true], "bool");
    check(echo_bytes(&[0, 255])? == [0, 255], "bytes");
    check(halves(&[1.0, 3.0])? == [0.5, 1.5], "float");
    check(total_size(&["ab", "c\0d", ""])? == 5, "strings keep every character");
    check(upper(&["ab\0c", "", "xyz"])? == ["AB\0C", "", "XYZ"], "strings go and come back");
    check(empty_strings()? == ["", ""], "empty strings come back");
    check(upper(&["héllo 😀"])?[0] == "HéLLO 😀", "UTF-8 goes through (C++ changes the ASCII)");
    check(upper(&[])?.is_empty(), "no strings");
    check(sum(&[1, 2], 7)? == 3007, "a parameter named like the size of the vector");
    check(primes()? == [2, 3, 5, 7], "a result by reference");

    check(
        rotate(&[Color::RED, Color::BLUE, Color(2)])? == [Color::GREEN, Color::RED, Color::RED],
        "enums",
    );
    check(count_high(&[Level::HIGH, Level::LOW, Level::HIGH])? == 2, "enums without a fixed type");
    check(levels(3)? == [Level::LOW, Level::HIGH, Level::LOW], "enums come back");
    let shifted = shift(&[Point { x: 1.0, y: 2.0 }, Point { x: 3.0, y: 4.0 }], 0.5)?;
    check(
        shifted == [Point { x: 1.5, y: 2.0 }, Point { x: 3.5, y: 4.0 }],
        "structs",
    );

    // -- Arrays have a length: the type says it -------------------------------------------------

    check(twice(&[1.0, 2.0, 3.5, 4.0])? == [2.0, 4.0, 7.0, 8.0], "std::array");
    let swapped = swap(&[Point { x: 1.0, y: 2.0 }, Point { x: 3.0, y: 4.0 }])?;
    check(swapped[0].x == 3.0 && swapped[1].x == 1.0, "an array of structs");
    check(colors()? == [Color::BLUE, Color::GREEN, Color::RED], "an array of enums comes back");
    check(sum_levels(&[Level::HIGH, Level::HIGH])? == 2, "an array of enums goes in");

    // -- Refusals -------------------------------------------------------------------------------

    // An element of an enum without a fixed type that C++ cannot hold is refused before the call,
    // and the message says which one it is.
    let error = count_high(&[Level::HIGH, Level(2)]).expect_err("2 is not a Level");
    check(error.kind() == ErrorKind::InvalidArgument, "the kind of an invalid element");
    check(error.message().contains("levels[1]"), "the element is named");
    let error = count_high(&[Level(-1)]).expect_err("-1 is not a Level");
    check(error.message().contains("levels[0]"), "the first element is named");
    check(sum_levels(&[Level::HIGH, Level(7)]).is_err(), "an array of invalid enums");
    // A value that is too big for a byte is not one that a byte would make valid.
    check(count_high(&[Level(256)]).is_err(), "256 is not 0");
    check(count_high(&[Level(1 << 24), Level::HIGH]).is_err(), "no truncation");
    check(sum_levels(&[Level::HIGH, Level(257)]).is_err(), "257 is not 1");
    let error = too_big().expect_err("the vector is too big");
    check(error.kind() == ErrorKind::Exception && error.message().contains("max_size"), "a throw");

    eprintln!("containers: class");

    // -- A class that takes and returns vectors -------------------------------------------------

    {
        let mut bag = Bag::new(&["a", "b"])?;
        bag.add(&[1, 2])?;
        bag.add(&[3])?;
        check(bag.counts()? == [1, 2, 3], "counts");
        check(bag.items()? == ["a", "b"], "items");
    }

    // -- What C++ allocated is released, also in many threads ------------------------------------

    eprintln!("containers: big results");
    {
        let many = "x".repeat(1000);
        let words: Vec<&str> = vec![many.as_str(); 1000]; // 1 MB of text
        for _ in 0..40 {
            check(upper(&words)?.len() == 1000, "a big vector of strings");
            check(scale(&vec![0; 100_000], 1)?.len() == 100_000, "a big vector of numbers");
        }
    }
    eprintln!("containers: threads");
    let threads: Vec<_> = (0..8_i64)
        .map(|number| {
            std::thread::spawn(move || {
                for turn in 0..100_i64 {
                    let given = [number, turn, -number];
                    let scaled = scale(&given, 3).map_err(|error| error.to_string())?;
                    if scaled != [3 * number, 3 * turn, -3 * number] {
                        return Err(format!("scale {number} {turn}"));
                    }
                    let text = format!("thread {number} turn {turn}");
                    let loud = upper(&[text.as_str(), "é"]).map_err(|error| error.to_string())?;
                    if loud[0] != text.to_uppercase() {
                        return Err(format!("upper {number} {turn}"));
                    }
                }
                Ok(())
            })
        })
        .collect();
    for thread in threads {
        thread.join().expect("a thread panicked").expect("a thread failed");
    }

    println!("containers: Rust round trip OK");
    Ok(())
}
