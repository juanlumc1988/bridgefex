// SPDX-License-Identifier: Apache-2.0
#include "vectors.h"

#include <algorithm>
#include <cctype>
#include <stdexcept>

namespace vec {

std::vector<std::int64_t> scale(const std::vector<std::int64_t>& values, std::int64_t factor)
{
    std::vector<std::int64_t> result;
    for (std::int64_t value : values) {
        result.push_back(value * factor);
    }
    return result;
}

std::vector<bool> invert(const std::vector<bool>& flags)
{
    std::vector<bool> result;
    for (bool flag : flags) {
        result.push_back(!flag);
    }
    return result;
}

Bytes echo_bytes(const Bytes& data)
{
    return data;
}

std::vector<float> halves(std::vector<float> values)
{
    for (float& value : values) {
        value /= 2;
    }
    return values;
}

std::size_t total_size(std::vector<std::string>&& words)
{
    std::size_t total = 0;
    for (const std::string& word : words) {
        total += word.size();
    }
    return total;
}

std::vector<std::string> upper(const std::vector<std::string>& words)
{
    std::vector<std::string> result = words;
    for (std::string& word : result) {
        for (char& c : word) {
            c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
        }
    }
    return result;
}

std::vector<Color> rotate(const std::vector<Color>& colors)
{
    std::vector<Color> result;
    for (Color color : colors) {
        result.push_back(static_cast<Color>((static_cast<int>(color) + 1) % 3));
    }
    return result;
}

int count_high(const std::vector<Level>& levels)
{
    return static_cast<int>(std::count(levels.begin(), levels.end(), High));
}

std::vector<Level> levels(int count)
{
    std::vector<Level> result;
    for (int i = 0; i < count; ++i) {
        result.push_back(i % 2 == 0 ? Low : High);
    }
    return result;
}

std::vector<Point> shift(const std::vector<Point>& points, double dx)
{
    std::vector<Point> result;
    for (const Point& point : points) {
        result.push_back(Point{point.x + dx, point.y});
    }
    return result;
}

const std::vector<int>& primes()
{
    static const std::vector<int> values = {2, 3, 5, 7};
    return values;
}

std::vector<int> too_big()
{
    throw std::length_error("cannot create std::vector larger than max_size()");
}

std::vector<std::string> empty_strings()
{
    return {"", ""};
}

std::size_t sum(const std::vector<int>& data, int data_size)
{
    std::size_t total = 0;
    for (int value : data) {
        total += static_cast<std::size_t>(value);
    }
    return total * 1000 + static_cast<std::size_t>(data_size);
}

Bag::Bag(const std::vector<std::string>& items) : items_(items) {}

std::vector<std::string> Bag::items() const
{
    return items_;
}

void Bag::add(const std::vector<int>& counts)
{
    counts_.insert(counts_.end(), counts.begin(), counts.end());
}

std::vector<int> Bag::counts() const
{
    return counts_;
}

}  // namespace vec
