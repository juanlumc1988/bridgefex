// SPDX-License-Identifier: Apache-2.0
#include "widget.h"

#include <utility>

namespace inh {

Counter::Counter() : count_(0), padding_{7, 7} {}

std::int32_t Counter::next()
{
    return ++count_;
}

std::int32_t Counter::count() const
{
    // The padding is there to make a Counter big; reading it keeps Clang from
    // warning that the private field is unused.
    return count_ + static_cast<std::int32_t>(padding_[0] - padding_[1]);
}

Labeled::Labeled() : label_("unnamed") {}

Labeled::Labeled(const std::string& label) : label_(label) {}

Labeled::~Labeled() = default;

std::string Labeled::label() const
{
    return label_;
}

void Labeled::setLabel(const std::string& label)
{
    label_ = label;
}

Widget::Widget() = default;

Widget::~Widget() = default;

std::string Widget::label() const
{
    return "widget " + Labeled::label();
}

std::int32_t Widget::both() const
{
    return count() * 100 + static_cast<std::int32_t>(label().size());
}

std::int32_t bump(Counter& counter)
{
    return counter.next();
}

std::int32_t peek(Counter counter)
{
    return counter.count();
}

std::string name_of(const Labeled* labeled)
{
    return labeled != nullptr ? labeled->label() : std::string("none");
}

Tagged::Tagged() : tag_(41) {}

Tagged::~Tagged() = default;

std::int32_t Tagged::tag() const
{
    return tag_;
}

Observer::Observer(Counter& watched) : watched_(watched) {}

Observer::~Observer() = default;

std::int32_t Observer::watched() const
{
    return watched_.count();
}

Shelf::Shelf() = default;

Shelf::~Shelf() = default;

void Shelf::put(std::unique_ptr<Labeled> item)
{
    item_ = std::move(item);
}

std::unique_ptr<Observer> Shelf::pop()
{
    return std::unique_ptr<Observer>(static_cast<Observer*>(item_.release()));
}

Plain::Plain() = default;

Plain::~Plain() = default;

std::int32_t Plain::value() const
{
    return 5;
}

PlainDerived::PlainDerived() = default;

PlainDerived::~PlainDerived() = default;

std::int32_t PlainDerived::extra() const
{
    return 9;
}

std::int32_t take_plain(std::unique_ptr<Plain> plain)
{
    return plain->value();
}

}  // namespace inh
