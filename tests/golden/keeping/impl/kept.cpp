// SPDX-License-Identifier: Apache-2.0
#include "kept.h"

#include <cstring>
#include <stdexcept>
#include <vector>

namespace keep {

namespace {

constexpr std::uint32_t kAlive = 0xC0FFEEu;
std::int32_t g_gates = 0;
std::int32_t g_latches = 0;
std::int32_t g_pens = 0;
std::int32_t g_stamps = 0;
std::int32_t g_bad = 0;

// Never destroyed: what is pinned is reachable from here until the program ends.
std::vector<const Gate*>& pinned()
{
    static std::vector<const Gate*>* gates = new std::vector<const Gate*>();
    return *gates;
}

std::uint32_t hash(const char* data, std::size_t size)
{
    std::uint32_t h = 2166136261u;
    for (std::size_t i = 0; i < size; ++i) {
        h = (h ^ static_cast<unsigned char>(data[i])) * 16777619u;
    }
    return h;
}

enum Slot { kTitle, kRaw, kView, kMaybe, kSlots };

// The text that is kept for ever.
const char* g_data = nullptr;
std::size_t g_size = 0;
std::uint32_t g_sum = 0;

}  // namespace

Gate::Gate() : magic_(kAlive), latches_(0) { ++g_gates; }

Gate::~Gate()
{
    magic_ = 0;
    --g_gates;
}

std::unique_ptr<Latch> Gate::latch()
{
    ++latches_;
    return std::unique_ptr<Latch>(new Latch(*this, latches_));
}

void Gate::pin() { pinned().push_back(this); }

std::int32_t Gate::latches() const { return latches_; }

std::int32_t Gate::live() { return g_gates; }

bool Gate::pinnedAlive()
{
    for (const Gate* gate : pinned()) {
        if (gate->magic_ != kAlive) {
            return false;
        }
    }
    return true;
}

Latch::Latch(const Gate& gate, std::int32_t number) : gate_(&gate), number_(number)
{
    ++g_latches;
}

Latch::~Latch()
{
    // Its Gate is destroyed after it: the destructor reads it.
    if (gate_->magic_ != kAlive) {
        ++g_bad;
    }
    --g_latches;
}

bool Latch::open() const { return gate_->magic_ == kAlive; }

std::int32_t Latch::number() const { return number_; }

std::int32_t Latch::live() { return g_latches; }

Label::Label() : data_{nullptr, nullptr, nullptr, nullptr}, sizes_{0, 0, 0, 0}, sums_{0, 0, 0, 0} {}

Label::Label(const char* title) : Label()
{
    data_[kTitle] = title;
    sizes_[kTitle] = std::strlen(title);
    sums_[kTitle] = hash(title, sizes_[kTitle]);
}

Label::Label(std::string_view title) : Label()
{
    data_[kTitle] = title.data();
    sizes_[kTitle] = title.size();
    sums_[kTitle] = hash(title.data(), title.size());
}

Label::~Label()
{
    // What it points to is destroyed after it: the destructor reads it.
    if (!intact()) {
        ++g_bad;
    }
}

void Label::setRaw(const char* text)
{
    data_[kRaw] = text;
    sizes_[kRaw] = std::strlen(text);
    sums_[kRaw] = hash(text, sizes_[kRaw]);
}

void Label::setView(std::string_view text)
{
    data_[kView] = text.data();
    sizes_[kView] = text.size();
    sums_[kView] = hash(text.data(), text.size());
}

void Label::setMaybe(const char* text)
{
    data_[kMaybe] = text;
    sizes_[kMaybe] = text != nullptr ? std::strlen(text) : 0;
    sums_[kMaybe] = text != nullptr ? hash(text, sizes_[kMaybe]) : 0;
}

void Label::setRawThrow(const char* text)
{
    setRaw(text);
    throw std::runtime_error("setRawThrow: stored, then failed");
}

void Label::setCopy(const std::string& text) { copy_ = text; }

bool Label::intact() const
{
    for (int slot = 0; slot < kSlots; ++slot) {
        if (data_[slot] != nullptr && hash(data_[slot], sizes_[slot]) != sums_[slot]) {
            return false;
        }
    }
    return true;
}

std::int32_t Label::kept() const
{
    std::size_t total = 0;
    for (int slot = 0; slot < kSlots; ++slot) {
        total += sizes_[slot];
    }
    return static_cast<std::int32_t>(total);
}

void Label::keepView(std::string_view text)
{
    g_data = text.data();
    g_size = text.size();
    g_sum = hash(text.data(), text.size());
}

bool Label::keptIntact() { return g_data == nullptr || hash(g_data, g_size) == g_sum; }

std::int32_t Label::badDestructions() { return g_bad; }

void Label::scribble()
{
    std::vector<char*> blocks;
    for (std::size_t size = 1; size < 600; ++size) {
        for (int copy = 0; copy < 8; ++copy) {
            char* block = new char[size];
            std::memset(block, 0xEE, size);
            blocks.push_back(block);
        }
    }
    for (char* block : blocks) {
        delete[] block;
    }
}

Pen::Pen() : magic_(kAlive), data_{nullptr, nullptr}, sizes_{0, 0}, sums_{0, 0} { ++g_pens; }

Pen::~Pen()
{
    magic_ = 0;
    --g_pens;
}

std::unique_ptr<Stamp> Pen::stamp() { return std::unique_ptr<Stamp>(new Stamp(*this)); }

void Pen::note(const char* text)
{
    data_[0] = text;
    sizes_[0] = std::strlen(text);
    sums_[0] = hash(text, sizes_[0]);
}

void Pen::noteView(std::string_view text)
{
    data_[1] = text.data();
    sizes_[1] = text.size();
    sums_[1] = hash(text.data(), text.size());
}

bool Pen::notesIntact() const
{
    for (int slot = 0; slot < 2; ++slot) {
        if (data_[slot] != nullptr && hash(data_[slot], sizes_[slot]) != sums_[slot]) {
            return false;
        }
    }
    return true;
}

std::int32_t Pen::live() { return g_pens; }

Stamp::Stamp(const Pen& pen) : pen_(&pen) { ++g_stamps; }

Stamp::~Stamp()
{
    if (pen_->magic_ != kAlive) {
        ++g_bad;
    }
    --g_stamps;
}

bool Stamp::open() const { return pen_->magic_ == kAlive; }

std::int32_t Stamp::live() { return g_stamps; }

}  // namespace keep
