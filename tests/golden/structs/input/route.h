// SPDX-License-Identifier: Apache-2.0
// Uses the value structs of geo.h, and geo.h is used back through a forward declaration.
#pragma once

#include "geo.h"

namespace route {

struct Leg {
    geo::Point from;
    geo::Point to;
    geo::Kind kind;
};

double length(const Leg& leg);
Leg reverse(const Leg& leg);
geo::Point start(const Leg& leg);

}  // namespace route
