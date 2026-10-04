// SPDX-License-Identifier: Apache-2.0
// Test input: global namespace, every supported scalar type, parameter names
// that must be renamed, unnamed parameters and an implicit constructor.
#pragma once

#include <cstddef>
#include <cstdint>

// A struct with an implicit default constructor and a static method.
struct Accumulator {
    void push(double value);
    double total() const;
    std::size_t count() const;
    void reset();
    static int version();

private:
    double total_ = 0.0;
    std::size_t count_ = 0;
};

bool echo_bool(bool value);
signed char echo_schar(signed char value);
unsigned char echo_uchar(unsigned char value);
short echo_short(short value);
unsigned short echo_ushort(unsigned short value);
int echo_int(int value);
unsigned int echo_uint(unsigned int value);
long echo_long(long value);
unsigned long echo_ulong(unsigned long value);
long long echo_llong(long long value);
unsigned long long echo_ullong(unsigned long long value);
float echo_float(float value);
double echo_double(double value);
std::int8_t echo_int8(std::int8_t value);
std::int16_t echo_int16(std::int16_t value);
std::int32_t echo_int32(std::int32_t value);
std::int64_t echo_int64(std::int64_t value);
std::uint8_t echo_uint8(std::uint8_t value);
std::uint16_t echo_uint16(std::uint16_t value);
std::uint32_t echo_uint32(std::uint32_t value);
std::uint64_t echo_uint64(std::uint64_t value);
std::size_t echo_size(std::size_t value);
// Top-level const is ignored.
std::int32_t echo_const(const std::int32_t value);

// Parameter names that are keywords in C or Python, or used by the generated code.
int sum_renamed(int lambda, int self, int restrict, int out_result);
// Unnamed parameter.
int twice(int);

// An overload set in the global namespace.
double scale(double value);
double scale(double value, double factor);

// Throws std::domain_error when divisor is 0.
int divide(int dividend, int divisor);

void do_nothing();
