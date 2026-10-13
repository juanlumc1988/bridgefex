// SPDX-License-Identifier: Apache-2.0
// std::vector across the C boundary.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace vec {

using Bytes = std::vector<std::uint8_t>;

enum class Color : std::uint8_t { Red, Green, Blue };
enum Level { Low, High };  // without a fixed type: 0..1

struct Point {
    double x;
    double y;
};

std::vector<std::int64_t> scale(const std::vector<std::int64_t>& values, std::int64_t factor);
std::vector<bool> invert(const std::vector<bool>& flags);
Bytes echo_bytes(const Bytes& data);
std::vector<float> halves(std::vector<float> values);
std::size_t total_size(std::vector<std::string>&& words);
std::vector<std::string> upper(const std::vector<std::string>& words);
std::vector<Color> rotate(const std::vector<Color>& colors);
int count_high(const std::vector<Level>& levels);
std::vector<Level> levels(int count);
std::vector<Point> shift(const std::vector<Point>& points, double dx);
const std::vector<int>& primes();
// Throws std::length_error.
std::vector<int> too_big();
std::vector<std::string> empty_strings();
// The size parameter of 'data' gets another name.
std::size_t sum(const std::vector<int>& data, int data_size);

class Bag {
public:
    explicit Bag(const std::vector<std::string>& items);
    std::vector<std::string> items() const;
    void add(const std::vector<int>& counts);
    std::vector<int> counts() const;

private:
    std::vector<std::string> items_;
    std::vector<int> counts_;
};

}  // namespace vec
