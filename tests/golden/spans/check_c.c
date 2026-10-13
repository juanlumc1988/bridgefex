/* SPDX-License-Identifier: Apache-2.0 */
/*
 * Round trip through the generated C API of the "spans" case, from plain C.
 * tests/test_roundtrip.py builds it against the generated library and runs it.
 */
#include "views_c.h"

#include <stdio.h>

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
    const int16_t values[] = {1, 2, 3};
    const views_Color colors[] = {views_Color_Red, views_Color_Green, views_Color_Red};
    const views_Point points[] = {{1.5, 0.0}, {2.5, 0.0}};
    const spn_string words[] = {{"ab", 2}, {"c", 1}};
    int64_t sum = 0;
    int32_t* numbers = NULL;
    size_t n = 0;
    int red = 0;
    double x = 0.0;
    size_t total = 0;

    check(views_sum(values, 3, &sum) == SPN_OK && sum == 6, "sum");
    check(views_sum(NULL, 0, &sum) == SPN_OK && sum == 0, "empty");
    check(views_sum(NULL, 1, &sum) == SPN_ERROR_NULL_ARGUMENT, "NULL with a size");
    check(views_numbers(&numbers, &n) == SPN_OK && n == 3 && numbers[2] == -3, "span result");
    spn_free(numbers);
    check(views_count_red(colors, 3, &red) == SPN_OK && red == 2, "enums");
    check(views_sum_x(points, 2, &x) == SPN_OK && x == 4.0, "structs");
    check(views_total(words, 2, &total) == SPN_OK && total == 3, "strings");

    if (failures == 0) {
        printf("spans: C round trip OK\n");
    }
    return failures == 0 ? 0 : 1;
}
