// SPDX-License-Identifier: Apache-2.0
#include "geo.h"

#include <stdexcept>

namespace geo {

Point mid(const Point& a, Point b)
{
    return Point{(a.x + b.x) / 2, (a.y + b.y) / 2};
}

Msg echo(const Msg& m)
{
    return m;
}

Msg shift(Msg m, std::int16_t delta)
{
    // By index: a reference to a member of a packed struct may be misaligned.
    for (std::size_t i = 0; i < 3; ++i) {
        m.samples[i] = static_cast<std::int16_t>(m.samples[i] + delta);
    }
    m.tag = static_cast<std::uint8_t>(m.tag + 1);
    return m;
}

Config configure(const Config& c)
{
    Config result = c;
    result.baud *= 2;
    return result;
}

Wide widen(Wide w)
{
    w.big *= 2;
    return w;
}

Names rename(Names n)
{
    n.from += 1;
    return n;
}

Tagged toggle(Tagged t)
{
    t.mode = t.mode == Tagged::Mode::Off ? Tagged::Mode::On : Tagged::Mode::Off;
    return t;
}

Point scale(const Point& p, double factor)
{
    if (factor < 0) {
        throw std::invalid_argument("negative scale");
    }
    return Point{p.x * factor, p.y * factor};
}

int area(const Point& a)
{
    return static_cast<int>(a.x * a.y);
}

int area(const Wide& w)
{
    return static_cast<int>(w.f);
}

Path::Path(const Point& start) : last_(start) {}

void Path::add(const Point& p)
{
    last_ = p;
}

Point Path::last() const
{
    return last_;
}

Point Path::origin()
{
    return Point{0, 0};
}

}  // namespace geo
