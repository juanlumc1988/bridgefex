/* SPDX-License-Identifier: Apache-2.0 */
/*
 * Round trip through the generated C API of the "callbacks" case, from plain C.
 * tests/test_roundtrip.py builds it against the generated library and runs it.
 */
#include "events_c.h"

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

/* The user_data of every callback: counts its calls and its releases. */
typedef struct {
    int calls;
    int releases;
    int32_t last;
} counter;

static void release(void* user_data)
{
    ((counter*)user_data)->releases++;
}

static void twice(void* user_data, int32_t value, int32_t* out_result)
{
    ((counter*)user_data)->calls++;
    *out_result = 2 * value;
}

static void silent(void* user_data, int32_t value, int32_t* out_result)
{
    /* Does not write: C++ gets 0. */
    (void)value;
    (void)out_result;
    ((counter*)user_data)->calls++;
}

static void even(void* user_data, int64_t value, bool* out_result)
{
    ((counter*)user_data)->calls++;
    *out_result = value % 2 == 0;
}

static void nothing(void* user_data)
{
    ((counter*)user_data)->calls++;
}

static void error_level(void* user_data, int32_t value, ev_Level* out_result)
{
    (void)value;
    ((counter*)user_data)->calls++;
    *out_result = ev_Level_Error;
}

static void bad_mode(void* user_data, ev_Mode* out_result)
{
    ((counter*)user_data)->calls++;
    *out_result = 42; /* not a value of ev::Mode: C++ gets Off */
}

static void identity(void* user_data, double x, double* out_result)
{
    ((counter*)user_data)->calls++;
    *out_result = x;
}

static void visit(void* user_data, const ev_Point* point, const char* name, double* out_result)
{
    counter* seen = (counter*)user_data;
    seen->calls++;
    check(seen->calls == 1 ? strcmp(name, "first") == 0 : name == NULL, "walk name");
    *out_result = point->x;
}

static void sink(void* user_data, const ev_Point* point, ev_Mode mode)
{
    ((counter*)user_data)->calls++;
    check(point->x == -1 && point->y == 1 && mode == ev_Mode_Auto, "shift");
}

static void texts(void* user_data, const char* a, size_t a_size, const char* b, size_t b_size,
                  const char* c, size_t c_size)
{
    ((counter*)user_data)->calls++;
    check(a_size == 3 && memcmp(a, "a\0b", 3) == 0, "string argument");
    check(b_size == 3 && memcmp(b, "a\0b", 3) == 0, "string argument by value");
    check(c_size == 3 && memcmp(c, "a\0b", 3) == 0, "string_view argument");
}

static void length(void* user_data, const char* text, size_t size, int32_t* out_result)
{
    ((counter*)user_data)->calls++;
    *out_result = memcmp(text, "ab!", 3) == 0 ? (int32_t)size : -1;
}

static void notified(void* user_data, const char* name, size_t size, const ev_Point* at)
{
    ((counter*)user_data)->last = size == 1 && name[0] == 'w' && at->x == 1 ? 1 : -1;
}

static void tick(void* user_data, int32_t value)
{
    counter* seen = (counter*)user_data;
    seen->calls++;
    seen->last = value;
}

static void ignore(void* user_data, int32_t value)
{
    /* Called from several threads at once: writes nothing. */
    (void)user_data;
    (void)value;
}

static void handler(void* user_data, ev_Level level, const char* message, size_t size)
{
    (void)level;
    (void)message;
    (void)size;
    ((counter*)user_data)->calls++;
}

int main(void)
{
    counter a = {0, 0, 0};
    counter b = {0, 0, 0};
    counter w = {0, 0, 0};
    int64_t total = 0;
    int32_t count = 0;
    bool empty = false;
    ev_Level level = ev_Level_Debug;
    ev_Mode mode = ev_Mode_On;
    double x = 0;
    ev_Timer* timer = NULL;
    bool logged = true;

    /* Synchronous calls: released once, before the function returns. */
    check(ev_sum(4, twice, &a, release, &total) == CBX_OK && total == 12, "sum");
    check(a.calls == 4 && a.releases == 1, "sum: calls and release");
    check(ev_sum(3, silent, &b, release, &total) == CBX_OK && total == 0, "result not written");
    check(b.calls == 3 && b.releases == 1, "silent: release");
    check(ev_count_if(5, even, &a, release, &count) == CBX_OK && count == 3, "count_if");
    check(a.releases == 2, "count_if: release");
    check(ev_is_empty(NULL, NULL, NULL, &empty) == CBX_OK && empty, "NULL is empty");
    check(ev_is_empty(nothing, &a, release, &empty) == CBX_OK && !empty, "not empty");
    check(a.releases == 3, "is_empty: release");
    check(ev_classify(1, error_level, &a, release, &level) == CBX_OK && level == ev_Level_Error,
          "enum result");
    check(ev_pick(bad_mode, &a, release, &mode) == CBX_OK && mode == ev_Mode_Off,
          "invalid enum result");
    check(ev_integrate(identity, &a, release, 0, 2, 4, &x) == CBX_OK && x == 2, "integrate");
    check(ev_walk(visit, &w, release, &x) == CBX_OK && x == 4.5, "struct argument");
    check(ev_shift(sink, &a, release) == CBX_OK, "struct and enum arguments");
    check(ev_strings(texts, &a, release) == CBX_OK, "string arguments");
    b.releases = 0;
    check(ev_parallel(4, 10, ignore, &b, release) == CBX_OK && b.releases == 1, "parallel");

    /* Beside by-reference parameters. */
    a.releases = 0;
    check(ev_prefixed("ab", 2, length, &a, release, &count) == CBX_OK && count == 3, "prefixed");
    const int32_t values[] = {4, 5, 6};
    check(ev_Timer_create(&timer, NULL, NULL, NULL) == CBX_OK, "Timer without callback");
    check(ev_Timer_each(timer, values, 3, tick, &a, release, &count) == CBX_OK && count == 3
              && a.last == 6,
          "each");
    ev_Timer_destroy(timer);
    ev_Point at = {1, 2};
    ev_Watcher* watcher = NULL;
    check(ev_Watcher_create(&watcher, "w", 1, &at, notified, &a, release) == CBX_OK, "Watcher");
    check(ev_Watcher_notify(watcher) == CBX_OK && a.last == 1, "notify");
    check(a.releases == 2, "kept by the watcher");
    ev_Watcher_destroy(watcher);
    check(a.releases == 3, "released with the watcher");

    /* Every failure releases too. */
    a.releases = 0;
    check(ev_sum(4, twice, &a, release, NULL) == CBX_ERROR_NULL_ARGUMENT, "NULL out_result");
    check(ev_sum(1, NULL, &a, release, &total) == CBX_ERROR_EXCEPTION, "NULL callback called");
    check(strstr(cbx_last_error(), "function") != NULL, "bad_function_call");
    check(ev_integrate(identity, &a, release, 0, 1, 0, &x) == CBX_ERROR_EXCEPTION, "throws");
    check(ev_Timer_create(NULL, tick, &a, release) == CBX_ERROR_NULL_ARGUMENT, "NULL out_self");
    check(a.releases == 4, "released on every failure");

    /* Stored callbacks: released on replacement and destruction. */
    a.releases = 0;
    b.releases = 0;
    check(ev_Timer_create(&timer, tick, &a, release) == CBX_OK, "Timer");
    check(ev_Timer_tick(timer, 3) == CBX_OK && a.last == 2, "tick");
    check(a.releases == 0, "kept by the timer");
    check(ev_Timer_set_on_tick(timer, tick, &b, release) == CBX_OK && a.releases == 1, "replaced");
    check(ev_Timer_set_on_tick(timer, tick, NULL, NULL) == CBX_OK && b.releases == 1, "no release");
    ev_Timer_destroy(timer);
    check(ev_Timer_apply(21, twice, &a, release, &count) == CBX_OK && count == 42, "static");

    /* After disabling: neither called (C++ gets the default) nor released. */
    a.calls = 0;
    a.releases = 0;
    check(ev_set_log_handler(handler, &a, release) == CBX_OK && a.calls == 1, "set handler");
    cbx_disable_callbacks(release);
    check(ev_log(ev_Level_Info, "x", 1, &logged) == CBX_OK && logged, "log");
    check(a.calls == 1, "disabled: not called");
    check(ev_sum(2, twice, &a, release, &total) == CBX_OK && total == 0, "disabled: default");
    check(ev_set_log_handler(NULL, NULL, NULL) == CBX_OK && a.releases == 0, "not released");
    cbx_disable_callbacks(NULL);

    if (failures != 0) {
        return 1;
    }
    puts("callbacks: C round trip OK");
    return 0;
}
