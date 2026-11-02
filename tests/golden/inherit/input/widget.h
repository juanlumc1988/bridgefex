// SPDX-License-Identifier: Apache-2.0
// Several base classes, one of them not at the start of the object; a base class
// without a virtual destructor.
#pragma once

#include <cstdint>
#include <memory>
#include <string>

namespace inh {

// Not polymorphic.
class Counter {
public:
    Counter();
    std::int32_t next();
    std::int32_t count() const;

private:
    std::int32_t count_;
    std::int64_t padding_[2];
};

class Labeled {
public:
    Labeled();
    explicit Labeled(const std::string& label);
    virtual ~Labeled();
    virtual std::string label() const;
    void setLabel(const std::string& label);

private:
    std::string label_;
};

// Counter and Labeled cannot both be at offset 0.
class Widget : public Counter, public Labeled {
public:
    Widget();
    ~Widget() override;
    std::string label() const override;
    // The count and the length of the label.
    std::int32_t both() const;
};

// Take the bases of a Widget; a Counter by value is a copy.
std::int32_t bump(Counter& counter);
std::int32_t peek(Counter counter);
std::string name_of(const Labeled* labeled);

// A base class that is not polymorphic: its destructor is not virtual.
class Plain {
public:
    Plain();
    ~Plain();
    std::int32_t value() const;
};

class PlainDerived : public Plain {
public:
    PlainDerived();
    ~PlainDerived();
    std::int32_t extra() const;
};

// Takes the object and destroys it as a Plain: its value.
std::int32_t take_plain(std::unique_ptr<Plain> plain);

}  // namespace inh
