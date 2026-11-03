// SPDX-License-Identifier: Apache-2.0
// Test input: a director in C++11 (no std::string_view, no C++14 or later syntax).
#pragma once

#include <cstdint>
#include <string>

namespace dr1 {

enum Kind { Small, Large = 10 };

struct Pair {
    std::int32_t first;
    std::int32_t second;
};

struct Tagged {
    Kind kind;
    std::int32_t number;
};

class Base {
public:
    explicit Base(std::int32_t seed);
    Base(Kind kind, std::int32_t seed);
    virtual ~Base();
    virtual std::string text(const std::string& prefix) const = 0;
    virtual Pair pair() const;
    virtual Tagged tagged() const noexcept;
    virtual Kind kind(Kind requested) const noexcept;
    virtual void touch(std::int32_t amount, const std::int32_t& limit);
    virtual std::int32_t value(std::int32_t x) const;
    virtual double value(double x) const;
    virtual bool flag(bool on) const;
    virtual std::uint64_t big(std::int64_t x, float f, std::uint8_t byte) const;
    std::string show() const;
    bool show_flag(bool on) const;
    std::uint64_t show_big(std::int64_t x, float f, std::uint8_t byte) const;
    Tagged show_tagged() const;
    Kind show_kind(Kind requested) const;
    std::int32_t seed() const;

protected:
    Base();
    virtual void hook();

private:
    std::int32_t seed_;
};

class Derived : public Base {
public:
    Derived();
    std::string text(const std::string& prefix) const override;
};

}  // namespace dr1
