// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <cstdint>
#include <memory>

namespace life {

class Res {
public:
    explicit Res(std::int32_t id);
    ~Res();
    std::int32_t id() const;
    void set(std::int32_t id);
    // Sleeps, then reads the id: it uses the object while the call lasts.
    std::int32_t slowId(std::int32_t milliseconds) const;
    static std::int32_t live();

private:
    std::int32_t id_;
};

// Keeps a pointer to a Res, and checks in its destructor that the Res is still there.
class Holder {
public:
    Holder();
    ~Holder();
    void set(Res* res);
    // Stores the pointer, then throws.
    void setThenThrow(Res* res);
    // The id of the Res it points to.
    std::int32_t peek() const;
    // A Res that is part of the holder.
    Res* inner();
    static std::int32_t live();
    // The number of holders that were destroyed after the Res they point to.
    static std::int32_t badDestructions();

private:
    Res mine_;
    Res* res_;
};

std::int32_t consume(std::unique_ptr<Res> res);

}  // namespace life
