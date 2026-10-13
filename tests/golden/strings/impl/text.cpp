// SPDX-License-Identifier: Apache-2.0
#include "text.h"

#include <csignal>
#include <stdexcept>
#include <utility>

namespace text {

Person::Person(const std::string& name) : name_(name), age_(0) {}

Person::Person(const char* name, int age) : name_(name), age_(age) {}

std::string Person::name() const
{
    return name_;
}

const std::string& Person::name_ref() const
{
    return name_;
}

const char* Person::c_name() const
{
    return name_.c_str();
}

void Person::set_name(std::string name)
{
    name_ = std::move(name);
}

int Person::age() const
{
    return age_;
}

std::string Person::greet(const std::string& who, const char* tag) const
{
    return std::string(tag) + " " + who + ", I am " + name_;
}

std::string Person::species()
{
    return "human";
}

std::size_t count(const std::string& s)
{
    return s.size();
}

std::size_t count(const char* s)
{
    return 1000 + std::string(s).size();
}

std::string concat(const std::string& a, std::string b)
{
    return a + b;
}

std::string echo(const std::string& s)
{
    return s;
}

const char* maybe_null(bool null)
{
    return null ? nullptr : "not null";
}

std::string fail(const std::string& message)
{
    throw std::runtime_error(message);
}

std::string binary()
{
    return std::string("\0\xffx", 3);
}

const char* first_char(const std::string& s)
{
    return s.c_str();
}

std::size_t length(const std::string& s, std::size_t s_size)
{
    return s.size() * 1000 + s_size;
}

std::size_t total(const std::string& from, const std::string& x_, const std::string& third)
{
    return from.size() * 100 + x_.size() * 10 + third.size();
}

Name alias(const Name& name)
{
    return name + "!";
}

cstr_t c_alias(cstr_t text)
{
    return text;
}

std::string signal_twice(std::size_t size)
{
    std::raise(SIGINT);
#if defined(SIGUSR1)
    std::raise(SIGUSR1);
#endif
    return std::string(size, 'x');
}

}  // namespace text
