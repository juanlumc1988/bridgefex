// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <array>
#include <cstdint>
#include <span>
#include <string>
#include <vector>

namespace dco {

// Without a fixed type: only 0 and 1 are values.
enum Color { Red, Green };
enum class Mode : std::uint8_t { Off, On, Auto };

struct Point {
    double x;
    double y;
};

class Source {
public:
    Source();
    virtual ~Source();

    virtual std::int32_t total(const std::vector<std::int32_t>& values) const;
    virtual std::int64_t span_total(std::span<const std::int32_t> values) const;
    virtual std::vector<std::int32_t> doubled(const std::vector<std::int32_t>& values) const;
    virtual std::vector<long> longs(const std::vector<long>& values) const;
    virtual std::vector<unsigned long> ulongs(const std::vector<unsigned long>& values) const;
    virtual std::vector<Color> colors(const std::vector<Color>& values) const;
    virtual std::vector<Mode> modes(const std::vector<Mode>& values) const;
    virtual std::vector<Point> points(const std::vector<Point>& values) const;
    virtual std::vector<std::string> words(const std::vector<std::string>& values) const;
    virtual std::vector<bool> flags(const std::vector<bool>& values) const;
    virtual std::array<std::int32_t, 2> pair(const std::array<std::int32_t, 2>& values) const;
    virtual std::array<Point, 2> ends(const std::array<Point, 2>& values) const;
    virtual std::vector<std::int32_t> nothing() const;
    virtual std::int32_t mixed(const std::vector<std::int32_t>& values,
                               std::span<const double> weights, const std::string& label) const;

    // Not virtual: they call the virtual methods above, which is what a director is for.
    std::int32_t call_total(const std::vector<std::int32_t>& values) const;
    std::int64_t call_span_total(const std::vector<std::int32_t>& values) const;
    std::vector<std::int32_t> call_doubled(const std::vector<std::int32_t>& values) const;
    std::vector<long> call_longs(const std::vector<long>& values) const;
    std::vector<unsigned long> call_ulongs(const std::vector<unsigned long>& values) const;
    std::vector<Color> call_colors(const std::vector<Color>& values) const;
    std::vector<Mode> call_modes(const std::vector<Mode>& values) const;
    std::vector<Point> call_points(const std::vector<Point>& values) const;
    std::vector<std::string> call_words(const std::vector<std::string>& values) const;
    std::vector<bool> call_flags(const std::vector<bool>& values) const;
    std::array<std::int32_t, 2> call_pair(const std::array<std::int32_t, 2>& values) const;
    std::array<Point, 2> call_ends(const std::array<Point, 2>& values) const;
    std::int32_t call_nothing() const;
    std::int32_t call_mixed(std::int32_t count, const std::string& label) const;

    // An empty vector, and one that is as large as memory allows in a test.
    std::int32_t call_total_empty() const;
    std::int32_t call_words_empty() const;
    std::int64_t call_total_big(std::int32_t count) const;
    std::int32_t call_words_many(std::int32_t count) const;
    // Text that is not UTF-8: the override must not get it.
    std::int32_t call_words_bad() const;
    // The result of an override that fails is the empty container.
    std::int32_t call_doubled_size(const std::vector<std::int32_t>& values) const;
};

}  // namespace dco
