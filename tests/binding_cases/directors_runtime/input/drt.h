// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <cstdint>
#include <functional>
#include <memory>

namespace drt {

// n comes before u8: a result whose u8 is out of range is written up to there if it is written member
// by member.
struct Pad {
    std::int32_t n;
    std::uint8_t u8;
};

class Item {
public:
    Item();
    explicit Item(std::int32_t id);
    virtual ~Item();

    virtual std::int32_t value() const;
    virtual std::int32_t pure() const = 0;
    virtual std::int32_t adopt(std::unique_ptr<Item> item) const;
    virtual Pad pad() const;

    std::int32_t id() const;
    // value() and then adopt(item): if the override of the first fails, the second one never runs.
    std::int32_t adopt_after(std::unique_ptr<Item> item) const;
    // Keeps what pad() gives back: Pad{} if the override fails.
    void store_pad();
    Pad stored_pad() const;
    static std::int32_t alive();

private:
    std::int32_t id_;
    Pad stored_;
};

// An item that is not a director.
std::unique_ptr<Item> make_plain(std::int32_t id);

// A thread of C++ owns the item in a thread_local that it makes before it first calls Java, calls
// its value(), and ends: the item dies with the thread, after the thread was detached from the JVM.
std::int32_t tl_run(std::unique_ptr<Item> item);

// Calls `before` and then item.value().
std::int32_t call_after(const Item& item, const std::function<void()>& before);

}  // namespace drt
