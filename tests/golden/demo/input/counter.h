// SPDX-License-Identifier: Apache-2.0
// Test input: a class in a namespace, overloads and exceptions.
#pragma once

#include <cstddef>
#include <cstdint>

namespace demo {

// Counts up to a limit.
class Counter {
public:
    Counter();
    explicit Counter(std::int32_t start);
    Counter(const Counter&) = delete;
    Counter& operator=(const Counter&) = delete;
    ~Counter();

    // Throws std::overflow_error when the limit is reached.
    void increment();
    void add(std::int32_t delta);
    // Adds the rounded value.
    void add(double delta);
    std::int32_t value() const;
    bool isZero() const;
    // Throws std::invalid_argument if limit is negative.
    void setLimit(std::int64_t limit);
    std::int64_t limit() const;
    // Throws std::bad_alloc if bytes is larger than the limit.
    void reserve(std::size_t bytes);
    // Throws an exception that is not a std::exception.
    void fail() const;

    static std::size_t liveInstances();

private:
    std::int32_t value_;
    std::int64_t limit_;
};

std::int64_t multiply(std::int32_t a, std::int32_t b);

}  // namespace demo
