// SPDX-License-Identifier: Apache-2.0
// Round trip through the generated Rust bindings of the "edge" case: names that are keywords or
// builtins somewhere, and a destructor that throws.
//
// tests/test_rust_roundtrip.py builds this file as the main.rs of a crate that depends on the
// generated one, and runs it with the library of the case on the library path.

use edge::edge::{add, combine, getattr, threading, Registry};
use edge::ErrorKind;

fn check(condition: bool, what: &str) {
    assert!(condition, "check failed: {what}");
}

fn main() -> edge::Result<()> {
    check(add(2, 3)? == 5, "add");
    check(combine(1, 2, 3)? == 123, "combine");
    check(getattr(41)? == 42, "getattr");
    check(threading()? == 4, "threading");

    check(Registry::live_count()? == 0, "no registry yet");
    let registry = Registry::new()?;
    check(registry.classmethod()? == 1, "classmethod");
    check(registry.staticmethod()? == 2, "staticmethod");
    drop(registry);
    check(Registry::live_count()? == 0, "dropped");

    // A method that throws: the error has the message of C++.
    {
        let mut failing = Registry::new()?;
        let error = failing.fail().expect_err("the call must fail");
        check(error.kind() == ErrorKind::Exception, "kind");
        check(error.message() == "failed on purpose", "message");
        // A destructor that throws: dropping still destroys the object, and does not unwind.
        failing.throw_on_destroy(true)?;
    }
    check(Registry::live_count()? == 0, "a destructor that throws still destroys the object");

    println!("round trip OK");
    Ok(())
}
