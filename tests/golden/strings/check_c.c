/* SPDX-License-Identifier: Apache-2.0 */
/*
 * Round trip through the generated C API of the "strings" case, from plain C.
 * tests/test_roundtrip.py builds it against the generated library and runs it.
 */
#include "text_c.h"
#include "views_c.h"

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

/* True if the result is exactly the size bytes at expected, NUL-terminated. */
static int is(const char* result, size_t result_size, const char* expected, size_t size)
{
    return result != NULL && result_size == size && memcmp(result, expected, size) == 0 &&
           result[size] == '\0';
}

int main(void)
{
    static char garbage[] = "garbage";
    text_Person* person = NULL;
    char* s = NULL;
    size_t n = 0;
    size_t count = 0;
    int age = 0;

    /* std::string parameters and results, with embedded NULs. */
    check(text_Person_create_cstr_int(&person, "Ada", 36) == TXT_OK, "create");
    check(text_Person_name(person, &s, &n) == TXT_OK && is(s, n, "Ada", 3), "name");
    txt_free(s);
    check(text_Person_age(person, &age) == TXT_OK && age == 36, "age");
    check(text_Person_set_name(person, "a\0b", 3) == TXT_OK, "set_name with a NUL");
    check(text_Person_name_ref(person, &s, &n) == TXT_OK && is(s, n, "a\0b", 3), "name_ref");
    txt_free(s);
    /* A const char* result stops at the first NUL. */
    check(text_Person_c_name(person, &s, &n) == TXT_OK && is(s, n, "a", 1), "c_name");
    txt_free(s);
    check(text_echo("x\0y\0", 4, &s, &n) == TXT_OK && is(s, n, "x\0y\0", 4), "echo NULs");
    txt_free(s);

    /* NULL with size 0 is the empty string; with a size, an error. */
    check(text_Person_set_name(person, NULL, 0) == TXT_OK, "set_name(NULL, 0)");
    check(text_Person_name(person, &s, &n) == TXT_OK && is(s, n, "", 0), "empty name");
    txt_free(s);
    check(text_Person_set_name(person, NULL, 1) == TXT_ERROR_NULL_ARGUMENT, "set_name(NULL, 1)");
    check(contains(txt_last_error(), "'name' is NULL and 'name_size' is not 0"), "message");
    check(text_count_cstr(NULL, &count) == TXT_ERROR_NULL_ARGUMENT, "NULL const char*");

    /* The outputs are NULL and 0 after every failure. */
    s = garbage;
    n = 7;
    check(text_concat(NULL, 2, "x", 1, &s, &n) == TXT_ERROR_NULL_ARGUMENT, "NULL input");
    check(s == NULL && n == 0, "outputs reset on a NULL input");
    s = garbage;
    n = 7;
    check(text_Person_greet(NULL, "x", 1, "hi", &s, &n) == TXT_ERROR_NULL_ARGUMENT, "NULL self");
    check(s == NULL && n == 0, "outputs reset on a NULL self");
    s = garbage;
    n = 7;
    check(text_fail("boom", 4, &s, &n) == TXT_ERROR_EXCEPTION, "exception");
    check(contains(txt_last_error(), "boom"), "message of the exception");
    check(s == NULL && n == 0, "outputs reset on an exception");
    n = 7;
    check(text_echo("x", 1, NULL, &n) == TXT_ERROR_NULL_ARGUMENT, "NULL out_result");
    s = garbage;
    check(text_echo("x", 1, &s, NULL) == TXT_ERROR_NULL_ARGUMENT, "NULL out_result_size");
    check(s == NULL, "out_result reset when out_result_size is NULL");

    /* const char* results: NULL stays NULL. */
    check(text_maybe_null(true, &s, &n) == TXT_OK && s == NULL && n == 0, "NULL result");
    check(text_maybe_null(false, &s, &n) == TXT_OK && is(s, n, "not null", 8), "result");
    txt_free(s);
    /* A result that points into a temporary of the wrapper is copied in time. */
    check(text_first_char("temporary", 9, &s, &n) == TXT_OK && is(s, n, "temporary", 9),
          "first_char");
    txt_free(s);

    check(text_Person_greet(person, "Bob", 3, "Hi", &s, &n) == TXT_OK &&
              is(s, n, "Hi Bob, I am ", 13),
          "greet");
    txt_free(s);
    check(text_Person_species(&s, &n) == TXT_OK && is(s, n, "human", 5), "static method");
    txt_free(s);
    check(text_count_string("abc", 3, &count) == TXT_OK && count == 3, "count_string");
    check(text_count_cstr("abc", &count) == TXT_OK && count == 1003, "count_cstr");
    check(text_length("abc", 3, 7, &count) == TXT_OK && count == 3007, "renamed size");
    check(text_total("a", 1, "bc", 2, "def", 3, &count) == TXT_OK && count == 123, "total");
    check(text_binary(&s, &n) == TXT_OK && is(s, n, "\0\xffx", 3), "binary");
    txt_free(s);

    /* std::string_view. */
    check(views_first_word("hello world", 11, &s, &n) == TXT_OK && is(s, n, "hello", 5),
          "first_word");
    txt_free(s);
    check(views_size_of(NULL, 0, &count) == TXT_OK && count == 0, "NULL view");
    check(views_empty(&s, &n) == TXT_OK && is(s, n, "", 0), "default view");
    txt_free(s);
    check(views_literal(&s, &n) == TXT_OK && is(s, n, "a literal", 9), "literal");
    txt_free(s);

    txt_free(NULL);
    text_Person_destroy(person);

    if (failures == 0) {
        printf("strings: C round trip OK\n");
    }
    return failures == 0 ? 0 : 1;
}
