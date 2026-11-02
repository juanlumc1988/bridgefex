// SPDX-License-Identifier: Apache-2.0
// Test input: classes that other languages may derive from (directors): pure and
// ordinary virtual methods, public and protected, with scalars, enums, strings and
// value structs, overloads, noexcept, and the constructors of a base class.
#pragma once

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace drc {

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
    virtual Mode mode(Mode requested) const;
    virtual void scale(double factor);
    virtual std::int32_t add(std::int32_t a, std::int32_t b) const;
    virtual double add(double a, double b) const;
    virtual std::string greet(const std::string& who, std::string_view tag, const char* mark) const;
    virtual std::int32_t quiet() const noexcept;
    // Not overridable from another language (yet): the C++ implementation is kept.
    virtual std::int32_t total(const std::vector<std::int32_t>& values) const;
    // Cannot be overridden at all.
    virtual std::int32_t fixed() const final;

    // Not virtual: they call the virtual methods, which is what a director is for.
    std::string describe() const;
    double scaled_area(double factor);
    Point middle(const Point& other) const;
    Mode choose(Mode requested) const;
    std::string welcome(const std::string& who) const;
    std::int32_t sum(std::int32_t a, std::int32_t b) const;
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

}  // namespace drc
