/* SPDX-License-Identifier: Apache-2.0 */
/*
 * Round trip through the generated C API of the "ownership" case, from plain C.
 * tests/test_roundtrip.py builds it against the generated library and runs it.
 */
#include "node_c.h"
#include "tree_c.h"

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

static int mentions(const char* text)
{
    const char* message = own_last_error();
    return message != NULL && strstr(message, text) != NULL;
}

int main(void)
{
    own_Node* a = NULL;
    own_Node* b = NULL;
    own_Item* item = NULL;
    own_Tree* tree = NULL;
    own_Driver* driver = NULL;
    own_Pair* pair = NULL;
    int32_t out = 0;
    own_Tree* global = NULL;
    size_t live = 0;
    size_t baseline = 0;

    /* The singleton and its root Node live until the program ends. */
    check(own_Tree_global(&global) == OWN_OK && global != NULL, "static reference");
    check(own_Node_live(&baseline) == OWN_OK, "live");

    check(own_Node_create(&a, 3) == OWN_OK && own_Node_create(&b, 4) == OWN_OK, "Node");
    check(own_Item_create(&item, 10) == OWN_OK, "Item");
    check(own_Tree_create(&tree) == OWN_OK, "Tree");

    /* References: const T& and T&. */
    check(own_Tree_sum(tree, a, b, &out) == OWN_OK && out == 7, "sum");
    check(own_Tree_bump(tree, a) == OWN_OK, "bump");
    check(own_Node_value(a, &out) == OWN_OK && out == 4, "bumped");

    /* Pointers. */
    check(own_Tree_read(tree, b, &out) == OWN_OK && out == 4, "read");
    check(own_Tree_write(tree, b, 40) == OWN_OK, "write");
    check(own_Node_value(b, &out) == OWN_OK && out == 40, "written");

    /* A copy: the caller keeps its object, and the copy is gone afterwards. */
    check(own_Node_live(&live) == OWN_OK, "live");
    {
        size_t during = 0;
        check(own_Tree_addCopy(tree, a) == OWN_OK, "addCopy");
        check(own_Node_live(&during) == OWN_OK && during == live, "the copy is destroyed");
    }
    check(own_Node_value(a, &out) == OWN_OK && out == 4, "the original is unchanged");
    check(own_Tree_copies(tree, &out) == OWN_OK && out == 1004, "the copy was changed");

    /* Overloads that differ in the way they take the node. */
    check(own_Tree_weight_own_Node(tree, a, &out) == OWN_OK && out == 4, "const T&");
    check(own_Tree_weight_own_Node_ref(tree, a, &out) == OWN_OK && out == 8, "T&");
    check(own_Tree_weight_own_Node_cptr(tree, a, &out) == OWN_OK && out == 12, "const T*");
    check(own_Tree_weight_own_Node_ptr(tree, a, &out) == OWN_OK && out == 16, "T*");
    check(own_Tree_twice(a, &out) == OWN_OK && out == 8, "static");

    /* A class of another header, and a move-only one by const reference. */
    check(own_total(a, item, &out) == OWN_OK && out == 14, "total");
    check(own_idOf(item, &out) == OWN_OK && out == 10, "idOf");

    /* NULL, for every handle. */
    check(own_Tree_sum(NULL, a, b, &out) == OWN_ERROR_NULL_ARGUMENT && mentions("'self' is NULL"),
          "NULL self");
    check(own_Tree_sum(tree, NULL, b, &out) == OWN_ERROR_NULL_ARGUMENT && mentions("'a' is NULL"),
          "NULL a");
    check(own_Tree_sum(tree, a, NULL, &out) == OWN_ERROR_NULL_ARGUMENT && mentions("'b' is NULL"),
          "NULL b");
    check(own_Tree_bump(tree, NULL) == OWN_ERROR_NULL_ARGUMENT, "NULL T&");
    check(own_Tree_read(tree, NULL, &out) == OWN_ERROR_NULL_ARGUMENT, "NULL const T*");
    check(own_Tree_write(tree, NULL, 1) == OWN_ERROR_NULL_ARGUMENT, "NULL T*");
    check(own_Tree_addCopy(tree, NULL) == OWN_ERROR_NULL_ARGUMENT && mentions("'node' is NULL"),
          "NULL copy");
    check(own_total(a, NULL, &out) == OWN_ERROR_NULL_ARGUMENT && mentions("'item' is NULL"),
          "NULL item");
    check(own_Tree_sum(tree, a, b, NULL) == OWN_ERROR_NULL_ARGUMENT, "NULL out_result");

    /* Results by value and in a std::unique_ptr: new objects that the caller owns. */
    {
        own_Node* made = NULL;
        own_Item* made_item = NULL;
        size_t before = 0;
        check(own_Node_live(&before) == OWN_OK, "live");
        check(own_Tree_makeNode(tree, 9, &made) == OWN_OK && made != NULL, "makeNode");
        check(own_Node_value(made, &out) == OWN_OK && out == 9, "made value");
        check(own_Node_live(&live) == OWN_OK && live == before + 1, "one more Node");
        own_Node_destroy(made);
        check(own_Node_live(&live) == OWN_OK && live == before, "destroyed");
        check(own_Tree_makeItem(tree, 3, &made_item) == OWN_OK && made_item != NULL, "makeItem");
        own_Item_destroy(made_item);
        check(own_Tree_makeUnique(tree, 4, true, &made_item) == OWN_OK && made_item != NULL,
              "makeUnique");
        check(own_Item_id(made_item, &out) == OWN_OK && out == 4, "unique value");
        own_Item_destroy(made_item);
        check(own_Tree_makeUnique(tree, 4, false, &made_item) == OWN_OK && made_item == NULL,
              "an empty std::unique_ptr is NULL");
        /* The output is NULL on every failure. */
        made = a;
        check(own_Tree_makeChecked(tree, -1, &made) == OWN_ERROR_EXCEPTION && made == NULL,
              "a throwing function leaves NULL");
        check(mentions("negative"), "the message of the exception");
        made = a;
        check(own_Tree_makeNode(NULL, 1, &made) == OWN_ERROR_NULL_ARGUMENT && made == NULL,
              "NULL self leaves NULL");
        check(own_Tree_makeNode(tree, 1, NULL) == OWN_ERROR_NULL_ARGUMENT, "NULL out_result");
        check(own_Node_live(&live) == OWN_OK && live == before, "nothing leaked");
    }

    /* Results by reference: the C++ code keeps owning them. */
    {
        own_Node* root = NULL;
        const own_Node* croot = NULL;
        own_Node* picked = NULL;
        check(own_Tree_root(tree, &root) == OWN_OK && root != NULL, "root");
        check(own_Node_setValue(root, 50) == OWN_OK, "setValue of the root");
        check(own_Tree_croot(tree, &croot) == OWN_OK && croot == root, "croot is the same object");
        check(own_Node_value(croot, &out) == OWN_OK && out == 50, "const reference");
        check(own_pick(a, b, true, &picked) == OWN_OK && picked == b, "pick second");
        check(own_pick(a, b, false, &picked) == OWN_OK && picked == a, "pick first");
        check(own_pick(a, NULL, false, &picked) == OWN_ERROR_NULL_ARGUMENT, "NULL argument");
        check(own_Node_me(root, &picked) == OWN_OK && picked == root, "me");
        check(own_Tree_root(NULL, &root) == OWN_ERROR_NULL_ARGUMENT, "NULL self");
    }

    /* Objects that keep the pointers they get. */
    check(own_Driver_create(&driver, a) == OWN_OK, "Driver");
    check(own_Node_setValue(a, 5) == OWN_OK, "setValue");
    check(own_Driver_read(driver, &out) == OWN_OK && out == 5, "Driver sees the node");
    check(own_Driver_create(NULL, a) == OWN_ERROR_NULL_ARGUMENT, "NULL out_self");
    check(own_Driver_create(&driver, NULL) == OWN_ERROR_NULL_ARGUMENT && driver == NULL,
          "NULL node: the output is reset");
    check(own_Pair_create(&pair, a, b) == OWN_OK, "Pair");
    check(own_Pair_sum(pair, &out) == OWN_OK && out == 45, "Pair sum");

    own_Pair_destroy(pair);
    own_Driver_destroy(driver);
    own_Tree_destroy(tree);
    own_Item_destroy(item);
    own_Node_destroy(a);
    own_Node_destroy(b);
    check(own_Node_live(&live) == OWN_OK && live == baseline, "no Node left");
    check(own_Item_live(&live) == OWN_OK && live == 0, "no Item left");

    if (failures != 0) {
        return 1;
    }
    puts("ownership: C round trip OK");
    return 0;
}
