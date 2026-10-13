/* SPDX-License-Identifier: Apache-2.0 */
/*
 * Round trip through the generated C API of the "structs" case, from plain C.
 * tests/test_roundtrip.py builds it against the generated library and runs it.
 */
#include "geo_c.h"
#include "route_c.h"

#include <stddef.h>
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
    geo_Point a = {1.0, 2.0};
    geo_Point b = {3.0, 6.0};
    geo_Point p = {0.0, 0.0};
    geo_Config config;
    geo_Msg msg;
    geo_Msg out;
    geo_Wide wide;
    geo_Names names = {1, 2, 3};
    geo_Tagged tagged = {geo_Tagged_Mode_Off, 5};
    route_Leg leg;
    geo_Path* path = NULL;
    double length = 0.0;
    int area = 0;

    /* The layouts, which tests/test_roundtrip.py compares with the ctypes mirrors. */
    printf("layout geo_Msg %u tag=%u where=%u samples=%u ok=%u path=%u quality=%u\n",
           (unsigned)sizeof(geo_Msg), (unsigned)offsetof(geo_Msg, tag),
           (unsigned)offsetof(geo_Msg, where), (unsigned)offsetof(geo_Msg, samples),
           (unsigned)offsetof(geo_Msg, ok), (unsigned)offsetof(geo_Msg, path),
           (unsigned)offsetof(geo_Msg, quality));
    printf("layout geo_Wide %u f=%u big=%u size=%u l=%u c=%u bytes=%u\n",
           (unsigned)sizeof(geo_Wide), (unsigned)offsetof(geo_Wide, f),
           (unsigned)offsetof(geo_Wide, big), (unsigned)offsetof(geo_Wide, size),
           (unsigned)offsetof(geo_Wide, l), (unsigned)offsetof(geo_Wide, c),
           (unsigned)offsetof(geo_Wide, bytes));
    printf("layout route_Leg %u from_=%u to=%u kind=%u\n", (unsigned)sizeof(route_Leg),
           (unsigned)offsetof(route_Leg, from_), (unsigned)offsetof(route_Leg, to),
           (unsigned)offsetof(route_Leg, kind));

    check(geo_mid(&a, &b, &p) == VALS_OK && p.x == 2.0 && p.y == 4.0, "mid");
    /* An input may be the output. */
    check(geo_mid(&a, &b, &a) == VALS_OK && a.x == 2.0 && a.y == 4.0, "aliased output");
    /* On failure, the output is left alone. */
    p.x = -1.0;
    check(geo_scale(&b, -1.0, &p) == VALS_ERROR_EXCEPTION && p.x == -1.0, "failure");
    check(contains(vals_last_error(), "negative scale"), "message");
    check(geo_mid(NULL, &b, &p) == VALS_ERROR_NULL_ARGUMENT, "NULL input");
    check(geo_mid(&a, &b, NULL) == VALS_ERROR_NULL_ARGUMENT, "NULL output");

    /* The C++ defaults. */
    check(geo_Config_init(&config) == VALS_OK && config.baud == 115200 && config.parity &&
              config.kind == geo_Kind_Marked,
          "Config defaults");
    check(geo_configure(&config, &config) == VALS_OK && config.baud == 230400, "configure");
    check(geo_Msg_init(&msg) == VALS_OK && msg.tag == 7, "Msg defaults");

    /* A packed struct with nested structs and arrays. */
    memset(&msg, 0, sizeof msg);
    msg.tag = 8;
    msg.where.x = 1.5;
    msg.samples[0] = 1;
    msg.samples[2] = 123;
    msg.ok = true;
    msg.path[1].y = 9.5;
    msg.quality = geo_Quality_Best;
    check(geo_echo(&msg, &out) == VALS_OK && out.tag == 8 && out.where.x == 1.5 &&
              out.samples[2] == 123 && out.ok && out.path[1].y == 9.5 &&
              out.quality == geo_Quality_Best,
          "echo");
    check(geo_shift(&msg, 10, &msg) == VALS_OK && msg.tag == 9 && msg.samples[0] == 11 &&
              msg.samples[2] == 133,
          "shift into the input");
    /* A member of an enum without a fixed type, out of its range. */
    msg.quality = 4;
    out.tag = 99;
    check(geo_echo(&msg, &out) == VALS_ERROR_INVALID_ARGUMENT && out.tag == 99, "invalid member");
    check(contains(vals_last_error(), "'geo::Msg::quality' is not a value of geo::Quality (0..3)"),
          "message of the invalid member");

    memset(&wide, 0, sizeof wide);
    wide.big = 21;
    wide.f = 3.0f;
    wide.bytes[3] = 255;
    check(geo_widen(&wide, &wide) == VALS_OK && wide.big == 42 && wide.bytes[3] == 255, "widen");
    check(geo_area_geo_Wide(&wide, &area) == VALS_OK && area == 3, "overload on a struct");
    check(geo_area_geo_Point(&b, &area) == VALS_OK && area == 18, "overload on another");
    check(geo_rename(&names, &names) == VALS_OK && names.from_ == 2 && names.restrict_ == 2,
          "renamed members");
    check(geo_toggle(&tagged, &tagged) == VALS_OK && tagged.mode == geo_Tagged_Mode_On &&
              tagged.value == 5,
          "nested enum");

    check(geo_Path_create(&path, &b) == VALS_OK, "class with a struct parameter");
    check(geo_Path_add(path, &a) == VALS_OK, "add");
    check(geo_Path_last(path, &p) == VALS_OK && p.x == a.x, "last");
    check(geo_Path_origin(&p) == VALS_OK && p.x == 0.0, "static method");
    geo_Path_destroy(path);

    /* A struct of another header. */
    leg.from_.x = 0.0;
    leg.from_.y = 0.0;
    leg.to.x = 3.0;
    leg.to.y = 4.0;
    leg.kind = geo_Kind_Plain;
    check(route_length(&leg, &length) == VALS_OK && length == 5.0, "length");
    check(route_reverse(&leg, &leg) == VALS_OK && leg.from_.x == 3.0, "reverse");
    check(route_start(&leg, &p) == VALS_OK && p.x == 3.0, "start");

    if (failures == 0) {
        printf("structs: C round trip OK\n");
    }
    return failures == 0 ? 0 : 1;
}
