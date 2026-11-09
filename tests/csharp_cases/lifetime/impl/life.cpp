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

}  // namespace life
