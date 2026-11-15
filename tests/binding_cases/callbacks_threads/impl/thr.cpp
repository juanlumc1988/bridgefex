// SPDX-License-Identifier: Apache-2.0
#include "thr.h"

#include <atomic>
#include <chrono>
#include <thread>
#include <utility>

namespace thr {

namespace {

std::atomic<std::int32_t> g_running{0};

std::int8_t g_byte = 99;
float g_float = 99.0F;
double g_double = 99.0;
std::int64_t g_long = 99;
bool g_bool = true;

}  // namespace

void spawn(std::function<void(std::int32_t)> f, std::int32_t value, std::int32_t delay_ms)
{
    ++g_running;
    std::thread([f = std::move(f), value, delay_ms]() mutable {
        std::this_thread::sleep_for(std::chrono::milliseconds(delay_ms));
        f(value);
        f = nullptr;  // destroyed (and released) on this thread
        --g_running;
    }).detach();
}

std::int32_t running()
{
    return g_running.load();
}

void linger(std::function<void(std::int32_t)> f)
{
    std::thread([f = std::move(f)] {
        f(1);
        std::this_thread::sleep_for(std::chrono::seconds(60));
    }).detach();
}

void run_byte(std::function<std::int8_t(std::int8_t)> f)
{
    g_byte = f(-5);
}

void run_float(std::function<float(float)> f)
{
    g_float = f(1.5F);
}

void run_double(std::function<double(double)> f)
{
    g_double = f(2.5);
}

void run_long(std::function<std::int64_t(std::int64_t)> f)
{
    g_long = f(std::int64_t{1} << 40);
}

void run_bool(std::function<bool(bool)> f)
{
    g_bool = f(true);
}

std::int8_t last_byte()
{
    return g_byte;
}

float last_float()
{
    return g_float;
}

double last_double()
{
    return g_double;
}

std::int64_t last_long()
{
    return g_long;
}

bool last_bool()
{
    return g_bool;
}

std::string describe(std::function<std::int32_t()> f)
{
    return "value=" + std::to_string(f());
}

std::vector<std::int32_t> collect(std::int32_t n, std::function<std::int32_t(std::int32_t)> f)
{
    std::vector<std::int32_t> values;
    for (std::int32_t i = 0; i < n; ++i) {
        values.push_back(f(i));
    }
    return values;
}

}  // namespace thr
