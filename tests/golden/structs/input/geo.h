// SPDX-License-Identifier: Apache-2.0
// Value structs across the C boundary.
#pragma once

#include <cstddef>
#include <cstdint>

namespace geo {

struct Point {
    double x;
    double y;
};

enum class Kind : std::uint8_t { Plain, Marked = 7 };
enum Quality { Bad, Good, Best };  // without a fixed type: 0..3

// Default member initializers reach C and Python.
struct Config {
    std::uint32_t baud = 115200;
    bool parity = true;
    Kind kind = Kind::Marked;
    static constexpr int kVersion = 2;  // not part of the value
};

#pragma pack(push, 1)
struct Msg {
    std::uint8_t tag = 7;
    Point where;
    std::int16_t samples[3];
    bool ok;
    Point path[2];
    Quality quality;
};
#pragma pack(pop)

#if defined(_MSC_VER)
#pragma warning(push)
#pragma warning(disable : 4324)  // padded because of alignas: that is the point
#endif
struct alignas(16) Wide {
    float f;
    std::int64_t big;
    std::size_t size;
    long l;
    signed char c;
    std::uint8_t bytes[4];
};
#if defined(_MSC_VER)
#pragma warning(pop)
#endif

typedef struct {
    int from;      // a Python keyword
    int restrict;  // a C keyword
    int errno_;
} Names;

struct Tagged {
    enum class Mode : std::int8_t { Off, On };
    Mode mode;
    int value;
};

Point mid(const Point& a, Point b);
Msg echo(const Msg& m);
Msg shift(Msg m, std::int16_t delta);
Config configure(const Config& c);
Wide widen(Wide w);
Names rename(Names n);
Tagged toggle(Tagged t);
// Throws std::invalid_argument for negative scales.
Point scale(const Point& p, double factor);
int area(const Point& a);
int area(const Wide& w);

class Path {
public:
    explicit Path(const Point& start);
    void add(const Point& p);
    Point last() const;
    static Point origin();

private:
    Point last_;
};

}  // namespace geo
