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
from own.tree import Driver, Pair, Tree, idOf, pick, same, total, twin


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

for obj in (a, b, tree):
    obj.close()
assert own is not None
print("ownership: Python round trip OK")
