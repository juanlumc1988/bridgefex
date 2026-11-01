// SPDX-License-Identifier: Apache-2.0
// Wrapped classes as parameters: references, pointers and copies, also of a
// class of another header.
#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>

#include "node.h"

namespace own {

class Tree {
public:
    Tree();
    ~Tree();

    // Sums the values of both nodes.
    std::int32_t sum(const Node& a, const Node& b) const;
    // Adds one to its value.
    void bump(Node& node);
    // The value of the node, or -1 if there is none.
    std::int32_t read(const Node* node) const;
    // Sets the value of the node.
    void write(Node* node, std::int32_t value);
    // Copies the node (the caller keeps its own).
    void addCopy(Node node);
    // The sum of the copies.
    std::int32_t copies() const;
    // Takes the item: the tree owns it from now on.
    void adopt(std::unique_ptr<Item> item);
    // Throws std::invalid_argument if tag is negative, after both items were
    // taken: they are destroyed anyway.
    void adoptBoth(std::unique_ptr<Item> first, std::int32_t tag, std::unique_ptr<Item> second);
    // Sleeps, then takes the item (for the KeyboardInterrupt tests).
    void slowAdopt(std::unique_ptr<Item> item, std::int32_t milliseconds);
    // The first item, which the tree keeps owning. Throws std::out_of_range if
    // there is none.
    Item& first();
    // The number of items, and the sum of their ids.
    std::size_t items() const;
    std::int32_t itemSum() const;
    // Pointers whose ownership the header does not say (annotations.toml does).
    // A node of the tree, or NULL: the tree keeps owning it.
    Node* find(std::int32_t value);
    const Node* cfind(std::int32_t value) const;
    // The last item, which the caller owns from now on; NULL if there is none.
    Item* detach();
    // Takes the item (a raw pointer).
    void adoptRaw(Item* item);
    // The value of the node, or -1 if there is none.
    std::int32_t valueOf(const Node* node) const;
    // The sum of two nodes, either of which may be NULL.
    std::int32_t both(const Node* first, const Node* second) const;
    // Keeps the pointer: the node must outlive its use.
    void setCursor(Node* node);
    std::int32_t cursorValue() const;
    // Keeps the pointer for every tree.
    static void setSharedCursor(const Node* node);
    static std::int32_t sharedCursorValue();
    // The length of the label, or -1 if there is none.
    static std::int32_t labelLength(const char* label);
    // Binary: the bytes 'a', 0, 0xff.
    std::string signature() const;
    // Overloads that differ in the way they take the node.
    std::int32_t weight(const Node& node) const;
    std::int32_t weight(Node& node);
    std::int32_t weight(const Node* node) const;
    std::int32_t weight(Node* node);
    static std::int32_t twice(const Node& node);

    // Results by value: a new object that the caller owns.
    Node makeNode(std::int32_t value) const;
    // Throws std::invalid_argument if value is negative.
    Node makeChecked(std::int32_t value) const;
    Item makeItem(std::int32_t id) const;
    // In a std::unique_ptr; empty unless make is true.
    std::unique_ptr<Item> makeUnique(std::int32_t id, bool make) const;
    // Results by reference: the tree keeps owning them.
    Node& root();
    const Node& croot() const;
    // Owned by nobody in particular: it lives until the program ends.
    static Tree& global();
    // Sleep for some milliseconds, then return a new object (for the
    // KeyboardInterrupt tests).
    std::unique_ptr<Item> slowMake(std::int32_t id, std::int32_t milliseconds) const;
    Node slowNode(std::int32_t value, std::int32_t milliseconds) const;

private:
    static constexpr std::size_t kMaxItems = 256;
    std::int32_t copies_;
    Node root_;
    std::unique_ptr<Item> items_[kMaxItems];
    std::size_t count_;
    Node* cursor_ = nullptr;
};

// Keeps a reference to its node: the node must outlive it.
class Driver {
public:
    explicit Driver(Node& node);
    std::int32_t read() const;

private:
    Node* node_;
};

// Keeps two pointers.
class Pair {
public:
    Pair(const Node* first, Node* second);
    std::int32_t sum() const;

private:
    const Node* first_;
    Node* second_;
};

// A copy of the node.
Node twin(const Node& node);
// The argument itself, or the other one: borrowed from the arguments.
Node& pick(Node& first, Node& second, bool useSecond);
const Node& same(const Node& node);

// Takes ownership of its first item.
class Pipeline {
public:
    explicit Pipeline(std::unique_ptr<Item> first);
    // Throws std::invalid_argument if the item is not positive: the item is
    // taken anyway.
    Pipeline(std::unique_ptr<Item> first, std::int32_t minimum);
    std::int32_t firstId() const;

private:
    std::unique_ptr<Item> first_;
};

// Takes the item, and returns its id.
std::int32_t consume(std::unique_ptr<Item> item);

// Only reads the node while it is built.
class Peek {
public:
    explicit Peek(const Node& node);
    std::int32_t seen() const;

private:
    std::int32_t seen_;
};

// Keeps the pointer in a global: the node must outlive every call.
void setGlobalNode(Node* node);
std::int32_t globalNodeValue();

// A free function that takes both classes.
std::int32_t total(const Node& node, const Item& item);

// The Item is copied: a move-only class by value does not compile.
std::int32_t idOf(const Item& item);

}  // namespace own
