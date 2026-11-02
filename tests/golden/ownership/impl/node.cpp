// SPDX-License-Identifier: Apache-2.0
#include "node.h"

namespace own {

namespace {
std::size_t g_nodes = 0;
std::size_t g_items = 0;
}  // namespace

Node::Node(std::int32_t value) : value_(value)
{
    ++g_nodes;
}

Node::Node(const Node& other) : value_(other.value_)
{
    ++g_nodes;
}

Node::~Node()
{
    --g_nodes;
}

std::int32_t Node::value() const
{
    return value_;
}

void Node::setValue(std::int32_t value)
{
    value_ = value;
}

std::size_t Node::live()
{
    return g_nodes;
}

Node& Node::me()
{
    return *this;
}

const Node& Node::cme() const
{
    return *this;
}

Item::Item(std::int32_t id) : id_(id)
{
    ++g_items;
}

Item::Item(Item&& other) noexcept : id_(other.id_)
{
    ++g_items;
}

Item::~Item()
{
    --g_items;
}

std::int32_t Item::id() const
{
    return id_;
}

std::size_t Item::live()
{
    return g_items;
}

}  // namespace own
