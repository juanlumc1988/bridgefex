// SPDX-License-Identifier: Apache-2.0
// Test input: classes that other languages may derive from (directors): pure and
// ordinary virtual methods, public and protected, with scalars, enums, strings and
// value structs, overloads, noexcept, and the constructors of a base class.
#pragma once

#include <array>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

namespace drc {

class Holder;

enum class Mode : std::uint8_t { Off, On, Auto = 5 };

struct Point {
    double x;
    double y;
};

class Shape {
public:
    Shape();
    explicit Shape(const std::string& label, std::int32_t sides = 0);
    virtual ~Shape();

    // Pure: whoever derives from Shape has to implement it.
    virtual double area() const = 0;
    // The defaults call back into the class.
    virtual std::string name() const;
    virtual std::int32_t sides() const;
    virtual Point center() const;
    // Structs as parameters: by value, and by const reference.
    virtual Point move(Point by, const Point& other) const;
    virtual Mode mode(Mode requested) const;
    virtual void scale(double factor);
    virtual std::int32_t add(std::int32_t a, std::int32_t b) const;
    virtual double add(double a, double b) const;
    virtual std::string greet(const std::string& who, std::string_view tag, const char* mark) const;
    virtual std::int32_t quiet() const noexcept;
    // Containers, in both directions.
    virtual std::int32_t total(const std::vector<std::int32_t>& values) const;
    virtual std::vector<std::int32_t> numbers(const std::vector<std::int32_t>& values) const;
    virtual std::vector<std::string> words(std::vector<std::string> input) const;
    virtual std::vector<Mode> modes(const std::vector<Mode>& wanted) const;
    virtual std::vector<Point> points(const std::vector<Point>& list) const;
    virtual std::vector<bool> flags(const std::vector<bool>& input) const;
    virtual std::array<double, 3> triple(const std::array<double, 3>& values) const;
    // Not overridable from another language (yet): a class as a parameter, so the C++
    // implementation is kept.
    virtual std::int32_t peek(const Holder& holder) const;
    // Cannot be overridden at all.
    virtual std::int32_t fixed() const final;

    // Not virtual: they call the virtual methods, which is what a director is for.
    std::string describe() const;
    double scaled_area(double factor);
    Point middle(const Point& other) const;
    Point call_move(Point by) const;
    std::int32_t call_total(const std::vector<std::int32_t>& values) const;
    std::vector<std::int32_t> call_numbers(const std::vector<std::int32_t>& values) const;
    std::vector<std::string> call_words(const std::vector<std::string>& input) const;
    std::vector<Mode> call_modes(const std::vector<Mode>& wanted) const;
    std::vector<Point> call_points(const std::vector<Point>& list) const;
    std::vector<bool> call_flags(const std::vector<bool>& input) const;
    std::array<double, 3> call_triple(const std::array<double, 3>& values) const;
    std::int32_t call_peek(const Holder& holder) const;
    Mode choose(Mode requested) const;
    std::string welcome(const std::string& who) const;
    std::int32_t sum(std::int32_t a, std::int32_t b) const;
    double sum_double(double a, double b) const;
    std::int32_t total_of_three() const;
    std::int32_t how_quiet() const;
    // Calls the protected virtual method.
    void notify(std::int32_t reason);
    std::int32_t last_reason() const;
    std::string label() const;

    static std::int32_t alive();

protected:
    // Overridable, but not part of the API.
    virtual void changed(std::int32_t reason);

private:
    std::string label_;
    std::int32_t sides_;
    std::int32_t reason_;
};

class Circle : public Shape {
public:
    explicit Circle(double radius);
    ~Circle() override;

    double area() const override;
    std::string name() const override;
    // Hides Shape::add(double, double): a call of the implementation of the class must still
    // run the overload of Shape that the director overrides, not this one with a conversion.
    std::int32_t add(std::int32_t a, std::int32_t b) const override;
    double radius() const;

private:
    double radius_;
};

// Final: it cannot have a director.
class Dot final : public Shape {
public:
    Dot();
    ~Dot() override;
    double area() const override;
};

// Not asked for a director: its virtual methods are only called.
class Plain {
public:
    Plain();
    virtual ~Plain();
    virtual std::int32_t value() const;
};

// Keeps a Shape that another language made (it takes it and gives it back), and calls its
// virtual methods, also from a thread of C++ and inside a call that has a callback.
class Holder {
public:
    Holder();
    ~Holder();
    void put(std::unique_ptr<Shape> shape);
    std::unique_ptr<Shape> take();
    bool empty() const;
    std::string describe() const;
    std::string describe_on_thread() const;
    std::int32_t apply(const std::function<std::int32_t(std::int32_t)>& f) const;

private:
    std::unique_ptr<Shape> shape_;
};

}  // namespace drc
