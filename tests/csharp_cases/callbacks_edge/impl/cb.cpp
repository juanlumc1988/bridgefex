// SPDX-License-Identifier: Apache-2.0
#include "cb.h"

#include <atomic>
#include <chrono>
#include <stdexcept>
#include <thread>
#include <utility>

namespace cb {

namespace {

std::function<std::int32_t(std::int32_t)> g_kept;
std::thread g_thread;
std::atomic<bool> g_go{false};

}  // namespace

void run(std::function<void()> f, const std::string& text)
{
    if (text == "fail") {
        throw std::runtime_error("run failed");
    }
    f();
}

void call_then_throw(std::function<void()> f)
{
    f();
    throw std::runtime_error("after the callback");
}

void keep_and_throw(std::function<std::int32_t(std::int32_t)> f)
{
    g_kept = std::move(f);
    throw std::runtime_error("kept and failed");
}

std::int32_t kept(std::int32_t value)
{
    return g_kept ? g_kept(value) : -1;
}

void drop_kept()
{
    g_kept = nullptr;
}

void start(std::function<void(std::int32_t)> f)
{
    if (g_thread.joinable()) {
        g_thread.join();
    }
    g_go = false;
    g_thread = std::thread([f]() {
        while (!g_go) {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        f(1);
    });
}

void go()
{
    g_go = true;
}

void join()
{
    g_go = true;
    if (g_thread.joinable()) {
        g_thread.join();
    }
}

double scalars(std::function<double(std::int8_t, std::uint8_t, std::int16_t, std::uint16_t,
                                    std::int32_t, std::uint32_t, std::int64_t, std::uint64_t,
                                    float, double, bool, long, unsigned long, std::size_t)> f)
{
    return f(-1, 200, -300, 60000, -70000, 4000000000u, -5000000000ll, 18000000000000000000ull,
             1.5f, 2.25, true, -7, 7ul, 123456);
}

long long_back(std::function<long(long)> f, long value)
{
    return f(value);
}

unsigned long ulong_back(std::function<unsigned long(unsigned long)> f, unsigned long value)
{
    return f(value);
}

bool flip(std::function<bool(bool)> f, bool value)
{
    return f(value);
}

std::uint8_t narrow(std::function<std::uint8_t(std::int16_t)> f, std::int16_t value)
{
    return f(value);
}

std::int32_t sixteen(std::function<std::int32_t(
                         std::int32_t, std::int32_t, std::int32_t, std::int32_t, std::int32_t,
                         std::int32_t, std::int32_t, std::int32_t, std::int32_t, std::int32_t,
                         std::int32_t, std::int32_t, std::int32_t, std::int32_t, std::int32_t,
                         std::int32_t)> f)
{
    return f(1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16);
}

void bad_text(std::function<void(const std::string&)> f)
{
    f(std::string("a\xff"
                  "b"));
}

void bad_c_text(std::function<void(const char*)> f)
{
    f("a\xff"
      "b");
}

std::int32_t nest(std::int32_t n, std::function<std::int32_t(std::int32_t)> f)
{
    return f(n);
}

std::string big(std::function<std::int32_t()> f)
{
    f();
    return std::string(1 << 20, 'x');
}

std::vector<std::int32_t> numbers(std::function<void()> f)
{
    f();
    return std::vector<std::int32_t>(1 << 18, 7);
}

std::vector<std::string> bad_strings(std::int32_t megabytes)
{
    return {std::string(static_cast<std::size_t>(megabytes) << 20, 'a'), std::string("ab\xff")};
}

std::vector<long> longs(const std::vector<long>& values)
{
    return {values.rbegin(), values.rend()};
}

std::vector<unsigned long> ulongs(const std::vector<unsigned long>& values)
{
    return {values.rbegin(), values.rend()};
}

std::vector<LongE> long_enums(const std::vector<LongE>& values)
{
    return {values.rbegin(), values.rend()};
}

std::array<long, 2> two(const std::array<long, 2>& values)
{
    return {values[1], values[0]};
}

LongE long_enum(std::function<LongE(LongE)> f, LongE value)
{
    return f(value);
}

}  // namespace cb
