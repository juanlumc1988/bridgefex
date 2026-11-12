// SPDX-License-Identifier: Apache-2.0
#include "de.h"

#include <chrono>
#include <stdexcept>
#include <thread>

namespace de {

namespace {
std::int32_t g_alive = 0;
}

Base::Base() : made_(2) { ++g_alive; }
Base::Base(std::int32_t made) : made_(made) { ++g_alive; }
Base::Base(const std::string&) : made_(3) { ++g_alive; }
Base::~Base() { --g_alive; }

std::int32_t Base::value() const { return 1; }
bool Base::flag(bool on) const { return on; }
long Base::wide(long x) const { return x; }
Color Base::color() const { return Red; }
std::string Base::text(const char* mark) const { return mark == nullptr ? "null" : mark; }
std::string Base::named() const { return "base"; }
std::int32_t Base::add(std::int32_t a, std::int32_t b) const { return a + b; }
double Base::add(double a) const { return a * 2; }

std::int32_t Base::call_value() const { return value(); }
bool Base::call_flag(bool on) const { return flag(on); }
long Base::call_wide(long x) const { return wide(x); }
Color Base::call_color() const { return color(); }
std::string Base::call_text(const char* mark) const { return text(mark); }
std::string Base::call_text_null() const { return text(nullptr); }
std::string Base::call_named() const { return named(); }
std::int32_t Base::call_add(std::int32_t a, std::int32_t b) const { return add(a, b); }
double Base::call_add_double(double a) const { return add(a); }
std::string Base::pair() const
{
    std::int32_t v = value();
    return std::to_string(v) + ":" + named();
}
std::int32_t Base::made() const { return made_; }
std::int32_t Base::alive() { return g_alive; }

Sub::Sub() = default;
std::int32_t Sub::add(std::int32_t a, std::int32_t b) const { return a * b; }

Leaf::Leaf() = default;
std::int32_t Leaf::leaf() const { return 9; }

Thrower::Thrower(std::int32_t v)
{
    if (v < 0) {
        throw std::invalid_argument("negative");
    }
}
Thrower::~Thrower() = default;
std::int32_t Thrower::get() const { return 4; }
std::int32_t Thrower::call_get() const { return get(); }

Registry::Registry() : kept_(nullptr) {}
Registry::~Registry() = default;
void Registry::keep(Base* base) { kept_ = base; }
std::string Registry::sequence() const
{
    return kept_->named() + "|" + std::to_string(kept_->value());
}
std::string Registry::sequence_of(const Base& base, std::int32_t milliseconds)
{
    std::string text = base.named();
    std::this_thread::sleep_for(std::chrono::milliseconds(milliseconds));
    text += "|" + std::to_string(base.value());
    std::this_thread::sleep_for(std::chrono::milliseconds(milliseconds));
    return text + "|" + base.named();
}
std::int32_t Registry::value_on_thread() const
{
    std::int32_t result = 0;
    std::thread worker([&]() { result = kept_->value(); });
    worker.join();
    return result;
}

}  // namespace de
