// SPDX-License-Identifier: Apache-2.0
#include "sleeper.h"

#include <atomic>
#include <chrono>
#include <thread>

namespace demo {

namespace {
std::atomic<std::size_t> live_instances{0};
}

Sleeper::Sleeper(std::uint32_t milliseconds)
{
    nap(milliseconds);
    ++live_instances;
}

Sleeper::~Sleeper()
{
    --live_instances;
}

void Sleeper::nap(std::uint32_t milliseconds) const
{
    std::this_thread::sleep_for(std::chrono::milliseconds(milliseconds));
}

std::size_t Sleeper::liveInstances()
{
    return live_instances;
}

}  // namespace demo
