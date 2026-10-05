// SPDX-License-Identifier: Apache-2.0
#include "geometry.h"

#include <cmath>
#include <stdexcept>

namespace demo {

double multiply(double a, double b)
{
    return a * b;
}

namespace geometry {

Circle::Circle(double radius) : radius_(radius)
{
    if (radius < 0.0) {
        throw std::invalid_argument("radius must not be negative");
    }
}

double Circle::radius() const
{
    return radius_;
}

double Circle::area() const
{
    return 3.141592653589793 * radius_ * radius_;
}

void Circle::scale(double factor)
{
    if (!(factor > 0.0)) {
        throw std::invalid_argument("factor must be positive");
    }
    radius_ *= factor;
}

double distance(double x1, double y1, double x2, double y2)
{
    return std::hypot(x2 - x1, y2 - y1);
}

}  // namespace geometry
}  // namespace demo
