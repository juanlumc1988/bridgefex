// SPDX-License-Identifier: Apache-2.0
#include "arena.h"

std::size_t arena_checksum(const std::string& text) {
    std::size_t sum = 0;
    for (unsigned char byte : text) {
        sum += byte;
    }
    return sum;
}

std::uint64_t arena_hash(const std::vector<std::string>& strings) {
    std::uint64_t hash = 14695981039346656037ull;
    for (const std::string& text : strings) {
        for (unsigned char byte : text) {
            hash = (hash ^ byte) * 1099511628211ull;
        }
        hash = (hash ^ 0xFF) * 1099511628211ull;
    }
    return hash;
}

Box::Box() : value_(42) {}
int Box::value() const { return value_; }
