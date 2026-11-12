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

// Several bases, to give and to read the object as one that is not at its address.
class Q {
public:
    Q();
    virtual ~Q();
    std::int32_t q() const;
    void setQ(std::int32_t value);

private:
    std::int32_t q_;
};

class R {
public:
    R();
    virtual ~R();
    std::int32_t r() const;
    void setR(std::int32_t value);

private:
    std::int32_t r_;
};

// No virtual destructor: C++ cannot destroy an object of a derived class through it.
class P {
public:
    P();
    ~P();
    std::int32_t p() const;

private:
    std::int32_t p_;
};

class N : public Q, public R {
public:
    N();
};

class M : public Q, public P {
public:
    M();
    ~M() override;
};

std::int32_t takeBoth(std::unique_ptr<Q> first, std::unique_ptr<R> second);
std::int32_t takeP(std::unique_ptr<P> p);
// The object N that is there for ever, through a const reference.
const N& constN();
std::int32_t touch(R& r);
std::int32_t look(const R& r);

}  // namespace life
