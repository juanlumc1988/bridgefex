// SPDX-License-Identifier: Apache-2.0
// Round trip through the generated C# bindings of the "edge" case: names that are keywords or
// builtins somewhere, and a destructor that throws.
//
// tests/test_csharp_roundtrip.py builds this file together with the generated project and runs
// it with EDGE_LIBRARY pointing at the built library.

using Edge;
using F = Edge.Edge.Functions;
using Registry = Edge.Edge.Registry;

static void Check(bool condition, string what)
{
    if (!condition)
    {
        throw new Exception("check failed: " + what);
    }
}

Check(F.Add(2, 3) == 5, "add");
Check(F.Combine(1, 2, 3) == 123, "combine");
Check(F.Combine(int32_t_: 4, size_t_: 5, EDGE_OK_: 6) == 456, "named arguments");
Check(F.Getattr(41) == 42, "getattr");
Check(F.Threading() == 4, "threading");

Check(Registry.LiveCount() == 0, "no registry yet");
var registry = new Registry();
Check(registry.Classmethod() == 1, "classmethod");
Check(registry.Staticmethod() == 2, "staticmethod");
registry.Close();
Check(Registry.LiveCount() == 0, "closed");

// A method that throws: the exception has the message of C++.
using (var failing = new Registry())
{
    try
    {
        failing.Fail();
        throw new Exception("no exception");
    }
    catch (BridgefexException error)
    {
        Check(error.Message == "failed on purpose", "message: " + error.Message);
        Check(error.Status == BridgefexException.Exception, "status");
    }
    // A destructor that throws: Close still releases the object.
    failing.ThrowOnDestroy(true);
}
Check(Registry.LiveCount() == 0, "a destructor that throws still destroys the object");

Console.WriteLine("edge: C# round trip OK");
