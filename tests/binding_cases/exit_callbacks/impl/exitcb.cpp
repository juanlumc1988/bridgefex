// SPDX-License-Identifier: Apache-2.0
#include "exitcb.h"

#include <cstdio>
#include <thread>
#include <utility>

namespace exitcb {

namespace {

struct Holder {
    std::function<void(std::int32_t)> handler;
    std::int32_t mode = 0;

    ~Holder()
    {
        if (!handler || mode == 0) {
            return;
        }
        std::fputs("exitcb: destructor\n", stderr);
        if (mode == 1) {
            handler(1);
        } else {
            std::thread worker([this] { handler(2); });
            worker.join();
        }
        std::fputs("exitcb: done\n", stderr);
    }
};

Holder g_holder;

}  // namespace

void keep(std::function<void(std::int32_t)> handler, std::int32_t mode)
{
    g_holder.handler = std::move(handler);
    g_holder.mode = mode;
}

void call(std::int32_t value)
{
    if (g_holder.handler) {
        g_holder.handler(value);
    }
}

}  // namespace exitcb
