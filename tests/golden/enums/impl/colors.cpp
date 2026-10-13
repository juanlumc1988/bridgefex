// SPDX-License-Identifier: Apache-2.0
#include "colors.h"

#include "shapes.h"

namespace colors {

Color mix(Color a, Color b)
{
    return static_cast<Color>((static_cast<int>(a) + static_cast<int>(b)) / 2);
}

Mode toggle(Mode m)
{
    return m == Off ? On : Off;
}

Signed negate(Signed s)
{
    return static_cast<Signed>(-static_cast<int>(s));
}

Flags flip(Flags f)
{
    return static_cast<Flags>(~static_cast<std::uint64_t>(f));
}

Plain plain(Plain p)
{
    return p;
}

Wide wide(Wide w)
{
    return w;
}

Small small(Small s)
{
    return s;
}

Names pick(Names n)
{
    return n;
}

Id next(Id id)
{
    return static_cast<Id>(static_cast<std::uint32_t>(id) + 1);
}

Old current()
{
    return Old::Current;
}

Shape default_shape()
{
    return Shape::Square;
}

int f(Color c)
{
    return 1000 + static_cast<int>(c);
}

int f(int i)
{
    return i;
}

Lamp::Lamp(Color color) : color_(color), state_(State::Dark), level_(Dim) {}

Color Lamp::color() const
{
    return color_;
}

Lamp::State Lamp::state() const
{
    return state_;
}

void Lamp::set(State state, Level level)
{
    state_ = state;
    level_ = level;
}

Lamp::Level Lamp::level() const
{
    return level_;
}

Color color_of(Shape shape)
{
    return shape == Shape::Circle ? Color::Red : Color::Green;
}

Shape shape_of(Color color)
{
    return color == Color::Red ? Shape::Circle : Shape::Square;
}

}  // namespace colors
