// SPDX-License-Identifier: Apache-2.0
// Wrapped classes as parameters: references, pointers and copies, also of a
// class of another header.
#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>

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
    std::int32_t copies_;
    Node root_;
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

// A free function that takes both classes.
std::int32_t total(const Node& node, const Item& item);

// The Item is copied: a move-only class by value does not compile.
std::int32_t idOf(const Item& item);

}  // namespace own
