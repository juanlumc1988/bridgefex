/* SPDX-License-Identifier: Apache-2.0 */
/*
 * Round trip through the generated C API of the "containers" case, from plain C.
 * tests/test_roundtrip.py builds it against the generated library and runs it.
 */
#include "vectors_c.h"

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
    static int64_t garbage[1];
    const int64_t values[] = {1, -2, 3};
    const bool flags[] = {true, false, false, true};
    const cont_string words[] = {{"ab\0c", 4}, {NULL, 0}, {"xyz", 3}};
    const cont_string bad_words[] = {{"a", 1}, {NULL, 3}};
    const vec_Color colors[] = {vec_Color_Red, vec_Color_Blue};
    const vec_Level levels[] = {vec_Level_High, vec_Level_Low, vec_Level_High};
    const vec_Level bad_levels[] = {vec_Level_High, 2};
    const vec_Point points[] = {{1.0, 2.0}, {3.0, 4.0}};
    const int counts[] = {4, 5};
    int64_t* out = NULL;
    bool* out_flags = NULL;
    cont_string* out_words = NULL;
    vec_Color* out_colors = NULL;
    vec_Level* out_levels = NULL;
    vec_Point* out_points = NULL;
    int* out_ints = NULL;
    size_t n = 0;
    size_t total = 0;
    int high = 0;
    vec_Bag* bag = NULL;

    check(vec_scale(values, 3, 10, &out, &n) == CONT_OK && n == 3 && out[0] == 10 &&
              out[1] == -20 && out[2] == 30,
          "scale");
    cont_free(out);
    /* Empty: NULL is fine as an input; the result is never NULL. */
    check(vec_scale(NULL, 0, 10, &out, &n) == CONT_OK && out != NULL && n == 0, "empty");
    cont_free(out);
    /* NULL with a size, or an exception: the outputs are NULL and 0. */
    out = garbage;
    n = 7;
    check(vec_scale(NULL, 2, 10, &out, &n) == CONT_ERROR_NULL_ARGUMENT && out == NULL && n == 0,
          "NULL input");
    out = garbage;
    n = 7;
    check(vec_too_big((int**)&out, &n) == CONT_ERROR_EXCEPTION && out == NULL && n == 0,
          "exception");
    check(contains(cont_last_error(), "max_size"), "message");

    check(vec_invert(flags, 4, &out_flags, &n) == CONT_OK && n == 4 && !out_flags[0] &&
              out_flags[1] && out_flags[2] && !out_flags[3],
          "vector<bool>");
    cont_free(out_flags);

    check(vec_upper(words, 3, &out_words, &n) == CONT_OK && n == 3, "upper");
    check(out_words[0].size == 4 && memcmp(out_words[0].data, "AB\0C", 5) == 0, "embedded NUL");
    check(out_words[1].size == 0 && out_words[1].data != NULL && out_words[1].data[0] == '\0',
          "empty string");
    check(strcmp(out_words[2].data, "XYZ") == 0, "NUL-terminated");
    cont_free(out_words);
    check(vec_upper(bad_words, 2, &out_words, &n) == CONT_ERROR_NULL_ARGUMENT, "NULL element");
    check(contains(cont_last_error(), "an element of 'words' is NULL"), "message of NULL element");
    check(vec_total_size(words, 3, &total) == CONT_OK && total == 7, "rvalue reference");
    check(vec_empty_strings(&out_words, &n) == CONT_OK && n == 2 && out_words[1].size == 0,
          "empty strings");
    cont_free(out_words);

    check(vec_rotate(colors, 2, &out_colors, &n) == CONT_OK && n == 2 &&
              out_colors[0] == vec_Color_Green && out_colors[1] == vec_Color_Red,
          "enums");
    cont_free(out_colors);
    check(vec_count_high(levels, 3, &high) == CONT_OK && high == 2, "unfixed enums");
    check(vec_count_high(bad_levels, 2, &high) == CONT_ERROR_INVALID_ARGUMENT, "invalid element");
    check(contains(cont_last_error(), "an element of 'levels' is not a value of vec::Level"),
          "message of invalid element");
    check(vec_levels(3, &out_levels, &n) == CONT_OK && n == 3 && out_levels[1] == vec_Level_High,
          "enum results");
    cont_free(out_levels);

    check(vec_shift(points, 2, 0.5, &out_points, &n) == CONT_OK && n == 2 &&
              out_points[1].x == 3.5 && out_points[1].y == 4.0,
          "structs");
    cont_free(out_points);
    check(vec_primes(&out_ints, &n) == CONT_OK && n == 4 && out_ints[3] == 7, "const& result");
    cont_free(out_ints);
    check(vec_sum(counts, 2, 7, &total) == CONT_OK && total == 9007, "renamed size");

    check(vec_Bag_create(&bag, words, 3) == CONT_OK, "constructor");
    check(vec_Bag_add(bag, counts, 2) == CONT_OK, "add");
    check(vec_Bag_counts(bag, &out_ints, &n) == CONT_OK && n == 2 && out_ints[1] == 5, "counts");
    cont_free(out_ints);
    check(vec_Bag_items(bag, &out_words, &n) == CONT_OK && n == 3 && out_words[2].size == 3,
          "items");
    cont_free(out_words);
    vec_Bag_destroy(bag);

    if (failures == 0) {
        printf("containers: C round trip OK\n");
    }
    return failures == 0 ? 0 : 1;
}
