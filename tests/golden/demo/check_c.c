/* SPDX-License-Identifier: Apache-2.0 */
/*
 * Round trip through the generated C API of the "demo" case, from plain C.
 * tests/test_roundtrip.py builds it against the generated library and runs it.
 */
#include "counter_c.h"
#include "geometry_c.h"

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

static int contains(const char* text, const char* part)
{
    return strstr(text, part) != NULL;
}

int main(void)
{
    demo_Counter* counter = NULL;
    demo_geometry_Circle* circle = NULL;
    int32_t value = 0;
    int64_t product = 0;
    size_t live = 99;
    bool zero = false;
    double area = 0.0;

    check(demo_Counter_create_int32(&counter, 41) == DEMO_OK, "create");
    check(counter != NULL, "handle");
    check(demo_Counter_increment(counter) == DEMO_OK, "increment");
    check(demo_Counter_value(counter, &value) == DEMO_OK && value == 42, "value");
    check(demo_Counter_isZero(counter, &zero) == DEMO_OK && !zero, "isZero");
    check(demo_Counter_liveInstances(&live) == DEMO_OK && live == 1, "liveInstances");

    check(demo_Counter_setLimit(counter, -1) == DEMO_ERROR_EXCEPTION, "std::exception");
    check(contains(demo_last_error(), "negative"), "message of std::exception");
    check(demo_Counter_fail(counter) == DEMO_ERROR_UNKNOWN_EXCEPTION, "unknown exception");
    check(contains(demo_last_error(), "unknown"), "message of unknown exception");
    check(demo_Counter_setLimit(counter, 10) == DEMO_OK, "setLimit");
    check(demo_Counter_reserve(counter, 11) == DEMO_ERROR_OUT_OF_MEMORY, "bad_alloc");

    /* NULL arguments are reported, never dereferenced. */
    check(demo_Counter_value(NULL, &value) == DEMO_ERROR_NULL_ARGUMENT, "NULL self");
    check(contains(demo_last_error(), "'self' is NULL"), "message of NULL self");
    check(demo_Counter_value(counter, NULL) == DEMO_ERROR_NULL_ARGUMENT, "NULL out");
    check(demo_Counter_create_void(NULL) == DEMO_ERROR_NULL_ARGUMENT, "NULL out_self");

    check(demo_multiply_int32_int32(-3, 7, &product) == DEMO_OK && product == -21, "multiply");

    demo_Counter_destroy(counter);
    demo_Counter_destroy(NULL);
    check(demo_Counter_liveInstances(&live) == DEMO_OK && live == 0, "destroy");

    /* A throwing constructor leaves the handle NULL. */
    circle = (demo_geometry_Circle*)&live; /* any non-NULL value */
    check(demo_geometry_Circle_create(&circle, -1.0) == DEMO_ERROR_EXCEPTION, "throwing create");
    check(circle == NULL, "handle reset on failure");
    check(demo_geometry_Circle_create(&circle, 1.0) == DEMO_OK, "create circle");
    check(demo_geometry_Circle_area(circle, &area) == DEMO_OK && area > 3.14 && area < 3.15,
          "area");
    demo_geometry_Circle_destroy(circle);

    check(strlen(demo_api_fingerprint()) == 32, "fingerprint");

    if (failures == 0) {
        printf("demo: C round trip OK\n");
    }
    return failures == 0 ? 0 : 1;
}
