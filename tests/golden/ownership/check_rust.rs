// SPDX-License-Identifier: Apache-2.0
// Round trip through the generated Rust bindings of the "ownership" case.
//
// tests/test_rust_roundtrip.py builds this file as the main.rs of a crate that depends on the
// generated one, and runs it with the library of the case on the library path. What Rust refuses
// to compile (a use after a move, two `&mut` of one object, a borrow that outlives its owner) is
// tested there, not here.

use own::node::{Item, Node};
use own::tree::{Driver, Pair, Peek, Pipeline, Tree};
use own::{Error, ErrorKind, Owned};

fn check(condition: bool, what: &str) {
    assert!(condition, "check failed: {what}");
}

// The error of a call that must fail, with the kind and the message that it must have.
fn fails<T>(result: own::Result<T>, kind: ErrorKind, message: &str) -> Error {
    match result {
        Ok(_) => panic!("the call succeeded, and {kind:?} ({message}) was expected"),
        Err(error) => {
            assert!(
                error.kind() == kind && error.message().contains(message),
                "{error} is not {kind:?} with '{message}'"
            );
            error
        }
    }
}

fn main() -> own::Result<()> {
    // The singleton and its root Node live until the program ends.
    let _ = unsafe { Tree::global()? };

    let mut a = Node::new(3)?;
    let mut b = Node::new(4)?;
    let mut tree = Tree::new()?;

    // A class is `Debug` (it has no state to show), so what holds an `Owned` can derive it.
    #[derive(Debug)]
    #[allow(dead_code)]
    struct Holder {
        node: Owned<Node>,
    }
    check(format!("{:?}", Holder { node: Node::new(1)? }) == "Holder { node: Node { .. } }", "Debug");

    // References and pointers.
    check(tree.sum(&a, &b)? == 7, "sum");
    tree.bump(&mut a)?;
    check(a.value()? == 4, "bump");
    check(tree.read(&b)? == 4, "read");
    tree.write(&mut b, 40)?;
    check(b.value()? == 40, "write");

    // A copy: the caller keeps its object.
    let live = Node::live()?;
    tree.add_copy(&a)?;
    check(Node::live()? == live, "the copy is destroyed");
    check(a.value()? == 4 && tree.copies()? == 1004, "copies");

    // Overloads that differ in the way they take the node.
    check(tree.weight_own_node(&a)? == 4, "weight(const Node&)");
    check(tree.weight_own_node_ref(&mut a)? == 8, "weight(Node&)");
    check(tree.weight_own_node_cptr(&a)? == 12, "weight(const Node*)");
    check(tree.weight_own_node_ptr(&mut a)? == 16, "weight(Node*)");
    check(Tree::twice(&a)? == 8, "twice");

    // A class of another header, and a move-only one.
    {
        let item = Item::new(10)?;
        check(own::tree::total(&a, &item)? == 14, "total");
        check(own::tree::id_of(&item)? == 10, "idOf");
    }

    // Results by value and in a std::unique_ptr: new objects that Rust owns.
    let before = Node::live()?;
    let made = tree.make_node(9)?;
    check(made.value()? == 9 && Node::live()? == before + 1, "makeNode");
    drop(made);
    check(Node::live()? == before, "dropping destroys it");
    check(tree.make_item(3)?.id()? == 3, "makeItem");
    {
        let unique = tree.make_unique(4, true)?;
        check(unique.as_ref().map(|item| item.id()).transpose()? == Some(4), "makeUnique");
    }
    check(tree.make_unique(4, false)?.is_none(), "an empty unique_ptr is None");
    fails(tree.make_checked(-1), ErrorKind::Exception, "negative");
    check(tree.make_checked(2)?.value()? == 2, "makeChecked");
    check(Node::live()? == before, "nothing leaked");

    // Results by reference are borrowed: the C++ object belongs to its owner.
    tree.root()?.set_value(50)?;
    check(tree.croot()?.value()? == 50 && tree.root()?.value()? == 50, "root");
    check(tree.sum(tree.croot()?, &b)? == 50 + b.value()?, "a read-only borrow as an argument");
    check(own::tree::twin(tree.croot()?)?.value()? == 50, "a read-only borrow is copied");
    check(tree.value_of(tree.cfind(50)?)? == 50, "a read-only pointer as a const pointer");
    check(own::tree::same(tree.croot()?)?.value()? == 50, "and as the argument of a function");
    check(tree.root()?.me()?.cme()?.value()? == 50, "chained borrows");
    check(Node::live()? == before, "borrowing creates nothing");

    // A function returns what it borrows from its arguments.
    let mut first = Node::new(1)?;
    let mut second = Node::new(2)?;
    check(own::tree::pick(&mut first, &mut second, true)?.value()? == 2, "pick second");
    {
        let picked = own::tree::pick(&mut first, &mut second, false)?;
        picked.set_value(11)?;
        check(picked.value()? == 11, "pick first");
    }
    check(first.value()? == 11, "the borrowed object is the argument");
    check(own::tree::twin(&first)?.value()? == 11, "twin");
    check(own::tree::same(&first)?.value()? == 11, "same");
    {
        let global = unsafe { Tree::global()? };
        check(global.root()?.value()? == global.croot()?.value()?, "global");
    }

    // Transfer: the C++ code takes the objects, and Rust gives them up.
    let items_before = Item::live()?;
    tree.adopt(Item::new(5)?)?;
    check(tree.items()? == 1 && tree.item_sum()? == 5, "adopt");
    check(Item::live()? == items_before + 1, "the tree owns the item");
    check(tree.first()?.id()? == 5, "first");

    // An exception after the objects were taken: they are destroyed anyway.
    let error = fails(tree.adopt_both(Item::new(7)?, -1, Item::new(8)?), ErrorKind::Exception, "tag");
    check(error.kind() == ErrorKind::Exception, "kind");
    check(Item::live()? == items_before + 1, "taken and destroyed");
    tree.adopt_both(Item::new(9)?, 1, Item::new(10)?)?;
    check(tree.items()? == 3 && tree.item_sum()? == 24, "adoptBoth");

    // Constructors and functions take them too.
    let pipeline = Pipeline::new_own_item_unique(Item::new(11)?)?;
    check(pipeline.first_id()? == 11 && Item::live()? == items_before + 4, "pipeline");
    drop(pipeline);
    check(Item::live()? == items_before + 3, "pipeline destroys its item");
    fails(
        Pipeline::new_own_item_unique_int32(Item::new(12)?, 100),
        ErrorKind::Exception,
        "below the minimum",
    );
    check(Item::live()? == items_before + 3, "the item of a failed constructor is destroyed");
    check(own::tree::consume(Item::new(14)?)? == 14, "consume");
    check(Item::live()? == items_before + 3, "consumed");

    // What the tree owns is destroyed with it.
    {
        let mut with_items = Tree::new()?;
        with_items.adopt(Item::new(1)?)?;
        check(Item::live()? == items_before + 4, "owned by the tree");
    }
    check(Item::live()? == items_before + 3, "destroyed with the tree");

    // A subclass is not a Rust notion: the case "inherit" tests what a Rust class gets from its
    // base. A class that is dropped in the order of its declaration, backwards, is what
    // `retain` relies on: the C++ object that keeps a pointer is declared after what it keeps.
    let node_live = Node::live()?;
    {
        let mut node = Node::new(7)?;
        let driver = unsafe { Driver::new(&mut node)? };
        check(driver.read()? == 7, "the driver reads its node");
    }
    check(Node::live()? == node_live, "no leak");
    {
        let first = Node::new(1)?;
        let mut second = Node::new(2)?;
        let pair = unsafe { Pair::new(&first, &mut second)? };
        check(pair.sum()? == 3, "the pair reads its nodes");
        second.set_value(5)?;
        check(pair.sum()? == 6, "and sees the changes");
    }
    check(Node::live()? == node_live, "no leak");

    // Raw pointers: the annotations say who owns them.
    tree.root()?.set_value(50)?;
    check(tree.find(50)?.map(|node| node.value()).transpose()? == Some(50), "find");
    check(tree.find(51)?.is_none(), "find nothing");
    check(tree.cfind(50)?.map(|node| node.value()).transpose()? == Some(50), "cfind");
    check(tree.cfind(51)?.is_none(), "cfind nothing");

    // An owned pointer is Rust's: None when there is nothing, else to destroy.
    while tree.items()? > 0 {
        drop(tree.detach()?);
    }
    check(tree.detach()?.is_none(), "nothing to detach");
    let base_items = Item::live()?;
    tree.adopt(Item::new(31)?)?;
    check(Item::live()? == base_items + 1, "adopted");
    {
        let detached = tree.detach()?;
        check(detached.as_ref().map(|item| item.id()).transpose()? == Some(31), "detach");
    }
    check(Item::live()? == base_items, "detached and destroyed");
    tree.adopt_raw(Item::new(20)?)?;
    check(tree.detach()?.map(|item| item.id()).transpose()? == Some(20), "adoptRaw");

    // Pointers that may be null are options.
    check(tree.value_of(None)? == -1 && tree.value_of(Some(&*a))? == a.value()?, "valueOf");
    check(tree.both(None, Some(&*b))? == b.value()? && tree.both(None, None)? == 0, "both");
    check(Tree::label_length(None)? == -1 && Tree::label_length(Some("abc"))? == 3, "labelLength");
    fails(Tree::label_length(Some("a\0b")), ErrorKind::InvalidArgument, "NUL");

    // A reference is borrowed from the arguments that are not null.
    let mut only = Node::new(61)?;
    check(own::tree::pick_first(&mut only, None)?.value()? == 61, "pickFirst");
    let mut other = Node::new(62)?;
    check(own::tree::pick_first(&mut only, Some(&mut *other))?.value()? == 62, "pickFirst of two");

    // Bytes, not text: the annotations say so for this result (the bytes 'a', 0, 0xff).
    check(tree.signature()? == [b'a', 0, 0xff], "signature");

    // What a C++ object keeps a pointer to must outlive it: `retain` is `unsafe`, and the code
    // that calls it says why it is sound. Locals are dropped in reverse order of declaration.
    let mut cursor = Node::new(77)?;
    let mut holder = Tree::new()?;
    unsafe { holder.set_cursor(&mut cursor)? };
    check(holder.cursor_value()? == 77, "the cursor is kept");
    cursor.set_value(78)?;
    check(holder.cursor_value()? == 78, "the cursor is the node itself");

    // A taken object keeps what it retained, also when it is given back.
    let mut slot = own::tree::Box::new()?;
    slot.put(Tree::new()?)?;
    let back = slot.pop()?;
    check(back.is_some() && slot.pop()?.is_none(), "put and pop");
    drop(slot);
    check(back.map(|tree| tree.items()).transpose()? == Some(0), "the tree came back");

    // A constructor that only reads its argument is safe.
    let peek = Peek::new(&a)?;
    check(peek.seen()? == a.value()?, "peek reads the node");

    // Without an object to keep them, a function or a static method keeps them for ever: the
    // objects are leaked on purpose, and the references last as long as the program.
    let nodes = Node::live()?;
    unsafe {
        Tree::set_shared_cursor(Owned::leak(Node::new(79)?))?;
        own::tree::set_global_node(Owned::leak(Node::new(80)?))?;
    }
    check(Tree::shared_cursor_value()? == 79 && own::tree::global_node_value()? == 80, "kept");
    check(Node::live()? == nodes + 2, "two nodes kept for ever");

    println!("round trip OK");
    Ok(())
}
