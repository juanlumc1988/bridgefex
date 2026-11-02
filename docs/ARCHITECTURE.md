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

`tests/test_architecture.py` enforces the layering by reading the source: the
model, the plan, the naming rules and the templates of the C layer contain no word
that names a language, no neutral module imports `bridgefex.backends`, and a
backend imports nothing but the plan, the model, `naming`, `typemap`, `errors`,
`rendering` and itself. A backend never calls the parser, the generator or another
backend.

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
whichever languages are selected, so the C API never depends on that choice. A
backend can also rename in its own lowering (a keyword of its language becomes
`@event` or `r#type`) and declare nothing: the reservations are for names that the
C API shares with the bindings, like the keyword arguments of the Python bindings.

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
   collides in it) are `GenerationError`s raised from `render`.
3. Output must be deterministic and independent of the platform: a golden test
   compares it byte by byte.
4. Add the golden files (`tests/golden/<case>/expected/<name>/`) and a runner of
   the conformance scenarios (see below).

The Python backend is the reference: `backends/python/ffi.py` maps `Ffi` to ctypes,
`lower.py` lowers parameters and results, `module.py` builds the modules, classes,
enums and structs, `check.py` compiles and imports the generated package before it
is written, and `templates/` holds the Jinja templates and the runtime.

## Tests

- `tests/test_plan.py` checks the plan and, through the Python backend's view of
  it, the Python lowering; `tests/test_backends.py` the registry and the checks.
- `tests/test_architecture.py` the layering described above.
- `tests/golden/<case>/` holds, per case, the input headers, the C++ implementation
  and the expected generated files; `test_golden.py` compares them byte by byte
  and `test_roundtrip.py` builds the library and runs `check_c.c` and
  `check_python.py` against it.
