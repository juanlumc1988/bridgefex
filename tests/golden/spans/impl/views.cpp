// SPDX-License-Identifier: Apache-2.0
#include "views.h"

namespace views {

std::int64_t sum(std::span<const std::int16_t> values)
{
    std::int64_t total = 0;
    for (std::int16_t value : values) {
        total += value;
    }
    return total;
}

std::span<const std::int32_t> numbers()
{
    static const std::int32_t values[] = {-1, 2, -3};
    return values;
}

int count_red(std::span<const Color> colors)
{
    int count = 0;
    for (Color color : colors) {
        count += color == Color::Red ? 1 : 0;
    }
    return count;
}

double sum_x(std::span<const Point> points)
{
    double total = 0;
    for (const Point& point : points) {
        total += point.x;
    }
    return total;
}

std::size_t total(std::span<const std::string, std::dynamic_extent> words)
{
    std::size_t size = 0;
    for (const std::string& word : words) {
        size += word.size();
    }
    return size;
}

}  // namespace views
