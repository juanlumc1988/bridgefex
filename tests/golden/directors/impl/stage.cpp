// SPDX-License-Identifier: Apache-2.0
#include "stage.h"

namespace drc {

Stage::Stage() : steps_(2) {}

Stage::Stage(std::int32_t steps) : steps_(steps) {}

Stage::~Stage() = default;

std::string Stage::run(Mode mode)
{
    std::string result;
    for (std::int32_t number = 1; number <= steps_; ++number) {
        result += step(mode, number);
    }
    const Point start = origin();
    return result + "@" + std::to_string(static_cast<long long>(start.x));
}

std::int32_t Stage::steps() const
{
    return steps_;
}

Point Stage::origin() const
{
    return Point{0.0, 0.0};
}

}  // namespace drc
