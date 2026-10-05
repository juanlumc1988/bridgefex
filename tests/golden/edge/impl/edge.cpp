// SPDX-License-Identifier: Apache-2.0
#include "edge.h"

#include <stdexcept>

namespace edge {

namespace {
int live_count = 0;
}

int add(int a, int b)
{
    return a + b;
}

std::int64_t combine(std::int32_t int32_t, std::size_t size_t, int EDGE_OK)
{
    return int32_t * 100 + static_cast<std::int64_t>(size_t) * 10 + EDGE_OK;
}

int getattr(int value)
{
    return value + 1;
}

int threading()
{
    return 4;
}

Registry::Registry()
{
    ++live_count;
}

Registry::~Registry() noexcept(false)
{
    --live_count;
    if (throw_on_destroy_) {
        throw std::runtime_error("destructor failed");
    }
}

int Registry::classmethod() const
{
    return hook();
}

int Registry::staticmethod() const
{
    return 2;
}

void Registry::throwOnDestroy(bool enabled)
{
    throw_on_destroy_ = enabled;
}

void Registry::fail() const
{
    throw std::runtime_error("failed on purpose");
}

int Registry::liveCount()
{
    return live_count;
}

int Registry::hook() const
{
    return 1;
}

}  // namespace edge
