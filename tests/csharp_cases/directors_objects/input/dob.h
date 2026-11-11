// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <cstdint>
#include <memory>

namespace dob {

// Has a director: a class of the user that derives from it is the object that C++ lends.
class Node {
public:
    explicit Node(std::int32_t value);
    virtual ~Node();
    virtual std::int32_t value() const;
    std::int32_t call_value() const;
    void set(std::int32_t value);
    static std::int32_t alive();

private:
    std::int32_t value_;
};

// No virtual destructor, and a class that derives from it: C++ destroys the object that an override gives
// through Plain, so only an object of exactly that class can be given.
class Plain {
public:
    explicit Plain(std::int32_t value);
    ~Plain();
    std::int32_t value() const;

private:
    std::int32_t value_;
};

class Derived : public Plain {
public:
    explicit Derived(std::int32_t value);
};

class Visitor {
public:
    Visitor();
    virtual ~Visitor();

    virtual std::int32_t seen(const Node& node) const;
    virtual std::int32_t touch(Node& node) const;
    virtual std::int32_t pointer(const Node* node) const;
    virtual std::int32_t take(std::unique_ptr<Node> node) const;
    virtual std::unique_ptr<Node> make(std::int32_t value) const;
    virtual std::unique_ptr<Plain> make_plain(std::int32_t value) const;
    virtual std::int32_t both(std::unique_ptr<Node> a, const Node& b, std::unique_ptr<Node> c) const;
    virtual std::int32_t marker() const;
    virtual bool same(const Visitor* other) const;

    std::int32_t call_seen(const Node& node) const;
    std::int32_t call_touch(Node& node) const;
    std::int32_t call_pointer(const Node* node) const;
    std::int32_t call_take(std::unique_ptr<Node> node) const;
    std::unique_ptr<Node> call_make(std::int32_t value) const;
    std::unique_ptr<Plain> call_make_plain(std::int32_t value) const;
    std::int32_t call_both(std::unique_ptr<Node> a, const Node& b, std::unique_ptr<Node> c) const;
    bool call_same(const Visitor* other) const;
    // C++ uses the object after the override returned.
    std::int32_t call_seen_then_value(const Node& node) const;
    // The first override fails: the second must not run, and the object that it would get is destroyed.
    std::int32_t call_marker_then_take(std::unique_ptr<Node> node) const;
    std::int32_t call_seen_on_thread(const Node& node) const;
};

// Keeps a Visitor that another language made, and asks it.
class Keeper {
public:
    Keeper();
    ~Keeper();
    void put(std::unique_ptr<Visitor> visitor);
    std::unique_ptr<Visitor> release();
    bool ask_same() const;
    std::int32_t ask_seen(const Node& node) const;

private:
    std::unique_ptr<Visitor> visitor_;
};

}  // namespace dob
