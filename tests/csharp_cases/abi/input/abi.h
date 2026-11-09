// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <cstdint>

namespace abi {

// Underlying types that C# does not have: they are read as C long.
enum class Signed : long { Low = -2, High = 2 };
enum class Unsigned : unsigned long { Low = 0, High = 3000000000UL };

struct Mixed {
    Signed signed_value;
    Unsigned unsigned_value;
    Signed several[2];
    long number;
    unsigned long unsigned_number;
};

inline Signed flip(Signed value)
{
    return value == Signed::Low ? Signed::High : Signed::Low;
}

inline Unsigned flipUnsigned(Unsigned value)
{
    return value == Unsigned::Low ? Unsigned::High : Unsigned::Low;
}

inline Mixed echo(const Mixed& value)
{
    return value;
}

// A C array can have megabytes: the struct cannot go through the stack of the thread.
struct Big {
    std::uint8_t data[300000];
    std::int32_t tail;
};

struct Outer {
    Big big;
    std::int32_t count;
};

inline Big echoBig(const Big& value)
{
    return value;
}

inline std::int64_t sum(const Big& value)
{
    std::int64_t total = value.tail;
    for (std::uint8_t byte : value.data) {
        total += byte;
    }
    return total;
}

inline Outer makeOuter(std::int32_t count)
{
    Outer outer{};
    outer.count = count;
    outer.big.tail = count;
    outer.big.data[0] = 1;
    outer.big.data[299999] = 2;
    return outer;
}

inline std::int32_t countOf(const Outer& value)
{
    return value.count + value.big.tail;
}

}  // namespace abi
