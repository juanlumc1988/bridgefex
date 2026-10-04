// SPDX-License-Identifier: Apache-2.0
// Test input: corner cases that must be accepted and translated correctly.
#pragma once

#include <cstddef>
#include <cstdint>

namespace edge {

// Declared through a function type.
typedef int BinaryOp(int, int);
BinaryOp add;

// Parameter names that the generated C would otherwise mistake for a type or
// for one of its own macros.
std::int64_t combine(std::int32_t int32_t, std::size_t size_t, int EDGE_OK);

// Python names that would hide a builtin or a generated global.
int getattr(int value);
int threading();

// A final class: a private virtual method is fine, and the destructor may throw.
class Registry final {
public:
    Registry();
    ~Registry() noexcept(false);
    int classmethod() const;
    int staticmethod() const;
    // Makes the destructor throw.
    void throwOnDestroy(bool enabled);
    // Throws std::runtime_error("failed on purpose").
    void fail() const;
    static int liveCount();

private:
    virtual int hook() const;
    bool throw_on_destroy_ = false;
};

}  // namespace edge
