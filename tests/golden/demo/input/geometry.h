// SPDX-License-Identifier: Apache-2.0
// Test input: nested namespaces and an overload set split across headers.
#pragma once

namespace demo {

// Overloads demo::multiply from counter.h.
double multiply(double a, double b);

namespace geometry {

class Circle {
public:
    // Throws std::invalid_argument if radius is negative.
    explicit Circle(double radius);

    double radius() const;
    double area() const;
    // Throws std::invalid_argument if factor is not positive.
    void scale(double factor);

private:
    double radius_;
};

double distance(double x1, double y1, double x2, double y2);

}  // namespace geometry
}  // namespace demo
