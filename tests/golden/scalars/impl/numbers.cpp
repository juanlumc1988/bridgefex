// SPDX-License-Identifier: Apache-2.0
#include "numbers.h"

#include <stdexcept>

void Accumulator::push(double value)
{
    total_ += value;
    ++count_;
}

double Accumulator::total() const
{
    return total_;
}

std::size_t Accumulator::count() const
{
    return count_;
}

void Accumulator::reset()
{
    total_ = 0.0;
    count_ = 0;
}

int Accumulator::version()
{
    return 3;
}

bool echo_bool(bool value) { return value; }
signed char echo_schar(signed char value) { return value; }
unsigned char echo_uchar(unsigned char value) { return value; }
short echo_short(short value) { return value; }
unsigned short echo_ushort(unsigned short value) { return value; }
int echo_int(int value) { return value; }
unsigned int echo_uint(unsigned int value) { return value; }
long echo_long(long value) { return value; }
unsigned long echo_ulong(unsigned long value) { return value; }
long long echo_llong(long long value) { return value; }
unsigned long long echo_ullong(unsigned long long value) { return value; }
float echo_float(float value) { return value; }
double echo_double(double value) { return value; }
std::int8_t echo_int8(std::int8_t value) { return value; }
std::int16_t echo_int16(std::int16_t value) { return value; }
std::int32_t echo_int32(std::int32_t value) { return value; }
std::int64_t echo_int64(std::int64_t value) { return value; }
std::uint8_t echo_uint8(std::uint8_t value) { return value; }
std::uint16_t echo_uint16(std::uint16_t value) { return value; }
std::uint32_t echo_uint32(std::uint32_t value) { return value; }
std::uint64_t echo_uint64(std::uint64_t value) { return value; }
std::size_t echo_size(std::size_t value) { return value; }
std::int32_t echo_const(const std::int32_t value) { return value; }

int sum_renamed(int lambda, int self, int restrict, int out_result)
{
    return lambda + self + restrict + out_result;
}

int twice(int value)
{
    return 2 * value;
}

double scale(double value)
{
    return scale(value, 2.0);
}

double scale(double value, double factor)
{
    return value * factor;
}

int divide(int dividend, int divisor)
{
    if (divisor == 0) {
        throw std::domain_error("division by zero");
    }
    return dividend / divisor;
}

void do_nothing() {}
