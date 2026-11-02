// SPDX-License-Identifier: Apache-2.0
// Test input: a class of another header, with only protected constructors and a
// protected pure virtual method, and what it uses from the first header.
#pragma once

#include <cstdint>
#include <string>

#include "shape.h"

namespace drc {

class Stage {
public:
    virtual ~Stage();
    // Runs the steps: the protected virtual methods.
    std::string run(Mode mode);
    std::int32_t steps() const;

protected:
    Stage();
    explicit Stage(std::int32_t steps);
    virtual std::string step(Mode mode, std::int32_t number) = 0;
    virtual Point origin() const;

private:
    std::int32_t steps_;
};

}  // namespace drc
