// SPDX-License-Identifier: Apache-2.0
#include "dob.h"

#include <thread>
#include <utility>

namespace dob {

namespace {
std::int32_t g_alive = 0;
}

Node::Node(std::int32_t value) : value_(value) { ++g_alive; }
Node::~Node() { --g_alive; }
std::int32_t Node::value() const { return value_; }
std::int32_t Node::call_value() const { return value(); }
void Node::set(std::int32_t value) { value_ = value; }
std::int32_t Node::alive() { return g_alive; }

Plain::Plain(std::int32_t value) : value_(value) {}
Plain::~Plain() = default;
std::int32_t Plain::value() const { return value_; }

Derived::Derived(std::int32_t value) : Plain(value) {}

Visitor::Visitor() = default;
Visitor::~Visitor() = default;

std::int32_t Visitor::seen(const Node& node) const { return node.value(); }

std::int32_t Visitor::touch(Node& node) const
{
    node.set(node.value() + 1);
    return node.value();
}

std::int32_t Visitor::pointer(const Node* node) const { return node == nullptr ? -1 : node->value(); }

std::int32_t Visitor::take(std::unique_ptr<Node> node) const { return node ? node->value() : -1; }

std::unique_ptr<Node> Visitor::make(std::int32_t value) const
{
    return std::unique_ptr<Node>(new Node(value));
}

std::unique_ptr<Plain> Visitor::make_plain(std::int32_t value) const
{
    return std::unique_ptr<Plain>(new Plain(value));
}

std::unique_ptr<Node> Visitor::pass(const Node& node) const
{
    return std::unique_ptr<Node>(new Node(node.value()));
}

std::int32_t Visitor::touch_pointer(Node* node) const
{
    node->set(node->value() + 10);
    return node->value();
}

std::int32_t Visitor::both(std::unique_ptr<Node> a, const Node& b, std::unique_ptr<Node> c) const
{
    return (a ? a->value() : 0) + b.value() * 10 + (c ? c->value() : 0) * 100;
}

std::int32_t Visitor::marker() const { return 0; }

bool Visitor::same(const Visitor* other) const { return other == this; }

std::int32_t Visitor::call_seen(const Node& node) const { return seen(node); }
std::int32_t Visitor::call_touch(Node& node) const { return touch(node); }
std::int32_t Visitor::call_pointer(const Node* node) const { return pointer(node); }
std::int32_t Visitor::call_take(std::unique_ptr<Node> node) const { return take(std::move(node)); }
std::unique_ptr<Node> Visitor::call_make(std::int32_t value) const { return make(value); }
std::unique_ptr<Plain> Visitor::call_make_plain(std::int32_t value) const { return make_plain(value); }

std::unique_ptr<Node> Visitor::call_pass(const Node& node) const { return pass(node); }
std::int32_t Visitor::call_touch_pointer(Node* node) const { return touch_pointer(node); }

std::int32_t Visitor::call_both(std::unique_ptr<Node> a, const Node& b, std::unique_ptr<Node> c) const
{
    return both(std::move(a), b, std::move(c));
}

bool Visitor::call_same(const Visitor* other) const { return same(other); }

std::int32_t Visitor::call_seen_then_value(const Node& node) const
{
    seen(node);
    return node.value();
}

std::int32_t Visitor::call_marker_then_take(std::unique_ptr<Node> node) const
{
    std::int32_t first = marker();
    return take(std::move(node)) + first;
}

std::int32_t Visitor::call_seen_on_thread(const Node& node) const
{
    std::int32_t result = 0;
    std::thread worker([&] { result = seen(node); });
    worker.join();
    return result;
}

Keeper::Keeper() = default;
Keeper::~Keeper() = default;
void Keeper::put(std::unique_ptr<Visitor> visitor) { visitor_ = std::move(visitor); }
std::unique_ptr<Visitor> Keeper::release() { return std::move(visitor_); }
bool Keeper::ask_same() const { return visitor_ && visitor_->same(visitor_.get()); }
std::int32_t Keeper::ask_seen(const Node& node) const { return visitor_ ? visitor_->seen(node) : -1; }

std::int32_t run(const std::function<std::int32_t(std::int32_t)>& f, const std::string& label)
{
    return f(static_cast<std::int32_t>(label.size()));
}

}  // namespace dob
