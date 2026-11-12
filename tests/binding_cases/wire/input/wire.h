// SPDX-License-Identifier: Apache-2.0
// Java only: what crosses as the bits that C++ gave, and a message of an error that is not UTF-8.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

// std::string::npos: what C++ gives for "not found", and takes back.
std::size_t wire_npos();
std::size_t wire_echo_size(std::size_t value);
std::uint64_t wire_echo_u64(std::uint64_t value);
std::uint8_t wire_echo_u8(std::uint8_t value);
std::uint16_t wire_echo_u16(std::uint16_t value);
// Whether the argument arrived as the unsigned value (200, 50000, 2^32 - 1).
bool wire_is_200(std::uint8_t value);
bool wire_is_50000(std::uint16_t value);
bool wire_is_max32(std::uint32_t value);

struct Sizes {
    std::size_t count;
    std::uint64_t big;
    std::uint8_t byte;
    std::uint16_t word;
};

Sizes wire_sizes();
Sizes wire_echo(Sizes sizes);

// Throws std::runtime_error whose message is not UTF-8 (Latin 1).
void wire_fail_latin1();
// Throws std::runtime_error with a message that is UTF-8.
void wire_fail_utf8();
