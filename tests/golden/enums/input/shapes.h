// SPDX-License-Identifier: Apache-2.0
// Uses an enum of colors.h through an opaque declaration.
#pragma once

#include <cstdint>

namespace colors {

enum class Color : std::uint8_t;

enum class Shape : std::int16_t { Circle = -1, Square = 4 };

Color color_of(Shape shape);
Shape shape_of(Color color);

}  // namespace colors
