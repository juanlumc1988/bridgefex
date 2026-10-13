// SPDX-License-Identifier: Apache-2.0
#include "route.h"

#include <cmath>

namespace route {

double length(const Leg& leg)
{
    return std::hypot(leg.to.x - leg.from.x, leg.to.y - leg.from.y);
}

Leg reverse(const Leg& leg)
{
    return Leg{leg.to, leg.from, leg.kind};
}

geo::Point start(const Leg& leg)
{
    return leg.from;
}

}  // namespace route
