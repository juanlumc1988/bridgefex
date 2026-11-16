/* SPDX-License-Identifier: Apache-2.0 */
/*
 * Round trip through the generated C API of the "keeping" case, from plain C.
 * tests/test_roundtrip.py builds it against the generated library and runs it. In C the caller
 * keeps what the library points to: the comments of the C header say what that is.
 */
#include "keep_c.h"

#include <stdbool.h>
#include <stdio.h>
#include <string.h>

static int failures = 0;

static void check(int condition, const char* what)
{
    if (!condition) {
        fprintf(stderr, "FAILED: %s\n", what);
        ++failures;
    }
}

int main(void)
{
    keep_Gate* gate = NULL;
    keep_Latch* latch = NULL;
    keep_Label* label = NULL;
    int32_t number = 0;
    int32_t live = 0;
    bool flag = false;
    static const char title[] = "a title that the library keeps";
    static const char raw[] = "a raw text that the library keeps";
    static const char view[] = "a view that the library keeps";

    /* `this`: the Latch reads the Gate that made it, so the Gate is destroyed after it. */
    check(keep_Gate_create(&gate) == KEEP_OK, "Gate");
    check(keep_Gate_latch(gate, &latch) == KEEP_OK && latch != NULL, "latch");
    check(keep_Latch_open(latch, &flag) == KEEP_OK && flag, "the Latch reads its Gate");
    check(keep_Latch_number(latch, &number) == KEEP_OK && number == 1, "number");
    check(keep_Gate_live(&live) == KEEP_OK && live == 1, "one Gate");
    keep_Latch_destroy(latch);
    keep_Gate_destroy(gate);
    check(keep_Gate_live(&live) == KEEP_OK && live == 0, "the Gate is gone");

    /* A constructor keeps the Gate that it is given. */
    check(keep_Gate_create(&gate) == KEEP_OK, "Gate");
    check(keep_Latch_create(&latch, gate, 5) == KEEP_OK, "Latch");
    check(keep_Latch_open(latch, &flag) == KEEP_OK && flag, "open");
    keep_Latch_destroy(latch);
    keep_Gate_destroy(gate);

    /* Texts: the library keeps pointers to them, and the caller keeps them valid. */
    check(keep_Label_create_cstr(&label, title) == KEEP_OK, "Label");
    check(keep_Label_setRaw(label, raw) == KEEP_OK, "setRaw");
    check(keep_Label_setView(label, view, strlen(view)) == KEEP_OK, "setView");
    check(keep_Label_setMaybe(label, NULL) == KEEP_OK, "setMaybe NULL");
    check(keep_Label_setCopy(label, "copy", 4) == KEEP_OK, "setCopy");
    check(keep_Label_intact(label, &flag) == KEEP_OK && flag, "intact");
    check(keep_Label_kept(label, &number) == KEEP_OK
              && number == (int32_t)(strlen(title) + strlen(raw) + strlen(view)),
          "kept");
    keep_Label_destroy(label);

    if (failures != 0) {
        fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    printf("round trip OK\n");
    return 0;
}
