/* SPDX-License-Identifier: Apache-2.0 */
/* Round trip through the generated C API of the "edge" case, from plain C. */
#include "edge_c.h"

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

/* Function pointers typed from the C header: the C declaration and the C++
 * definition must have the same type (CFI and -fsanitize=function check it). */
typedef edge_status (*create_fn)(edge_Registry**);
typedef void (*destroy_fn)(edge_Registry*);
typedef edge_status (*method_fn)(const edge_Registry*, int*);

int main(void)
{
    create_fn create = edge_Registry_create;
    destroy_fn destroy = edge_Registry_destroy;
    method_fn method = edge_Registry_staticmethod;
    edge_Registry* registry = NULL;
    int value = 0;
    int64_t combined = 0;

    check(edge_add(20, 22, &value) == EDGE_OK && value == 42, "add");
    check(edge_combine(1, 2, 3, &combined) == EDGE_OK && combined == 123, "combine");
    check(edge_getattr(1, &value) == EDGE_OK && value == 2, "getattr");

    check(create(&registry) == EDGE_OK, "create through a pointer");
    check(method(registry, &value) == EDGE_OK && value == 2, "method through a pointer");
    check(edge_Registry_fail(registry) == EDGE_ERROR_EXCEPTION, "fail");
    check(edge_Registry_throwOnDestroy(registry, true) == EDGE_OK, "throwOnDestroy");
    destroy(registry); /* the destructor throws; the exception must not escape */
    check(strcmp(edge_last_error(), "failed on purpose") == 0, "last error kept");
    check(edge_Registry_liveCount(&value) == EDGE_OK && value == 0, "destroyed");

    if (failures == 0) {
        printf("edge: C round trip OK\n");
    }
    return failures == 0 ? 0 : 1;
}
