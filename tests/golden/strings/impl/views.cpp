// SPDX-License-Identifier: Apache-2.0
#include "views.h"

namespace views {

std::string_view first_word(std::string_view s)
{
    return s.substr(0, s.find(' '));
}

std::size_t size_of(const std::string_view& s)
{
    return s.size();
}

std::string_view empty()
{
    return std::string_view();
}

std::string_view literal()
{
    return "a literal";
}

}  // namespace views
