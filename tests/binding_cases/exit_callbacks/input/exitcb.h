// SPDX-License-Identifier: Apache-2.0
// A callback that a C++ global holds, and calls from its destructor when the process exits.
#pragma once

#include <cstdint>
#include <functional>

namespace exitcb {

// Keeps handler in a global. When the process exits, the destructor of the global calls it with 1
// (mode 1: on the thread that exits) or with 2 (mode 2: on a thread that it starts and joins), and
// writes "exitcb: destructor" before and "exitcb: done" after to the standard error. Mode 0 does not
// call it.
void keep(std::function<void(std::int32_t)> handler, std::int32_t mode);

// The handler, called now with value.
void call(std::int32_t value);

}  // namespace exitcb
