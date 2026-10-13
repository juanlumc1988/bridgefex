/* SPDX-License-Identifier: Apache-2.0 */
/*
 * Round trip through the generated C API of the "enums" case, from plain C.
 * tests/test_roundtrip.py builds it against the generated library and runs it.
 */
#include "colors_c.h"
#include "shapes_c.h"

#include <inttypes.h>
#include <stdio.h>
#include <string.h>

/* The constants work in #if, with their C type. */
#if colors_Color_Max != 255 || colors_Signed_First != -3 || colors_Wide_Min >= 0
#error "wrong enumerator constants"
#endif
#if colors_Flags_All != UINT64_C(18446744073709551615) || colors_Plain_Low >= 0
#error "wrong 64-bit enumerator constants"
#endif

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

static const char* name_of(colors_Mode mode)
{
    switch (mode) {
    case colors_Mode_Off:
        return "off";
    case colors_Mode_On:
        return "on";
    default:
        return "other";
    }
}

int main(void)
{
    colors_Color color = 0;
    colors_Mode mode = colors_Mode_Auto;
    colors_Signed sign = 0;
    colors_Flags flags = 0;
    colors_Plain plain = 0;
    colors_Wide wide = 0;
    colors_Shape shape = 0;
    colors_Id id = 0;
    colors_Old old = 0;
    colors_Lamp* lamp = NULL;
    colors_Lamp_State state = 0;
    colors_Lamp_Level level = 0;
    char text[32];
    int number = 0;

    check(colors_mix(colors_Color_Red, colors_Color_Green, &color) == ENM_OK && color == 100,
          "mix");
    /* Every value of an enum with a fixed underlying type is valid. */
    check(colors_mix(17, 33, &color) == ENM_OK && color == 25, "values that are not enumerators");

    check(colors_toggle(colors_Mode_Off, &mode) == ENM_OK && mode == colors_Mode_On, "toggle");
    check(strcmp(name_of(mode), "on") == 0, "switch");
    /* Without a fixed underlying type, only the values of [dcl.enum] are valid. */
    check(colors_toggle(7, &mode) == ENM_OK, "highest valid value");
    check(colors_toggle(8, &mode) == ENM_ERROR_INVALID_ARGUMENT, "too large");
    check(contains(enm_last_error(), "'m' is not a value of colors::Mode (0..7)"), "message");
    check(colors_toggle(-1, &mode) == ENM_ERROR_INVALID_ARGUMENT, "negative");
    check(colors_negate(colors_Signed_First, &sign) == ENM_OK && sign == 3, "negate");
    check(colors_negate(-5, &sign) == ENM_ERROR_INVALID_ARGUMENT, "below the range");

    /* ~ keeps the upper bits of a 64-bit flag word. */
    check(colors_flip(colors_Flags_Bit31, &flags) == ENM_OK && flags == ~colors_Flags_Bit31,
          "flip");
    snprintf(text, sizeof text, "%" PRIu64, (uint64_t)colors_Flags_All);
    check(strcmp(text, "18446744073709551615") == 0, "PRIu64");
    check(colors_plain(colors_Plain_Low, &plain) == ENM_OK && plain == colors_Plain_Low, "plain");
    check(colors_wide(colors_Wide_Min, &wide) == ENM_OK && wide == colors_Wide_Min, "wide");
    check(colors_next(41, &id) == ENM_OK && id == 42, "strong type");
    check(colors_current(&old) == ENM_OK && old == colors_Old_Current, "current");
    check(colors_default_shape(&shape) == ENM_OK && shape == colors_Shape_Square, "opaque");
    check(colors_color_of(colors_Shape_Circle, &color) == ENM_OK && color == colors_Color_Red,
          "color_of");
    check(colors_shape_of(colors_Color_Green, &shape) == ENM_OK && shape == colors_Shape_Square,
          "shape_of");
    check(colors_f_colors_Color(colors_Color_Max, &number) == ENM_OK && number == 1255,
          "overload on the enum");
    check(colors_f_int(7, &number) == ENM_OK && number == 7, "overload on int");

    check(colors_Lamp_create(&lamp, colors_Color_Green) == ENM_OK, "lamp");
    check(colors_Lamp_set(lamp, colors_Lamp_State_Lit, colors_Lamp_Level_Bright) == ENM_OK, "set");
    check(colors_Lamp_state(lamp, &state) == ENM_OK && state == colors_Lamp_State_Lit, "state");
    check(colors_Lamp_level(lamp, &level) == ENM_OK && level == colors_Lamp_Level_Bright, "level");
    check(colors_Lamp_set(lamp, colors_Lamp_State_Dark, 4) == ENM_ERROR_INVALID_ARGUMENT,
          "nested enum out of range");
    check(colors_Lamp_set(NULL, colors_Lamp_State_Dark, 4) == ENM_ERROR_NULL_ARGUMENT,
          "NULL checks come first");
    check(colors_Lamp_color(lamp, &color) == ENM_OK && color == colors_Color_Green, "color");
    colors_Lamp_destroy(lamp);

    if (failures == 0) {
        printf("enums: C round trip OK\n");
    }
    return failures == 0 ? 0 : 1;
}
