// SPDX-License-Identifier: Apache-2.0
// What C++ keeps a pointer to: the object that a method is called on, and texts.
#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <string_view>

namespace keep {

class Latch;

// A mutex: the Latch that it gives keeps a pointer to it.
class Gate {
public:
    Gate();
    ~Gate();
    Gate(const Gate&) = delete;
    Gate& operator=(const Gate&) = delete;

    // The Latch keeps a pointer to this Gate.
    std::unique_ptr<Latch> latch();
    // The program keeps a pointer to this Gate (a registry): for ever.
    void pin();
    std::int32_t latches() const;
    // The number of Gate objects that exist.
    static std::int32_t live();
    // Whether every Gate that was pinned is still there.
    static bool pinnedAlive();

private:
    friend class Latch;
    std::uint32_t magic_;
    std::int32_t latches_;
};

// A guard: it reads the Gate that it was made by.
class Latch {
public:
    // It keeps a pointer to the Gate (a constructor does, unless the annotations say it only
    // reads it).
    Latch(const Gate& gate, std::int32_t number);
    ~Latch();
    // Whether its Gate is still there (it reads the Gate: a Gate that is gone is not).
    bool open() const;
    std::int32_t number() const;
    // The number of Latch objects that exist.
    static std::int32_t live();

private:
    const Gate* gate_;
    std::int32_t number_;
};

// It stores the texts that it is given, without copying them.
class Label {
public:
    Label();
    explicit Label(const char* title);
    ~Label();
    Label(const Label&) = delete;
    Label& operator=(const Label&) = delete;

    void setRaw(const char* text);
    void setView(std::string_view text);
    void setMaybe(const char* text);
    // A copy: there is nothing to keep.
    void setCopy(const std::string& text);
    // Whether every text that it keeps is what it was given (a text that is gone or changed is
    // not).
    bool intact() const;
    // The number of characters that it keeps in all.
    std::int32_t kept() const;
    // The text that is kept for ever (see keepView).
    static void keepView(std::string_view text);
    static bool keptIntact();
    // A helper for the tests: allocates blocks of many sizes, fills them and frees them, so that
    // memory that was freed is reused and overwritten before something reads it.
    static void scribble();

private:
    // Each text that is kept: where it is, how long, and what it added up to when it was given.
    const char* data_[4];
    std::size_t sizes_[4];
    std::uint32_t sums_[4];
    std::string copy_;
};

}  // namespace keep
