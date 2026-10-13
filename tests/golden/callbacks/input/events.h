// SPDX-License-Identifier: Apache-2.0
// std::function parameters: C callbacks with a user_data and a release function.
#pragma once

#include <cstdint>
#include <functional>
#include <string>
#include <string_view>
#include <vector>

namespace ev {

enum class Level : std::uint8_t { Debug, Info, Error };
// Without a fixed underlying type: only 0..3 are values.
enum Mode { Off, On, Auto };

struct Point {
    double x;
    double y;
};

// Synchronous: f(0) + ... + f(n - 1).
std::int64_t sum(std::int32_t n, const std::function<std::int32_t(std::int32_t)>& f);
// The number of i in [0, n) for which predicate(i) holds.
std::int32_t count_if(std::int32_t n, std::function<bool(const std::int64_t&)> predicate);
// True if the std::function it gets is empty.
bool is_empty(std::function<void()> f);
Level classify(std::int32_t value, std::function<Level(std::int32_t)> classifier);
Mode pick(std::function<Mode()> chooser);
double integrate(std::function<double(double)>&& f, double a, double b, std::int32_t steps);
// Calls visit with each point and its name, then a nameless one.
double walk(std::function<double(const Point&, const char*)> visit);
void shift(std::function<void(Point, Mode)> sink);
// Calls f with "a\0b" as each string type.
void strings(std::function<void(const std::string&, std::string, std::string_view)> f);
// Calls f n times from 'threads' C++ threads, and joins them before returning.
void parallel(std::int32_t threads, std::int32_t n, std::function<void(std::int32_t)> f);

// Callbacks beside by-reference parameters: f(prefix + "!").
std::int32_t prefixed(const std::string& prefix,
                      const std::function<std::int32_t(const std::string&)>& f);

// Stores handler (see fire), then sleeps for ms milliseconds.
void store_and_sleep(std::function<std::int32_t(std::int32_t)> handler, std::int32_t ms);
// The stored handler's result for value, or -1 if there is none.
std::int32_t fire(std::int32_t value);

// Stored in a global, replaced by the next call; called right away with
// (Info, "set"). The global is destroyed at exit.
void set_log_handler(std::function<void(Level, const std::string&)> handler);
// Calls the stored handler, if any; false if there is none.
bool log(Level level, const std::string& message);
// When enabled, a static destructor calls the stored handler at exit.
void log_at_exit(bool enable);

class Timer {
public:
    explicit Timer(std::function<void(std::int32_t)> on_tick);
    Timer(const Timer&) = delete;
    Timer& operator=(const Timer&) = delete;
    void set_on_tick(std::function<void(std::int32_t)> on_tick);
    // Calls on_tick with 0 .. times - 1.
    void tick(std::int32_t times);
    std::int32_t ticks() const;
    // Calls f with each value; the number of values.
    std::int32_t each(const std::vector<std::int32_t>& values, std::function<void(std::int32_t)> f);
    static std::int32_t apply(std::int32_t value, std::function<std::int32_t(std::int32_t)> f);

private:
    std::function<void(std::int32_t)> on_tick_;
    std::int32_t ticks_ = 0;
};

// Keeps its callback, called by notify with the name and the point.
class Watcher {
public:
    Watcher(const std::string& name, const Point& at,
            std::function<void(const std::string&, Point)> on_notify);
    void notify() const;

private:
    std::string name_;
    Point at_;
    std::function<void(const std::string&, Point)> on_notify_;
};

}  // namespace ev
