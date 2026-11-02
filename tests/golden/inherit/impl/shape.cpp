// SPDX-License-Identifier: Apache-2.0
#include "shape.h"

namespace inh {

Named::~Named() = default;

std::string Named::greeting() const
{
    return "I am " + name();
}

Shape::~Shape() = default;

std::int32_t Shape::sides() const
{
    return 0;
}

std::string Shape::describe() const
{
    return name() + ": " + std::to_string(static_cast<long>(area())) + " units";
}

std::int32_t Shape::scale(std::int32_t factor)
{
    scale_ = factor;
    return scale_;
}

std::int32_t Shape::scaled() const
{
    return scale_;
}

}  // namespace inh
