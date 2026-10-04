/* SPDX-License-Identifier: Apache-2.0 */
/* Round trip through the generated C API of the "scalars" case, from plain C. */
#include "numbers_c.h"

#include <limits.h>
#include <stdint.h>
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
    scalars_Accumulator* accumulator = NULL;
    double total = 0.0;
    size_t count = 1;
    int version = 0;
    int result = 0;
    bool flag = false;
    long long_value = 0;
    uint64_t u64 = 0;
    int8_t i8 = 0;
    size_t size = 0;
    double scaled = 0.0;

    check(scalars_Accumulator_create(&accumulator) == SCALARS_OK, "create");
    check(scalars_Accumulator_count(accumulator, &count) == SCALARS_OK && count == 0, "count");
    check(scalars_Accumulator_push(accumulator, 2.5) == SCALARS_OK, "push");
    check(scalars_Accumulator_total(accumulator, &total) == SCALARS_OK && total == 2.5, "total");
    check(scalars_Accumulator_version(&version) == SCALARS_OK && version == 3, "version");
    scalars_Accumulator_destroy(accumulator);

    check(scalars_echo_bool(true, &flag) == SCALARS_OK && flag, "bool");
    check(scalars_echo_long(LONG_MIN, &long_value) == SCALARS_OK && long_value == LONG_MIN, "long");
    check(scalars_echo_uint64(UINT64_MAX, &u64) == SCALARS_OK && u64 == UINT64_MAX, "uint64");
    check(scalars_echo_int8(INT8_MIN, &i8) == SCALARS_OK && i8 == INT8_MIN, "int8");
    check(scalars_echo_size(SIZE_MAX, &size) == SCALARS_OK && size == SIZE_MAX, "size_t");
    check(scalars_sum_renamed(1, 2, 3, 4, &result) == SCALARS_OK && result == 10, "renamed");
    check(scalars_twice(21, &result) == SCALARS_OK && result == 42, "unnamed parameter");
    check(scalars_scale_double_double(1.5, 2.0, &scaled) == SCALARS_OK && scaled == 3.0, "scale");
    check(scalars_divide(1, 0, &result) == SCALARS_ERROR_EXCEPTION, "exception");
    check(strcmp(scalars_last_error(), "division by zero") == 0, "message");
    check(scalars_do_nothing() == SCALARS_OK, "void function");

    if (failures == 0) {
        printf("scalars: C round trip OK\n");
    }
    return failures == 0 ? 0 : 1;
}
