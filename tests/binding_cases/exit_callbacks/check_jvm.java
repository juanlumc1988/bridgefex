// SPDX-License-Identifier: Apache-2.0
// The callback that a C++ global holds is called while the process runs, and released when it is
// replaced. (What happens at exit is tests/test_jvm_callbacks.py's.)

import static checks.Checks.check;

import exitcb.BridgefexCallbacks;
import exitcb.exitcb.Functions;
import java.util.ArrayList;
import java.util.List;

class Check {
    public static void main(String[] args) {
        final List<Integer> seen = new ArrayList<Integer>();
        Functions.keep(seen::add, 0);
        check(BridgefexCallbacks.registered() == 1, "C++ holds the handler");
        Functions.call(7);
        check(seen.size() == 1 && seen.get(0) == 7, "called: " + seen);
        Functions.keep(null, 0);
        check(BridgefexCallbacks.registered() == 0, "released");
        System.out.println("exit callbacks: round trip OK");
    }
}
