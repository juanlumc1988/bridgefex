# SPDX-License-Identifier: Apache-2.0
"""Round trip through the generated Python bindings of the "ownership" case.

tests/test_roundtrip.py runs this file in a fresh interpreter, with the
generated package on PYTHONPATH and OWN_LIBRARY pointing at the built library.
"""

import copy
import gc
import pickle
import subprocess
import sys

import own
from own import Error
from own.node import Item, Node
from own.tree import (
    Box,
    Driver,
    Pair,
    Peek,
    Pipeline,
    Tree,
    consume,
    globalNodeValue,
    idOf,
    pick,
    pickFirst,
    same,
    setGlobalNode,
    total,
    twin,
)


def expect(exception_type, function, *args, message=None):
    try:
        function(*args)
    except exception_type as error:
        if message is not None:
            assert message in str(error), str(error)
        return error
    raise AssertionError(f"{function.__name__}{args} did not raise {exception_type.__name__}")


# The singleton and its root Node live until the program ends.
Tree.global_()
SINGLETON_NODES = Node.live()

a, b = Node(3), Node(4)
tree = Tree()

# References and pointers.
assert tree.sum(a, b) == 7
tree.bump(a)
assert a.value() == 4
assert tree.read(b) == 4
tree.write(b, 40)
assert b.value() == 40

# A copy: the caller keeps its object.
live = Node.live()
tree.addCopy(a)
assert Node.live() == live
assert a.value() == 4 and tree.copies() == 1004

# Overloads that differ in the way they take the node.
assert tree.weight_own_Node(a) == 4
assert tree.weight_own_Node_ref(a) == 8
assert tree.weight_own_Node_cptr(a) == 12
assert tree.weight_own_Node_ptr(a) == 16
assert Tree.twice(a) == 8

# A class of another header, and a move-only one.
with Item(10) as item:
    assert total(a, item) == 14
    assert idOf(item) == 10

# Wrong arguments are refused before anything runs.
expect(TypeError, tree.sum, a, None, message="argument 'b' must be Node, not NoneType")
expect(TypeError, tree.sum, a, 5, message="argument 'b' must be Node, not int")
expect(TypeError, tree.sum, tree, b, message="argument 'a' must be Node, not own.tree.Tree")
expect(TypeError, tree.read, None, message="argument 'node' must be Node")
expect(TypeError, total, a, b, message="argument 'item' must be Item, not own.node.Node")
expect(TypeError, tree.sum, a, b, 1)
assert tree.sum(a, b) == 44

# A closed object cannot be used, as a parameter or as self.
closed = Node(1)
closed.close()
expect(ValueError, tree.sum, a, closed, message="Node object is closed")
expect(ValueError, tree.bump, closed, message="Node object is closed")
expect(ValueError, closed.value, message="Node object is closed")
closed.close()  # again: nothing
dead = Tree()
dead.close()
expect(ValueError, dead.sum, a, b, message="Tree object is closed")

# Results by value and in a std::unique_ptr: new objects that Python owns.
before = Node.live()
made = tree.makeNode(9)
assert type(made) is Node and made.value() == 9 and Node.live() == before + 1
made.close()
assert Node.live() == before
with tree.makeItem(3) as made_item:
    assert type(made_item) is Item and made_item.id() == 3
with tree.makeUnique(4, True) as unique:
    assert type(unique) is Item and unique.id() == 4
assert tree.makeUnique(4, False) is None
expect(Error, tree.makeChecked, -1, message="negative")
assert tree.makeChecked(2).value() == 2
expect(TypeError, tree.makeNode, "x")
made = tree.makeNode(1)
del made
gc.collect()
assert Node.live() == before  # a collected object is destroyed

# Results by reference are borrowed: the C++ object belongs to its owner.
root = tree.root()
root.setValue(50)
assert tree.croot().value() == 50 and root.value() == 50
croot = tree.croot()
expect(TypeError, croot.setValue, 1, message="Node object is read-only")
expect(TypeError, tree.bump, croot, message="Node object is read-only")
assert croot.value() == 50
root.close()  # destroys nothing
assert tree.root().value() == 50
expect(ValueError, root.value, message="Node object is closed")

# It keeps its owners alive, and is invalid once one of them is closed.
other = Tree()
borrowed = other.root()
other_live = Node.live()
del other
gc.collect()
assert Node.live() == other_live and borrowed.value() == 0  # `other` is still alive
chained = borrowed.me()
assert chained.value() == 0 and chained.cme().value() == 0
borrowed._owners[0].close()
expect(ValueError, borrowed.value, message="no longer valid")
expect(ValueError, chained.value, message="no longer valid: an object it was obtained from")
del borrowed, chained
gc.collect()
assert Node.live() == other_live - 1  # the root of `other` went with it

# A static method or a function returns what it borrows from its arguments.
first, second = Node(1), Node(2)
assert pick(first, second, True).value() == 2
picked = pick(first, second, False)
first.setValue(11)
assert picked.value() == 11
second.close()
expect(ValueError, picked.value, message="no longer valid")  # borrowed from both
del picked  # it kept both alive
assert twin(first).value() == 11
assert same(first).value() == 11
expect(TypeError, same(first).setValue, 1, message="read-only")
global_tree = Tree.global_()
assert global_tree.root().value() == global_tree.croot().value()
global_tree.close()  # borrowed from nobody: nothing is destroyed
assert Tree.global_().croot().value() == 0

# A borrowed object cannot be copied or pickled either.
expect(TypeError, copy.copy, root)
expect(TypeError, pickle.dumps, root)

# Transfer: the C++ code takes the objects, and Python gives them up.
items_before = Item.live()
five = Item(5)
tree.adopt(five)
expect(ValueError, five.id, message="Item object is closed")
five.close()  # nothing left to destroy
assert tree.items() == 1 and tree.itemSum() == 5 and Item.live() == items_before + 1
assert tree.first().id() == 5
expect(ValueError, tree.adopt, five, message="argument 'item': Item object is closed")

# Everything is checked before anything is given up.
x, y = Item(7), Item(8)
expect(ValueError, tree.adoptBoth, x, 1, x, message="argument 'second' is the same object")
expect(ValueError, tree.adoptBoth, x, 1, five, message="argument 'second': Item object is closed")
expect(ValueError, tree.adoptBoth, tree.first(), 1, y, message="cannot be transferred")
expect(TypeError, tree.adoptBoth, x, 1, Node(1), message="argument 'second' must be Item")
expect(TypeError, tree.adoptBoth, x, "1", y)
expect(ValueError, dead.adoptBoth, x, 1, y, message="Tree object is closed")
assert x.id() == 7 and y.id() == 8  # still theirs
assert tree.items() == 1
# self is not an argument.
expect(ValueError, tree.adopt, tree.first(), message="cannot be transferred")

# An exception after the objects were taken: they are destroyed anyway.
expect(Error, tree.adoptBoth, x, -1, y, message="tag")
expect(ValueError, x.id, message="Item object is closed")
expect(ValueError, y.id, message="Item object is closed")
assert Item.live() == items_before + 1
tree.adoptBoth(Item(9), 1, Item(10))
assert tree.items() == 3 and tree.itemSum() == 24

# Constructors and functions take them too.
item = Item(11)
pipeline = Pipeline.create_own_Item_unique(item)
expect(ValueError, item.id, message="Item object is closed")
assert pipeline.firstId() == 11 and Item.live() == items_before + 4
pipeline.close()
assert Item.live() == items_before + 3
failing = Item(12)
expect(Error, Pipeline.create_own_Item_unique_int32, failing, 100, message="below the minimum")
expect(ValueError, failing.id, message="Item object is closed")
assert Item.live() == items_before + 3
last = Item(14)
assert consume(last) == 14
expect(ValueError, last.id, message="Item object is closed")
assert Item.live() == items_before + 3

# What the tree owns is destroyed with it.
tree_with_items = Tree()
tree_with_items.adopt(Item(1))
assert Item.live() == items_before + 4
tree_with_items.close()
assert Item.live() == items_before + 3

# Subclasses are accepted.


class Special(Node):
    pass


special = Special(100)
assert tree.sum(special, a) == 104
special.close()

# A constructor keeps what the C++ object keeps a pointer to.
node = Node(7)
driver = Driver(node)
node_live = Node.live()
del node
gc.collect()
assert Node.live() == node_live  # the driver keeps it alive
assert driver.read() == 7
driver.close()
gc.collect()
assert Node.live() == node_live - 1

first, second = Node(1), Node(2)
pair = Pair(first, second)
second.setValue(20)
del first, second
gc.collect()
assert pair.sum() == 21
pair.close()
gc.collect()
assert Node.live() == live

# A failed constructor retains nothing that matters, and leaks nothing.
expect(TypeError, Driver, None, message="argument 'node' must be Node")
expect(TypeError, Pair, Node(1), 5, message="argument 'second' must be Node, not int")
gc.collect()
assert Node.live() == live

# Header modules never import each other when they are imported, so any order works.
PROGRAMS = (
    "import own.tree, own.node",
    "import own.node, own.tree",
    "from own.tree import Tree\nfrom own.node import Node\n"
    "assert Tree().sum(Node(1), Node(2)) == 3",
    "from own.tree import total\nfrom own.node import Item, Node\n"
    "assert total(Node(1), Item(2)) == 3",
)
for code in PROGRAMS:
    subprocess.run([sys.executable, "-c", code], check=True)

# Raw pointers: the annotations say who owns them.
base_nodes = Node.live()
root = tree.root()
root.setValue(50)
found = tree.find(50)
assert found is not None and found.value() == 50
assert tree.find(51) is None
cfound = tree.cfind(50)
assert cfound is not None and cfound.value() == 50
expect(TypeError, cfound.setValue, 1, message="read-only")
assert tree.cfind(51) is None
# A borrowed object keeps its owner alive, and is invalid once the owner is closed.
spare = Tree()
inside = spare.find(0)
del spare
gc.collect()
assert inside is not None and inside.value() == 0
inside._owners[0].close()
expect(ValueError, inside.value, message="no longer valid")
del inside
gc.collect()
assert Node.live() == base_nodes

# An owned pointer is Python's: None when there is nothing, else to destroy.
while tree.items():
    tree.detach().close()
assert tree.detach() is None
base_items = Item.live()
tree.adopt(Item(31))
assert Item.live() == base_items + 1
with tree.detach() as detached:
    assert type(detached) is Item and detached.id() == 31
assert Item.live() == base_items
raw = Item(20)
tree.adoptRaw(raw)
expect(ValueError, raw.id, message="Item object is closed")
expect(TypeError, tree.adoptRaw, None, message="argument 'item' must be Item, not NoneType")
assert tree.detach().id() == 20

# Pointers that may be NULL.
assert tree.valueOf(None) == -1 and tree.valueOf(a) == a.value()
assert tree.both(None, b) == b.value() and tree.both(None, None) == 0
expect(TypeError, tree.valueOf, 5, message="argument 'node' must be Node or None, not int")
expect(TypeError, tree.sum, None, b, message="argument 'a' must be Node, not NoneType")
assert Tree.labelLength(None) == -1 and Tree.labelLength("abc") == 3
expect(ValueError, Tree.labelLength, "a\0b", message="NUL")
expect(TypeError, Tree.labelLength, 5)

# A reference is borrowed from the arguments that are not None.
only = Node(61)
picked_first = pickFirst(only, None)
assert picked_first.value() == 61
assert picked_first._owners == (only,)
other = Node(62)
assert pickFirst(only, other).value() == 62
del picked_first
only.close()
other.close()

# Bytes, not text.
assert tree.signature() == b"a\x00\xff"

# What the C++ object keeps pointers to is kept alive by it, until it is closed.
cursor = Node(77)
tree.setCursor(cursor)
del cursor
gc.collect()
assert Node.live() == base_nodes + 1 and tree.cursorValue() == 77
holder = Tree()
holder.setCursor(Node(78))
gc.collect()
assert holder.cursorValue() == 78 and Node.live() == base_nodes + 3
holder.close()
gc.collect()
assert Node.live() == base_nodes + 1  # its root and its cursor went with it
# A pointer that is replaced stays alive until the object is closed.
tree.setCursor(a)
gc.collect()
assert Node.live() == base_nodes + 1 and tree.cursorValue() == a.value()

# A taken object keeps pointing to what it retained, also when it is given back.
back_before = Node.live()
box = Box()
holder = Tree()
pointee = Node(88)
holder.setCursor(pointee)
box.put(holder)
del pointee
gc.collect()
back = box.pop()
box.close()
gc.collect()
assert back.cursorValue() == 88 and Node.live() == back_before + 2  # holder's root, the node
back.close()
gc.collect()
assert Node.live() == back_before

# A borrowed object was not built by its __init__, and cannot be built again.
expect(TypeError, tree.root().__init__, 99, message="already initialized")

# A constructor keeps what it gets, unless it only reads it.
transient = Node(4)
peek = Peek(transient)
del transient
gc.collect()
assert peek.seen() == 4 and Node.live() == base_nodes + 1
peek.close()

for obj in (a, b, tree):
    obj.close()
gc.collect()
assert Node.live() == SINGLETON_NODES and Item.live() == 0

# Without an object to keep them, a function or a static method keeps them forever.
kept = len(own._runtime.forever)
shared = Node(79)
Tree.setSharedCursor(shared)
del shared
global_node = Node(80)
setGlobalNode(global_node)
del global_node
gc.collect()
assert Tree.sharedCursorValue() == 79 and globalNodeValue() == 80
assert len(own._runtime.forever) == kept + 2 and Node.live() == SINGLETON_NODES + 2
assert own is not None
print("ownership: Python round trip OK")
