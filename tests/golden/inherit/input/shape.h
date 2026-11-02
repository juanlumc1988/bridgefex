// SPDX-License-Identifier: Apache-2.0
// Base classes: an interface, an abstract class with a virtual method that has an
// implementation, and a method that calls virtual ones.
#pragma once

#include <cstdint>
#include <string>

namespace inh {

// An interface: no state, pure virtual methods.
class Named {
public:
    virtual ~Named();
    virtual std::string name() const = 0;
    // Not virtual: it calls the virtual one.
    std::string greeting() const;
};

class Shape : public Named {
public:
    ~Shape() override;
    virtual double area() const = 0;
    // The default; Square overrides it.
    virtual std::int32_t sides() const;
    // Not virtual: it calls name() and area().
    std::string describe() const;
    // A non-const method of a base class; Circle hides it with another one.
    std::int32_t scale(std::int32_t factor);
    std::int32_t scaled() const;

private:
    std::int32_t scale_ = 1;
};

}  // namespace inh
