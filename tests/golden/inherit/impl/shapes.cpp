// SPDX-License-Identifier: Apache-2.0
#include "shapes.h"

#include <stdexcept>

namespace inh {

namespace {
std::int32_t g_alive = 0;
const double kPi = 3.0;  // exact results
}  // namespace

Circle::Circle(double radius) : radius_(radius)
{
    ++g_alive;
}

Circle::~Circle()
{
    --g_alive;
}

std::string Circle::name() const
{
    return "circle";
}

double Circle::area() const
{
    return kPi * radius_ * radius_;
}

double Circle::radius() const
{
    return radius_;
}

double Circle::scale(double factor)
{
    radius_ *= factor;
    return radius_;
}

Square::Square(double side) : side_(side)
{
    ++g_alive;
}

Square::~Square()
{
    --g_alive;
}

std::string Square::name() const
{
    return "square";
}

double Square::area() const
{
    return side_ * side_;
}

std::int32_t Square::sides() const
{
    return 4;
}

Ring::Ring(double outer, double inner) : Circle(outer), inner_(inner) {}

Ring::~Ring() = default;

std::string Ring::name() const
{
    return "ring";
}

double Ring::area() const
{
    return Circle::area() - kPi * inner_ * inner_;
}

std::unique_ptr<Shape> make_shape(std::int32_t kind, double size)
{
    switch (kind) {
    case 0:
        return std::unique_ptr<Shape>(new Circle(size));
    case 1:
        return std::unique_ptr<Shape>(new Square(size));
    case 2:
        return std::unique_ptr<Shape>(new Ring(size, size / 5));
    default:
        return nullptr;
    }
}

double total_area(const Shape& first, const Shape& second)
{
    return first.area() + second.area();
}

double grow(Shape& shape, std::int32_t factor)
{
    shape.scale(factor);
    return shape.area() * shape.scaled();
}

std::string describe_first(const Shape* shape)
{
    return shape != nullptr ? shape->describe() : std::string("nothing");
}

std::string call_name(const Named& named)
{
    return named.greeting();
}

Shape& largest(Shape& first, Shape& second)
{
    return first.area() >= second.area() ? first : second;
}

std::int32_t consume(std::unique_ptr<Shape> shape)
{
    if (!shape) {
        throw std::invalid_argument("no shape");
    }
    return shape->sides();
}

std::int32_t shapes_alive()
{
    return g_alive;
}

}  // namespace inh
