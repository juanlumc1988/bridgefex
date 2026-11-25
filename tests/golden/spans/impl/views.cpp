// SPDX-License-Identifier: Apache-2.0
#include "views.h"

#include <cstring>

namespace views {

std::int64_t sum(std::span<const std::int16_t> values)
{
    std::int64_t total = 0;
    for (std::int16_t value : values) {
        total += value;
    }
    return total;
}

std::span<const std::int32_t> numbers()
{
    static const std::int32_t values[] = {-1, 2, -3};
    return values;
}

int count_red(std::span<const Color> colors)
{
    int count = 0;
    for (Color color : colors) {
        count += color == Color::Red ? 1 : 0;
    }
    return count;
}

double sum_x(std::span<const Point> points)
{
    double total = 0;
    for (const Point& point : points) {
        total += point.x;
    }
    return total;
}

std::size_t total(std::span<const std::string, std::dynamic_extent> words)
{
    std::size_t size = 0;
    for (const std::string& word : words) {
        size += word.size();
    }
    return size;
}

namespace {

std::uint32_t hash(const void* data, std::size_t bytes)
{
    const unsigned char* p = static_cast<const unsigned char*>(data);
    std::uint32_t sum = 2166136261u;
    for (std::size_t i = 0; i < bytes; ++i) {
        sum = (sum ^ p[i]) * 16777619u;
    }
    return sum;
}

enum { kFirst, kInts, kDoubles, kFlags, kLongs };

const void* g_data = nullptr;
std::size_t g_bytes = 0;
std::uint32_t g_sum = 0;
std::int32_t g_bad = 0;

}  // namespace

Ledger::Ledger(std::span<const std::int32_t> first) : slots_{}, copy_()
{
    add(first);
    slots_[kFirst] = slots_[kInts];
    slots_[kInts] = Slot{};
}

Ledger::~Ledger()
{
    // What it points to is destroyed after it: the destructor reads it.
    if (!intact()) {
        ++g_bad;
    }
}

template <typename T>
void Ledger::store(Slot& slot, std::span<const T> values)
{
    slot.data = values.data();
    slot.bytes = values.size_bytes();
    slot.sum = hash(values.data(), values.size_bytes());
    slot.count = values.size();
}

void Ledger::add(std::span<const std::int32_t> values) { store(slots_[kInts], values); }
void Ledger::addDoubles(std::span<const double> values) { store(slots_[kDoubles], values); }
void Ledger::addFlags(std::span<const bool> values) { store(slots_[kFlags], values); }
void Ledger::addLongs(std::span<const long> values) { store(slots_[kLongs], values); }
void Ledger::addCopy(const std::vector<std::int32_t>& values) { copy_ = values; }

bool Ledger::intact() const
{
    for (const Slot& slot : slots_) {
        if (slot.data != nullptr && hash(slot.data, slot.bytes) != slot.sum) {
            return false;
        }
    }
    return true;
}

std::int64_t Ledger::total() const
{
    std::int64_t total = 0;
    for (int index : {kFirst, kInts}) {
        const Slot& slot = slots_[index];
        const std::int32_t* numbers = static_cast<const std::int32_t*>(slot.data);
        for (std::size_t i = 0; i < slot.count; ++i) {
            total += numbers[i];
        }
    }
    return total;
}

std::int32_t Ledger::kept() const
{
    std::size_t total = 0;
    for (const Slot& slot : slots_) {
        total += slot.count;
    }
    return static_cast<std::int32_t>(total);
}

void Ledger::pin(std::span<const std::int32_t> values)
{
    g_data = values.data();
    g_bytes = values.size_bytes();
    g_sum = hash(values.data(), values.size_bytes());
}

bool Ledger::keptIntact() { return g_data == nullptr || hash(g_data, g_bytes) == g_sum; }

void Ledger::scribble()
{
    std::vector<char*> blocks;
    for (std::size_t size = 1; size < 600; ++size) {
        for (int copy = 0; copy < 8; ++copy) {
            char* block = new char[size];
            std::memset(block, 0xEE, size);
            blocks.push_back(block);
        }
    }
    for (char* block : blocks) {
        delete[] block;
    }
}

std::int32_t Ledger::badDestructions() { return g_bad; }

}  // namespace views
