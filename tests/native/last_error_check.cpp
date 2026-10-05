// SPDX-License-Identifier: Apache-2.0
// Checks the generated runtime of the "demo" case: truncation of long
// messages, UTF-8 boundaries, NULL messages and one message per thread.
#define DEMO_C_API_STATIC
#include "demo_runtime_internal.hpp"

#include <cstdio>
#include <cstring>
#include <string>
#include <thread>

namespace {

int failures = 0;

void check(bool condition, const char* what)
{
    if (!condition) {
        std::printf("FAILED: %s\n", what);
        ++failures;
    }
}

}  // namespace

int main()
{
    const std::string long_message(2000, 'x');
    demo_detail::set_last_error(long_message.c_str());
    check(std::strlen(demo_last_error()) == 1023, "long messages are truncated to 1023 bytes");
    check(long_message.compare(0, 1023, demo_last_error()) == 0, "the kept bytes are the first ones");

    const std::string exact(1023, 'y');
    demo_detail::set_last_error(exact.c_str());
    check(exact == demo_last_error(), "a 1023-byte message is kept whole");

    std::string utf8(1022, 'z');
    for (int i = 0; i < 10; ++i) {
        utf8 += "\xC3\xA9";  // U+00E9 in UTF-8
    }
    demo_detail::set_last_error(utf8.c_str());
    check(std::strlen(demo_last_error()) == 1022, "a UTF-8 sequence is not cut in half");

    demo_detail::set_last_error(nullptr);
    check(std::strlen(demo_last_error()) == 0, "NULL becomes an empty message");

    demo_detail::set_last_error("main");
    bool worker_ok = false;
    std::thread worker([&worker_ok]() {
        worker_ok = std::strlen(demo_last_error()) == 0;
        demo_detail::set_last_error("worker");
        worker_ok = worker_ok && std::strcmp(demo_last_error(), "worker") == 0;
    });
    worker.join();
    check(worker_ok, "a new thread starts empty and has its own message");
    check(std::strcmp(demo_last_error(), "main") == 0, "other threads do not overwrite it");

    if (failures == 0) {
        std::printf("last_error OK\n");
    }
    return failures == 0 ? 0 : 1;
}
