# SPDX-License-Identifier: Apache-2.0
"""Round trip through the generated Python bindings of the "keeping" case.

tests/test_roundtrip.py runs this file in a fresh interpreter, with the generated package on
PYTHONPATH and KEEP_LIBRARY pointing at the built library. What C++ keeps a pointer to has to
live as long as C++ may use it: the C++ code reads it later (the Latch reads its Gate, the Label
adds up the bytes of its texts again), and a Gate or a text that Python had freed shows.
"""

import gc

import keep
from keep.kept import Gate, Label, Latch


def churn():
    """Allocate and free objects of many sizes, so that memory that was freed is reused (and
    overwritten) before C++ reads it."""
    junk = [bytes([index % 251]) * size for size in range(1, 300) for index in range(40)]
    del junk
    gc.collect()
    Label.scribble()  # and the memory of C++


# `this = "retain"`: the object that the method returns keeps the object it was called on.
gate = Gate()
latch = gate.latch()
del gate
gc.collect()
churn()
assert latch.open() and latch.number() == 1
assert Gate.live() == 1  # the Gate is still there: the Latch keeps it alive
del latch
gc.collect()
assert Latch.live() == 0 and Gate.live() == 0  # both went, the Latch first

# A Gate that nothing keeps is destroyed when it is dropped.
Gate()
gc.collect()
assert Gate.live() == 0

# Several results keep the same object, and it goes with the last of them.
gate = Gate()
first, second = gate.latch(), gate.latch()
del gate
gc.collect()
assert first.open() and second.open() and Gate.live() == 1
del first
gc.collect()
assert second.open() and Gate.live() == 1
del second
gc.collect()
assert Gate.live() == 0

# A constructor keeps the object that it is given (the default for an object).
latch = Latch(Gate(), 5)
gc.collect()
churn()
assert latch.open() and latch.number() == 5 and Gate.live() == 1
del latch
gc.collect()
assert Gate.live() == 0

# Without an object that keeps it, the object that C++ keeps is kept for ever.
kept = len(keep._runtime.forever)
base = Gate.live()
gate = Gate()
gate.pin()
del gate
gc.collect()
churn()
assert Gate.live() == base + 1 and Gate.pinnedAlive()
assert len(keep._runtime.forever) == kept + 1

# `retain` on a text: the text that C++ stores is kept as long as the object that stores it.
label = Label.create_cstr("a title that is a temporary " + "t" * 20)
label.setRaw("a raw text that is a temporary " + "r" * 30)
label.setView("a view that is a temporary " + "v" * 40)
label.setMaybe("maybe " + "m" * 10)
label.setCopy("a copy " + "c" * 50)
gc.collect()
churn()
assert label.intact()
expected = len("a title that is a temporary ") + 20
expected += len("a raw text that is a temporary ") + 30
expected += len("a view that is a temporary ") + 40
expected += len("maybe ") + 10
assert label.kept() == expected
label.setMaybe(None)  # NULL: no text
assert label.kept() == expected - len("maybe ") - 10
gc.collect()
churn()
assert label.intact()

# A text that is replaced is still kept: C++ may use the old one until the object is closed.
for number in range(100):
    label.setView(f"text number {number} " + "w" * (number % 17))
    churn_text = bytes([number % 200]) * (number % 31 + 1)  # reuses the memory that was freed
gc.collect()
churn()
assert label.intact()

# Closing the object lets go of its texts.
label.close()
gc.collect()

# Without an object, for ever (a function or a static method).
kept = len(keep._runtime.forever)
Label.keepView("a text for ever " + "f" * 30)
gc.collect()
churn()
assert Label.keptIntact()
assert len(keep._runtime.forever) == kept + 1

assert keep is not None
print("keeping: Python round trip OK")
