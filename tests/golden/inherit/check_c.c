/* SPDX-License-Identifier: Apache-2.0 */
/*
 * Round trip through the generated C API of the "inherit" case, from plain C.
 * tests/test_roundtrip.py builds it against the generated library and runs it.
 */
#include "shape_c.h"
#include "shapes_c.h"
#include "widget_c.h"

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
    inh_free(text);
    return same;
}

int main(void)
{
    double area = 0;
    int32_t number = 0;
    char* text = NULL;
    size_t size = 0;

    /* A class and its ancestors: the same object, seen as each of them. */
    inh_Ring* ring = NULL;
    check(inh_Ring_create(&ring, 10.0, 5.0) == INH_OK, "Ring");
    inh_Circle* circle = inh_Ring_as_inh_Circle(ring);
    inh_Shape* shape = inh_Ring_as_inh_Shape(ring);
    inh_Named* named = inh_Ring_as_inh_Named(ring);
    check(circle != NULL && shape != NULL && named != NULL, "upcasts");
    check(inh_Circle_as_inh_Shape(circle) == shape, "Circle to Shape");
    check(inh_Shape_as_inh_Named(shape) == named, "Shape to Named");
    check(inh_Ring_as_inh_Shape_const(ring) == shape, "const upcast");
    check(inh_Ring_as_inh_Named_const(ring) == named, "const upcast to the root");

    /* A virtual method called through each of them runs the override of Ring. */
    check(inh_Shape_area(shape, &area) == INH_OK && area == 225.0, "Shape_area is Ring's");
    check(inh_Circle_area(circle, &area) == INH_OK && area == 225.0, "Circle_area is Ring's");
    check(inh_Named_name(named, &text, &size) == INH_OK && equals(text, size, "ring"), "name");
    check(inh_Shape_describe(shape, &text, &size) == INH_OK && equals(text, size, "ring: 225 units"),
          "describe calls the overrides");
    check(inh_Shape_sides(shape, &number) == INH_OK && number == 0, "default of a virtual method");
    check(inh_call_name(named, &text, &size) == INH_OK && equals(text, size, "I am ring"),
          "a Ring as a Named");
    check(inh_total_area(shape, shape, &area) == INH_OK && area == 450.0, "a Ring as a Shape");

    /* NULL stays NULL. */
    check(inh_Ring_as_inh_Shape(NULL) == NULL && inh_Ring_as_inh_Named_const(NULL) == NULL, "NULL");
    inh_Ring_destroy(ring);

    /* An abstract class has no constructor; the objects come from functions, and the
     * class that is destroyed is the one of the object. */
    inh_Shape* made = NULL;
    check(inh_make_shape(1, 3.0, &made) == INH_OK && made != NULL, "make_shape");
    check(inh_Shape_sides(made, &number) == INH_OK && number == 4, "Square::sides through Shape");
    check(inh_shapes_alive(&number) == INH_OK && number == 1, "one alive");
    inh_Shape_destroy(made);
    check(inh_shapes_alive(&number) == INH_OK && number == 0, "destroyed through the base class");
    check(inh_make_shape(7, 1.0, &made) == INH_OK && made == NULL, "no shape");

    /* Ownership is taken whatever the status, also when the base is not at the start. */
    inh_Circle* taken = NULL;
    check(inh_Circle_create(&taken, 1.0) == INH_OK, "Circle");
    check(inh_consume(inh_Circle_as_inh_Shape(taken), &number) == INH_OK && number == 0, "consume");
    check(inh_shapes_alive(&number) == INH_OK && number == 0, "consumed");

    /* A base class that is not at the start of the object. */
    inh_Widget* widget = NULL;
    check(inh_Widget_create(&widget) == INH_OK, "Widget");
    inh_Counter* counter = inh_Widget_as_inh_Counter(widget);
    inh_Labeled* labeled = inh_Widget_as_inh_Labeled(widget);
    check(counter != NULL && labeled != NULL && (void*)counter != (void*)labeled, "two bases");
    check((void*)counter != (void*)widget || (void*)labeled != (void*)widget,
          "one of them is not at the start");
    check(inh_Counter_next(counter, &number) == INH_OK && number == 1, "Counter_next");
    check(inh_bump(counter, &number) == INH_OK && number == 2, "bump");
    check(inh_peek(inh_Widget_as_inh_Counter_const(widget), &number) == INH_OK && number == 2,
          "a copy of the base");
    check(inh_Labeled_setLabel(labeled, "ab", 2) == INH_OK, "setLabel");
    check(inh_name_of(labeled, &text, &size) == INH_OK && equals(text, size, "widget ab"),
          "virtual call through the second base");
    check(inh_Widget_both(widget, &number) == INH_OK && number == 209, "both: count and label");
    inh_Widget_destroy(widget);

    /* The base that is not at the start of the object is not at its address either. */
    inh_Counter* watched = NULL;
    check(inh_Counter_create(&watched) == INH_OK, "Counter");
    inh_Observer* observer = NULL;
    check(inh_Observer_create(&observer, watched) == INH_OK, "Observer");
    check((void*)inh_Observer_as_inh_Labeled(observer) != (void*)observer, "Labeled is not first");
    check((void*)inh_Observer_as_inh_Tagged(observer) == (void*)observer, "Tagged is first");
    inh_Shelf* shelf = NULL;
    check(inh_Shelf_create(&shelf) == INH_OK, "Shelf");
    check(inh_Shelf_put(shelf, inh_Observer_as_inh_Labeled(observer)) == INH_OK, "put");
    inh_Observer* back = NULL;
    check(inh_Shelf_pop(shelf, &back) == INH_OK && back == observer, "pop gives the same object");
    check(inh_Counter_next(watched, &number) == INH_OK && number == 1, "next");
    check(inh_Observer_watched(back, &number) == INH_OK && number == 1, "watched");
    inh_Observer_destroy(back);
    inh_Shelf_destroy(shelf);
    inh_Counter_destroy(watched);

    /* Without a virtual destructor, only an object of exactly that class can be given. */
    inh_Plain* plain = NULL;
    check(inh_Plain_create(&plain) == INH_OK, "Plain");
    check(inh_take_plain(plain, &number) == INH_OK && number == 5, "take_plain");

    if (failures != 0) {
        return 1;
    }
    puts("inherit: C round trip OK");
    return 0;
}
