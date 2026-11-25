// SPDX-License-Identifier: Apache-2.0
// std::span<const T> across the C boundary (C++20).
#pragma once

#include <cstddef>
#include <cstdint>
#include <span>
#include <string>
#include <vector>

namespace views {

enum class Color : std::uint8_t { Red, Green };

struct Point {
    double x;
    double y;
};

std::int64_t sum(std::span<const std::int16_t> values);
std::span<const std::int32_t> numbers();
int count_red(std::span<const Color> colors);
double sum_x(std::span<const Point> points);
std::size_t total(std::span<const std::string, std::dynamic_extent> words);

// It stores the spans that it is given, without copying them: the numbers have to be where they
// were as long as it lives (the annotations say so with retain).
class Ledger {
public:
    explicit Ledger(std::span<const std::int32_t> first);
    ~Ledger();
    Ledger(const Ledger&) = delete;
    Ledger& operator=(const Ledger&) = delete;

    void add(std::span<const std::int32_t> values);
    void addDoubles(std::span<const double> values);
    void addFlags(std::span<const bool> values);
    void addLongs(std::span<const long> values);
    // A copy: there is nothing to keep.
    void addCopy(const std::vector<std::int32_t>& values);
    // Whether every span that it keeps still has the numbers that it was given (memory that is
    // gone or reused is not).
    bool intact() const;
    // The sum of the int32_t that it keeps (it reads them).
    std::int64_t total() const;
    // The number of elements that it keeps in all.
    std::int32_t kept() const;
    // The span that is kept for ever (see keptIntact).
    static void pin(std::span<const std::int32_t> values);
    static bool keptIntact();
    // A helper for the tests: allocates blocks of many sizes, fills them and frees them, so that
    // memory that was freed is reused and overwritten before something reads it.
    static void scribble();
    // The number of Ledger objects that found, when they were destroyed, that what they point to
    // was gone or changed (their destructors read it).
    static std::int32_t badDestructions();

private:
    struct Slot {
        const void* data;
        std::size_t bytes;
        std::uint32_t sum;
        std::size_t count;
    };
    template <typename T>
    static void store(Slot& slot, std::span<const T> values);
    Slot slots_[5];
    std::vector<std::int32_t> copy_;
};

}  // namespace views
