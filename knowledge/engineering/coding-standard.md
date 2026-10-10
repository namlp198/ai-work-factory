# Coding standard (L3 company knowledge)

- Follow the conventions already used in the repository (naming, layout, error handling, test style).
- Small, focused functions; no dead code; no commented-out code.
- Every behaviour change ships with a unit test; bug fixes ship with a regression test.
- Validate inputs at module boundaries; fail with explicit errors, never silently.
- No secrets, tokens or credentials in code, tests, fixtures or logs.
- Public functions have a one-line docstring/comment when the name is not self-explanatory.
- Python: type hints on public functions, pytest for tests, no network access in unit tests.
- C++: RAII, no raw new/delete, const-correctness, CTest/GoogleTest for tests.
