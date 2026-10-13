// SPDX-License-Identifier: Apache-2.0
#include "events.h"

#include <chrono>
#include <stdexcept>
#include <thread>
#include <utility>
#include <vector>

namespace ev {

namespace {

std::function<void(Level, const std::string&)> g_handler;

struct AtExit {
    bool enabled = false;
    ~AtExit()
    {
        if (enabled && g_handler) {
            g_handler(Level::Error, "exit");
        }
    }
};

AtExit g_at_exit;

std::function<std::int32_t(std::int32_t)> g_stored;

}  // namespace

std::int64_t sum(std::int32_t n, const std::function<std::int32_t(std::int32_t)>& f)
{
    std::int64_t total = 0;
    for (std::int32_t i = 0; i < n; ++i) {
        total += f(i);
    }
    return total;
}

std::int32_t count_if(std::int32_t n, std::function<bool(const std::int64_t&)> predicate)
{
    std::int32_t count = 0;
    for (std::int64_t i = 0; i < n; ++i) {
        count += predicate(i) ? 1 : 0;
    }
    return count;
}

bool is_empty(std::function<void()> f)
{
    return !f;
}

Level classify(std::int32_t value, std::function<Level(std::int32_t)> classifier)
{
    return classifier(value);
}

Mode pick(std::function<Mode()> chooser)
{
    return chooser();
}

double integrate(std::function<double(double)>&& f, double a, double b, std::int32_t steps)
{
    if (steps <= 0) {
        throw std::invalid_argument("steps must be positive");
    }
    double width = (b - a) / steps;
    double total = 0;
    for (std::int32_t i = 0; i < steps; ++i) {
        total += f(a + (i + 0.5) * width) * width;
    }
    return total;
}

double walk(std::function<double(const Point&, const char*)> visit)
{
    double first = visit(Point{1.5, 2.5}, "first");
    return first + visit(Point{3, 4}, nullptr);
}

void shift(std::function<void(Point, Mode)> sink)
{
    sink(Point{-1, 1}, Auto);
}

void strings(std::function<void(const std::string&, std::string, std::string_view)> f)
{
    std::string text("a\0b", 3);
    f(text, text, std::string_view(text));
}

void parallel(std::int32_t threads, std::int32_t n, std::function<void(std::int32_t)> f)
{
    std::vector<std::thread> workers;
    for (std::int32_t t = 0; t < threads; ++t) {
        workers.emplace_back([=]() {
            for (std::int32_t i = 0; i < n; ++i) {
                f(t);
            }
        });
    }
    for (auto& worker : workers) {
        worker.join();
    }
}

std::int32_t prefixed(const std::string& prefix,
                      const std::function<std::int32_t(const std::string&)>& f)
{
    return f(prefix + "!");
}

void store_and_sleep(std::function<std::int32_t(std::int32_t)> handler, std::int32_t ms)
{
    g_stored = std::move(handler);
    std::this_thread::sleep_for(std::chrono::milliseconds(ms));
}

std::int32_t fire(std::int32_t value)
{
    return g_stored ? g_stored(value) : -1;
}

void set_log_handler(std::function<void(Level, const std::string&)> handler)
{
    g_handler = std::move(handler);
    if (g_handler) {
        g_handler(Level::Info, "set");
    }
}

bool log(Level level, const std::string& message)
{
    if (!g_handler) {
        return false;
    }
    g_handler(level, message);
    return true;
}

void log_at_exit(bool enable)
{
    g_at_exit.enabled = enable;
}

Timer::Timer(std::function<void(std::int32_t)> on_tick) : on_tick_(std::move(on_tick)) {}

void Timer::set_on_tick(std::function<void(std::int32_t)> on_tick)
{
    on_tick_ = std::move(on_tick);
}

void Timer::tick(std::int32_t times)
{
    for (std::int32_t i = 0; i < times; ++i) {
        ++ticks_;
        if (on_tick_) {
            on_tick_(i);
        }
    }
}

std::int32_t Timer::ticks() const
{
    return ticks_;
}

std::int32_t Timer::each(const std::vector<std::int32_t>& values, std::function<void(std::int32_t)> f)
{
    for (std::int32_t value : values) {
        f(value);
    }
    return static_cast<std::int32_t>(values.size());
}

std::int32_t Timer::apply(std::int32_t value, std::function<std::int32_t(std::int32_t)> f)
{
    return f(value);
}

Watcher::Watcher(const std::string& name, const Point& at,
                 std::function<void(const std::string&, Point)> on_notify)
    : name_(name), at_(at), on_notify_(std::move(on_notify))
{
}

void Watcher::notify() const
{
    if (on_notify_) {
        on_notify_(name_, at_);
    }
}

}  // namespace ev
