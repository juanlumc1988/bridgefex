// SPDX-License-Identifier: Apache-2.0
#include "base.h"

namespace dr1 {

Base::Base(std::int32_t seed) : seed_(seed) {}

Base::Base(Kind kind, std::int32_t seed) : seed_(seed + static_cast<std::int32_t>(kind)) {}

Base::Base() : seed_(1) {}

Base::~Base() {}

Pair Base::pair() const
{
    return Pair{seed_, seed_ + 1};
}

Tagged Base::tagged() const noexcept
{
    return Tagged{Large, 7};
}

Kind Base::kind(Kind requested) const noexcept
{
    return requested;
}

void Base::touch(std::int32_t amount, const std::int32_t& limit)
{
    seed_ = amount < limit ? amount : limit;
}

std::int32_t Base::value(std::int32_t x) const
{
    return x + seed_;
}

double Base::value(double x) const
{
    return x * 2;
}

bool Base::flag(bool on) const
{
    return !on;
}

std::uint64_t Base::big(std::int64_t x, float f, std::uint8_t byte) const
{
    return static_cast<std::uint64_t>(x) + static_cast<std::uint64_t>(f) + byte;
}

bool Base::show_flag(bool on) const
{
    return flag(on);
}

std::uint64_t Base::show_big(std::int64_t x, float f, std::uint8_t byte) const
{
    return big(x, f, byte);
}

std::string Base::show() const
{
    return text("p");
}

Tagged Base::show_tagged() const
{
    return tagged();
}

Kind Base::show_kind(Kind requested) const
{
    return kind(requested);
}

std::int32_t Base::seed() const
{
    return seed_;
}

void Base::hook() {}

Derived::Derived() {}

std::string Derived::text(const std::string& prefix) const
{
    return prefix + "!";
}

}  // namespace dr1
