// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>

namespace cb {

// Calls f once. The text is a parameter after the callback.
void run(std::function<void()> f, const std::string& text);

// Calls f, then fails.
void call_then_throw(std::function<void()> f);

// Keeps f, then fails: C++ holds the callback all the same.
void keep_and_throw(std::function<std::int32_t(std::int32_t)> f);
// The kept callback's result for value, or -1.
std::int32_t kept(std::int32_t value);
void drop_kept();

// start() keeps f and returns; go() lets a C++ thread call f(1); join() waits for it.
void start(std::function<void(std::int32_t)> f);
void go();
void join();

// Every kind of scalar goes through a callback, in both directions.
double scalars(std::function<double(std::int8_t, std::uint8_t, std::int16_t, std::uint16_t,
                                    std::int32_t, std::uint32_t, std::int64_t, std::uint64_t,
                                    float, double, bool, long, unsigned long, std::size_t)> f);
long long_back(std::function<long(long)> f, long value);
unsigned long ulong_back(std::function<unsigned long(unsigned long)> f, unsigned long value);
bool flip(std::function<bool(bool)> f, bool value);
std::uint8_t narrow(std::function<std::uint8_t(std::int16_t)> f, std::int16_t value);

// The widest delegate of .NET.
std::int32_t sixteen(std::function<std::int32_t(
                         std::int32_t, std::int32_t, std::int32_t, std::int32_t, std::int32_t,
                         std::int32_t, std::int32_t, std::int32_t, std::int32_t, std::int32_t,
                         std::int32_t, std::int32_t, std::int32_t, std::int32_t, std::int32_t,
                         std::int32_t)> f);

// Text that is not UTF-8 goes to the callback.
void bad_text(std::function<void(const std::string&)> f);
void bad_c_text(std::function<void(const char*)> f);

// A callback that calls this function again, n levels deep.
std::int32_t nest(std::int32_t n, std::function<std::int32_t(std::int32_t)> f);

}  // namespace cb
