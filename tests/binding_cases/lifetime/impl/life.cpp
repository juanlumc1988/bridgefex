// SPDX-License-Identifier: Apache-2.0
#include "life.h"

#include <atomic>
#include <chrono>
#include <mutex>
#include <set>
#include <stdexcept>
#include <thread>

namespace life {

namespace {
std::mutex registry_mutex;
std::set<const Res*> alive;
std::atomic<std::int32_t> live_res{0};
std::atomic<std::int32_t> live_holders{0};
std::atomic<std::int32_t> bad{0};
}  // namespace

Res::Res(std::int32_t id) : id_(id)
{
    std::lock_guard<std::mutex> lock(registry_mutex);
    alive.insert(this);
    ++live_res;
}

Res::~Res()
{
    std::lock_guard<std::mutex> lock(registry_mutex);
    alive.erase(this);
    --live_res;
}

std::int32_t Res::id() const
{
    return id_;
}

void Res::set(std::int32_t id)
{
    id_ = id;
}

std::int32_t Res::slowId(std::int32_t milliseconds) const
{
    std::this_thread::sleep_for(std::chrono::milliseconds(milliseconds));
    return id_;
}

std::int32_t Res::live()
{
    return live_res;
}

Holder::Holder() : mine_(0), res_(&mine_)
{
    ++live_holders;
}

Holder::~Holder()
{
    {
        std::lock_guard<std::mutex> lock(registry_mutex);
        if (res_ != &mine_ && alive.count(res_) == 0) {
            ++bad;
        }
    }
    --live_holders;
}

void Holder::set(Res* res)
{
    res_ = res;
}

void Holder::setThenThrow(Res* res)
{
    res_ = res;
    throw std::runtime_error("stored, then failed");
}

std::int32_t Holder::peek() const
{
    return res_->id();
}

Res* Holder::inner()
{
    return &mine_;
}

std::int32_t Holder::live()
{
    return live_holders;
}

std::int32_t Holder::badDestructions()
{
    return bad;
}

std::int32_t consume(std::unique_ptr<Res> res)
{
    return res->id();
}

Shelf::Shelf() = default;
Shelf::~Shelf() = default;
Holder& Shelf::slot() { return slot_; }

Q::Q() : q_(1) {}
Q::~Q() = default;
std::int32_t Q::q() const { return q_; }
void Q::setQ(std::int32_t value) { q_ = value; }

R::R() : r_(2) {}
R::~R() = default;
std::int32_t R::r() const { return r_; }
void R::setR(std::int32_t value) { r_ = value; }

P::P() : p_(3) {}
P::~P() = default;
std::int32_t P::p() const { return p_; }

N::N() = default;
M::M() = default;
M::~M() = default;

std::int32_t takeBoth(std::unique_ptr<Q> first, std::unique_ptr<R> second)
{
    return first->q() * 10 + second->r();
}

std::int32_t takeP(std::unique_ptr<P> p)
{
    return p->p();
}

const N& constN()
{
    static const N instance;
    return instance;
}

std::int32_t touch(R& r)
{
    r.setR(r.r() + 1);
    return r.r();
}

std::int32_t look(const R& r)
{
    return r.r();
}

}  // namespace life
