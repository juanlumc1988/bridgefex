// SPDX-License-Identifier: Apache-2.0
// Round trip through the generated Java bindings of the "edge" case: names that are keywords or
// builtins somewhere, and a destructor that throws.
//
// tests/test_jvm_roundtrip.py builds this file together with the generated project and
// tests/support/jvm/Checks.java, and runs it with EDGE_LIBRARY pointing at the built library.

import static checks.Checks.check;

import edge.BridgefexException;
import edge.edge.Functions;
import edge.edge.Registry;

class Check {
    public static void main(String[] args) {
        check(Functions.add(2, 3) == 5, "add");
        check(Functions.combine(1, 2, 3) == 123, "combine");
        check(Functions.getattr(41) == 42, "getattr");
        check(Functions.threading() == 4, "threading");

        check(Registry.liveCount() == 0, "no registry yet");
        Registry registry = new Registry();
        check(registry.classmethod() == 1, "classmethod");
        check(registry.staticmethod() == 2, "staticmethod");
        registry.close();
        check(Registry.liveCount() == 0, "closed");

        // A method that throws: the exception has the message of C++.
        try (Registry failing = new Registry()) {
            try {
                failing.fail();
                throw new AssertionError("no exception");
            } catch (BridgefexException error) {
                check(error.getMessage().equals("failed on purpose"), "message: " + error.getMessage());
                check(error.status() == BridgefexException.EXCEPTION, "status");
            }
            // A destructor that throws: close still releases the object.
            failing.throwOnDestroy(true);
        }
        check(Registry.liveCount() == 0, "a destructor that throws still destroys the object");

        System.out.println("edge: Java round trip OK");
    }
}
