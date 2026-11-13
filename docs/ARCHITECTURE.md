# Architecture

bridgefex turns C++ headers into a pure C API and, on top of it, the bindings of
one or more languages. This page describes how the code is split, so that a new
language is a new *backend* and nothing else changes. Read the README first for
what the generated code looks like.

## The pipeline

```
headers ──parser (libclang)──▶ model.Module ──build_plan──▶ plan.ModulePlan
                                                              │
                          ┌───────────────────────────────────┤
                          ▼                                   ▼
          generator.render: the C layer             backend.render(plan, fingerprint)
          (templates/, same for every language)     one backend per language
                          │                                   │
                          └───────────── files ───────────────┘
                                          │
                       verify (libclang on the C layer, backend.check)
                                          │
                                     write_files
```

| Step | Module | Knows |
|---|---|---|
| Read the headers | `parser`, `libclang`, `annotations` | C++, libclang, TOML annotations |
| The model | `model`, `typemap`, `naming` | C++ declarations and types, no language |
| The plan | `plan` | the C API and what each callable means across it, no language |
| The C layer | `generator`, `templates/` | the plan |
| A language | `backends/<name>/` | the plan, and that language only |
| The checks | `verify` | libclang, and the checks the backends provide |

`tests/test_architecture.py` enforces the layering by reading the source. The
model, the plan, the naming rules, the templates of the C layer and the internals of
the pipeline contain no word that names a language (`python`, `ctypes`, `jni`...),
import nothing that only some languages have (`keyword`, `ctypes`), and import no
backend; no module is imported by a computed name. A backend imports nothing but
the plan, the model, `naming`, `typemap`, `errors`, `rendering`, the base that the
backends share (`backends.base`) and itself: it never calls the parser, the
generator or another backend. The checks are themselves tested on sources that
break the rules.

## The plan

`build_plan(module, annotations, reservations)` resolves every decision once:
the C names, the signature of each C function, which parameters are outputs,
which objects are borrowed or given up, how overloads are told apart. The result
is a tree of frozen dataclasses:

- `ModulePlan` → `HeaderPlan` (one per header) → items in source order:
  `ClassPlan`, `FunctionPlan`, `EnumPlan`, `StructPlan`.
- `CFunction`: the C function, with its `CParam`s. Each parameter and the return
  type carry an `Ffi`, a *structured* C type (`SCALAR` with its C name, `HANDLE`
  with its C type, `STRUCT`, `STRING`, `CALLBACK`, `POINTER` to another `Ffi`...),
  so a backend maps types without parsing C declarations.
- `CallablePlan`: a `CFunction` with its meaning for a binding. `ParamSpec` and
  `ResultSpec` say what each parameter and result *is* (the C++ type, the enum,
  struct or class it refers to, the annotation words `nullable`, `transfer`,
  `retain` and `call`, the C parameters that hold its size, user data and release
  function), `CallableKind` says whether it is a free function, a static method,
  a method or a constructor.
- `ClassPlan` also has `bases` and `views`: the C API converts a derived pointer
  to each ancestor (`<class>_as_<ancestor>`), because with several bases the
  address changes.

- `ClassPlan.director` (only for the classes that the annotations file lists in
  `directors`) describes a generated subclass whose virtual methods call back through
  a table of functions: a `DirectorEntry` for each overridable virtual method, with
  its signature seen as for a `std::function` (`CallbackSpec`, `thunk_key`), whether
  it is pure and its base call (a `CallablePlan` of kind `DIRECTOR_BASE`), and the
  functions that create the director (`DIRECTOR_CONSTRUCTOR`, one per constructor
  that a derived class can call, which write the object, to own and destroy it from
  the moment it exists, and the director, for the base calls). The C++ of the
  subclass belongs to the shared C layer (`DirectorPlan.glue`), so every language
  gets the same one; a backend implements the table: it builds the functions of the
  vtable from its own overrides, keeps what they need alive until `release`, and
  calls the base variants for `super()`. `DirectorEntry.method` is the declaration
  of the method (the same object as `CallablePlan.source` of its wrapper, so that a
  backend finds the wrapper of a public one: a location is not enough, a macro can
  declare several methods at one), and `DirectorEntry.key` names its hook. The part of
  a callback that is a container (`CallbackPart.element`) arrives as a pointer and a
  size, or a pointer for a `std::array`, and a result is given with the container
  sinks of the runtime (`<module>_vector_sink_set`, `..._set_strings`), as a string is
  with its own. An object
  (`CallbackPart.cls`) is a pointer to its handle type: lent for the call if the C++ type
  is a reference or a raw pointer, given up to the language if it is a `std::unique_ptr`
  (a `std::unique_ptr` result is a `T**` that C++ takes). A backend that wants to give the
  override the object it already has for a director object finds it by address: the
  address of the C++ object as each class it may be passed as.
  `HeaderPlan.c_functions` is what the bindings call; `HeaderPlan.director_functions`
  is the rest of what the C header declares.

  The Python backend (`backends/python/directors.py`, `thunks.py`) fills the vtable
  with one fixed thunk per virtual method, whose `user_data` is the key of a registry
  entry that holds the object (weakly while Python owns the C++ object, strongly once
  C++ does). A class that overrides a method with the Python name of its wrapper makes
  its objects through the create function of the director, which registers the entry
  when the object exists; the wrappers of the class call the base function of the
  director of the object, so `super().method()` does not call the override again. An
  exception of an override goes to the call that made C++ run it, as for a callback,
  and an object cannot be closed while one of its overrides runs.

The plan never says how a language spells any of it. The C layer is rendered from
it by `generator.render`, with the templates in `src/bridgefex/templates/`.

### What a backend must respect

The C API has conventions (README, "The generated C API"): the status of each call,
the thread-local last error, how strings are returned and released, what a callback
is called with, who owns an object after a call. A backend implements those, it does
not change them. `C_API_CONVENTIONS` and the API fingerprint (a hash of the public C
headers) exist so that bindings and library agree: the render function gets the
fingerprint, embeds it, and the generated code refuses to load a library built from
another version of the API.

The names of the C API must not collide with what a binding uses, and the C API
is the same whichever languages are generated. So each backend declares, in
`Backend.reservations` (a `Reservations`), what it needs left alone:

- `words`: the keywords of the language, which no parameter, member or field takes;
- `parameter_words`: the names the bindings use for the parameters and locals of
  their functions, which no parameter takes;
- `names(module)`: the classes and module aliases the bindings use unqualified;
- `locals(count)`: the local variables of a generated function of `count` parameters;
- `module_problem(name)` and `header_problem(stem)`: why the bindings cannot be
  named like the module, or have a module for a header (a standard library module,
  a name the package defines...).

`api.generate` passes the union of **all** registered backends to `build_plan`,
whichever languages are selected, so the C API does not depend on that choice. The
plan records what it reserved (`ModulePlan.reservations`), and `generator.render`
refuses to give a backend a plan that was built without that backend's words.

What does change the C API is the set of registered backends: a new backend with
`words={"event"}` renames every C parameter called `event` for all users, whatever
language they generate, and so changes the API fingerprint of their libraries.
Declare only what the C API must share with the bindings. A backend can rename in
its own lowering instead (a keyword of its language becomes `@event` or `r#type`),
which needs no reservation: Python reserves its keywords because its bindings take
the C parameter names as keyword arguments.

## A backend

```python
# src/bridgefex/backends/<name>/__init__.py
BACKEND = Backend(
    name="<name>",  # --lang <name>; files go to <name>/...
    render=render,  # (ModulePlan, fingerprint) -> {path: text}
    reservations=Reservations(...),  # names the C API leaves alone
    check=check,  # optional: (ModulePlan, files, scratch) -> [Diagnostic]
)
```

To add a language:

1. Create the package `src/bridgefex/backends/<name>/` with its templates and
   a `BACKEND`, and add it to `BACKENDS` in `backends/__init__.py`. `--lang`,
   `Options.languages` and the verification pick it up from there.
2. Lower the plan to the language's own model (the Python backend does it in
   `lower.py` and `module.py`: it turns each `CallablePlan` into the parameters,
   the checks, the call and the result of one function), then render templates
   with `rendering.Renderer`. Errors that only the language has (a name that
   collides in it) are `GenerationError`s raised from `render`, so they appear
   after the errors of the plan and only when that language is generated. A backend
   writes below `<name>/` only (`generator.render` checks it), and its name is
   a lowercase identifier other than `c`.
3. Output must be deterministic and independent of the platform: a golden test
   compares it byte by byte.
4. Add the golden files (`tests/golden/<case>/expected/<name>/`). Registering a
   backend can change the C parameter names of the existing goldens (see above):
   regenerate them and check that the C files only change in that.
5. Add a runner of the conformance scenarios (see below).
6. Validate it before its pull request, with the same bar for every language: the
   round trip of each golden case (also under sanitizers, and for 32-bit ARM what
   the CI's armhf job can run), the conformance runner, tests for what is specific
   to the language, and an **independent adversarial review**: reviewers that did not
   write the code, each with a focus (lifetime and concurrency, the generated code
   on hostile headers, the user's experience and the quality of the tests, the C
   layer and its ABI), who try to break it and report only what they can reproduce.
   Every finding is fixed (or answered with a reason) in the pull request, each
   fix with a test that fails without it.

The Python backend is the reference: `backends/python/ffi.py` maps `Ffi` to ctypes,
`lower.py` lowers parameters and results, `module.py` builds the modules, classes,
enums and structs, `check.py` compiles and imports the generated package before it
is written, and `templates/` holds the Jinja templates and the runtime.

The C# backend has the same parts: `mapping.py` maps `Ffi` and the scalars to the blittable
types of P/Invoke (`LibraryImport`, pointers, no marshalling), `lower.py` lowers a
`CallablePlan` to a method (a `fixed` block per string, a `Lease` per object, a claim per
object that is given, the retains after the call), `containers.py` and `elements.py` lower the
containers (a `ReadOnlySpan<T>` is pinned or converted into a `Block<T>` or a `StringBlock`; a result is
copied and what C++ allocated is freed in a `finally` that includes the check of the call), `callbacks.py` the callbacks (a delegate, one thunk per C
signature, `[UnmanagedCallersOnly]`), `directors.py` the classes that C# can derive from (a `virtual`
method for each virtual method of the C++ class, a thunk per method, and the vtable that the
constructor of a derived class fills with the ones it overrides), `module.py` writes the files (`Native.cs`,
a file per header with its classes, enums and structs), `check.py` builds the project with
`dotnet build` and warnings as errors when the SDK is there, and `templates/` holds the runtime
(`Runtime.cs`, `Objects.cs`, the `.csproj`, and `BridgefexCallbacks.cs`, only for a module that has
callbacks or directors). What C# cannot do the way the plan assumes is
solved in the backend: a class has one base class, so the first base of a C++ class is the
C# one and the others are views of the same object (`AsLabeled()`-like accessors, an implicit conversion, and the
base's methods copied into the class); a garbage collector does not order finalizers, so what a
C++ object keeps pointers to is held (`Keep`) until the object has been destroyed; C++ can call a
callback on any thread and a managed exception must not cross it, so a thunk catches it and a
`CallScope` (the call that gave the callback, or the innermost one of the thread) raises it when C++
returns (a scope that gave no callback is reached by its own thread only, so it takes no lock and the
thread reuses it). The container of an override is a `ReadOnlySpan<T>` over what C++ holds (a
span cannot be kept, so it cannot outlive the call), or an array if its elements are converted, and
the result is a `T[]` that C++ copies through the sink of the vtable before the thunk returns. An object that C++ lends to an override is found by the address of its C++ object among the
directors of the C# objects (`DirectorBox.At`): the object of the user if it still owns it (held until the
override returns), else a wrapper that a `Lent` closes at the end; a `std::unique_ptr` that it is given is wrapped
before anything can fail, and destroyed if the override never runs; one that it returns is given up as a call
that takes the object gives it. The
classes that C++ calls back are annotated `DynamicallyAccessedMembers`, so that the trimmer and the
native AOT compiler keep the methods of the classes of the user that the constructors read by
reflection; the project turns the analyzers of both on, so that the generation check fails on a
warning. What a
backend does not support yet is `Unsupported`, which `render` turns into a `GenerationError`
that says so, instead of a half-working class.

The JVM backend (`backends/jvm`) has two flavors that write the same Java API: `jvm` (Java 22, the foreign function
API) and `jni` (Java 8, Android and 32-bit ARM, over `native` methods and glue in C++). A `Flavor` (`flavor.py`) is
all that the generator says differently: the types of memory, `var` and the patterns of `instanceof`, and where the
project goes. The generated code of a header is the same text for both, because each runtime has the classes that it
uses with the same names and signatures: the jni one has a mini FFM over direct buffers (`BridgefexSegment`,
`BridgefexArena`, `BridgefexLayout`, `BridgefexStruct`: `get`/`set` by layout, `allocate`, `asSlice`, `view`), which is
what the lowering of containers, structs and results needs too. `jni.py` writes `BridgefexNative` (a public method
with the signature of the FFM one, over a private `native` method that takes the pointers as `long`) and the glue,
which registers its natives (`bridgefex_<module>_jni_register`, called by `JNI_OnLoad` unless the library has one of its
own) and calls the C API with explicit casts; the sizes and the alignments of 64-bit values of the platform are taken
from Java by the layouts and checked against the library when it loads. The memory of a call is the slab of its
outermost arena, taken from a pool (a thread that is not in a call keeps none, so a class loader can be collected and
virtual threads do not allocate one each), and the buffers of the requests that do not fit it are kept by the arena
until it is closed (the address that the native method takes does not keep a buffer alive). The runtime of the objects (handle, lease,
keep, object) is one set of templates for both flavors (Java 8 syntax; `Cleaner` or a `PhantomReference` queue).
The FFM output did not change when the flavor was introduced: the goldens say so.
The callbacks (`callbacks.py`) are the same text for both flavors, `BridgefexCallbacks` (a table of the callbacks that C++
holds, by a key that is the `user_data`; the scope of each call that gives callbacks, which keeps their first exception for the
call; the release function; the disable at exit) and one *thunk* for each C signature and Java types: a static method with the C
signature of the callback. Over the foreign function API an upcall stub makes it a function pointer and it writes the result where
C++ says; over JNI the glue has a C++ function with that C signature for each thunk, which attaches the thread if C++ started it,
calls the thunk with what JNI carries (a pointer is a `long`) and writes what it returns. The glue holds the class of the thunks
weakly: a strong reference would keep the class loader of the bindings from being collected.

The parts of the backend are these. `mapping.py` maps `Ffi` to the layouts and the `MethodHandle` types of the downcalls
(`BridgefexNative` has a static method for each function of the C API over a handle in a nested class of its own,
which is initialised on the first call of the function, after the library was loaded: a failed load can be retried,
a function that the library lacks fails alone, and no class initialiser nears the 64 KB that Java limits it to; an
unsigned argument of 8 or 16 bits goes as a 32-bit integer, which the callee may rely on being extended), and the scalars to Java types
(no unsigned: a widening and a range check up to 32 bits, `@BridgefexUnsigned long` for 64 bits and `size_t`; the
canonical layouts for C `long` and `size_t`, which have the width of the platform). `lower.py` lowers a `CallablePlan` to a method:
a confined `Arena` for the memory of the call (zeroed, closed when the call ends), a `try`
resource for the lease of each object, and the claim, the retains and the free of what C++ allocated for the result as
in C#. `classes.py` writes a class (a first base is the superclass, the others are `as<Base>()` views and copies of
their methods; Java cannot hide a method, so what C++ hides is refused), `enums.py` an enum as a final class with
a constant per enumerator (a flag or a strong integer type has values that no enumerator has), `structs.py` a value
struct (its layout is the natural one capped at 8, as the `#pragma pack` of the C mirror), `check.py` compiles with
`javac --release 22 -Xlint:all -Werror` when a JDK is there. A garbage collector does not order cleaning either, so
`BridgefexHandle` has a state of its own (the lease count of the calls that use the object, the closed, claimed and given
bits, and the pins of the C++ objects that point to it): the `Cleaner` destroys an object only when nothing points to
it and no call uses it, a pin defers it until the C++ object that points to it was destroyed (the collector that
finds a wrapper under a call defers it too: the call may still make a C++ object point to it), what a destruction
releases is released by a queue of the thread, not by recursion, and a claim to give an object to C++ needs it for
the call alone. What a wrapper needs from the generated class is public (the handle constructor
is `protected`, with a static `wrap`, so that `new X(null)` is not ambiguous) but documented as not API.

## The conformance suite

The behavior that every language must share (what a call returns, which failures
there are, when an object is closed, how inheritance and ownership look) is
described once, in `tests/golden/<case>/scenarios.toml`, in terms of the C++ API:

```toml
[[scenario]]
name = "a counter counts, refuses and closes"
steps = [
  { new = "counter.Counter", overload = "_int32", args = [41], as = "c" },
  { call = "c.increment" },
  { call = "c.value", expect = 42 },
  { call = "c.setLimit", args = [-1], error = "exception", message = "negative" },
  { close = "c" },
  { call = "c.value", error = "closed" },
]
```

`tests/conformance/scenarios.py` documents the format (names, steps, values, the
kinds of failure) and checks every scenario against the neutral plan, so that a
name, an overload, a number or a kind of argument or result that the headers do not
have, or the use of an object after `close`, is an error whichever language runs
it. (Members of a struct value are checked for names and order, not yet for kind.) Each backend has a runner in `tests/conformance/` and an
entry in `RUNNERS` of `tests/test_conformance.py`; a registered backend without a
runner fails the build. `runner_python.py` plays the scenarios with the generated
package and nothing else, as a user would, and the tests check that it fails when a
scenario is not true.

A scenario can also derive a class from one that has a director, overriding virtual
methods with a few neutral behaviors (give a value, call the implementation of the
base, give an argument back, fail), so that every language shows the same: C++ runs the
overrides, a base call does not call the override again, the types cross in both
directions, and the failure of an override reaches the call that made C++ run it
(`tests/golden/directors/scenarios.toml`).

A runner decides how the neutral names reach its language: the Python one finds a
header as a module, a constructor overload as `Class.create<overload>`, a struct
member by its position (the scenario gives all of them in declaration order) and a
kind of failure as an exception class. The C# one is a program that is built with the
generated project and plays the scenarios by reflection from a JSON file (it has no TOML
parser): `scenarios.resolve` gives each `new` and `call` the C function it wraps, which the
bindings name in the attribute `[BridgefexFunction]` of the member, so that the runner needs
none of the rules by which C# names things; the Python harness also gives it the C# names of
the enums and structs. The Java one is the same: `Runner.java` is compiled with the generated sources and plays the
scenarios by reflection from a JSON file, finding each member by the `@BridgefexFunction` that names its C function. A language whose toolchain is not installed skips its runner, unless the
environment requires it (`BRIDGEFEX_REQUIRE_DOTNET` and `BRIDGEFEX_REQUIRE_JAVA`, set by the CI). Behavior that is not shared (how a class that is
not instantiable fails to be, a `TypeError` for the wrong kind of argument, a copy or a pickle) stays in the
`check_<language>` program of the case. Callbacks and ownership annotations are not
in the scenarios yet.

## Tests

- `tests/test_plan.py` checks the plan and, through the Python backend's view of
  it, the Python lowering; `tests/test_backends.py` the registry and the checks;
  `tests/test_csharp_backend.py` what the C# backend writes, names and refuses; `tests/test_jvm_backend.py` the same for Java.
- `tests/test_architecture.py` the layering described above; `tests/test_conformance.py`
  the conformance suite.
- `tests/golden/<case>/` holds, per case, the input headers, the C++ implementation
  and the expected generated files; `test_golden.py` compares them byte by byte
  and `test_roundtrip.py` builds the library and runs `check_c.c` and
  `check_python.py` against it; `test_csharp_roundtrip.py` does the same with
  `check_csharp.cs`, built with the generated project like a user's program, and `test_jvm_roundtrip.py` with `check_jvm.java` (with the helpers of `tests/support/jvm/Checks.java`). Cases of the bindings alone, with no C program, are in `tests/binding_cases/`.
  A case lists the bindings that its `expected/` has (`languages` in `case.toml`).
