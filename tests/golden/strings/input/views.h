// SPDX-License-Identifier: Apache-2.0
// std::string_view across the C boundary (C++17).
#pragma once

#include <cstddef>
#include <string_view>

namespace views {

// Points into the argument.
std::string_view first_word(std::string_view s);
std::size_t size_of(const std::string_view& s);
// A default std::string_view, whose data() is NULL.
std::string_view empty();
std::string_view literal();

}  // namespace views
