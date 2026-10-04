// SPDX-License-Identifier: Apache-2.0
#include "counter.h"

#include <cmath>
#include <limits>
#include <new>
#include <stdexcept>

namespace demo {

namespace {
std::size_t live_instances = 0;
}

Counter::Counter() : Counter(0) {}

Counter::Counter(std::int32_t start) : value_(start), limit_(std::numeric_limits<std::int32_t>::max())
{
    ++live_instances;
}

Counter::~Counter()
{
    --live_instances;
}

void Counter::increment()
{
    if (value_ >= limit_) {
        throw std::overflow_error("counter limit reached");
    }
    ++value_;
}

void Counter::add(std::int32_t delta)
{
    value_ += delta;
}

void Counter::add(double delta)
{
    value_ += static_cast<std::int32_t>(std::lround(delta));
}

std::int32_t Counter::value() const
{
    return value_;
}

bool Counter::isZero() const
{
    return value_ == 0;
}

void Counter::setLimit(std::int64_t limit)
{
    if (limit < 0) {
        throw std::invalid_argument("limit must not be negative");
    }
    limit_ = limit;
}

std::int64_t Counter::limit() const
{
    return limit_;
}

void Counter::reserve(std::size_t bytes)
{
    if (bytes > static_cast<std::size_t>(limit_)) {
        throw std::bad_alloc();
    }
}

void Counter::fail() const
{
    throw 42;
}

std::size_t Counter::liveInstances()
{
    return live_instances;
}

std::int64_t multiply(std::int32_t a, std::int32_t b)
{
    return static_cast<std::int64_t>(a) * b;
}

}  // namespace demo
