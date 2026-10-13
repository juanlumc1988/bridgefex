// SPDX-License-Identifier: Apache-2.0
// std::span<const T> across the C boundary (C++20).
#pragma once

#include <cstddef>
#include <cstdint>
#include <span>
#include <string>
#include <vector>

namespace views {

enum class Color : std::uint8_t { Red, Green };

struct Point {
    double x;
    double y;
};

std::int64_t sum(std::span<const std::int16_t> values);
std::span<const std::int32_t> numbers();
int count_red(std::span<const Color> colors);
double sum_x(std::span<const Point> points);
std::size_t total(std::span<const std::string, std::dynamic_extent> words);

}  // namespace views
