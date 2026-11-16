// SPDX-License-Identifier: Apache-2.0
// Round trip through the generated C# bindings of the "keeping" case.
//
// tests/test_csharp_roundtrip.py builds this file together with the generated project and runs
// it with KEEP_LIBRARY pointing at the built library. What C++ keeps a pointer to has to live as
// long as C++ may use it: the C++ code reads it later (the Latch reads its Gate, the Label adds up
// the bytes of its texts again), and a Gate or a text that the collector had freed shows.

using System.Runtime.CompilerServices;
using Keep.Kept;

static void Check(bool condition, string what)
{
    if (!condition)
    {
        throw new Exception("check failed: " + what);
    }
}

static void Collect()
{
    GC.Collect();
    GC.WaitForPendingFinalizers();
    GC.Collect();
}

// Memory that was freed is reused (and overwritten) before C++ reads it.
static void Churn()
{
    var junk = new List<byte[]>();
    for (int size = 1; size < 400; size++)
    {
        for (int index = 0; index < 20; index++)
        {
            var block = new byte[size];
            Array.Fill(block, (byte)(index + 1));
            junk.Add(block);
        }
    }
    junk.Clear();
    Collect();
    Label.Scribble();
}

// Whether something becomes true once the garbage is collected (what is kept goes after what keeps it).
static bool Eventually(Func<bool> condition)
{
    for (int round = 0; round < 10; round++)
    {
        Collect();
        if (condition())
        {
            return true;
        }
    }
    return false;
}

// Objects that are made and forgotten in another method: the unoptimized code of the caller
// would keep their references in slots of the stack.
[MethodImpl(MethodImplOptions.NoInlining)]
static Latch LatchOfForgottenGate() => new Gate().Latch()!;

[MethodImpl(MethodImplOptions.NoInlining)]
static (Latch, Latch) TwoLatchesOfForgottenGate()
{
    var gate = new Gate();
    return (gate.Latch()!, gate.Latch()!);
}

[MethodImpl(MethodImplOptions.NoInlining)]
static Latch ConstructedOfForgottenGate() => new Latch(new Gate(), 5);

[MethodImpl(MethodImplOptions.NoInlining)]
static void ForgetGate() => new Gate();

[MethodImpl(MethodImplOptions.NoInlining)]
static void PinForgottenGate() => new Gate().Pin();

[MethodImpl(MethodImplOptions.NoInlining)]
static void SetTemporaryTexts(Label label)
{
    label.SetRaw("a raw text that is a temporary " + new string('r', 30));
    label.SetView("a view that is a temporary " + new string('v', 40));
    label.SetMaybe("maybe " + new string('m', 10));
    label.SetCopy("a copy " + new string('c', 50));
}

[MethodImpl(MethodImplOptions.NoInlining)]
static Label LabelOfTemporaryTitle() => new Label("a title that is a temporary " + new string('t', 20));

// `this = "retain"`: the object that the method returns keeps the object it was called on.
var latch = LatchOfForgottenGate();
Churn();
Check(latch.Open() && latch.Number() == 1, "the Latch reads its Gate");
Check(Gate.Live() == 1, "the Gate is still there: the Latch keeps it alive");
latch.Dispose();
latch = null;
Check(Eventually(() => Latch.Live() == 0 && Gate.Live() == 0), "both went, the Latch first");

// A Gate that nothing keeps is destroyed when it is dropped.
ForgetGate();
Check(Eventually(() => Gate.Live() == 0), "a Gate that nothing keeps");

// Several results keep the same object, and it goes with the last of them.
var (first, second) = TwoLatchesOfForgottenGate();
Churn();
Check(first.Open() && second.Open() && Gate.Live() == 1, "two latches, one gate");
first.Dispose();
Churn();
Check(second.Open() && Gate.Live() == 1, "the second one still keeps it");
second.Dispose();
Check(Eventually(() => Gate.Live() == 0), "gone with the last");

// A constructor keeps the object that it is given (the default for an object).
var constructed = ConstructedOfForgottenGate();
Churn();
Check(constructed.Open() && constructed.Number() == 5 && Gate.Live() == 1, "the constructor keeps it");
constructed.Dispose();
Check(Eventually(() => Gate.Live() == 0), "gone");

// Without an object that keeps it, the object that C++ keeps is kept for ever.
int baseGates = Gate.Live();
PinForgottenGate();
Churn();
Check(Gate.Live() == baseGates + 1 && Gate.PinnedAlive(), "pinned for ever");

// `retain` on a text: the text that C++ stores is kept as long as the object that stores it.
var label = LabelOfTemporaryTitle();
SetTemporaryTexts(label);
Churn();
Check(label.Intact(), "every text is intact");
int expected = "a title that is a temporary ".Length + 20;
expected += "a raw text that is a temporary ".Length + 30;
expected += "a view that is a temporary ".Length + 40;
expected += "maybe ".Length + 10;
Check(label.Kept() == expected, "the length of what it keeps");
label.SetMaybe(null);
Check(label.Kept() == expected - "maybe ".Length - 10, "NULL is no text");
Churn();
Check(label.Intact(), "still intact");

// A text that is replaced is still kept: C++ may use the old one until the object is closed.
for (int number = 0; number < 100; number++)
{
    label.SetView($"text number {number} " + new string('w', number % 17));
}
Churn();
Check(label.Intact(), "the replaced texts are intact");

// Closing the object lets go of its texts, and the C++ object does not read them again.
label.Dispose();
Churn();

// A constructor that fails keeps nothing, and the copy of the text is not leaked (the collector
// releases it).
try
{
    _ = new Label("a title with a NUL \0 inside");
    Check(false, "a text with a NUL is refused");
}
catch (ArgumentException)
{
}

// Without an object, for ever (a function or a static method).
Label.KeepView("a text for ever " + new string('f', 30));
Churn();
Check(Label.KeptIntact(), "the text for ever");

// What is not annotated stays as it was: a std::string is copied for the call.
var other = new Label();
other.SetCopy("copied");
Check(other.Intact(), "a copy is intact");
other.Dispose();

Console.WriteLine("keeping: C# round trip OK");
