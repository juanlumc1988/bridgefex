// SPDX-License-Identifier: Apache-2.0
#include "drt.h"

#include <atomic>
#include <thread>
#include <utility>

namespace drt {

namespace {
std::atomic<std::int32_t> g_alive{0};
thread_local std::unique_ptr<Item> t_item;

class Plain final : public Item {
public:
    explicit Plain(std::int32_t id) : Item(id) {}
    std::int32_t pure() const override { return 0; }
};
}  // namespace

Item::Item() : id_(0), stored_{0, 0} { ++g_alive; }
Item::Item(std::int32_t id) : id_(id), stored_{0, 0} { ++g_alive; }
Item::~Item() { --g_alive; }

std::int32_t Item::value() const { return id_; }
std::int32_t Item::adopt(std::unique_ptr<Item> item) const { return item ? item->id() : -1; }
Pad Item::pad() const { return Pad{1, 2}; }
std::int32_t Item::id() const { return id_; }

std::int32_t Item::adopt_after(std::unique_ptr<Item> item) const
{
    const std::int32_t first = value();
    return first + adopt(std::move(item));
}

void Item::store_pad() { stored_ = pad(); }
Pad Item::stored_pad() const { return stored_; }
std::int32_t Item::alive() { return g_alive.load(); }

std::unique_ptr<Item> make_plain(std::int32_t id) { return std::make_unique<Plain>(id); }

std::int32_t tl_run(std::unique_ptr<Item> item)
{
    std::int32_t result = -1;
    Item* raw = item.release();
    std::thread thread([&result, raw] {
        t_item.reset(raw);
        result = t_item->value();
    });
    thread.join();
    return result;
}

std::int32_t call_after(const Item& item, const std::function<void()>& before)
{
    before();
    return item.value();
}

}  // namespace drt
