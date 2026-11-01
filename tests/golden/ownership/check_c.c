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

    /* Transfer: the function owns what it is given, whatever the status. */
    {
        own_Item* x = NULL;
        own_Item* y = NULL;
        own_Item* first = NULL;
        own_Pipeline* pipeline = NULL;
        size_t items_before = 0;
        size_t count = 0;
        check(own_Item_live(&items_before) == OWN_OK, "live");
        check(own_Item_create(&x, 5) == OWN_OK && own_Item_create(&y, 6) == OWN_OK, "Items");
        check(own_Tree_adopt(tree, x) == OWN_OK, "adopt");
        check(own_Tree_items(tree, &count) == OWN_OK && count == 1, "one item");
        check(own_Tree_itemSum(tree, &out) == OWN_OK && out == 5, "the sum of the ids");
        check(own_Tree_first(tree, &first) == OWN_OK && first == x, "the item is the same");
        /* A NULL self, a NULL output... the object is destroyed anyway. */
        check(own_Tree_adopt(NULL, y) == OWN_ERROR_NULL_ARGUMENT && mentions("'self' is NULL"),
              "NULL self");
        check(own_Item_live(&live) == OWN_OK && live == items_before + 1, "y was destroyed");
        /* An exception after the objects were taken: they are destroyed. */
        check(own_Item_create(&x, 7) == OWN_OK && own_Item_create(&y, 8) == OWN_OK, "Items");
        check(own_Tree_adoptBoth(tree, x, -1, y) == OWN_ERROR_EXCEPTION && mentions("tag"),
              "adoptBoth throws");
        check(own_Item_live(&live) == OWN_OK && live == items_before + 1, "both destroyed");
        /* A NULL argument: the other one is destroyed too. */
        check(own_Item_create(&x, 7) == OWN_OK, "Item");
        check(own_Tree_adoptBoth(tree, x, 1, NULL) == OWN_ERROR_NULL_ARGUMENT
                  && mentions("'second' is NULL"),
              "NULL second");
        check(own_Tree_adoptBoth(tree, NULL, 1, NULL) == OWN_ERROR_NULL_ARGUMENT, "both NULL");
        check(own_Item_live(&live) == OWN_OK && live == items_before + 1, "x was destroyed");
        check(own_Item_create(&x, 9) == OWN_OK && own_Item_create(&y, 10) == OWN_OK, "Items");
        check(own_Tree_adoptBoth(tree, x, 1, y) == OWN_OK, "adoptBoth");
        check(own_Tree_items(tree, &count) == OWN_OK && count == 3, "three items");
        check(own_Tree_itemSum(tree, &out) == OWN_OK && out == 24, "the sum");
        /* Constructors, and a free function. */
        check(own_Item_create(&x, 11) == OWN_OK, "Item");
        check(own_Pipeline_create_own_Item_unique(&pipeline, x) == OWN_OK, "Pipeline");
        check(own_Pipeline_firstId(pipeline, &out) == OWN_OK && out == 11, "firstId");
        /* A constructor that throws resets its output, and takes the object. */
        check(own_Item_create(&x, 12) == OWN_OK, "Item");
        {
            own_Pipeline* other = pipeline;
            check(own_Pipeline_create_own_Item_unique_int32(&other, x, 100) == OWN_ERROR_EXCEPTION
                      && other == NULL,
                  "a constructor that throws");
        }
        own_Pipeline_destroy(pipeline);
        check(own_Item_create(&x, 13) == OWN_OK, "Item");
        check(own_Pipeline_create_own_Item_unique(NULL, x) == OWN_ERROR_NULL_ARGUMENT,
              "NULL out_self");
        check(own_Item_create(&x, 14) == OWN_OK, "Item");
        check(own_consume(x, &out) == OWN_OK && out == 14, "consume");
        check(own_Item_live(&live) == OWN_OK && live == items_before + 3, "nothing leaked");
    }

    /* Raw pointers: the annotations say who owns them. */
    {
        own_Node* found = NULL;
        const own_Node* cfound = NULL;
        own_Node* root = NULL;
        own_Item* detached = NULL;
        own_Item* raw = NULL;
        char* data = NULL;
        size_t size = 0;
        size_t count = 0;
        check(own_Tree_root(tree, &root) == OWN_OK, "root");
        check(own_Tree_find(tree, 50, &found) == OWN_OK && found == root, "find");
        check(own_Tree_find(tree, 51, &found) == OWN_OK && found == NULL, "find: not found");
        check(own_Tree_cfind(tree, 50, &cfound) == OWN_OK && cfound == root, "cfind");
        check(own_Tree_cfind(tree, 51, &cfound) == OWN_OK && cfound == NULL, "cfind: not found");
        /* An owned pointer is the caller's: NULL when there is nothing, else to destroy. */
        check(own_Tree_items(tree, &count) == OWN_OK, "items");
        while (count > 0) {
            check(own_Tree_detach(tree, &detached) == OWN_OK && detached != NULL, "detach");
            own_Item_destroy(detached);
            check(own_Tree_items(tree, &count) == OWN_OK, "items");
        }
        check(own_Item_create(&raw, 20) == OWN_OK, "Item");
        detached = raw;
        check(own_Tree_detach(tree, &detached) == OWN_OK && detached == NULL, "nothing to detach");
        check(own_Tree_adoptRaw(tree, raw) == OWN_OK, "adoptRaw");
        check(own_Tree_adoptRaw(tree, NULL) == OWN_ERROR_NULL_ARGUMENT && mentions("'item' is NULL"),
              "a pointer that is taken is not nullable");
        check(own_Tree_detach(tree, &detached) == OWN_OK && detached == raw, "the same item");
        own_Item_destroy(detached);
        /* Pointers that may be NULL. */
        check(own_Tree_valueOf(tree, NULL, &out) == OWN_OK && out == -1, "NULL is allowed");
        check(own_Tree_valueOf(tree, root, &out) == OWN_OK && out == 50, "valueOf");
        check(own_Tree_both(tree, NULL, b, &out) == OWN_OK && out == 40, "both: first is NULL");
        check(own_Tree_both(tree, NULL, NULL, &out) == OWN_OK && out == 0, "both are NULL");
        check(own_Tree_both(tree, a, b, &out) == OWN_OK && out == 44, "both");
        check(own_Tree_labelLength(NULL, &out) == OWN_OK && out == -1, "a NULL string");
        check(own_Tree_labelLength("abc", &out) == OWN_OK && out == 3, "labelLength");
        /* The others are still checked. */
        check(own_Tree_sum(tree, NULL, b, &out) == OWN_ERROR_NULL_ARGUMENT, "a, not nullable");
        /* Bytes. */
        check(own_Tree_signature(tree, &data, &size) == OWN_OK && size == 3, "signature");
        check(memcmp(data, "a\0\xff", 3) == 0, "the bytes");
        own_free(data);
        /* What the C++ object keeps. */
        check(own_Tree_setCursor(tree, a) == OWN_OK, "setCursor");
        check(own_Tree_cursorValue(tree, &out) == OWN_OK && out == 4, "cursorValue");
        check(own_Tree_setSharedCursor(b) == OWN_OK, "setSharedCursor");
        check(own_Tree_sharedCursorValue(&out) == OWN_OK && out == 40, "sharedCursorValue");
        check(own_setGlobalNode(a) == OWN_OK, "setGlobalNode");
        check(own_globalNodeValue(&out) == OWN_OK && out == 4, "globalNodeValue");
        /* Not kept: the constructor only reads it. */
        {
            own_Peek* peek = NULL;
            check(own_Peek_create(&peek, a) == OWN_OK, "Peek");
            check(own_Peek_seen(peek, &out) == OWN_OK && out == 4, "seen");
            own_Peek_destroy(peek);
        }
        /* Nobody uses them any more. */
        check(own_Tree_setCursor(tree, NULL) == OWN_ERROR_NULL_ARGUMENT, "not nullable");
        check(own_Tree_setSharedCursor(NULL) == OWN_ERROR_NULL_ARGUMENT, "not nullable either");
        check(own_Tree_sharedCursorValue(&out) == OWN_OK, "sharedCursorValue");
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
