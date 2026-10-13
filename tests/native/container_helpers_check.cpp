// SPDX-License-Identifier: Apache-2.0
// Checks of the container helpers of the generated runtime: sizes that would
// overflow a 32-bit size_t throw std::bad_alloc before anything is copied, and
// the outputs are left alone. Built by tests/test_native.py.
#include "cont_runtime_internal.hpp"

#include <cstdint>
#include <cstdio>
#include <new>
#include <string>

namespace {

int failures = 0;

void check(bool condition, const char* what)
{
    if (!condition) {
        std::printf("FAILED: %s\n", what);
        ++failures;
    }
}

template <typename Function>
bool throws_bad_alloc(const Function& function)
{
    try {
        function();
    } catch (const std::bad_alloc&) {
        return true;
    }
    return false;
}

// A range of count elements that all are value: nothing is stored.
template <typename T>
struct fake_range {
    size_t count;
    T value;

    struct iterator {
        size_t index;
        const T* value;
        const T& operator*() const { return *value; }
        const T* operator->() const { return value; }
        iterator& operator++()
        {
            ++index;
            return *this;
        }
        bool operator!=(const iterator& other) const { return index != other.index; }
    };

    size_t size() const { return count; }
    iterator begin() const { return iterator{0, &value}; }
    iterator end() const { return iterator{count, &value}; }
};

}  // namespace

int main()
{
    using cont_detail::checked_add;
    using cont_detail::checked_mul;
    // 32-bit sizes, as on 32-bit ARM.
    check(checked_add<std::uint32_t>(0xFFFFFFFEu, 1) == 0xFFFFFFFFu, "add at the limit");
    check(throws_bad_alloc([] { checked_add<std::uint32_t>(0xFFFFFFFFu, 1); }), "add overflow");
    check(checked_mul<std::uint32_t>(0xFFFFu, 0x10001u) == 0xFFFFFFFFu, "mul at the limit");
    check(throws_bad_alloc([] { checked_mul<std::uint32_t>(0x10000u, 0x10000u); }), "mul overflow");
    check(checked_mul<std::uint32_t>(0, 0xFFFFFFFFu) == 0, "mul by 0");

    // A block of 0 bytes is never NULL.
    cont_detail::malloc_block empty(0);
    check(empty.get() != nullptr, "0-byte block");

    // Too many elements: bad_alloc before any copy, outputs untouched.
    std::int32_t marker = 0;
    std::int32_t* out = &marker;
    size_t out_size = 7;
    fake_range<std::int32_t> many{static_cast<size_t>(-1) / 2, 1};
    check(throws_bad_alloc([&] { cont_detail::copy_out(many, &out, &out_size); }), "copy_out");
    check(out == &marker && out_size == 7, "copy_out leaves the outputs alone");

    cont_string marker_string = {nullptr, 0};
    cont_string* strings = &marker_string;
    fake_range<std::string> many_strings{static_cast<size_t>(-1) / 4, std::string("x")};
    check(throws_bad_alloc([&] { cont_detail::copy_out_strings(many_strings, &strings, &out_size); }),
          "copy_out_strings: too many strings");
    struct huge_string {
        size_t size() const { return static_cast<size_t>(-1) / 2; }
        const char* data() const { return ""; }
    };
    fake_range<huge_string> huge{2, huge_string()};
    check(throws_bad_alloc([&] { cont_detail::copy_out_strings(huge, &strings, &out_size); }),
          "copy_out_strings: too many bytes");
    check(strings == &marker_string && out_size == 7, "copy_out_strings leaves the outputs alone");

    if (failures == 0) {
        std::printf("container helpers OK\n");
    }
    return failures == 0 ? 0 : 1;
}
