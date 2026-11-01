// SPDX-License-Identifier: Apache-2.0
#include "tree.h"

#include <chrono>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>

namespace own {

Tree::Tree() : copies_(0), root_(0), count_(0) {}

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

void Tree::adopt(std::unique_ptr<Item> item)
{
    if (count_ == kMaxItems) {
        throw std::length_error("the tree is full");
    }
    items_[count_++] = std::move(item);
}

void Tree::adoptBoth(std::unique_ptr<Item> first, std::int32_t tag, std::unique_ptr<Item> second)
{
    // Both were taken by the call; the exception destroys them.
    if (tag < 0) {
        throw std::invalid_argument("tag must not be negative");
    }
    adopt(std::move(first));
    adopt(std::move(second));
}

void Tree::slowAdopt(std::unique_ptr<Item> item, std::int32_t milliseconds)
{
    std::this_thread::sleep_for(std::chrono::milliseconds(milliseconds));
    adopt(std::move(item));
}

Item& Tree::first()
{
    if (count_ == 0) {
        throw std::out_of_range("the tree has no items");
    }
    return *items_[0];
}

std::size_t Tree::items() const
{
    return count_;
}

std::int32_t Tree::itemSum() const
{
    std::int32_t sum = 0;
    for (std::size_t i = 0; i < count_; ++i) {
        sum += items_[i] ? items_[i]->id() : 0;
    }
    return sum;
}

Node* Tree::find(std::int32_t value)
{
    return root_.value() == value ? &root_ : nullptr;
}

const Node* Tree::cfind(std::int32_t value) const
{
    return root_.value() == value ? &root_ : nullptr;
}

Item* Tree::detach()
{
    if (count_ == 0) {
        return nullptr;
    }
    return items_[--count_].release();
}

void Tree::adoptRaw(Item* item)
{
    adopt(std::unique_ptr<Item>(item));
}

std::int32_t Tree::valueOf(const Node* node) const
{
    return node != nullptr ? node->value() : -1;
}

std::int32_t Tree::both(const Node* first, const Node* second) const
{
    return (first != nullptr ? first->value() : 0) + (second != nullptr ? second->value() : 0);
}

void Tree::setCursor(Node* node)
{
    cursor_ = node;
}

std::int32_t Tree::cursorValue() const
{
    return cursor_ != nullptr ? cursor_->value() : -1;
}

namespace {
const Node* g_shared = nullptr;
Node* g_global = nullptr;
}  // namespace

void Tree::setSharedCursor(const Node* node)
{
    g_shared = node;
}

std::int32_t Tree::sharedCursorValue()
{
    return g_shared != nullptr ? g_shared->value() : -1;
}

std::int32_t Tree::labelLength(const char* label)
{
    return label != nullptr ? static_cast<std::int32_t>(std::string(label).size()) : -1;
}

std::string Tree::signature() const
{
    return std::string("a\0\xff", 3);
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

Pipeline::Pipeline(std::unique_ptr<Item> first) : first_(std::move(first)) {}

Pipeline::Pipeline(std::unique_ptr<Item> first, std::int32_t minimum) : first_(std::move(first))
{
    if (first_->id() < minimum) {
        throw std::invalid_argument("the id is below the minimum");
    }
}

std::int32_t Pipeline::firstId() const
{
    return first_->id();
}

std::int32_t consume(std::unique_ptr<Item> item)
{
    return item->id();
}

Peek::Peek(const Node& node) : seen_(node.value()) {}

std::int32_t Peek::seen() const
{
    return seen_;
}

void setGlobalNode(Node* node)
{
    g_global = node;
}

std::int32_t globalNodeValue()
{
    return g_global != nullptr ? g_global->value() : -1;
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
