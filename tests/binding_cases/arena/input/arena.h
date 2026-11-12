// SPDX-License-Identifier: Apache-2.0
// JNI only: the memory of the calls that the JNI bindings make, and what lives while they do.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

// The bytes of the text, added up: it reads all of it, so that memory that was freed is seen.
std::size_t arena_checksum(const std::string& text);

// FNV-1a over the bytes of each string and a separator: the order and the contents count.
std::uint64_t arena_hash(const std::vector<std::string>& strings);

// An object that something can hold on to (the cleaner that destroys it, the class loader).
class Box {
public:
    Box();
    int value() const;

private:
    int value_;
};
