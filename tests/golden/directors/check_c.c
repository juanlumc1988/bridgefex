/* SPDX-License-Identifier: Apache-2.0 */
/*
 * Round trip through the directors of the "directors" case, from plain C: the
 * vtables are implemented here, and C++ calls them through the virtual methods.
 * tests/test_roundtrip.py builds it against the generated library and runs it.
 */
#include "shape_c.h"
#include "stage_c.h"

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

static int equals(char* text, size_t size, const char* expected)
{
    int same = text != NULL && size == strlen(expected) && memcmp(text, expected, size) == 0;
    drc_free(text);
    return same;
}

/* What the functions of the vtables see: the user_data of a director. */
typedef struct {
    int released;
    int calls;
    double factor;
    int32_t reason;
    drc_Mode mode;
    int32_t number;
    drc_Shape_director* director; /* to call the implementation of the class */
} state;

static void shape_release(void* user_data)
{
    ++((state*)user_data)->released;
}

static void shape_area(void* user_data, double* out_result)
{
    ((state*)user_data)->calls += 1;
    *out_result = 6.5;
}

static void shape_name(void* user_data, drc_string_sink* out_result)
{
    (void)user_data;
    drc_string_sink_set(out_result, "tri\0x", 5); /* a NUL is a character */
}

static void shape_sides(void* user_data, int32_t* out_result)
{
    (void)user_data;
    *out_result = 3;
}

static void shape_center(void* user_data, drc_Point* out_result)
{
    (void)user_data;
    out_result->x = 4.0;
    out_result->y = 6.0;
}

static void shape_mode(void* user_data, drc_Mode requested, drc_Mode* out_result)
{
    state* self = (state*)user_data;
    self->mode = requested;
    *out_result = requested == drc_Mode_Auto ? drc_Mode_Off : drc_Mode_On;
}

static void shape_scale(void* user_data, double factor)
{
    ((state*)user_data)->factor = factor;
}

static void shape_add(void* user_data, int32_t a, int32_t b, int32_t* out_result)
{
    (void)user_data;
    *out_result = a * 10 + b;
}

static void shape_greet(void* user_data, const char* who, size_t who_size, const char* tag,
                        size_t tag_size, const char* mark, drc_string_sink* out_result)
{
    char text[64];
    size_t size = 0;
    (void)user_data;
    memcpy(text + size, mark, strlen(mark));
    size += strlen(mark);
    text[size++] = '|';
    memcpy(text + size, tag, tag_size);
    size += tag_size;
    text[size++] = '|';
    memcpy(text + size, who, who_size);
    size += who_size;
    drc_string_sink_set(out_result, text, size);
}

/* Overrides a protected method and calls the implementation of the class. */
static void shape_changed(void* user_data, int32_t reason)
{
    state* self = (state*)user_data;
    self->reason = reason;
    check(drc_Shape_director_base_changed(self->director, reason + 100) == DRC_OK, "base changed");
}

static void shape_forgets_the_string(void* user_data, drc_string_sink* out_result)
{
    (void)user_data;
    (void)out_result;
}

static drc_Shape_vtable shape_vtable(state* user)
{
    drc_Shape_vtable vtable;
    memset(&vtable, 0, sizeof vtable);
    vtable.user_data = user;
    vtable.release = shape_release;
    vtable.area = shape_area;
    vtable.name = shape_name;
    vtable.sides = shape_sides;
    vtable.center = shape_center;
    vtable.mode = shape_mode;
    vtable.scale = shape_scale;
    vtable.add_int32_int32 = shape_add;
    vtable.greet = shape_greet;
    vtable.changed = shape_changed;
    return vtable;
}

static void check_a_director_of_an_abstract_class(void)
{
    state user;
    memset(&user, 0, sizeof user);
    drc_Shape_vtable vtable = shape_vtable(&user);
    drc_Shape_director* director = NULL;
    drc_Shape* shape = NULL;
    check(drc_Shape_director_create_void(&shape, &director, &vtable) == DRC_OK, "create");
    user.director = director;
    check(shape == drc_Shape_director_object(director), "the object of the director");
    check(shape != NULL, "the object");
    int32_t count = 0;
    check(drc_Shape_alive(&count) == DRC_OK && count == 1, "one shape alive");

    /* The methods that C++ calls on its own reach the vtable. */
    char* text = NULL;
    size_t size = 0;
    static const char described[] = "tri\0x: 6 units"; /* the NUL is part of the name */
    check(drc_Shape_describe(shape, &text, &size) == DRC_OK, "describe");
    check(size == sizeof described - 1 && memcmp(text, described, size) == 0,
          "describe calls the overrides of name and area");
    drc_free(text);
    check(user.calls == 1, "area was called once");

    double scaled = 0;
    check(drc_Shape_scaled_area(shape, 2.5, &scaled) == DRC_OK && scaled == 6.5, "scaled_area");
    check(user.factor == 2.5, "scale got its argument");

    drc_Point other = {2.0, 2.0};
    drc_Point middle = {0, 0};
    check(drc_Shape_middle(shape, &other, &middle) == DRC_OK, "middle");
    check(middle.x == 3.0 && middle.y == 4.0, "a struct result and a struct argument");

    drc_Mode mode = 99;
    check(drc_Shape_choose(shape, drc_Mode_Auto, &mode) == DRC_OK, "choose");
    check(mode == drc_Mode_Off && user.mode == drc_Mode_Auto, "an enum argument and result");

    int32_t sum = 0;
    check(drc_Shape_sum(shape, 4, 5, &sum) == DRC_OK && sum == 45, "an overloaded method");

    check(drc_Shape_welcome(shape, "Ana", 3, &text, &size) == DRC_OK, "welcome");
    check(equals(text, size, ">>|hello|Ana"), "strings in and out");

    int32_t sides = 0;
    check(drc_Shape_sides(shape, &sides) == DRC_OK && sides == 3, "the object's own function");

    /* A member that is NULL keeps the implementation of the class. */
    int32_t quiet = 0;
    check(drc_Shape_how_quiet(shape, &quiet) == DRC_OK && quiet == 1, "NULL keeps the class");
    double add_double = 0;
    check(drc_Shape_add_double_double(shape, 1.5, 2.0, &add_double) == DRC_OK && add_double == 3.5,
          "NULL keeps the class (overload)");
    int32_t total = 0;
    check(drc_Shape_total_of_three(shape, &total) == DRC_OK && total == 6,
          "a method that cannot be overridden keeps the class");
    int32_t fixed = 0;
    check(drc_Shape_fixed(shape, &fixed) == DRC_OK && fixed == 7, "a final method");

    /* A protected virtual method, and the implementation of the class. */
    check(drc_Shape_notify(shape, 9) == DRC_OK, "notify");
    int32_t reason = 0;
    check(drc_Shape_last_reason(shape, &reason) == DRC_OK && reason == 109, "the base call ran");
    check(user.reason == 9, "the override ran first");

    /* The base calls do not go through the vtable. */
    check(drc_Shape_director_base_name(director, &text, &size) == DRC_OK, "base name");
    check(equals(text, size, "shape"), "the implementation of the class");
    int32_t base_sides = -1;
    check(drc_Shape_director_base_sides(director, &base_sides) == DRC_OK && base_sides == 0,
          "base sides");
    check(drc_Shape_director_base_mode(director, drc_Mode_Auto, &mode) == DRC_OK &&
              mode == drc_Mode_On,
          "base mode");
    check(drc_Shape_director_base_greet(director, "Bo", 2, "t", 1, "m", &text, &size) == DRC_OK,
          "base greet");
    {
        /* The implementation of greet() calls name() virtually: the override. */
        static const char expected[] = "m t Bo, I am tri\0x";
        check(size == sizeof expected - 1 && memcmp(text, expected, size) == 0,
              "the base call of greet() reaches the override of name()");
        drc_free(text);
    }
    check(drc_Shape_director_base_name(NULL, &text, &size) != DRC_OK, "a NULL director");

    check(user.released == 0, "not released yet");
    drc_Shape_destroy(shape);
    check(user.released == 1, "released once");
    check(drc_Shape_alive(&count) == DRC_OK && count == 0, "destroyed");
}

static void check_what_a_vtable_must_have(void)
{
    state user;
    memset(&user, 0, sizeof user);
    drc_Shape_vtable vtable = shape_vtable(&user);
    drc_Shape_director* director = (drc_Shape_director*)&user;
    drc_Shape* shape = (drc_Shape*)&user;

    /* The pure virtual method has to be implemented. */
    vtable.area = NULL;
    check(drc_Shape_director_create_void(&shape, &director, &vtable) == DRC_ERROR_INVALID_ARGUMENT,
          "a missing pure virtual method");
    check(shape == NULL && director == NULL, "no object, no director");
    check(strstr(drc_last_error(), "vtable->area") != NULL, "the error names it");
    check(user.released == 1, "release is called when the creation fails");

    state other;
    memset(&other, 0, sizeof other);
    vtable = shape_vtable(&other);
    check(drc_Shape_director_create_void(NULL, &director, &vtable) == DRC_ERROR_NULL_ARGUMENT,
          "NULL object output");
    check(other.released == 1, "release is called when the arguments are wrong");
    check(drc_Shape_director_create_void(&shape, NULL, &vtable) == DRC_ERROR_NULL_ARGUMENT,
          "NULL director output");
    check(drc_Shape_director_create_void(&shape, &director, NULL) == DRC_ERROR_NULL_ARGUMENT,
          "no vtable");
    check(drc_Shape_director_object(NULL) == NULL, "NULL stays NULL");
}

static void check_the_constructors_of_the_class(void)
{
    state user;
    memset(&user, 0, sizeof user);
    drc_Shape_vtable vtable = shape_vtable(&user);
    vtable.name = NULL;
    vtable.sides = NULL;
    drc_Shape_director* director = NULL;
    drc_Shape* shape = NULL;
    check(drc_Shape_director_create_string_int32(&shape, &director, &vtable, "box", 3, 4) == DRC_OK,
          "constructor with arguments");
    check(shape == drc_Shape_director_object(director), "the object of the director");
    char* text = NULL;
    size_t size = 0;
    check(drc_Shape_name(shape, &text, &size) == DRC_OK && equals(text, size, "box"),
          "the constructor of the class ran");
    int32_t sides = 0;
    check(drc_Shape_sides(shape, &sides) == DRC_OK && sides == 4, "and kept its arguments");
    drc_Shape_destroy(shape);
    check(user.released == 1, "released");
}

static void check_a_string_that_is_not_given(void)
{
    state user;
    memset(&user, 0, sizeof user);
    drc_Shape_vtable vtable = shape_vtable(&user);
    vtable.name = shape_forgets_the_string;
    drc_Shape_director* director = NULL;
    drc_Shape* shape = NULL;
    check(drc_Shape_director_create_void(&shape, &director, &vtable) == DRC_OK, "create");
    check(shape == drc_Shape_director_object(director), "the object of the director");
    char* text = NULL;
    size_t size = 99;
    check(drc_Shape_name(shape, &text, &size) == DRC_OK && equals(text, size, ""),
          "no string given: the empty string");
    drc_string_sink_set(NULL, "x", 1); /* ignored */
    drc_Shape_destroy(shape);
}

/* A derived class: the director overrides what it inherits as well. */
static void circle_name(void* user_data, drc_string_sink* out_result)
{
    (void)user_data;
    drc_string_sink_set(out_result, "mine", 4);
}

static void check_a_director_of_a_derived_class(void)
{
    state user;
    memset(&user, 0, sizeof user);
    drc_Circle_vtable vtable;
    memset(&vtable, 0, sizeof vtable);
    vtable.user_data = &user;
    vtable.release = shape_release;
    vtable.name = circle_name;
    vtable.sides = shape_sides;
    drc_Circle_director* director = NULL;
    drc_Circle* circle = NULL;
    check(drc_Circle_director_create(&circle, &director, &vtable, 2.0) == DRC_OK, "create a Circle");
    check(circle == drc_Circle_director_object(director), "the object of the director");
    drc_Shape* shape = drc_Circle_as_drc_Shape(circle);
    char* text = NULL;
    size_t size = 0;
    /* The name is the override; the area is that of Circle (3 * r * r). */
    check(drc_Shape_describe(shape, &text, &size) == DRC_OK, "describe");
    check(equals(text, size, "mine: 12 units"), "an override and the class's own method");
    int32_t sides = 0;
    check(drc_Shape_sides(shape, &sides) == DRC_OK && sides == 3, "an inherited method");
    check(drc_Circle_director_base_name(director, &text, &size) == DRC_OK &&
              equals(text, size, "round"),
          "the base call of Circle is Circle's");
    double radius = 0;
    check(drc_Circle_radius(circle, &radius) == DRC_OK && radius == 2.0, "Circle's methods");
    /* Circle declares add(int, int), which hides Shape::add(double, double) from its name:
     * the vtable entry add_double_double is NULL, and the implementation to run is that of
     * Shape, not Circle's with a conversion of the arguments. */
    double sum = 0;
    check(drc_Shape_sum_double(shape, 2.5, 1.0, &sum) == DRC_OK && sum == 3.5,
          "the fallback of an overload that a derived class hides");
    check(drc_Circle_director_base_add_double_double(director, 2.5, 1.0, &sum) == DRC_OK &&
              sum == 3.5,
          "and its base call");
    int32_t total = 0;
    check(drc_Circle_director_base_add_int32_int32(director, 2, 1, &total) == DRC_OK &&
              total == 1003,
          "while the other overload is Circle's");
    drc_Circle_destroy(circle);
    check(user.released == 1, "released through the derived class");
}

/* Protected constructors, a protected pure virtual method, enums and numbers. */
static void stage_step(void* user_data, drc_Mode mode, int32_t number, drc_string_sink* out_result)
{
    state* self = (state*)user_data;
    char text[8];
    self->mode = mode;
    self->number += number;
    text[0] = (char)('a' + number);
    text[1] = mode == drc_Mode_Auto ? 'A' : 'o';
    drc_string_sink_set(out_result, text, 2);
}

static void stage_origin(void* user_data, drc_Point* out_result)
{
    (void)user_data;
    out_result->x = 5.0;
    out_result->y = 0.0;
}

static void check_protected_constructors_and_methods(void)
{
    state user;
    memset(&user, 0, sizeof user);
    drc_Stage_vtable vtable;
    memset(&vtable, 0, sizeof vtable);
    vtable.user_data = &user;
    vtable.release = shape_release;
    vtable.step = stage_step;
    drc_Stage_director* director = NULL;
    drc_Stage* stage = NULL;
    check(drc_Stage_director_create_int32(&stage, &director, &vtable, 3) == DRC_OK, "create a Stage");
    check(stage == drc_Stage_director_object(director), "the object of the director");
    char* text = NULL;
    size_t size = 0;
    check(drc_Stage_run(stage, drc_Mode_Auto, &text, &size) == DRC_OK, "run");
    check(equals(text, size, "bAcAdA@0"), "a protected pure virtual method; origin is the class's");
    check(user.number == 1 + 2 + 3 && user.mode == drc_Mode_Auto, "its arguments");
    drc_Stage_destroy(stage);
    check(user.released == 1, "released");

    vtable.origin = stage_origin;
    check(drc_Stage_director_create_void(&stage, &director, &vtable) == DRC_OK, "the default constructor");
    check(stage == drc_Stage_director_object(director), "the object of the other director");
    check(drc_Stage_run(stage, drc_Mode_On, &text, &size) == DRC_OK, "run again");
    check(equals(text, size, "boco@5"), "a protected virtual method that is overridden");
    drc_Stage_destroy(stage);

    vtable.step = NULL;
    check(drc_Stage_director_create_void(&stage, &director, &vtable) == DRC_ERROR_INVALID_ARGUMENT,
          "the protected pure method is required");
}

/* After disable_callbacks the vtable is not called, and not released. */
static void check_disabled_callbacks(void)
{
    state user;
    memset(&user, 0, sizeof user);
    drc_Shape_vtable vtable = shape_vtable(&user);
    drc_Shape_director* director = NULL;
    drc_Shape* shape = NULL;
    check(drc_Shape_director_create_void(&shape, &director, &vtable) == DRC_OK, "create");
    check(shape == drc_Shape_director_object(director), "the object of the director");
    drc_disable_callbacks(shape_release);
    char* text = NULL;
    size_t size = 0;
    double area = -1;
    check(drc_Shape_name(shape, &text, &size) == DRC_OK && equals(text, size, "shape"),
          "the class's implementation once disabled");
    check(drc_Shape_area(shape, &area) == DRC_OK && area == 0.0, "a pure method gives 0");
    check(user.calls == 0, "the vtable was not called");
    drc_Shape_destroy(shape);
    check(user.released == 0, "and not released");
    drc_enable_callbacks(shape_release);
}

static void check_other_classes(void)
{
    drc_Dot* dot = NULL;
    check(drc_Dot_create(&dot) == DRC_OK, "a final class is wrapped as usual");
    drc_Dot_destroy(dot);
    drc_Plain* plain = NULL;
    int32_t value = 0;
    check(drc_Plain_create(&plain) == DRC_OK && drc_Plain_value(plain, &value) == DRC_OK &&
              value == 5,
          "a class without a director is wrapped as usual");
    drc_Plain_destroy(plain);
}

/* Containers, in both directions. */
static void shape_numbers(void* user_data, const int32_t* values, size_t size,
                          drc_vector_sink* out_result)
{
    (void)user_data;
    int32_t doubled[8];
    for (size_t i = 0; i < size && i < 8; ++i) {
        doubled[i] = values[i] * 2;
    }
    drc_vector_sink_set(out_result, doubled, size, sizeof doubled[0]);
}

static void shape_total(void* user_data, const int32_t* values, size_t size, int32_t* out_result)
{
    (void)user_data;
    *out_result = (int32_t)size * 1000 + (size == 0 ? 0 : values[size - 1]);
}

static void shape_words(void* user_data, const drc_string* input, size_t size,
                        drc_vector_sink* out_result)
{
    (void)user_data;
    drc_string out[3];
    memset(out, 0, sizeof out);
    for (size_t i = 0; i < size && i < 3; ++i) {
        out[i] = input[size - 1 - i]; /* reversed; the data belongs to the caller of C++ */
    }
    drc_vector_sink_set_strings(out_result, out, size < 3 ? size : 3);
}

static void shape_modes(void* user_data, const drc_Mode* wanted, size_t size,
                        drc_vector_sink* out_result)
{
    (void)user_data;
    (void)wanted;
    (void)size;
    drc_Mode one[2] = {1, 5};
    drc_vector_sink_set(out_result, one, 2, sizeof one[0]);
}

static void shape_points(void* user_data, const drc_Point* list, size_t size,
                         drc_vector_sink* out_result)
{
    (void)user_data;
    drc_Point out[4];
    for (size_t i = 0; i < size && i < 4; ++i) {
        out[i].x = list[i].x + 1;
        out[i].y = list[i].y;
    }
    drc_vector_sink_set(out_result, out, size, sizeof out[0]);
}

static void shape_flags(void* user_data, const bool* input, size_t size, drc_vector_sink* out_result)
{
    (void)user_data;
    bool out[4];
    for (size_t i = 0; i < size && i < 4; ++i) {
        out[i] = input[i];
    }
    drc_vector_sink_set(out_result, out, size, sizeof out[0]);
}

static void shape_triple(void* user_data, const double* values, drc_vector_sink* out_result)
{
    (void)user_data;
    double out[3] = {values[2], values[1], values[0]};
    drc_vector_sink_set(out_result, out, 3, sizeof out[0]);
}

static void shape_triple_short(void* user_data, const double* values, drc_vector_sink* out_result)
{
    (void)user_data;
    drc_vector_sink_set(out_result, values, 2, sizeof values[0]); /* not 3 elements */
}

static void shape_numbers_wrong_size(void* user_data, const int32_t* values, size_t size,
                                     drc_vector_sink* out_result)
{
    (void)user_data;
    drc_vector_sink_set(out_result, values, size, sizeof(int64_t)); /* not an int32_t */
    drc_vector_sink_set(NULL, values, size, sizeof values[0]);       /* ignored */
}

static void shape_flags_raw(void* user_data, const bool* input, size_t size,
                            drc_vector_sink* out_result)
{
    (void)user_data;
    (void)input;
    (void)size;
    const unsigned char raw[3] = {0, 1, 2}; /* not a bool: it is read as true */
    drc_vector_sink_set(out_result, raw, 3, 1);
}

static void check_containers(void)
{
    state user;
    memset(&user, 0, sizeof user);
    drc_Shape_vtable vtable = shape_vtable(&user);
    vtable.numbers = shape_numbers;
    vtable.total = shape_total;
    vtable.words = shape_words;
    vtable.modes = shape_modes;
    vtable.points = shape_points;
    vtable.flags = shape_flags;
    vtable.triple = shape_triple;
    drc_Shape_director* director = NULL;
    drc_Shape* shape = NULL;
    check(drc_Shape_director_create_void(&shape, &director, &vtable) == DRC_OK, "create");

    const int32_t values[3] = {1, 2, 3};
    int32_t* numbers = NULL;
    size_t count = 0;
    check(drc_Shape_call_numbers(shape, values, 3, &numbers, &count) == DRC_OK && count == 3 &&
              numbers[0] == 2 && numbers[1] == 4 && numbers[2] == 6,
          "numbers go in and come back");
    drc_free(numbers);
    check(drc_Shape_call_numbers(shape, NULL, 0, &numbers, &count) == DRC_OK && count == 0,
          "no numbers");
    drc_free(numbers);
    int32_t total = 0;
    check(drc_Shape_call_total(shape, values, 3, &total) == DRC_OK && total == 3003,
          "a container and a scalar result");

    const drc_string input[2] = {{"ab", 2}, {"c\0d", 3}};
    drc_string* words = NULL;
    check(drc_Shape_call_words(shape, input, 2, &words, &count) == DRC_OK && count == 2 &&
              words[0].size == 3 && memcmp(words[0].data, "c\0d", 3) == 0 &&
              words[1].size == 2 && memcmp(words[1].data, "ab", 2) == 0,
          "strings, NULs included");
    drc_free(words);

    const drc_Mode wanted[1] = {0};
    drc_Mode* modes = NULL;
    check(drc_Shape_call_modes(shape, wanted, 1, &modes, &count) == DRC_OK && count == 2 &&
              modes[0] == 1 && modes[1] == 5,
          "enums");
    drc_free(modes);

    const drc_Point list[2] = {{1.0, 2.0}, {3.0, 4.0}};
    drc_Point* points = NULL;
    check(drc_Shape_call_points(shape, list, 2, &points, &count) == DRC_OK && count == 2 &&
              points[0].x == 2.0 && points[1].y == 4.0,
          "structs");
    drc_free(points);

    const bool flags[3] = {true, false, true};
    bool* back = NULL;
    check(drc_Shape_call_flags(shape, flags, 3, &back, &count) == DRC_OK && count == 3 &&
              back[0] && !back[1] && back[2],
          "a std::vector<bool>");
    drc_free(back);

    const double triple[3] = {1.0, 2.0, 3.0};
    double out[3] = {0};
    check(drc_Shape_call_triple(shape, triple, out) == DRC_OK && out[0] == 3.0 && out[2] == 1.0,
          "a std::array");

    /* A container that is not what the result is: the empty one, or the zero array. */
    vtable.triple = shape_triple_short;
    vtable.numbers = shape_numbers_wrong_size;
    vtable.flags = shape_flags_raw;
    drc_Shape_director* other_director = NULL;
    drc_Shape* other = NULL;
    check(drc_Shape_director_create_void(&other, &other_director, &vtable) == DRC_OK, "create");
    check(drc_Shape_call_triple(other, triple, out) == DRC_OK && out[0] == 0.0 && out[1] == 0.0 &&
              out[2] == 0.0,
          "an array of another size is the zero array");
    check(drc_Shape_call_flags(other, flags, 3, &back, &count) == DRC_OK && count == 3 &&
              ((unsigned char*)back)[0] == 0 && ((unsigned char*)back)[1] == 1 &&
              ((unsigned char*)back)[2] == 1,
          "a byte of a bool that is neither 0 nor 1 is true");
    drc_free(back);
    check(drc_Shape_call_numbers(other, values, 3, &numbers, &count) == DRC_OK && count == 0,
          "elements of another size are ignored");
    drc_free(numbers);

    /* Not given: the implementation of the class. */
    vtable.numbers = NULL;
    vtable.words = NULL;
    drc_Shape_director* plain_director = NULL;
    drc_Shape* plain = NULL;
    check(drc_Shape_director_create_void(&plain, &plain_director, &vtable) == DRC_OK, "create");
    check(drc_Shape_call_numbers(plain, values, 3, &numbers, &count) == DRC_OK && count == 3 &&
              numbers[0] == 3 && numbers[2] == 1,
          "the implementation of the class");
    drc_free(numbers);
    drc_Shape_destroy(plain);
    drc_Shape_destroy(other);
    drc_Shape_destroy(shape);
}

/* Objects in the signatures. */
static int beams_destroyed_by_the_vtable = 0;
static const drc_Shape* seen_by_compare = NULL;

static void shape_compare(void* user_data, const drc_Shape* other, double* out_result)
{
    (void)user_data;
    seen_by_compare = other; /* lent: valid during this call only */
    double area = 0.0;
    check(drc_Shape_area(other, &area) == DRC_OK, "the lent object works");
    *out_result = area + 1.0;
}

static void shape_same(void* user_data, const drc_Shape* other, bool* out_result)
{
    (void)user_data;
    *out_result = other == NULL; /* NULL is a NULL pointer */
}

static void shape_adopt(void* user_data, drc_Beam* beam, int32_t* out_result)
{
    (void)user_data;
    int32_t length = 0;
    check(drc_Beam_call_length(beam, &length) == DRC_OK, "the object that is given");
    *out_result = length + 100;
    drc_Beam_destroy(beam); /* it is the override's now */
    ++beams_destroyed_by_the_vtable;
}

static void shape_make_beam(void* user_data, int32_t length, drc_Beam** out_result)
{
    (void)user_data;
    if (length < 0) {
        return; /* not written: a NULL pointer */
    }
    check(drc_Beam_create(out_result, length * 3) == DRC_OK, "make the object to give");
}

static void check_objects(void)
{
    state user;
    memset(&user, 0, sizeof user);
    drc_Shape_vtable vtable = shape_vtable(&user);
    vtable.compare = shape_compare;
    vtable.same = shape_same;
    vtable.adopt = shape_adopt;
    vtable.make_beam = shape_make_beam;
    drc_Shape_director* director = NULL;
    drc_Shape* shape = NULL;
    check(drc_Shape_director_create_void(&shape, &director, &vtable) == DRC_OK, "create");

    double result = 0.0;
    check(drc_Shape_call_compare(shape, shape, &result) == DRC_OK && result == 7.5 &&
              seen_by_compare == shape,
          "a reference to an object: its own address");
    bool same = false;
    check(drc_Shape_call_same(shape, NULL, &same) == DRC_OK && same, "a NULL pointer");
    check(drc_Shape_call_same(shape, shape, &same) == DRC_OK && !same, "a pointer");

    drc_Beam* beam = NULL;
    check(drc_Beam_create(&beam, 5) == DRC_OK, "a Beam");
    int32_t value = 0;
    check(drc_Shape_call_adopt(shape, beam, &value) == DRC_OK && value == 105 &&
              beams_destroyed_by_the_vtable == 1,
          "a std::unique_ptr that C++ gives: the override destroys it");

    drc_Beam* made = NULL;
    check(drc_Shape_call_make_beam(shape, 4, &made) == DRC_OK && made != NULL &&
              drc_Beam_call_length(made, &value) == DRC_OK && value == 12,
          "a std::unique_ptr that the override gives");
    drc_Beam_destroy(made);
    made = (drc_Beam*)1;
    check(drc_Shape_call_make_beam(shape, -1, &made) == DRC_OK && made == NULL,
          "a std::unique_ptr that is not given is NULL");
    drc_Shape_destroy(shape);
}

int main(void)
{
    check_objects();
    check_containers();
    check_a_director_of_an_abstract_class();
    check_what_a_vtable_must_have();
    check_the_constructors_of_the_class();
    check_a_string_that_is_not_given();
    check_a_director_of_a_derived_class();
    check_protected_constructors_and_methods();
    check_disabled_callbacks();
    check_other_classes();
    if (failures != 0) {
        fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    printf("round trip OK\n");
    return 0;
}
