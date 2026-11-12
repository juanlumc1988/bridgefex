// SPDX-License-Identifier: Apache-2.0
#include "wire.h"

#include <stdexcept>

std::size_t wire_npos() { return static_cast<std::size_t>(-1); }
std::size_t wire_echo_size(std::size_t value) { return value; }
std::uint64_t wire_echo_u64(std::uint64_t value) { return value; }
std::uint8_t wire_echo_u8(std::uint8_t value) { return value; }
std::uint16_t wire_echo_u16(std::uint16_t value) { return value; }
bool wire_is_200(std::uint8_t value) { return value == 200; }
bool wire_is_50000(std::uint16_t value) { return value == 50000; }
bool wire_is_max32(std::uint32_t value) { return value == 0xFFFFFFFFu; }

Sizes wire_sizes() {
    return Sizes{static_cast<std::size_t>(-1), 0xFFFFFFFFFFFFFFFFull, 200, 50000};
}

Sizes wire_echo(Sizes sizes) { return sizes; }

void wire_fail_latin1() { throw std::runtime_error("caf\xe9 broke"); }
void wire_fail_utf8() { throw std::runtime_error("caf\xc3\xa9 broke"); }
