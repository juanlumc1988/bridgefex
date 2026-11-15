// SPDX-License-Identifier: Apache-2.0
// Callbacks run by threads that C++ starts, and the results that C++ gets from them.
#pragma once

#include <cstdint>
#include <functional>
#include <string>
#include <vector>

namespace thr {

// Starts a thread that waits delay_ms, calls f(value), destroys f and ends. Returns at once.
void spawn(std::function<void(std::int32_t)> f, std::int32_t value, std::int32_t delay_ms);
// The number of threads of spawn that have not finished.
std::int32_t running();
// Starts a thread that calls f(1) and then sleeps for a minute: it is still alive when the process ends.
void linger(std::function<void(std::int32_t)> f);

// Each one stores what the callback returned (the sentinel before the first call), and last_* gives it.
void run_byte(std::function<std::int8_t(std::int8_t)> f);
void run_float(std::function<float(float)> f);
void run_double(std::function<double(double)> f);
void run_long(std::function<std::int64_t(std::int64_t)> f);
void run_bool(std::function<bool(bool)> f);
std::int8_t last_byte();
float last_float();
double last_double();
std::int64_t last_long();
bool last_bool();

// A callback and a result that C++ allocates.
std::string describe(std::function<std::int32_t()> f);
std::vector<std::int32_t> collect(std::int32_t n, std::function<std::int32_t(std::int32_t)> f);

}  // namespace thr
