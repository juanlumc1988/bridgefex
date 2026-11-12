// SPDX-License-Identifier: Apache-2.0
#include "dco.h"

namespace dco {

Source::Source() = default;
Source::~Source() = default;

std::int32_t Source::total(const std::vector<std::int32_t>& values) const
{
    std::int32_t sum = 0;
    for (std::int32_t value : values) {
        sum += value;
    }
    return sum;
}

std::int64_t Source::span_total(std::span<const std::int32_t> values) const
{
    std::int64_t sum = 0;
    for (std::int32_t value : values) {
        sum += value;
    }
    return sum;
}

std::vector<std::int32_t> Source::doubled(const std::vector<std::int32_t>& values) const
{
    std::vector<std::int32_t> result;
    for (std::int32_t value : values) {
        result.push_back(value * 2);
    }
    return result;
}

std::vector<long> Source::longs(const std::vector<long>& values) const
{
    return std::vector<long>(values.rbegin(), values.rend());
}

std::vector<unsigned long> Source::ulongs(const std::vector<unsigned long>& values) const
{
    return std::vector<unsigned long>(values.rbegin(), values.rend());
}

std::vector<Color> Source::colors(const std::vector<Color>& values) const
{
    std::vector<Color> result;
    for (Color value : values) {
        result.push_back(value == Red ? Green : Red);
    }
    return result;
}

std::vector<Mode> Source::modes(const std::vector<Mode>& values) const
{
    return values;
}

std::vector<Point> Source::points(const std::vector<Point>& values) const
{
    std::vector<Point> result;
    for (const Point& point : values) {
        result.push_back(Point{point.y, point.x});
    }
    return result;
}

std::vector<std::string> Source::words(const std::vector<std::string>& values) const
{
    std::vector<std::string> result;
    for (const std::string& word : values) {
        result.push_back(word + "!");
    }
    return result;
}

std::vector<bool> Source::flags(const std::vector<bool>& values) const
{
    std::vector<bool> result;
    for (bool flag : values) {
        result.push_back(!flag);
    }
    return result;
}

std::array<std::int32_t, 2> Source::pair(const std::array<std::int32_t, 2>& values) const
{
    return {values[1], values[0]};
}

std::array<Point, 2> Source::ends(const std::array<Point, 2>& values) const
{
    return {values[1], values[0]};
}

std::vector<std::int32_t> Source::nothing() const
{
    return {1, 2, 3};
}

std::int32_t Source::mixed(const std::vector<std::int32_t>& values, std::span<const double> weights,
                           const std::string& label) const
{
    double sum = 0;
    for (std::size_t i = 0; i < values.size() && i < weights.size(); ++i) {
        sum += values[i] * weights[i];
    }
    return static_cast<std::int32_t>(sum) + static_cast<std::int32_t>(label.size());
}

std::int32_t Source::call_total(const std::vector<std::int32_t>& values) const
{
    return total(values);
}

std::int64_t Source::call_span_total(const std::vector<std::int32_t>& values) const
{
    return span_total(values);
}

std::vector<std::int32_t> Source::call_doubled(const std::vector<std::int32_t>& values) const
{
    return doubled(values);
}

std::vector<long> Source::call_longs(const std::vector<long>& values) const
{
    return longs(values);
}

std::vector<unsigned long> Source::call_ulongs(const std::vector<unsigned long>& values) const
{
    return ulongs(values);
}

std::vector<Color> Source::call_colors(const std::vector<Color>& values) const
{
    return colors(values);
}

std::vector<Mode> Source::call_modes(const std::vector<Mode>& values) const
{
    return modes(values);
}

std::vector<Point> Source::call_points(const std::vector<Point>& values) const
{
    return points(values);
}

std::vector<std::string> Source::call_words(const std::vector<std::string>& values) const
{
    return words(values);
}

std::vector<bool> Source::call_flags(const std::vector<bool>& values) const
{
    return flags(values);
}

std::array<std::int32_t, 2> Source::call_pair(const std::array<std::int32_t, 2>& values) const
{
    return pair(values);
}

std::array<Point, 2> Source::call_ends(const std::array<Point, 2>& values) const
{
    return ends(values);
}

std::int32_t Source::call_nothing() const
{
    return static_cast<std::int32_t>(nothing().size());
}

std::int32_t Source::call_mixed(std::int32_t count, const std::string& label) const
{
    std::vector<std::int32_t> values;
    std::vector<double> weights;
    for (std::int32_t i = 1; i <= count; ++i) {
        values.push_back(i);
        weights.push_back(0.5 * i);
    }
    return mixed(values, std::span<const double>(weights), label);
}

std::int32_t Source::call_total_empty() const
{
    return total({});
}

std::int32_t Source::call_words_empty() const
{
    return static_cast<std::int32_t>(words({}).size());
}

std::int64_t Source::call_total_big(std::int32_t count) const
{
    std::vector<std::int32_t> values(static_cast<std::size_t>(count), 1);
    return span_total(std::span<const std::int32_t>(values));
}

std::int32_t Source::call_words_many(std::int32_t count) const
{
    std::vector<std::string> values(static_cast<std::size_t>(count), "word");
    std::int32_t length = 0;
    for (const std::string& word : words(values)) {
        length += static_cast<std::int32_t>(word.size());
    }
    return length;
}

std::int32_t Source::call_words_bad() const
{
    return static_cast<std::int32_t>(words({"fine", "\xff\xfe"}).size());
}

std::int32_t Source::call_doubled_size(const std::vector<std::int32_t>& values) const
{
    return static_cast<std::int32_t>(doubled(values).size());
}

}  // namespace dco
