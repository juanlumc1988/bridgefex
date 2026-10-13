// SPDX-License-Identifier: Apache-2.0
// Enums across the C boundary.
#pragma once

#include <cstdint>

namespace colors {

// Defined in shapes.h, which uses Color in turn.
enum class Shape : std::int16_t;

enum class Color : std::uint8_t { Red, Green = 200, Max = 255 };

// Without a fixed underlying type: only 0..7 are valid.
enum Mode { Off, On, Auto = 5 };

// Named through a typedef; -4..3 are valid.
typedef enum { First = -3, Last = 2 } Signed;

enum Plain : long long { Low = -9223372036854775807LL - 1, High = 9223372036854775807LL };

enum class Flags : std::uint64_t { Nothing = 0, Bit31 = 2147483648ULL, All = 18446744073709551615ULL };

enum class Wide : std::int32_t { Min = INT32_MIN, Max = INT32_MAX };

enum class Small : unsigned short { Top = 65535 };

// Names that Python enums cannot take as they are.
enum class Names : int { None, mro, bit_length, name, value };

// A strong integer type, without enumerators.
enum class Id : std::uint32_t {};

enum class Old : int { Current = 1, Legacy [[deprecated("use Current")]] = 0, Alias = 1 };

Color mix(Color a, Color b);
Mode toggle(Mode m);
Signed negate(Signed s);
Flags flip(Flags f);
Plain plain(Plain p);
Wide wide(Wide w);
Small small(Small s);
Names pick(Names n);
Id next(Id id);
Old current();
Shape default_shape();

// Overloads on an enum.
int f(Color c);
int f(int i);

class Lamp {
public:
    enum class State : std::uint8_t { Dark, Lit };
    enum Level { Dim, Bright = 3 };

    explicit Lamp(Color color);
    Color color() const;
    State state() const;
    void set(State state, Level level);
    Level level() const;

private:
    Color color_;
    State state_;
    Level level_;
};

}  // namespace colors
