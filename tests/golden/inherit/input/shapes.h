// SPDX-License-Identifier: Apache-2.0
// Classes derived from the classes of another header, and the functions that take
// and return the base classes.
#pragma once

#include <cstdint>
#include <memory>
#include <string>

#include "shape.h"

namespace inh {

class Circle : public Shape {
public:
    explicit Circle(double radius);
    ~Circle() override;
    std::string name() const override;
    double area() const override;
    double radius() const;
    // Hides Shape::scale(std::int32_t).
    double scale(double factor);

private:
    double radius_;
};

class Square : public Shape {
public:
    explicit Square(double side);
    ~Square() override;
    std::string name() const override;
    double area() const override;
    std::int32_t sides() const override;

private:
    double side_;
};

// A class two levels below Named.
class Ring final : public Circle {
public:
    Ring(double outer, double inner);
    ~Ring() override;
    std::string name() const override;
    double area() const override;

private:
    double inner_;
};

// 0: Circle, 1: Square, 2: Ring (inner radius a fifth of size); NULL otherwise.
std::unique_ptr<Shape> make_shape(std::int32_t kind, double size);
// Takes a base class by reference, by const reference and by pointer.
double total_area(const Shape& first, const Shape& second);
double grow(Shape& shape, std::int32_t factor);
std::string describe_first(const Shape* shape);
std::string call_name(const Named& named);
// The larger of the two, which the caller still owns.
Shape& largest(Shape& first, Shape& second);
// Takes the shape (destroyed here): the number of its sides.
std::int32_t consume(std::unique_ptr<Shape> shape);
// The number of Shape objects alive (to check that nothing leaks).
std::int32_t shapes_alive();

}  // namespace inh
