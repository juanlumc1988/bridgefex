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
typedef struct demo_Counter demo_Counter;   /* in C++: the class itself */

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
| `-m`, `--module NAME` | Module name, required. It prefixes the runtime C symbols (`demo_status`, `demo_last_error`, `DEMO_API`...) and names the Python package. Lowercase identifier. |
| `-o`, `--output DIR` | Output directory. |
| `--lang {python,none}` | Bindings to generate on top of the C layer (default: `python`). |
| `--std STD` | C++ standard used to parse the headers (default: `c++17`). |
| `-I DIR`, `-D NAME[=VALUE]` | Include directories and macros for parsing. |
| `--clang-arg=ARG` | Any other libclang argument. |
| `--include-root DIR` | Make the `#include` lines of the generated sources relative to `DIR` (default: each header by its file name). |
| `--libclang PATH` | libclang shared library. |

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

Build `out/c/*.cpp` into the same shared library as your code, or into a separate one linked against it. Add `out/c` and the directories of your headers to the include path. For example:

```sh
g++ -std=c++17 -shared -fPIC -fvisibility=hidden -I out/c -I include \
    out/c/*.cpp src/*.cpp -o libdemo.so
```

The generated sources define `DEMO_BUILD`, so the functions are exported (`__declspec(dllexport)` on Windows, default visibility elsewhere). Define `DEMO_STATIC` when building or using a static library.

Generation is all-or-nothing. If any header contains something unsupported, every problem is reported as `file:line:column: message` and no file is written. Existing files in the output directory are overwritten; stale ones are not removed.

## Supported C++ (prototype)

| Supported | Notes |
|---|---|
| Concrete classes and structs at namespace scope | Public constructors, destructor, methods, static methods. The implicit default constructor is used when no constructor is declared. |
| Free functions | Including overloads, also across headers of the same module. |
| Namespaces | Nested namespaces too. |
| Scalar types by value | `bool`; `signed char`, `short`, `int`, `long`, `long long` and their unsigned versions; `float`, `double`; `int8_t` … `uint64_t`, `size_t` (with or without `std::`). `const` values and `const T&` of these are passed by value. |

Rejected with an error, for now:

- Templates.
- Inheritance and virtual methods (a virtual destructor is fine).
- Operators and conversion functions.
- Public data members, nested types.
- Enums, unions, global variables, `extern "C"` blocks.
- Anonymous and inline namespaces.
- Pointers and non-const references.
- Classes by value (`std::string`, `std::vector`...), plain `char`, `wchar_t`, `long double`.
- Type aliases other than the standard fixed-width ones.
- Non-ASCII names.

Copy and move constructors, deleted functions, and private or protected members are not part of the API and are skipped.

## The generated C API

- **Names.** Every symbol starts with the C++ namespaces joined by `_`. Declarations in the global namespace use the module name instead, so a wrapper never has the same name as the function it wraps. `demo::geometry::Circle::area` becomes `demo_geometry_Circle_area`.
- **Overloads** get a suffix built from the parameter types: `demo_Counter_add_int32`, `demo_Counter_add_double`, `demo_Counter_create_void`. Only overloaded names get one, so adding an overload renames the existing function. Every generated name is checked for clashes.
- **Handles.** Each class becomes an opaque handle. `X_create...` allocates an object and returns it through `X** out_self`, which is set to NULL on failure. `X_destroy(X*)` deletes it and accepts NULL. In C++ the handle type is a typedef of the class itself, so there are no casts.
- **Errors.** Every function that can fail returns a `<module>_status`:

  | Status | Meaning |
  |---|---|
  | `OK` (0) | Success. |
  | `ERROR_EXCEPTION` | The C++ code threw a `std::exception`. |
  | `ERROR_UNKNOWN_EXCEPTION` | Anything else was thrown. |
  | `ERROR_NULL_ARGUMENT` | A pointer argument was NULL. |
  | `ERROR_OUT_OF_MEMORY` | `std::bad_alloc` was thrown. |

  Results come back through an `out_result` pointer. `<module>_last_error()` returns the message (`what()`) of the last failure on the calling thread. It is a thread-local buffer, truncated to 1 KiB at a UTF-8 boundary, and never NULL. No exception ever crosses the C boundary.
- **Warnings.** `<MODULE>_NODISCARD` makes compilers warn when a status is ignored. It uses `[[nodiscard]]` in C++17 and C23, and `warn_unused_result` on GCC and Clang otherwise.
- **API fingerprint.** `<module>_api_fingerprint()` returns a hash of all generated declarations. The Python bindings refuse to load a library built from a different version of the API.

Language standards: the C headers compile as C99, C11, C17 and C23, and as C++. The C++ sources compile as C++11 through C++23. MSVC is tested with C11/C17/latest and C++14 through latest, since it has no older modes. Tests build all of them with warnings as errors.

## The Python bindings

- One module per header. Classes and functions keep their C++ names; Python keywords get a trailing `_` (`from` becomes `from_`).
- A class with one constructor is created with `Counter(args)`. With several, use the factories named after the C functions: `Counter.create_void()`, `Counter.create_int32(5)`.
- The C++ object is destroyed by `close()`, at the end of a `with` block, or by the garbage collector, whichever comes first. Using a closed object raises `ValueError`. Copying or pickling raises `TypeError`, because the handle cannot be shared.
- Arguments are checked before the call. Integers must fit the C type (`OverflowError` instead of ctypes' silent wrap-around). `bool` parameters only accept `True` and `False`. Floating-point parameters accept real numbers.
- C++ exceptions raise `<module>.Error`, with `.status`, `.status_name` and the message.
- The library is loaded from, in order:
  1. `<module>.load(path)`;
  2. the `<MODULE>_LIBRARY` environment variable;
  3. `lib<module>.so`, `<module>.dll` or `lib<module>.dylib` next to the package;
  4. the system search path.

  On Windows, a library's own dependencies are found in its directory and the system directories. Use `os.add_dll_directory()` for others.
- Objects are as thread-safe as the C++ class. ctypes releases the GIL during calls, so do not use one object from several threads without your own locking.

## Platforms

CI builds and runs everything on:

- Linux x86_64 and Linux arm64 (Ubuntu 24.04), with GCC 14 and Clang 20;
- Windows x64, with MSVC.

Each of these runs with Python 3.12 and 3.14. The generated files are byte-identical on every platform.

## Known limitations

- The wrapper sources must be compiled with the same compiler, standard library and options as the wrapped code. They call it directly.
- When no constructor is declared, bridgefex assumes the implicit default constructor exists. If it is deleted (for example, because of a reference member), the generated source does not compile.
- Classes aligned to more than 16 bytes need C++17 (aligned `new`) when the wrapper is compiled.
- Calling a `[[deprecated]]` function from the wrapper triggers the compiler's deprecation warning.

## Development

```sh
pip install -e '.[dev]'
pytest                              # all tests (native tests need CC/CXX, or cl.exe on Windows)
pytest --update-goldens             # rewrite tests/golden/*/expected after an intended change
ruff check . && ruff format --check . && mypy
```

- **Golden tests** compare the generated files with `tests/golden/<case>/expected` byte by byte.
- **Round-trip tests** build each case into a shared library with its `impl/` sources. They then run `check_c.c` and `check_python.py` against it, and check that only the C API is exported.
- **Standard tests** compile the generated code in every supported language standard.
- Set `BRIDGEFEX_SKIP_NATIVE_TESTS=1` to skip the tests that need a compiler.

Claude Code cloud sessions run [`.claude/hooks/session-start.sh`](.claude/hooks/session-start.sh), which installs libclang 20, the compilers and the virtual environment.

## Roadmap

1. Prototype: classes, functions and scalar types; C layer and Python bindings; golden and round-trip tests. *(this version)*
2. More types (strings, enums, POD structs, containers, callbacks) and ownership annotations.
3. A second target language (C# P/Invoke or Rust `extern`).
4. Inheritance and virtual methods.

## The name

*bridgefex* is a bridge for **F**oreign **EX**ports: it exports a C++ API through the C foreign function interface that every other language can call.

## License

bridgefex is licensed under the [Apache License, Version 2.0](LICENSE).

**Generated code.** The files that bridgefex generates from your headers (everything it writes to the output directory) are not covered by bridgefex's license. You may use, modify and distribute them under any terms you choose, including proprietary ones, without attribution. They carry no license header for that reason.
