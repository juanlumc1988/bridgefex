// SPDX-License-Identifier: Apache-2.0
// Test input: calls that last long enough for a signal to arrive during them.
#pragma once

#include <cstddef>
#include <cstdint>

namespace demo {

class Sleeper {
public:
    // Sleeps, then counts the new instance.
    explicit Sleeper(std::uint32_t milliseconds);
    Sleeper(const Sleeper&) = delete;
    Sleeper& operator=(const Sleeper&) = delete;
    ~Sleeper();

    void nap(std::uint32_t milliseconds) const;

    static std::size_t liveInstances();
};

}  // namespace demo
