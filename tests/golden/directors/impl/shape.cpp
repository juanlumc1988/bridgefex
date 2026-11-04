// SPDX-License-Identifier: Apache-2.0
#include "shape.h"

#include <thread>
#include <utility>

namespace drc {

namespace {
std::int32_t g_alive = 0;
}

Shape::Shape() : sides_(0), reason_(-1)
{
    ++g_alive;
}

Shape::Shape(const std::string& label, std::int32_t sides) : label_(label), sides_(sides), reason_(-1)
{
    ++g_alive;
}

Shape::~Shape()
{
    --g_alive;
}

std::string Shape::name() const
{
    return label_.empty() ? "shape" : label_;
}

std::int32_t Shape::sides() const
{
    return sides_;
}

Point Shape::center() const
{
    return Point{0.0, 0.0};
}

Mode Shape::mode(Mode requested) const
{
    return requested == Mode::Auto ? Mode::On : requested;
}

void Shape::scale(double factor)
{
    sides_ = static_cast<std::int32_t>(sides_ * factor);
}

std::int32_t Shape::add(std::int32_t a, std::int32_t b) const
{
    return a + b;
}

double Shape::add(double a, double b) const
{
    return a + b;
}

std::string Shape::greet(const std::string& who, std::string_view tag, const char* mark) const
{
    return std::string(mark) + " " + std::string(tag) + " " + who + ", I am " + name();
}

std::int32_t Shape::quiet() const noexcept
{
    return 1;
}

std::int32_t Shape::total(const std::vector<std::int32_t>& values) const
{
    std::int32_t total = 0;
    for (std::int32_t value : values) {
        total += value;
    }
    return total;
}

std::int32_t Shape::fixed() const
{
    return 7;
}

std::string Shape::describe() const
{
    return name() + ": " + std::to_string(static_cast<long long>(area())) + " units";
}

double Shape::scaled_area(double factor)
{
    scale(factor);
    return area();
}

Point Shape::middle(const Point& other) const
{
    const Point mine = center();
    return Point{(mine.x + other.x) / 2, (mine.y + other.y) / 2};
}

Mode Shape::choose(Mode requested) const
{
    return mode(requested);
}

std::string Shape::welcome(const std::string& who) const
{
    return greet(who, "hello", ">>");
}

std::int32_t Shape::sum(std::int32_t a, std::int32_t b) const
{
    return add(a, b);
}

double Shape::sum_double(double a, double b) const
{
    return add(a, b);
}

std::int32_t Shape::total_of_three() const
{
    return total({1, 2, 3});
}

std::int32_t Shape::how_quiet() const
{
    return quiet();
}

void Shape::notify(std::int32_t reason)
{
    changed(reason);
}

std::int32_t Shape::last_reason() const
{
    return reason_;
}

std::string Shape::label() const
{
    return label_;
}

std::int32_t Shape::alive()
{
    return g_alive;
}

void Shape::changed(std::int32_t reason)
{
    reason_ = reason;
}

Circle::Circle(double radius) : Shape("circle", 0), radius_(radius) {}

Circle::~Circle() = default;

double Circle::area() const
{
    return 3.0 * radius_ * radius_;
}

std::string Circle::name() const
{
    return "round";
}

std::int32_t Circle::add(std::int32_t a, std::int32_t b) const
{
    return a + b + 1000;
}

double Circle::radius() const
{
    return radius_;
}

Dot::Dot() = default;

Dot::~Dot() = default;

double Dot::area() const
{
    return 0.0;
}

Plain::Plain() = default;

Plain::~Plain() = default;

std::int32_t Plain::value() const
{
    return 5;
}

Holder::Holder() = default;

Holder::~Holder() = default;

void Holder::put(std::unique_ptr<Shape> shape)
{
    shape_ = std::move(shape);
}

std::unique_ptr<Shape> Holder::take()
{
    return std::move(shape_);
}

bool Holder::empty() const
{
    return !shape_;
}

std::string Holder::describe() const
{
    return shape_ ? shape_->describe() : "empty";
}

std::string Holder::describe_on_thread() const
{
    std::string result = "empty";
    if (shape_) {
        std::thread worker([&] { result = shape_->describe(); });
        worker.join();
    }
    return result;
}

std::int32_t Holder::apply(const std::function<std::int32_t(std::int32_t)>& f) const
{
    return shape_ ? f(shape_->sum(1, 2)) : -1;
}

}  // namespace drc
