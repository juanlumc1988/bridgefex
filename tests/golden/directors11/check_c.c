/* SPDX-License-Identifier: Apache-2.0 */
/*
 * Round trip through the directors of the C++11 case, from plain C: values that
 * a function of a vtable gives back and that are not valid, results it does not
 * write, a vtable that does not outlive the call that gave it, and the checks of
 * the constructors.
 */
#include "base_c.h"

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

typedef struct {
    int released;
} state;

static void release(void* user_data)
{
    ++((state*)user_data)->released;
}

static void text(void* user_data, const char* prefix, size_t prefix_size, dr1_string_sink* out)
{
    (void)user_data;
    (void)prefix;
    (void)prefix_size;
    dr1_string_sink_set(out, NULL, 5);            /* NULL with a size: ignored */
    dr1_string_sink_set(out, "ab", (size_t)-1);   /* too long: ignored */
    dr1_string_sink_set(out, "ok", 2);
}

static void invalid_kind(void* user_data, dr1_Kind requested, dr1_Kind* out)
{
    (void)user_data;
    (void)requested;
    *out = 99; /* not a Kind: Small (0) and Large (10) are, the values 0..15 are valid */
}

static void invalid_tagged(void* user_data, dr1_Tagged* out)
{
    (void)user_data;
    out->kind = 99;
    out->number = 5;
}

static void forgets_the_pair(void* user_data, dr1_Pair* out)
{
    (void)user_data;
    (void)out;
}

static int32_t sink_calls = 0;
static void counts(void* user_data, dr1_string_sink* out)
{
    (void)user_data;
    (void)out;
    ++sink_calls;
}

static void check_values_that_are_not_valid(void)
{
    state user = {0};
    dr1_Base_vtable vtable;
    memset(&vtable, 0, sizeof vtable);
    vtable.user_data = &user;
    vtable.release = release;
    vtable.text = text;
    vtable.kind = invalid_kind;
    vtable.tagged = invalid_tagged;
    vtable.pair = forgets_the_pair;
    dr1_Base_director* director = NULL;
    dr1_Base* base = NULL;
    check(dr1_Base_director_create_int32(&base, &director, &vtable, 3) == DR1_OK, "create");
    /* The vtable was copied: this one is gone. */
    memset(&vtable, 0xFF, sizeof vtable);
    check(base == dr1_Base_director_object(director), "the object of the director");

    char* result = NULL;
    size_t size = 0;
    check(dr1_Base_show(base, &result, &size) == DR1_OK, "show");
    check(size == 2 && memcmp(result, "ok", 2) == 0, "the string sink ignores what is not valid");
    dr1_free(result);

    dr1_Kind kind = 7;
    check(dr1_Base_show_kind(base, dr1_Kind_Large, &kind) == DR1_OK && kind == dr1_Kind_Small,
          "an enum result out of range gives the zero value");

    dr1_Tagged tagged = {7, 7};
    check(dr1_Base_show_tagged(base, &tagged) == DR1_OK, "a noexcept method with a bad struct");
    check(tagged.kind == dr1_Kind_Small && tagged.number == 0,
          "a struct result with an enum out of range gives the zero value");

    dr1_Pair pair = {7, 7};
    check(dr1_Base_pair(base, &pair) == DR1_OK && pair.first == 0 && pair.second == 0,
          "a result that the function does not write is value-initialized");
    dr1_Base_destroy(base);
    check(user.released == 1, "released once");
}

static void check_constructors(void)
{
    state user = {0};
    dr1_Base_vtable vtable;
    memset(&vtable, 0, sizeof vtable);
    vtable.user_data = &user;
    vtable.release = release;
    vtable.text = text;
    dr1_Base_director* director = NULL;
    dr1_Base* base = NULL;
    /* A value that the enum of a constructor cannot have is refused, and released. */
    check(dr1_Base_director_create_dr1_Kind_int32(&base, &director, &vtable, 99, 1) ==
              DR1_ERROR_INVALID_ARGUMENT,
          "an enum argument out of range");
    check(base == NULL && director == NULL && user.released == 1, "no object, released");
    check(dr1_Base_director_create_dr1_Kind_int32(&base, &director, &vtable, dr1_Kind_Large, 1) == DR1_OK,
          "a valid one");
    int32_t seed = 0;
    check(dr1_Base_seed(base, &seed) == DR1_OK && seed == 11,
          "the constructor ran with its arguments");
    dr1_Base_destroy(base);
    check(user.released == 2, "released");

    /* A protected default constructor. */
    check(dr1_Base_director_create_void(&base, &director, &vtable) == DR1_OK, "the default constructor");
    check(dr1_Base_seed(base, &seed) == DR1_OK && seed == 1, "the protected constructor ran");
    dr1_Base_destroy(base);
}

static void check_a_function_that_is_only_counted(void)
{
    state user = {0};
    dr1_Derived_vtable vtable;
    memset(&vtable, 0, sizeof vtable);
    vtable.user_data = &user;
    vtable.release = release;
    (void)counts;
    dr1_Derived_director* director = NULL;
    dr1_Derived* derived = NULL;
    check(dr1_Derived_director_create(&derived, &director, &vtable) == DR1_OK,
          "a derived class needs no function: text is implemented by Derived");
    check(derived == dr1_Derived_director_object(director), "the object of the director");
    char* result = NULL;
    size_t size = 0;
    check(dr1_Base_show(dr1_Derived_as_dr1_Base(derived), &result, &size) == DR1_OK &&
              size == 2 && memcmp(result, "p!", 2) == 0,
          "the class's own implementation");
    dr1_free(result);
    dr1_Derived_destroy(derived);
}

int main(void)
{
    check_values_that_are_not_valid();
    check_constructors();
    check_a_function_that_is_only_counted();
    if (failures != 0) {
        fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    printf("round trip OK\n");
    return 0;
}
