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

// Labeled is not at the start of an Observer: another polymorphic class comes first.
class Tagged {
public:
    Tagged();
    virtual ~Tagged();
    virtual std::int32_t tag() const;

private:
    std::int32_t tag_;
};

// Keeps a reference to a Counter, which the constructor retains.
class Observer : public Tagged, public Labeled {
public:
    explicit Observer(Counter& watched);
    ~Observer() override;
    // The count of the Counter it keeps.
    std::int32_t watched() const;

private:
    Counter& watched_;
};

// Takes an object as a Labeled, which is not where the Observer starts, and gives it
// back as the Observer that the caller knows it is.
class Shelf {
public:
    Shelf();
    ~Shelf();
    void put(std::unique_ptr<Labeled> item);
    std::unique_ptr<Observer> pop();

private:
    std::unique_ptr<Labeled> item_;
};

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
