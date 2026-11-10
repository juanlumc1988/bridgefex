// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <cstdint>
#include <string>

namespace de {

// Without a fixed type: only 0 and 1 are values.
enum Color { Red, Green };

class Base {
public:
    Base();
    explicit Base(std::int32_t made);
    virtual ~Base();

    virtual std::int32_t value() const;
    virtual bool flag(bool on) const;
    virtual long wide(long x) const;
    virtual Color color() const;
    virtual std::string text(const char* mark) const;
    virtual std::string named() const;
    virtual std::int32_t add(std::int32_t a, std::int32_t b) const;
    virtual double add(double a) const;

    std::int32_t call_value() const;
    bool call_flag(bool on) const;
    long call_wide(long x) const;
    Color call_color() const;
    std::string call_text(const char* mark) const;
    std::string call_text_null() const;
    std::string call_named() const;
    std::int32_t call_add(std::int32_t a, std::int32_t b) const;
    double call_add_double(double a) const;
    // value() then named(): if the override of the first fails, the second must not run.
    std::string pair() const;
    std::int32_t made() const;
    static std::int32_t alive();

protected:
    explicit Base(const std::string& tag);

private:
    std::int32_t made_;
};

// Redeclares one overload only.
class Sub : public Base {
public:
    Sub();
    std::int32_t add(std::int32_t a, std::int32_t b) const override;
};

// Derives from a class that has a director, but asks for none.
class Leaf : public Base {
public:
    Leaf();
    std::int32_t leaf() const;
};

// The constructor fails for a negative value.
class Thrower {
public:
    explicit Thrower(std::int32_t v);
    virtual ~Thrower();
    virtual std::int32_t get() const;
    std::int32_t call_get() const;
};

// Keeps a pointer to a Base that the caller owns, and uses its overrides.
class Registry {
public:
    Registry();
    ~Registry();
    void keep(Base* base);
    std::string sequence() const;
    // Calls name, value and name again, with a pause between them.
    static std::string sequence_of(const Base& base, std::int32_t milliseconds);
    // Calls value() from a thread of C++, and joins it.
    std::int32_t value_on_thread() const;

private:
    Base* kept_;
};

}  // namespace de
