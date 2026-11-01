// SPDX-License-Identifier: Apache-2.0
#include "tree.h"

#include <chrono>
#include <stdexcept>
#include <thread>

namespace own {

Tree::Tree() : copies_(0), root_(0) {}

Tree::~Tree() = default;

std::int32_t Tree::sum(const Node& a, const Node& b) const
{
    return a.value() + b.value();
}

void Tree::bump(Node& node)
{
    node.setValue(node.value() + 1);
}

std::int32_t Tree::read(const Node* node) const
{
    return node != nullptr ? node->value() : -1;
}

void Tree::write(Node* node, std::int32_t value)
{
    if (node != nullptr) {
        node->setValue(value);
    }
}

void Tree::addCopy(Node node)
{
    // The copy is changed: the caller's node is not.
    node.setValue(node.value() + 1000);
    copies_ += node.value();
}

std::int32_t Tree::copies() const
{
    return copies_;
}

std::int32_t Tree::weight(const Node& node) const
{
    return node.value() * 1;
}

std::int32_t Tree::weight(Node& node)
{
    return node.value() * 2;
}

std::int32_t Tree::weight(const Node* node) const
{
    return node->value() * 3;
}

std::int32_t Tree::weight(Node* node)
{
    return node->value() * 4;
}

std::int32_t Tree::twice(const Node& node)
{
    return node.value() * 2;
}

Node Tree::makeNode(std::int32_t value) const
{
    return Node(value);
}

Node Tree::makeChecked(std::int32_t value) const
{
    if (value < 0) {
        throw std::invalid_argument("value must not be negative");
    }
    return Node(value);
}

Item Tree::makeItem(std::int32_t id) const
{
    return Item(id);
}

std::unique_ptr<Item> Tree::makeUnique(std::int32_t id, bool make) const
{
    return make ? std::unique_ptr<Item>(new Item(id)) : std::unique_ptr<Item>();
}

Node& Tree::root()
{
    return root_;
}

const Node& Tree::croot() const
{
    return root_;
}

Tree& Tree::global()
{
    static Tree instance;
    return instance;
}

std::unique_ptr<Item> Tree::slowMake(std::int32_t id, std::int32_t milliseconds) const
{
    std::this_thread::sleep_for(std::chrono::milliseconds(milliseconds));
    return std::unique_ptr<Item>(new Item(id));
}

Node Tree::slowNode(std::int32_t value, std::int32_t milliseconds) const
{
    std::this_thread::sleep_for(std::chrono::milliseconds(milliseconds));
    return Node(value);
}

Node twin(const Node& node)
{
    return node;
}

Node& pick(Node& first, Node& second, bool useSecond)
{
    return useSecond ? second : first;
}

const Node& same(const Node& node)
{
    return node;
}

Driver::Driver(Node& node) : node_(&node) {}

std::int32_t Driver::read() const
{
    return node_->value();
}

Pair::Pair(const Node* first, Node* second) : first_(first), second_(second) {}

std::int32_t Pair::sum() const
{
    return first_->value() + second_->value();
}

std::int32_t total(const Node& node, const Item& item)
{
    return node.value() + item.id();
}

std::int32_t idOf(const Item& item)
{
    return item.id();
}

}  // namespace own
