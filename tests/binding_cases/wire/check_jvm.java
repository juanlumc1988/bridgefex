// SPDX-License-Identifier: Apache-2.0
// What an adversarial review of the Java bindings found about the numbers and the messages of the
// errors: a size_t that C++ gave (npos) is given back as it came, the unsigned values of 8 and 16
// bits arrive as such, and the text of an error is shown whatever its encoding.
//
// tests/test_jvm_roundtrip.py builds this file together with the generated project and
// tests/support/jvm/Checks.java, and runs it with WIRE_LIBRARY pointing at the built library.

import static checks.Checks.check;
import static checks.Checks.expect;

import wire.BridgefexException;
import wire.wire.Functions;
import wire.wire.Sizes;

class Check {
    public static void main(String[] args) {
        // A size_t is its bits: npos comes back as -1L (2^64 - 1) and goes back as it came.
        check(Functions.wireNpos() == -1L, "npos is the bits of a size_t");
        check(Functions.wireEchoSize(-1L) == -1L, "npos is given back");
        check(Functions.wireEchoSize(0L) == 0L, "zero");
        check(Functions.wireEchoSize(Long.MAX_VALUE) == Long.MAX_VALUE, "the biggest signed value");
        check(Functions.wireEchoSize(Long.MIN_VALUE) == Long.MIN_VALUE, "2^63 is a size too");
        check(Functions.wireEchoU64(-1L) == -1L, "the same for a uint64_t");

        // The unsigned values of 8 and 16 bits arrive as unsigned values, also the biggest.
        check(Functions.wireEchoU8((short) 200) == 200, "uint8_t 200");
        check(Functions.wireEchoU8((short) 255) == 255, "uint8_t 255");
        check(Functions.wireEchoU16(50000) == 50000, "uint16_t 50000");
        check(Functions.wireEchoU16(65535) == 65535, "uint16_t 65535");
        check(Functions.wireIs200((short) 200), "200 arrives as 200");
        check(Functions.wireIs50000(50000), "50000 arrives as 50000");
        check(Functions.wireIsMax32(4294967295L), "2^32 - 1 arrives as it is");
        expect(IllegalArgumentException.class, () -> Functions.wireEchoU8((short) 256), "does not fit");
        expect(IllegalArgumentException.class, () -> Functions.wireEchoU8((short) -1), "does not fit");
        expect(IllegalArgumentException.class, () -> Functions.wireEchoU16(65536), "does not fit");
        expect(IllegalArgumentException.class, () -> Functions.wireEchoU16(-1), "does not fit");

        // The same in a struct, which goes through memory.
        Sizes sizes = Functions.wireSizes();
        check(sizes.count == -1L && sizes.big == -1L, "the sizes of C++ are read as their bits");
        check(sizes.byte_ == 200 && sizes.word == 50000, "and the small ones as unsigned");
        Sizes again = Functions.wireEcho(sizes);
        check(again.equals(sizes), "a struct with the sizes that C++ gave goes back as it came");

        // The message of an error is shown whatever its encoding (it is not worth losing).
        BridgefexException latin = expect(BridgefexException.class, Functions::wireFailLatin1);
        check(latin.getMessage().startsWith("caf") && latin.getMessage().endsWith(" broke"),
            "a message that is not UTF-8 is shown: " + latin.getMessage());
        check(!latin.getMessage().contains("could not be read"), "and not as a failure to read it");
        BridgefexException utf8 = expect(BridgefexException.class, Functions::wireFailUtf8);
        check(utf8.getMessage().contains("café broke"), "a UTF-8 message is exact: " + utf8.getMessage());

        System.out.println("round trip OK");
    }
}
