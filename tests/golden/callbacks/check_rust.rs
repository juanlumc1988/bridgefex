// SPDX-License-Identifier: Apache-2.0
// Round trip through the generated Rust bindings of the "callbacks" case: a `std::function` goes in
// as a closure (`Fn`, `Send + Sync + 'static`; an `Option` of one where the annotations say that it
// may be empty), and C++ calls it, on any thread, while the call runs or later.
//
// tests/test_rust_roundtrip.py builds this file as the main.rs of a crate that depends on the
// generated one, and runs it with the library of the case on the library path.

use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use cbx::events::{
    classify, count_if, fire, integrate, is_empty, log, log_at_exit, parallel, pick, prefixed,
    set_log_handler, shift, store_and_sleep, strings, sum, walk, Level, Mode, Point, Timer,
    Watcher,
};
use cbx::ErrorKind;

fn check(condition: bool, what: &str) {
    assert!(condition, "check failed: {what}");
}

/// What a closure that C++ holds captures, to see when C++ lets it go.
struct Held(Arc<AtomicUsize>);

impl Held {
    fn new(live: &Arc<AtomicUsize>) -> Held {
        live.fetch_add(1, Ordering::SeqCst);
        Held(Arc::clone(live))
    }
}

impl Drop for Held {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::SeqCst);
    }
}

/// The text of the payload of a panic.
fn message(payload: &(dyn std::any::Any + Send)) -> String {
    if let Some(text) = payload.downcast_ref::<&str>() {
        (*text).to_string()
    } else if let Some(text) = payload.downcast_ref::<String>() {
        text.clone()
    } else {
        "?".to_string()
    }
}

fn main() -> cbx::Result<()> {
    let live = Arc::new(AtomicUsize::new(0));
    let nothing_held = |when: &str| {
        check(live.load(Ordering::SeqCst) == 0, &format!("C++ holds no closure {when}"));
    };

    // -- Results, and the closure is released once the call is over ----------------------------

    let held = Held::new(&live);
    check(sum(5, move |i| { let _ = &held; i * 2 })? == 20, "sum");
    nothing_held("after a synchronous call");
    check(count_if(6, |i| i % 3 == 0)? == 2, "count_if: a bool result and a 64-bit argument");
    check(is_empty(None::<fn()>)?, "None is an empty std::function");
    check(!is_empty(Some(|| {}))?, "a closure without arguments");
    check(classify(1, |_| Level::ERROR)? == Level::ERROR, "an enum result");
    check(pick(|| Mode::AUTO)? == Mode::AUTO, "an enum without a fixed type");
    check((integrate(|x| x, 0.0, 2.0, 4)? - 2.0).abs() < 1e-12, "a double");
    check(Timer::apply(21, |value| value * 2)? == 42, "a static method");
    check(sum(4, i32::abs)? == 6, "a function item is a closure too");

    // -- Arguments: structs, const char* (None for NULL), the three string types, enums -------------

    let seen: Arc<Mutex<Vec<(Point, Option<String>)>>> = Arc::default();
    let sink = Arc::clone(&seen);
    let total = walk(move |point, name| {
        sink.lock().unwrap().push((*point, name.map(str::to_string)));
        point.x
    })?;
    check(total == 4.5, "walk");
    let seen = seen.lock().unwrap();
    check(seen.len() == 2, "walk calls twice");
    check(seen[0] == (Point { x: 1.5, y: 2.5 }, Some("first".to_string())), "a struct and a string");
    check(seen[1] == (Point { x: 3.0, y: 4.0 }, None), "a NULL const char* is None");
    drop(seen);

    let shifted: Arc<Mutex<Vec<(Point, Mode)>>> = Arc::default();
    let sink = Arc::clone(&shifted);
    shift(move |point, mode| sink.lock().unwrap().push((*point, mode)))?;
    check(*shifted.lock().unwrap() == [(Point { x: -1.0, y: 1.0 }, Mode::AUTO)], "shift");

    let texts: Arc<Mutex<Vec<String>>> = Arc::default();
    let sink = Arc::clone(&texts);
    strings(move |a, b, c| sink.lock().unwrap().extend([a.to_string(), b.to_string(), c.to_string()]))?;
    check(*texts.lock().unwrap() == ["a\0b", "a\0b", "a\0b"], "string, std::string and std::string_view keep a NUL");
    nothing_held("after the callbacks with arguments");

    // -- A result that C++ cannot hold fails the closure, and the call raises it ---------------------

    std::panic::set_hook(Box::new(|_| {}));
    for wrong in [42, 4, -1] {
        let raised = catch_unwind(AssertUnwindSafe(|| pick(move || Mode(wrong))));
        let payload = raised.expect_err("the call raises the failure of the callback");
        check(message(&*payload).contains(&format!("a callback returned {wrong}")), "the message says the value");
    }
    check(pick(|| Mode(3))? == Mode(3), "the last value of the enum is accepted");
    check(classify(1, |_| Level(200))? == Level(200), "an enum with a fixed type holds every value of it");

    // -- The first panic of a closure is the one the call raises; C++ gets defaults for the rest -----

    let calls: Arc<Mutex<Vec<i32>>> = Arc::default();
    let record = Arc::clone(&calls);
    let held = Held::new(&live);
    let raised = catch_unwind(AssertUnwindSafe(|| {
        sum(1000, move |i| {
            let _ = &held;
            record.lock().unwrap().push(i);
            if i == 2 {
                panic!("boom");
            }
            i
        })
    }));
    let payload = raised.expect_err("the call raises the panic of the closure");
    check(message(&*payload) == "boom", "it is the payload of the closure");
    check(*calls.lock().unwrap() == [0, 1, 2], "the closures after the failure do not run");
    nothing_held("after a closure that panics");
    check(sum(3, |i| i)? == 3, "the call after the failure is not affected");

    // Of two closures that fail, the call raises the first.
    let raised = catch_unwind(AssertUnwindSafe(|| {
        sum(5, |i| if i == 0 { panic!("first") } else { panic!("second") })
    }));
    check(message(&*raised.expect_err("a panic")) == "first", "the first one");

    // A call that a closure makes raises its own closures' panics, and the closure may catch them.
    let value = sum(2, |i| {
        let inner = catch_unwind(AssertUnwindSafe(|| sum(1, |_| panic!("inner"))));
        if inner.is_err() { i } else { -100 }
    })?;
    check(value == 1, "an inner call raises its own callbacks' panics");
    check(sum(3, |i| sum(i, |j| j).unwrap() as i32)? == 1, "a call inside a closure");
    let raised = catch_unwind(AssertUnwindSafe(|| sum(2, |_| sum(1, |_| panic!("inner")).unwrap() as i32)));
    check(message(&*raised.expect_err("a panic")) == "inner", "an uncaught inner panic is the outer one");
    nothing_held("after nested calls");

    // A C++ exception: an error, and the closure is released.
    let error = integrate(|x| x, 0.0, 1.0, 0).expect_err("steps must be positive");
    check(error.kind() == ErrorKind::Exception && error.message().contains("steps must be positive"), "a C++ exception");

    // An empty std::function called by C++: bad_function_call.
    let error = prefixed("x", None::<fn(&str) -> i32>).expect_err("an empty function");
    check(error.kind() == ErrorKind::Exception && error.message().contains("function"), "bad_function_call");

    // -- Closures on C++ threads; a panic there is raised by the call that gave the closure ---------

    let hits: Arc<Mutex<Vec<i32>>> = Arc::default();
    let sink = Arc::clone(&hits);
    parallel(4, 25, move |thread| sink.lock().unwrap().push(thread))?;
    let mut hits = hits.lock().unwrap().clone();
    check(hits.len() == 100, "parallel");
    hits.sort_unstable();
    hits.dedup();
    check(hits == [0, 1, 2, 3], "every thread ran the closure");
    let raised = catch_unwind(AssertUnwindSafe(|| parallel(3, 5, |_| panic!("on a thread"))));
    check(message(&*raised.expect_err("a panic")) == "on a thread", "a panic of a C++ thread is raised");
    let runs = Arc::new(AtomicUsize::new(0));
    let count = Arc::clone(&runs);
    let raised = catch_unwind(AssertUnwindSafe(|| {
        parallel(1, 25, move |_| {
            count.fetch_add(1, Ordering::SeqCst);
            panic!("once");
        })
    }));
    check(raised.is_err() && runs.load(Ordering::SeqCst) == 1, "the closures after the failure do not run, on a C++ thread");
    nothing_held("after the closures of C++ threads");

    // -- Stored closures: kept while C++ keeps them, released on replacement ------------------------

    let log_lines: Arc<Mutex<Vec<(Level, String)>>> = Arc::default();
    let sink = Arc::clone(&log_lines);
    let held = Held::new(&live);
    set_log_handler(Some(move |level, text: &str| {
        let _ = &held;
        sink.lock().unwrap().push((level, text.to_string()));
    }))?;
    check(*log_lines.lock().unwrap() == [(Level::INFO, "set".to_string())], "the handler is called when it is stored");
    check(log(Level::DEBUG, "x\0y")?, "log");
    check(log_lines.lock().unwrap().last() == Some(&(Level::DEBUG, "x\0y".to_string())), "a NUL in a string argument");
    check(live.load(Ordering::SeqCst) == 1, "C++ holds the handler");
    set_log_handler(None::<fn(Level, &str)>)?;
    nothing_held("after replacing the handler");
    check(!log(Level::DEBUG, "lost")?, "no handler");

    // A panic in a stored closure, run by a call that gave no closure, belongs to no call: that call
    // succeeds (the panic hook has shown the message) and C++ goes on.
    set_log_handler(Some(|_: Level, text: &str| {
        if text == "late" {
            panic!("late");
        }
    }))?;
    check(log(Level::INFO, "late")?, "a call whose stored closure panics returns normally");
    check(log(Level::INFO, "fine")?, "the handler goes on");
    set_log_handler(None::<fn(Level, &str)>)?;

    // -- Objects: the closure lives as long as the C++ object ---------------------------------------

    let ticks: Arc<Mutex<Vec<i32>>> = Arc::default();
    let sink = Arc::clone(&ticks);
    let held = Held::new(&live);
    let mut timer = Timer::new(move |value| {
        let _ = &held;
        sink.lock().unwrap().push(value);
    })?;
    timer.tick(3)?;
    check(*ticks.lock().unwrap() == [0, 1, 2] && timer.ticks()? == 3, "a timer");
    check(live.load(Ordering::SeqCst) == 1, "the timer holds its closure");
    timer.set_on_tick(None::<fn(i32)>)?;
    nothing_held("after set_on_tick(None)");
    let sink = Arc::clone(&ticks);
    let held = Held::new(&live);
    timer.set_on_tick(Some(move |value| {
        let _ = &held;
        sink.lock().unwrap().push(value);
    }))?;
    drop(timer);
    nothing_held("after the timer is dropped");

    // The closure of a timer that panics runs inside a later call that gave no closures: every tick
    // is a panic of nobody, and C++ goes on to the end of its loop.
    let timer = Timer::new(|value| panic!("tick {value}"))?;
    let mut timer = timer;
    timer.tick(5)?;
    check(timer.ticks()? == 5, "the loop of C++ ran to its end");
    timer.set_on_tick(None::<fn(i32)>)?;
    timer.tick(2)?;
    check(timer.ticks()? == 7, "the object is usable after the panics");
    drop(timer);
    let _ = std::panic::take_hook();

    // -- Beside by-reference parameters ---------------------------------------------------------------

    let ok = |text: &str| if text == "ab!" { text.len() as i32 } else { -1 };
    check(prefixed("ab", Some(ok))? == 3, "a std::string argument and a callback");
    let each: Arc<Mutex<Vec<i32>>> = Arc::default();
    let sink = Arc::clone(&each);
    let mut plain = Timer::new(|_| {})?;
    check(plain.each(&[4, 5, 6], move |value| sink.lock().unwrap().push(value))? == 3, "a slice and a closure");
    check(*each.lock().unwrap() == [4, 5, 6], "each value");
    drop(plain);
    let notified: Arc<Mutex<Vec<(String, Point)>>> = Arc::default();
    let sink = Arc::clone(&notified);
    let held = Held::new(&live);
    let watcher = Watcher::new("w", Point { x: 1.0, y: 2.0 }, move |name, at| {
        let _ = &held;
        sink.lock().unwrap().push((name.to_string(), *at));
    })?;
    watcher.notify()?;
    check(*notified.lock().unwrap() == [("w".to_string(), Point { x: 1.0, y: 2.0 })], "a watcher");
    check(live.load(Ordering::SeqCst) == 1, "the watcher holds its closure");
    drop(watcher);
    nothing_held("after the watcher");

    // -- A closure that C++ keeps, in a call that has returned ----------------------------------------

    store_and_sleep(Some(|value| value * 2), 10)?;
    check(fire(21)? == 42, "fire");
    store_and_sleep(None::<fn(i32) -> i32>, 0)?;
    check(fire(21)? == -1, "no handler");

    // -- Many threads give closures at once -------------------------------------------------------------

    let threads: Vec<_> = (0..8_i32)
        .map(|number| {
            std::thread::spawn(move || {
                for turn in 0..200_i32 {
                    let got = sum(10, move |i| i * number + turn).map_err(|error| error.to_string())?;
                    let expected: i64 = (0..10).map(|i| i64::from(i * number + turn)).sum();
                    if got != expected {
                        return Err(format!("sum {number} {turn}: {got} != {expected}"));
                    }
                }
                Ok(())
            })
        })
        .collect();
    for thread in threads {
        thread.join().expect("a thread panicked").expect("a thread failed");
    }
    nothing_held("after the threads");

    // The handler that C++ calls from a static destructor, when the process ends.
    set_log_handler(Some(|level: Level, text: &str| println!("at exit: {level:?} {text}")))?;
    log_at_exit(true)?;

    println!("callbacks: Rust round trip OK");
    Ok(())
}
