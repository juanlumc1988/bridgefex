# bridgefex

Generate a pure C API from C++ headers, plus ready-to-use bindings for other languages (one file per header).

```
C++ headers ──libclang──▶ model ──▶ pure C layer (extern "C") ──▶ Python (ctypes) bindings
                                                                └▶ more languages later
```

bridgefex reads your C++ headers with libclang and writes:

1. **A C layer**: a C header and a C++ source file per input header. The source wraps the C++ API behind `extern "C"` functions, opaque handles and status codes, so no C++ type or exception crosses the boundary.
2. **Bindings** for another language that call the C layer. The first target is Python (`ctypes`, no compiled extension needed).

There is no runtime library to ship. The generated sources are compiled into the same shared library as your code, which makes the tool suitable for embedded and cross-compiled projects.

> **Status: prototype.** The supported subset of C++ is small, and it is documented below. Anything outside it is rejected with a clear error, never translated incorrectly.

## Example

Input, `counter.h`:

```cpp
#include <cstdint>

namespace demo {
class Counter {
public:
    explicit Counter(std::int32_t start);
    void increment();               // throws std::overflow_error after 42
    std::int32_t value() const;
};
}
```

Generated C API, `counter_c.h` (abridged):

```c
typedef struct demo_Counter demo_Counter;   /* opaque handle */

DEMO_NODISCARD DEMO_API demo_status demo_Counter_create(demo_Counter** out_self, int32_t start);
DEMO_API void demo_Counter_destroy(demo_Counter* self);
DEMO_NODISCARD DEMO_API demo_status demo_Counter_increment(demo_Counter* self);
DEMO_NODISCARD DEMO_API demo_status demo_Counter_value(const demo_Counter* self, int32_t* out_result);
```

Python:

```python
import demo
from demo import counter

demo.load("/path/to/libdemo.so")  # or set DEMO_LIBRARY
with counter.Counter(41) as c:
    c.increment()
    print(c.value())  # 42
    try:
        c.increment()
    except demo.Error as error:  # the C++ exception, as a Python exception
        print(error.status_name, error)
```

Complete examples, with their expected output, are in [`tests/golden`](tests/golden).

## Installation

Requirements:

- Python 3.12 or newer.
- **libclang 20**, including the clang resource headers (e.g. `stddef.h`).
  - Ubuntu 24.04: `sudo apt install libclang1-20 libclang-common-20-dev`
  - WSL2 with Ubuntu 24.04: [`scripts/setup-wsl.sh`](scripts/setup-wsl.sh) installs this and everything the tests need (see Development).
  - Windows: the official LLVM 20 installer, which installs `C:\Program Files\LLVM\bin\libclang.dll`.
  - Elsewhere: any LLVM 20 installation. Use `--libclang PATH` or the `BRIDGEFEX_LIBCLANG` environment variable if it is not found.
- To build the generated code: any C++11 compiler (GCC, Clang, MSVC).

Then:

```sh
pip install .            # or: pip install -e '.[dev]' for development
```

The `clang` Python package (libclang's bindings) is installed automatically. Its major version must match the libclang library, and bridgefex checks this when it starts.

> The PyPI package called `libclang` (which bundles the library) is not supported. Its latest release is libclang 18 and it lacks the clang resource headers.

## Usage

```sh
bridgefex --module demo --output out include/counter.h include/geometry.h
```

| Option | Meaning |
|---|---|
| `-m`, `--module NAME` | Module name, required. It prefixes the runtime C symbols (`demo_status`, `demo_last_error`, `DEMO_API`...) and names the Python package. Lowercase letters and digits, with single `_` between them. |
| `-o`, `--output DIR` | Output directory. |
| `--lang {python,none}` | Bindings to generate on top of the C layer (default: `python`). |
| `--std STD` | C++ standard used to parse the headers (default: `c++17`). C++11 or later: the generated code needs it. |
| `-I DIR`, `-D NAME[=VALUE]` | Include directories and macros for parsing. |
| `--clang-arg=ARG` | Any other libclang argument. |
| `--include-root DIR` | Make the `#include` lines of the generated sources relative to `DIR` (default: each header by its file name). |
| `--annotations FILE` | TOML file that says who owns the objects that cross the boundary (repeatable; see Ownership). |
| `--libclang PATH` | libclang shared library. |
| `--no-verify` | Do not compile the generated C layer with libclang before writing it (see below). |

Output:

```
out/
├── c/
│   ├── demo_runtime.h            shared: status codes, export macro, demo_last_error()
│   ├── demo_runtime.cpp
│   ├── demo_runtime_internal.hpp helpers for the generated sources (not public)
│   ├── counter_c.h               one header and one source per input header
│   ├── counter_c.cpp
│   └── ...
└── python/
    └── demo/
        ├── __init__.py           load(), Error, status constants
        ├── _runtime.py
        ├── counter.py            one module per input header
        └── ...
```

Build `out/c/*.cpp` into the same shared library as your code, or into a separate one linked against it. Add `out/c` and the directories of your headers to the include path. The generated sources include your headers with quotes, so `-iquote` is enough for the latter (GCC, Clang). Unlike `-I`, it does not let a header of yours named like a standard one (`time.h`) replace the standard header. MSVC has no `-iquote`: if a header of yours has such a name, generate with `--include-root` set to a parent directory and put that directory on `/I`. The verification (below) warns about such files: those named like a standard header, and those named like a header that the standard headers include (`features.h`, `bits/wordsize.h`...), unless the directory is a system include directory already. For example:

```sh
g++ -std=c++17 -shared -fPIC -fvisibility=hidden -I out/c -iquote include \
    out/c/*.cpp src/*.cpp -o libdemo.so
```

The generated sources define `DEMO_C_API_BUILD`, so the functions are exported (`__declspec(dllexport)` on Windows, default visibility elsewhere). Define `DEMO_C_API_STATIC` when building or using a static library.

Generation is all-or-nothing. If any header contains something unsupported, every problem is reported as `file:line:column: message` and no file is written. Existing files in the output directory are overwritten; stale ones are not removed.

**Verification.** Some C++ rules are only checked by a compiler: default arguments that make a call ambiguous, deleted or private `operator new`, implicitly deleted constructors, `consteval`, and so on. Before writing anything, bridgefex therefore compiles every generated C++ source against your header, and the generated C headers as C23, with libclang. Errors point at the declaration whose wrapper does not compile, and warnings in generated code (a call to a `[[deprecated]]` function, for example) are reported too. The verification uses the parsing options (`--std`, `-I`, `-D`...) plus the header's directory, or `--include-root`, as an `-iquote` directory. Options such as `-Werror` are left out there: the warnings of your headers are reported when parsing them. `--no-verify` skips it, and the warnings it gives.

## Supported C++ (prototype)

| Supported | Notes |
|---|---|
| Classes and structs at namespace scope | Public constructors (an abstract class has none), destructor, methods, static methods, virtual methods. The implicit default constructor is used when no constructor is declared. A destructor may throw (`noexcept(false)`); the destroy function swallows the exception. Private virtual methods are fine if the destructor is virtual or the class is `final`. |
| Free functions | Including overloads, also across headers of the same module, and functions declared through a function type (`typedef int Op(int); Op f;`). |
| Namespaces | Nested namespaces too. |
| Scalar types by value | `bool`; `signed char`, `short`, `int`, `long`, `long long` and their unsigned versions; `float`, `double`; `int8_t` … `uint64_t`, `size_t` (with or without `std::`). `const` values and `const T&` of these are passed by value. |
| Enums | Scoped and unscoped, at namespace scope or public in a wrapped class, also `typedef enum {...} Name;`, as parameters and results (`const E&` too). An enum may be declared opaquely (`enum class E : int;`) in a header and defined in another wrapped header. The underlying type, when fixed, must be a supported integer type other than `bool` and `size_t`. Without a fixed underlying type, every enumerator must fit in `int`. |
| Value structs | Classes with public data members only (and static ones, which are skipped), as parameters (`T`, `const T`, `const T&`) and results (`T`, `const T&`), also `typedef struct {...} Name;` and structs of another wrapped header. Members: scalars, enums, value structs and one-dimensional arrays of them; default member initializers, `alignas` and `#pragma pack` are fine. A value struct may declare a default constructor, copy and move operations and a destructor, and public enums, but no other constructors or methods yet. |
| `std::vector<T>` | `T` is a scalar (`bool` too), an enum, a value struct or `std::string`; with the default allocator. Parameters `V`, `const V`, `const V&` and `V&&`, results `V` and `const V&`. Aliases such as `using Bytes = std::vector<std::uint8_t>;` are fine; alias templates only if they are the container of their own parameters, in order (`template <class T> using Vec = std::vector<T>;`, not `template <class T> using Fixed = std::vector<std::int64_t>;`). |
| `std::array<T, N>` | `T` is a scalar, an enum or a value struct, `N` at least 1. The same forms as `std::vector`. |
| `std::span<const T>` | C++20, with a dynamic extent; `T` as for `std::vector`. Parameters `S`, `const S` and `const S&`, results `S` and `const S&`. |
| Strings | `std::string`, `std::string_view` (C++17) and `const char*`, as parameters and results, also through aliases. `const` values and `const T&` of `std::string` and `std::string_view` are passed by value. Not `std::string&` (an output parameter), `std::string&&`, `char*`, other character types (`std::wstring`, `std::u8string`...) or custom traits and allocators. |
| `std::function<R(A...)>` | Parameters (`F`, `const F`, `const F&`, `F&&`), also through a plain alias, of free functions, methods and constructors. Arguments: scalars (`const T&` too), enums, value structs and the three string types. Results: `void`, scalars and enums. See Callbacks below. |
| Inheritance | Public base classes that are wrapped classes of the module (in the same header or another one), also several. A derived object is accepted wherever its base class is, and its virtual methods run the override of its class. See Inheritance. |
| Wrapped classes | The classes of the module (also those of another header of it) as parameters: `T&`, `const T&`, `T*`, `const T*`, `T` (a copy) and `std::unique_ptr<T>` (also `&&`). As results: `T` and `std::unique_ptr<T>` (new objects that the caller owns), `T&` and `const T&` (borrowed), and `T*` and `const T*` when the annotations say who owns them. See Ownership. |

Rejected with an error, for now:

- Templates.
- Virtual base classes, base classes that are not wrapped classes of the module (a class of the standard library, a template, a value struct), a class that inherits the same base twice, an abstract class passed or returned by value. Private and protected base classes are not part of the API: nothing is exposed through them, and a `using` declaration that makes one of their members public is ignored.
- Operators and conversion functions.
- Public data members, nested types other than enums.
- Anonymous enums (`enum { kSize = 4 };`), enums that no wrapped header defines, `std::byte` and the other enums of the standard library.
- Unions, global variables, `extern "C"` blocks.
- Anonymous and inline namespaces.
- Pointers and non-const references, except to the wrapped classes; `T&&`, `std::shared_ptr`, `std::weak_ptr`, `std::unique_ptr` with a deleter, to a const class, of an array or passed by reference; classes of headers that are not wrapped, and a class as a container element, a callback argument or a member of a value struct.
- Function pointers, `std::function` results, `std::function&` and `std::function*`, callbacks that take containers or other callbacks, or that return strings, structs or references, variadic signatures, and `std::function` through an alias template.
- Classes of the standard library other than the containers above (`std::map`, `std::pair`, `std::optional`...), plain `char`, `wchar_t`, `long double`.
- Containers of other types (`char`, `const char*`, `std::string_view`, pointers, other containers; `std::string` in a `std::array`), vectors with another allocator (`std::pmr::vector`), `std::span` of non-const elements (an output buffer) or with a fixed extent, `std::array<T, 0>`, non-const references and pointers to containers, and containers in value structs.
- In value structs: bit-fields, `const`, reference, pointer and string members, `char` arrays, multi-dimensional or empty arrays, anonymous unions, base classes, methods and constructors with parameters, destructors that may throw, copy or move constructors that are deleted, private or explicit. Unions. Non-const references and pointers to value structs.
- Type aliases other than the standard fixed-width ones. A type is only taken as `int64_t`, `size_t`... if it is the standard one: an alias with that name declared by your code (`typedef unsigned size_t;` in your namespace) is rejected.
- Classes with virtual methods but no virtual destructor (unless `final`).
- Non-ASCII names, and names that would make a generated C name a keyword or a reserved identifier (`co::yield` gives `co_yield`; a namespace `_impl` gives `_impl_f`; a leading or trailing `_` elsewhere gives `__`).
- Generated C names that clash with each other, with a declaration at global scope or a macro seen by the header, with the generated macros, or with a function, variable, type or macro of the system headers. The system headers are a fixed list of C, POSIX and glibc headers, plus `<windows.h>` on Windows; only the ones that exist on the platform are read, whether the header includes them or not. `sched::yield` would give `sched_yield`, which a C program that links the library would call instead of the system's. A header can therefore be accepted on one platform and rejected on another (module `s` defines `S_OK`, which `<windows.h>` defines too). A `struct`, `union` or `enum` tag of the system headers only clashes with a handle type, which declares a tag too: `link::map` is rejected (`struct link_map`), `tcp::info()` is fine, although C++ code that uses both must then write `struct tcp_info`, as with POSIX `struct stat`.
- Headers named like a generated file (`<module>_runtime.h`, `<module>_types.h`, or `x_c.h` and `x_c_internal.hpp` when `x.h` is wrapped too), unless `--include-root` puts them in a directory: the generated sources would include the generated file instead.

Copy and move constructors, deleted functions, and private or protected members are not part of the API and are skipped.

## The generated C API

- **Names.** Every symbol starts with the C++ namespaces joined by `_`. Declarations in the global namespace use the module name instead, so a wrapper never has the same name as the function it wraps. `demo::geometry::Circle::area` becomes `demo_geometry_Circle_area`.
- **Overloads** get a suffix built from the parameter types: `demo_Counter_add_int32`, `demo_Counter_add_double`, `demo_Counter_create_void`. Only overloaded names get one, so adding an overload renames the existing function. Every generated name is checked for clashes.
- **Parameters** keep their C++ names, except names that the preprocessor or a language would change: keywords of C, C++ and Python, type names (`int32_t`), common macros of the C library and of the platforms (`errno`, `complex`, `I`, `st_mtime`, `unix`...), the generated macros and include guards, and the macros without arguments that the header's translation unit defines, and the generated type names (`demo_Color`, `demo_status`...). These get a trailing `_`, or a number when that name is taken too (`x2`), never `__`. Unnamed parameters become `arg1`, `arg2`...
- **Types.** `<module>_types.h`, which every `<stem>_c.h` includes, declares the handles and enums of all headers, so that a header can use the types of another.
- **Enums.** An enum becomes a `typedef` of its underlying type (`int` when it is not fixed) and one macro per enumerator: `typedef uint8_t demo_Color;` and `#define demo_Color_Red 0`. The macros have the type of the enum (`UINT64_C(2147483648)` for a `uint64_t` enum, so `~` keeps the upper bits) and work in `#if`. Every value of an enum with a fixed underlying type is accepted. Without one, only the values of [dcl.enum] are, the range in the comment of the typedef; others return `ERROR_INVALID_ARGUMENT`. The generated source checks with `static_assert` that the C types and values are the C++ ones, with the compiler and options that build it.
- **Vectors.** A `std::vector<T>` parameter becomes `const CT* v, size_t v_size` (NULL with size 0 is empty), where `CT` is the C type of `T`; a `std::string` element is a `<module>_string {const char* data; size_t size;}`. A result comes back through `CT** out_result, size_t* out_result_size`: one block that `<module>_free()` releases, never NULL on success (1 byte when empty), NULL and 0 after any failure. Strings in it point into the same block and end with a NUL. Sizes are checked for overflow (`ERROR_OUT_OF_MEMORY`).
- **Spans.** A `std::span<const T>` parameter crosses like a vector. For a scalar `T` that has the same type in C, the span points to the caller's memory, without a copy. Other elements (enums, structs, strings) are converted into a copy that lives during the call only, so a function must not keep such a span after it returns, even if the caller keeps its own array. A span result is copied into a block, like a vector result: the memory that it points to stays the library's.
- **Arrays.** A `std::array<T, N>` parameter becomes `const CT a[N]`, a pointer that must not be NULL, to `N` elements. Parameters taken by reference are converted on the heap, so a large array does not overflow the stack. A result is written into `CT out_result[N]`, which the caller provides, and only on success. `N` is the value on the platform that ran bridgefex: a build where it differs (`std::array<int, sizeof(long)>`) fails to compile.
- **Value structs** become a C struct with the same members (renamed like parameters when needed: `from` becomes `from_`), in `<module>_types.h`, with natural layout whatever packing is in effect (`#pragma pack(push, 8)`). They are passed by pointer: `const geo_Point* a` in, `geo_Point* out_result` out. A result is written only on success, so the output may be one of the inputs. `<c_type>_init(T* out)` gives the value of a default-constructed `T`, default member initializers included. The generated sources copy member by member, so the C++ struct may be packed or aligned differently. An array member has the length that it had on the platform that ran bridgefex; a `static_assert` stops a build where it differs. A member of an enum without a fixed underlying type that is out of its range returns `ERROR_INVALID_ARGUMENT`.
- **Handles.** Each class becomes an opaque handle: an incomplete `struct` with the same type in C and C++, so function pointers and control-flow integrity checks see one type. `X_create...` allocates an object and returns it through `X** out_self`, which is set to NULL on failure. `X_destroy(X*)` deletes it and accepts NULL. The generated source converts between handle and class with `reinterpret_cast`.
- **Wrapped classes.** A class passed by reference or pointer is its handle: `const X* x` for `const X&`, `const X*` and a copy (`X`), `X* x` for `X&` and `X*`. A handle is never NULL unless the header says that it may be (see Ownership); NULL gives `ERROR_NULL_ARGUMENT`. A result by value or in a `std::unique_ptr` is a new object, returned through `X** out_result`, which the caller destroys with `X_destroy` (NULL after any failure, and for an empty `std::unique_ptr`). A reference result is written through `X** out_result` too, and is **borrowed**: never NULL, never destroyed by the caller, valid while the object it came from is. A `std::unique_ptr` parameter, and a pointer annotated `transfer`, give the object to the function: **it is consumed whatever the status**, also when the call fails before it reaches the wrapped function (a NULL `self`, another NULL argument, or another argument that cannot be built), and when the wrapped function throws; the caller must not use or destroy it afterwards. For a raw pointer annotated `transfer` that holds from the call on: the function that receives it owns it (if it throws without deleting it, it leaks, and an argument built after the pointer was taken that fails first also leaks it), so prefer `std::unique_ptr`. The comment of each function in the C header says which of these apply, so the API fingerprint covers them. A class passed by value must be copyable (a `static_assert` says so); one returned by value needs C++17 unless it can be moved.
- **Inheritance.** Every class has only its own functions: the methods of a base class are not repeated in the classes that derive from it. A derived pointer is not always the pointer of its base (with several bases, one of them is not at the start of the object), so each class has a conversion to each of its ancestors, direct or not: `X* Derived_as_Base(Derived* self)` and `const X* Derived_as_Base_const(const Derived* self)` (NULL stays NULL, nothing can fail). To call a method of a base class, or to pass the object where a base class is expected, convert it first: `Base_method(Derived_as_Base(d))`. A virtual method is called through any pointer to a class that has it, and runs the override of the object's class; the pointer of a class that is returned as a base class (`std::unique_ptr<Base>`, `Base&`) is destroyed with `Base_destroy`, which needs a virtual destructor (a class with virtual methods must have one, unless it is `final`). An abstract class has no `create` function; its objects come from functions that return them.
- **Errors.** Every function that can fail returns a `<module>_status`:

  | Status | Meaning |
  |---|---|
  | `OK` (0) | Success. |
  | `ERROR_EXCEPTION` | The C++ code threw a `std::exception`. |
  | `ERROR_UNKNOWN_EXCEPTION` | Anything else was thrown. |
  | `ERROR_NULL_ARGUMENT` | A pointer argument was NULL. |
  | `ERROR_OUT_OF_MEMORY` | `std::bad_alloc` was thrown. |
  | `ERROR_INVALID_ARGUMENT` | An argument has a value that the C++ type cannot hold. |

  Results come back through an `out_result` pointer. `<module>_last_error()` returns the message (`what()`) of the last failure on the calling thread. It is a thread-local buffer, truncated to 1 KiB at a UTF-8 boundary, and never NULL; successful calls and destroy functions leave it alone. No exception ever crosses the C boundary, except glibc's thread cancellation (`pthread_exit`, `pthread_cancel`), which must unwind its thread. That works with libstdc++ only: with libc++, a cancellation inside a wrapped call aborts the process, because libc++abi cannot let it through a `catch (...)`.
- **Warnings.** `<MODULE>_NODISCARD` makes compilers warn when a status is ignored. It uses `[[nodiscard]]` in C++17 and C23, and `warn_unused_result` on GCC and Clang otherwise (which GCC does not let a `(void)` cast silence; use `if (f(...)) {}`).
- **Strings.** A `std::string` or `std::string_view` parameter becomes two: `const char* s, size_t s_size`, which may contain NULs. NULL is accepted with a size of 0 (the empty string). The size parameter is named after the C++ parameter (`s_size`), with a trailing `_` if that name is taken. A `const char*` parameter stays one NUL-terminated pointer, which must not be NULL. String results come back through `char** out_result, size_t* out_result_size`: a copy in memory that the caller releases with `<module>_free()`, never with `free()` (the library may use another C runtime, on Windows). The copy also ends with a NUL. A `const char*` result that is NULL gives NULL and 0.
- **Directors.** C++ code that calls a virtual method runs the override of the object's class, and a class of another language cannot be one of those: a *director* is a class that bridgefex derives from yours, whose virtual methods call back through a table of functions that the other language provides. It is asked for by class in the [annotations file](#ownership) (`directors = ["demo_Shape"]`, the C types, before the first table), so nothing is generated, and the library grows by nothing, for the classes that do not ask. The class must have a virtual destructor and not be `final`; **public and protected** virtual methods can be overridden (a `final` one cannot). For `demo::Shape` the header declares: `demo_Shape_vtable`, a struct with `void* user_data`, `void (*release)(void*)` and one function pointer for each overridable virtual method, those of its base classes included (an overload is told apart as in the C functions: `add_int32_int32`); `demo_Shape_director_create<suffix>(demo_Shape** out_object, demo_Shape_director** out_director, const demo_Shape_vtable* vtable, ...)`, one for each constructor that a derived class can call, protected ones too, so an abstract class whose constructors are protected has them, which writes the object (all the functions of the class, `demo_Shape_destroy()` and the conversions to its bases work on it, so whoever owns it can destroy it from the moment it exists) and the director (only for the base calls); `demo_Shape* demo_Shape_director_object(demo_Shape_director*)` gives the object of a director again; and `demo_Shape_director_base_<member>(director, ...)`, which runs the implementation of the class without going through the vtable (what `super().method()` is), for every method that is not pure (a protected one too). The functions of the vtable are like the callbacks: `void f(void* user_data, args..., R* out_result)` with the arguments as for a callback, `out_result` value-initialized beforehand, no status, called on any thread, never throwing nor `longjmp`ing; a string result is given with `<module>_string_sink_set(sink, data, size)`, which copies it (it may contain NULs; not calling it gives the empty string). A NULL member keeps the implementation of the class, with no call, but those of the pure virtual methods must be set (a private pure virtual method too: the director overrides it, since nothing else makes it concrete; a private virtual method that has an implementation is left to the class, as a generated class could not run it): `create` fails with `INVALID_ARGUMENT` and the message names the missing one. `release(user_data)` is called once, when the director is destroyed, and also when `create` fails; after `<module>_disable_callbacks(release)` (with a `release` that is not NULL, as for every callback) the vtable is no longer called, so every method runs the implementation of the class (0 for a pure one); if `<module>_string_sink_set` cannot copy the string (a size that no string can have, or no memory) it is ignored and the string stays as it was. The types are those that cross a callback, plus strings and value structs as results: scalars, enums, strings and value structs; a virtual method with another type (a container, a class, a reference or a `const` class result, a `std::string_view` result), a `volatile` one, one with a dynamic exception specification (`throw(Type)`) or with a `noexcept(expression)`, or one that two unrelated bases both declare and the class does not, cannot be overridden: the implementation of the class stays, and if the method is pure the class cannot have a director (the message says why). A class that no derived class could construct from C (its constructors take types that do not cross, or its implicit default constructor is deleted) cannot have one either; bridgefex says it when it verifies the generated code, with the compiler's message and the place in the annotations file that asked for the director.
- **Callbacks.** A `std::function<R(A...)>` parameter becomes three: `void (*f)(void* user_data, CA..., CR* out_result)`, `void* f_user_data` and `void (*f_release)(void*)`. C++ calls `f` with `user_data` first, then the arguments (a string as `const char*` and `size_t`, a struct as a `const` pointer, valid during the call only), then a pointer to the result, which is value-initialized beforehand: a callback that does not write it gives 0. For an enum without a fixed underlying type, a value outside its range also gives 0. `f` may be called on any thread and must not throw nor `longjmp`. Unless `f_release` is NULL, C++ calls `f_release(f_user_data)` exactly once, when it no longer holds the callback: possibly before the function returns (a callback used only during the call), when the object that stored it is destroyed or replaces it, and also when the function fails. A NULL `f` is an empty `std::function`. `<module>_disable_callbacks(release)` stops calling and releasing the callbacks given with that release function, for a language runtime that shuts down while C++ still holds some; every function that was disabled stays disabled, so several runtimes (sub-interpreters, other languages) can share the library, and `<module>_enable_callbacks(release)` undoes it for one of them (the Python bindings call it when they load the library, in case an earlier interpreter disabled a function at the same address).
- **Outputs.** Outputs that the function allocates (`out_self`, string results) are checked for NULL first, each set to NULL (or 0) right after its own check, so they are NULL after every failure, whatever the cause. Other results are only written on success. The checks come in this order: those outputs, then `self` and the other pointers in parameter order.
- **API fingerprint.** `<module>_api_fingerprint()` returns a hash of the generated public C headers and of the conventions they do not show, such as how memory is returned. The Python bindings refuse to load a library built from a different version of the API.

Language standards: the C headers compile as C99, C11, C17 and C23, and as C++. The C++ sources compile as C++11 through C++23. MSVC is tested with C11/C17/latest and C++14 through latest, since it has no older modes. Tests build all of them with warnings as errors.

## The Python bindings

- One module per header. Classes and functions keep their C++ names. Python keywords, and names the generated code needs (`ctypes`, `threading`...), get a trailing `_` (`from` becomes `from_`). Header names must not be `load`, `Error`, `OK` or `ERROR_*`, which the package itself defines.
- A class with one constructor is created with `Counter(args)`. With several, use the factories named after the C functions: `Counter.create_void()`, `Counter.create_int32(5)`.
- The C++ object is destroyed by `close()`, at the end of a `with` block, or by the garbage collector, whichever comes first. Objects still alive after the exit handlers (`atexit`) are not destroyed, because other threads may still be using them: close them, or use `with`. Objects that an exit handler registered after importing the package releases are destroyed. Using a closed object raises `ValueError`. Copying, pickling or calling `__init__` again raises `TypeError`, because the handle cannot be shared.
- Signals do not leak objects. A handler that raises (`KeyboardInterrupt`) when a constructor returns cannot leak the new object: the C call writes the handle that the garbage collector destroys, and the half-built object is collected with the exception. The garbage collector destroys an object without running Python code (the weak reference's callback is the C destroy function, which ctypes calls with the handle as `_as_parameter_`), so a signal that is pending at that time cannot skip the destruction either.
- Arguments are checked before the call. Integers must fit the C type (`OverflowError` instead of ctypes' silent wrap-around). `bool` parameters only accept `True` and `False`. Floating-point parameters accept real numbers.
- String parameters accept `str`, encoded as UTF-8 (strictly: lone surrogates raise `UnicodeEncodeError`), and bytes-like objects with 1-byte items (`bytes`, `bytearray`, `memoryview`...), which are copied first. A `const char*` parameter refuses NUL characters (`ValueError`), which C++ would not see. String results are decoded as strict UTF-8: on invalid data the `UnicodeDecodeError` keeps the bytes in `.object`. A NULL `const char*` result is `None`. The memory of a result is released even when Ctrl-C interrupts the call.
- Value structs become dataclasses with the members as fields (`geo.Point(1.0, 2.0)`), compared by value, copyable and picklable, with `__slots__`. Omitted fields take their value in a default-constructed C++ struct, which loads the library. Arrays are lists of the exact length (`bytes` too for arrays of bytes), nested structs are dataclasses. Values are checked when they are passed, with the path of the member in messages (`argument 'm.samples[1]' must be in [-32768, 32767]`). Results are new objects: changing them does not change C++ data.
- Callbacks accept any callable, or `None` for an empty `std::function`. Their results are checked like arguments (`OverflowError: the result of callback 'f' must be in [...]`). An exception raised by a callback during the call, on a thread of C++ or of the call, is raised by the call when it returns, and its other callbacks are then skipped (C++ gets 0); a stored callback that an unrelated call runs on another thread belongs to that call (the innermost one that passes callbacks, else `sys.unraisablehook`), never to the call that stored it; `KeyboardInterrupt` too, also when Ctrl-C arrives as a callback starts. C++ cannot be cancelled: a loop of callbacks still runs to its end, without calling Python. A callback that C++ stores is kept alive as long as C++ holds it, also when Ctrl-C arrives as the call returns; one that raises after the call has returned is reported by `sys.unraisablehook`, except `KeyboardInterrupt` on the main thread, which the running call of the bindings (such as an event loop's `run()`) raises when it returns. At exit, the callbacks are disabled before Python shuts down: C++ code that still calls one (a static destructor, a thread) gets 0, and a call that passes a callback raises `RuntimeError`.
- Wrapped classes: a parameter takes an object of the class (or of a subclass), checked with the other arguments, in order (`TypeError`); the handles are read last, in the call, so a closed object raises `ValueError`. A result by value or in a `std::unique_ptr` is a new object that Python owns; `None` for an empty `std::unique_ptr`. A **borrowed** object (a reference or an annotated pointer result) belongs to the C++ object it came from: it keeps that object alive, raises `ValueError` once it, or an object it came from, is closed, destroys nothing when closed, and is read-only when it came through `const` (`TypeError` for non-const methods and parameters). A function that is not a method borrows from the reference and pointer arguments it was given. A `std::unique_ptr` parameter (or a pointer annotated `transfer`) gives the object up: the Python object is closed afterwards, even if the call fails, and cannot be a borrowed object, `self` or given twice; everything is checked before any object is given up. What the objects kept (see `retain`) moves with them. A constructor keeps alive the references and pointers it gets, as long as the object lives (a method keeps nothing unless told so), and `close()` releases them after the destructor.
- Inheritance: a class derives from the Python classes of its public bases (`class Circle(Shape)`), also from another header's, so `isinstance` works, a method of a base is inherited, and a method that a class declares hides the base's, as in C++. A derived object is accepted where its base class is expected (the pointer is converted: with several bases it is not the same address), also to give it up as a `std::unique_ptr<Base>`; if the base class has no virtual destructor only an object of exactly that class is accepted (`TypeError`), since C++ would destroy the derived object through it. Virtual methods run the override of the object's class. An abstract class cannot be instantiated (`TypeError`); a function that returns one gives an object of that class, not of the class of the C++ object (there is no downcast). All wrapped classes derive from `_runtime.Object`, which holds their layout.
- **Overriding virtual methods.** A class that asks for a director in the [annotations file](#ownership) (`directors = ["demo_Shape"]`) can be derived from in Python: a subclass that defines a method with the name of a virtual method of the class (public or protected, also those of its bases; an overload is told apart as in the C API, `add_int32_int32`) makes objects whose C++ virtual calls run that method, also when C++ calls them from a thread of its own or inside another call of the bindings. `super().method()` runs the C++ implementation, never the override again. A class whose constructors are protected, or that is abstract, is created through its subclasses only (`Mine.create_int32(3)` as for any class with several constructors, or `Mine(...)` with one), and a subclass has to override every pure virtual method (`TypeError` otherwise). The arguments and results are those of a callback: the result is checked (`TypeError`, `OverflowError`), a string result is a `str` or bytes, a struct result a dataclass. An exception that an override raises reaches the call of the bindings that made C++ run it, and the remaining overrides of that call are skipped (C++ gets the default result); on a thread that C++ started it goes to `sys.unraisablehook`, as for a callback. The object is kept alive by Python while Python owns it, and by the registry once C++ does (it was given as a `std::unique_ptr`): the object that comes back is a plain wrapper of the class, not of the subclass, and releasing it releases the subclass object. A class that has no director of its own, derived from one that has, cannot override what it inherits (`TypeError` when the class is defined), and neither can a name that a base class calls but this class names differently (a class that declares one overload hides the others, so the wrapper of the overload it declares loses its suffix: `Circle.add`, `Shape.add_int32_int32`). While C++ runs a method on an object, one of its overrides cannot close it (`RuntimeError`: C++ would use what it frees); as for callbacks, closing or giving away the other objects that the running call uses is not supported either. An object that was given to C++ is closed for Python, and only its own overrides (which C++ runs) can still call the implementation of the class through `super()`, on the thread that runs them. A subclass that overrides nothing makes ordinary objects. Not yet: virtual methods with containers or classes in their signatures, and a protected constructor of a class that also has public ones.
- Vector parameters accept any sequence with `len()` and indexing (list, tuple, range, `array.array`, numpy arrays...), but not sets, mappings, iterators, strings or `memoryview` (except for byte vectors); each element is checked like a parameter, and errors name it (`argument 'values[1]' ...`), so a numpy array of `bool` is refused like any value that is not a Python `bool` (pass `array.tolist()`). Byte vectors also accept one-dimensional `bytes`-like objects of unsigned bytes, copied at once; signed ones (`array.array('b')`) are checked element by element. Results are lists.
- Enums become `IntEnum` classes, nested ones as class attributes (`Lamp.State`), with the C++ names, except Python keywords and the names that `IntEnum` reserves (`mro`, `bit_length`...), which get a trailing `_`. Arguments accept a member or an `int` (flag combinations), not `bool` or a member of another enum: `OverflowError` outside the C type, `ValueError` outside the valid range of an enum without a fixed underlying type. Results are members, or `int` for values that are not enumerators.
- C++ exceptions raise `<module>.Error`, with `.status`, `.status_name` and the message. Errors can be pickled, for example to come back from a worker process.
- The library is loaded from, in order:
  1. `<module>.load(path)`;
  2. the `<MODULE>_LIBRARY` environment variable;
  3. `lib<module>.so`, `<module>.dll` or `lib<module>.dylib` next to the package;
  4. the system search path.

  On Windows, a library's own dependencies are found in its directory and the system directories. Use `os.add_dll_directory()` for others.
- Objects are as thread-safe as the C++ class. ctypes releases the GIL during calls, so do not use one object from several threads without your own locking. That includes `close()`: closing an object while another thread, a `sys.setprofile`/`sys.settrace` hook or a signal handler is about to call a method of it is not supported. A cyclic object whose class defines `__del__` finds itself closed there, because CPython runs weak-reference callbacks before finalizers on cyclic garbage; use `with` or `close()` for such objects. Calling `__init__` again from inside the conversion of its own arguments is not supported either. Loading and binding are thread-safe, reentrant (a `__del__` may call into the bindings) and safe across `os.fork()`.

## Ownership

C++ declarations do not say who owns a `T*`, nor whether a function keeps a pointer it is given. bridgefex decides what it can from the types and asks for the rest:

| C++ | Default |
|---|---|
| result `T`, `std::unique_ptr<T>` | a new object that the caller owns |
| result `T&`, `const T&` | borrowed, never NULL |
| result `T*`, `const T*` | **an annotation is required**: `owned` or `borrowed`; it may be NULL (`None`) |
| parameter `T&`, `const T&`, `T*`, `const T*`, `T` | not NULL; used during the call (a constructor keeps references and pointers) |
| parameter `std::unique_ptr<T>` | taken by the function |

The **annotations file** is TOML with one table per generated C function (the name in the C header), keys for the parameters (by their C++ name, or by position counted from 1) and `return`:

```toml
[demo_Tree_find]                # Node* Tree::find(int value)
return = "borrowed"             # the tree keeps owning it

[demo_Tree_detach]              # Item* Tree::detach()
return = "owned"                # the caller destroys it

[demo_Tree_adoptRaw]            # void Tree::adoptRaw(Item* item)
item = "transfer"               # the tree takes it

[demo_Tree_both]                # int both(const Node* first, const Node* second)
first = "nullable"
2 = ["nullable"]                # the same, by position

[demo_Tree_setCursor]           # void setCursor(Node* node)
node = "retain"                 # the tree keeps the pointer: it keeps the object alive

[demo_Peek_create]              # explicit Peek(const Node& node), which only reads it
node = "call"                   # a constructor keeps what it gets, unless told so
```

| Word | Where | Meaning |
|---|---|---|
| `owned`, `borrowed` | `return` of `T*`, `const T*` (`owned` not for `const`) | who owns the result |
| `bytes` | `return` of a string | Python returns `bytes`, not `str` |
| `nullable` | `T*`, `const T*`, `std::unique_ptr`, `const char*` | NULL (`None`) is accepted |
| `transfer` | `T*` | the function takes the object (a `std::unique_ptr` always does) |
| `retain` | `T&`, `const T&`, `T*`, `const T*` | the C++ code keeps the pointer; Python keeps the object alive while the C++ object lives, or for ever for a function without one |
| `call` | the same | only used during the call (the default, except for constructors) |

A class that other languages may derive from is asked for before the first table with `directors = ["demo_Shape"]` (see Directors in the C API). Everything is checked, and all the problems are reported with `file:line`: an unknown function (adding an overload renames the C functions of the others, so an annotation of the old name is an error with suggestions), an unknown parameter or word, a word that does not apply to the type, `owned` with `borrowed` or `transfer` with `retain`, a result that is always owned or borrowed, a raw pointer result with no annotation. The policy is printed in the comments of the C header, so changing it changes the API fingerprint; `bytes`, which only affects Python, does not.

## Platforms

CI builds and runs everything on:

- Linux x86_64 and Linux arm64 (Ubuntu 24.04), with GCC 14 and Clang 20;
- Windows x64, with MSVC.

Each of these runs with Python 3.12 and 3.14. The generated files of the test cases are byte-identical on every platform.

The test suite does not run for 32-bit ARM (the arm64 runner is native). What does: the [Libraries workflow](.github/workflows/libraries.yml) generates the C layer of the `demo` example, builds its shared and static libraries and runs its C program (and those of the director examples, which override C++ virtual methods from C), for Linux x86_64, Linux arm64, Linux armhf (32-bit ARM, cross-compiled with GCC 14 and run under QEMU) and Windows x64 (MSVC), with [`scripts/build_libraries.py`](scripts/build_libraries.py). The checks that guard class layouts (see Known limitations) are written for 32-bit ARM, but were only reasoned about there, not run.

### Libraries built by CI

Those libraries, with the generated C headers, are published as the archives that the last commit of `main` made (the *Development build* pre-release, replaced by every push) and, for a tagged `v*` version, as a release:

| Target | Archive |
| --- | --- |
| Linux x86_64 | [`bridgefex-demo-linux-x86_64.tar.gz`](https://github.com/juanlumc1988/bridgefex/releases/download/nightly/bridgefex-demo-linux-x86_64.tar.gz) |
| Linux arm64 | [`bridgefex-demo-linux-aarch64.tar.gz`](https://github.com/juanlumc1988/bridgefex/releases/download/nightly/bridgefex-demo-linux-aarch64.tar.gz) |
| Linux armhf (ARMv7, 32-bit) | [`bridgefex-demo-linux-armhf.tar.gz`](https://github.com/juanlumc1988/bridgefex/releases/download/nightly/bridgefex-demo-linux-armhf.tar.gz) |
| Windows x64 | [`bridgefex-demo-windows-x64.zip`](https://github.com/juanlumc1988/bridgefex/releases/download/nightly/bridgefex-demo-windows-x64.zip) |

Each has `include/` (the generated headers), `lib/` (the shared library, with its import library on Windows, and the static library) and a README with the compiler that built it. They are the output of the example, to see the generated C layer work on each target: bridgefex generates the library of *your* headers, and does not distribute one.

## Known limitations

- The wrapper sources must be compiled with the same compiler, standard library and options as the wrapped code. They call it directly.
- Each generated source includes its wrapped header first, as your own sources do, so that macros the header defines for the standard library (`_FILE_OFFSET_BITS`, `_GLIBCXX_USE_CXX11_ABI`...) give the same class layouts as in your library. A header whose macros break a standard header included after it (`#define uint32_t ...`) breaks the generated source too. For example, `#define byte unsigned char` breaks `<new>` and `<exception>` with libc++ and MSVC in C++17 and later, where they declare `std::byte`; bridgefex warns about that one, because the verification with libstdc++ cannot see it. A generated source that includes `<string>` or `<string_view>` (when strings cross the boundary) breaks with libstdc++ too; the verification reports that.
- Unity builds and precompiled headers are not supported for the sources of a wrapped header that configures the standard library or the C library (`_FILE_OFFSET_BITS`, `_TIME_BITS`, `_GLIBCXX_USE_CXX11_ABI`, `_GLIBCXX_DEBUG`...): these macros are read in the first standard header of a translation unit only, and they also apply to every later source of it, other modules' included. Compile those sources as their own translation units, without a precompiled header (CMake: `SKIP_UNITY_BUILD_INCLUSION`, `SKIP_PRECOMPILE_HEADERS`). On glibc, the generated source stops with an `#error` when `_FILE_OFFSET_BITS` or `_TIME_BITS` came too late, which is only a partial safety net: it does not cover the other macros, the reverse case, `_TIME_BITS` on 64-bit targets or MinGW. Other sources compile as one translation unit in any order, as long as the wrapped headers do not use the names of the generated macros (`<MODULE>_OK`, `<MODULE>_API`...) or define `byte`. A macro named like a variable of the generated wrappers (`_arg1`, `_result`, `_callback`, `_in`, `_out`...) replaces it and the generated source does not compile; the verification names the macro, which has to be renamed.
- Over-aligned classes (more than the alignment of the platform's `operator new`, usually 16 bytes on 64-bit targets and 8 on 32-bit ones) need C++17 (aligned `new`) when the wrapper is compiled; a `static_assert` in the generated source enforces it.
- Deprecated declarations are wrapped without deprecation warnings: the generated sources turn them off (`-Wdeprecated-declarations`, MSVC C4996).
- Adding an overload renames the C functions of the existing ones (see Overloads). Methods that differ only in constness (`T& at(i)` and `const T& at(i) const`) get the same name and are rejected as a clash.
- Value structs and vectors cross the boundary as copies, on each call: from Python, a large vector or array costs a loop that checks every element (bytes-like objects are copied at once into byte vectors), and the data exists twice while the C++ vector is built. A user-provided default constructor that leaves members uninitialized gives indeterminate values in `<c_type>_init`, as in C++ (undefined behaviour for `bool` and enum members).
- A packed C++ struct whose members have non-trivial constructors is undefined behaviour in C++ itself; bridgefex cannot make it safe.
- Enumerator values are read with the options given to bridgefex. Building the library with other options that change them (`-D`, another target for an enumerator that depends on `sizeof`) fails at a `static_assert` in the generated source.
- A C++ identifier spelled like a generated macro (`demo_Color_Red`) is replaced by the preprocessor in code that includes the C headers.
- Strings cross the boundary as copies. A function that keeps a `const char*`, `std::string_view` or `const std::string&` argument after it returns keeps a dangling pointer. A deleted `f(std::string&&)` overload, the usual sign of that, makes the generation fail. A `const std::string&` argument is passed as an lvalue, so that an `f(std::string&&)` overload or a forwarding template of a header that is not wrapped cannot take the call.
- A `const char*` result must be NUL-terminated: it is copied with `strlen`. A `const char*` result that the caller must free (`strdup`, `new[]`) leaks. A `(const char* data, size_t size)` pair is two separate parameters, and Python cannot pass NULs through `data`.
- A string result exists briefly up to four times: as the C++ string, the C copy, Python bytes and `str`.
- `<module>_free` is an exported C symbol: the module name must not give a name that another library of the program exports (`g_free` of GLib, `curl_free` of libcurl).
- A callback that refers to the object that stores it (`timer.set_on_tick(lambda i: timer.tick(0))`) makes a reference cycle through C++ that the garbage collector cannot see: close such objects, or use a weak reference.
- Ctrl-C during a callback reaches the call through a `sys.unraisablehook` that the bindings install at import, one for the bindings of every package; code that replaces the hook (some test runners do) loses only that. A C++ thread that is inside a callback when Python shuts down can still crash or hang it.
- A borrowed object can only be checked against the objects it came from being closed. If the C++ owner frees it while it is open (`tree.release()` after `tree.find()`), using the borrowed object is a use after free, as with any reference in C++: copy it, or use it in a `with` scope of its owner. A reference that the C++ code returns from an argument is borrowed from `self` only (from the reference and pointer arguments, for a function without `self`).
- A thread that was not started with `threading` counts as a thread of C++ for the callbacks: a thread started with `_thread.start_new_thread` that runs a stored callback of a call that is still running on another thread gives its error to that call, not to `sys.unraisablehook`. After `Py_Finalize` and `Py_Initialize` in the same process, the callbacks that C++ still holds from the old interpreter cannot be told from the new interpreter's if its release function is at the same address (the bindings enable it when they load the library): destroy the objects that hold callbacks before finalizing.
- Taking an object gives it up for good: the objects borrowed from it become invalid, and a signal (Ctrl-C) that arrives between giving it up and the C call, a few microseconds, leaks that object (it is never destroyed twice).
- What `retain` keeps is kept until the C++ object is closed, once however many times it is passed: a pointer that a method replaces keeps its old object alive, and a function or static method that keeps a pointer keeps its object for ever (`<module>._runtime.forever`). When the C++ code gives back an object that was taken (a `pop`, a `release`), the new Python object keeps alive what the old one retained. C++ destructors that use what their object retained are not safe in reference cycles: objects that are collected together are destroyed in no particular order.
- A class with a destructor that throws (`noexcept(false)`) can be wrapped, but a `std::unique_ptr` of it that the wrapped function destroys ends the program, as in C++ (the destructor of `std::unique_ptr` is `noexcept`).
- Inheritance: a pointer to a derived class has to be converted to read it as a base class (see the C API); the conversions are generated for the classes of the module only, so every public base class has to be wrapped. There is no conversion from a base class to a derived one (`dynamic_cast` needs RTTI, which embedded builds often turn off): a function that returns `std::unique_ptr<Shape>` gives a `Shape` object, whose virtual methods run the right code but whose methods of `Circle` are not there. A Python class cannot extend wrapped classes of different C++ classes (`TypeError` when it is defined), and one that derives from a class with a director overrides only the virtual methods of that class (see Overriding virtual methods). A base class with only protected constructors and no pure virtual method is rejected like any class with no public constructor: make it abstract. A class that reaches the same base through a private path as well (`D : public A, private B` with `B : A`) fails to compile its conversion.
- A class returned by value that cannot be copied or moved needs C++17 (guaranteed copy elision). `std::shared_ptr` is not supported.
- Overrides in Python (directors): the table of functions that C++ calls is filled when the object is created, from the methods that its class has then. A method added to the class afterwards is not called by C++ (a method that already overrode one can be replaced: C++ looks it up by name each time it calls). A subclass of a class with a director that overrides nothing makes plain objects, and `create_*` functions do not run the `__init__` of the subclass. The result of an override that is a struct has to be the dataclass of the module (or a subclass).
- The object of a director has the guard against being closed while C++ runs one of its overrides, but a second wrapper of the same C++ object does not: `Holder.take()` gives a plain wrapper of the object that was given to C++, and closing that one while an override of the object runs (from the override itself, or from another thread) frees what C++ uses.
- An exception that an override raises reaches the call of the bindings that made C++ run it. If C++ runs it while it destroys an object that the garbage collector collects (a destructor that calls a virtual method of a class that Python derived, on a thread without a call of the bindings), no call is there to receive it: the next call of the bindings on that thread raises it, and from a thread of C++ it goes to `sys.unraisablehook`.
- A Python object whose class overrides C++ and that C++ owns (it was given as a `std::unique_ptr`) is kept alive by the registry until C++ destroys it. If the objects keep each other through C++ (a parent pointer in the C++ class, an override that holds the object that owns it), they are never collected: break the cycle with `close()`.
- A signal that a handler turns into an exception during `close()` can postpone the destruction until the object is collected. Under a `sys.settrace`, `sys.setprofile` or `sys.monitoring` hook, which runs Python code between two lines, such an exception can also leak the object.

## Development

```sh
pip install -e '.[dev]'
pytest                              # all tests (native tests need CC/CXX, or cl.exe on Windows)
pytest --update-goldens             # rewrite tests/golden/*/expected after an intended change
ruff check . && ruff format --check . && mypy
```

- **Golden tests** compare the generated files with `tests/golden/<case>/expected` byte by byte.
- **Round-trip tests** build each case into a shared library with its `impl/` sources. They then run `check_c.c` and `check_python.py` against it, and check that only the C API functions are exported. Some also run the Python bindings under AddressSanitizer (GCC on Linux), for example with signals arriving during C calls.
- **Standard tests** compile the generated code in every supported language standard.
- **Conformance tests** (`tests/test_conformance.py`): `tests/golden/<case>/scenarios.toml` tells, in terms of the C++ API and not of any language, what the bindings must do (create objects, call, expect a result or a kind of failure). Each backend has a runner that plays the scenarios against the library built from the case; a backend without a runner fails the build. What a language does beyond that is tested in the `check_<language>` program of the case.
- **Architecture tests** (`tests/test_architecture.py`) read the source to keep the layers apart: the plan and the templates of the C layer know no language, and a backend only reads the plan. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for how the code is split and how to add a language.
- **Generation tests** (`tests/test_generate.py`) feed adversarial headers through the whole pipeline: each must be rejected with a clear error or translated correctly.
- **Verification**: before writing anything, bridgefex compiles the generated C++ sources against your headers and the C headers as C23 and as strict C99, with libclang, and compiles and imports the generated Python package.
- **Native tests** check the thread-local last error, thread cancellation, the alignment check, unity builds and their glibc check, `NODISCARD`, and that the test compiler flags really turn warnings into errors.
- Set `BRIDGEFEX_SKIP_NATIVE_TESTS=1` to skip the tests that need a compiler.

On WSL2 with Ubuntu 24.04 (for example from VS Code with the WSL extension), [`scripts/setup-wsl.sh`](scripts/setup-wsl.sh) installs the system dependencies: git, GCC 14, Clang 20, libclang 20 with its resource headers and Python 3.12 with `venv`; `--with-arm` adds the GCC 14 cross compilers for 32-bit ARM (`arm-linux-gnueabihf-g++-14`). It also sets `CC=gcc-14` and `CXX=g++-14` in `~/.bashrc`, as the CI does. Then, in a clone kept in the Linux file system (not under `/mnt/c`):

```sh
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest
```

Claude Code cloud sessions run [`.claude/hooks/session-start.sh`](.claude/hooks/session-start.sh), which installs libclang 20, the compilers and the virtual environment.

## Roadmap

1. Prototype: classes, functions and scalar types; C layer and Python bindings; golden and round-trip tests. *(done)*
2. More types: strings, enums, POD structs, containers, callbacks, and wrapped classes across headers with ownership annotations. *(done)*
3. Python complete: the loose ends of the callbacks, and inheritance and virtual methods called from Python. *(done)*
4. Language-neutral architecture: one plan with one backend per language, and a conformance suite that every backend runs on the same golden headers. *(done, see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md))*
5. Classes of the target language that override the virtual methods of C++ ones. The generated C++ subclass that calls back through a table of functions belongs to the shared C layer, and the Python bindings use it *(done for scalars, enums, strings and value structs)*; the other languages come with their backends, and containers and classes in the signatures after that.
6. C# (P/Invoke), on that common base.
7. The JVM: Java bindings (JNI for Android, FFM for Java 22+), which Kotlin uses too.
8. Rust (`extern "C"` with safe wrappers).

## The name

*bridgefex* is a bridge for **F**oreign **EX**ports: it exports a C++ API through the C foreign function interface that every other language can call.

## License

bridgefex is licensed under the [Apache License, Version 2.0](LICENSE).

**Generated code.** The files that bridgefex generates from your headers (everything it writes to the output directory) are not covered by bridgefex's license. You may use, modify and distribute them under any terms you choose, including proprietary ones, without attribution. They carry no license header for that reason.
