// SPDX-License-Identifier: Apache-2.0
// Wrapped classes: the class that the other header uses.
#pragma once

#include <cstddef>
#include <cstdint>

namespace own {

// Copyable.
class Node {
public:
    explicit Node(std::int32_t value);
    Node(const Node& other);
    ~Node();
    std::int32_t value() const;
    void setValue(std::int32_t value);
    // The number of Node objects that exist.
    static std::size_t live();
    // The object itself, to chain borrowed objects.
    Node& me();
    const Node& cme() const;

private:
    std::int32_t value_;
};

// Move-only.
class Item {
public:
    explicit Item(std::int32_t id);
    Item(const Item&) = delete;
    Item(Item&& other) noexcept;
    ~Item();
    std::int32_t id() const;
    static std::size_t live();

private:
    std::int32_t id_;
};

}  // namespace own
