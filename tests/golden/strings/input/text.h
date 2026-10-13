// SPDX-License-Identifier: Apache-2.0
// std::string and const char* across the C boundary.
#pragma once

#include <cstddef>
#include <string>

namespace text {

using Name = std::string;
typedef const char* cstr_t;

class Person {
public:
    explicit Person(const std::string& name);
    Person(const char* name, int age);

    std::string name() const;
    const std::string& name_ref() const;
    const char* c_name() const;
    void set_name(std::string name);
    int age() const;
    std::string greet(const std::string& who, const char* tag) const;
    static std::string species();

private:
    std::string name_;
    int age_;
};

// Overloads on the string type.
std::size_t count(const std::string& s);
std::size_t count(const char* s);

std::string concat(const std::string& a, std::string b);
// Embedded NULs, both ways.
std::string echo(const std::string& s);
// NULL or a string.
const char* maybe_null(bool null);
// Throws std::runtime_error with message.
std::string fail(const std::string& message);
// Not UTF-8: "\0\xffx".
std::string binary();
// Points into the argument, a temporary of the wrapper.
const char* first_char(const std::string& s);
// The size parameter of s gets another name.
std::size_t length(const std::string& s, std::size_t s_size);
// Parameter names that change in C or Python.
std::size_t total(const std::string& from, const std::string& x_, const std::string&);
Name alias(const Name& name);
cstr_t c_alias(cstr_t text);
// Raises SIGINT and SIGUSR1 (where it exists), then returns size bytes.
std::string signal_twice(std::size_t size);

}  // namespace text
