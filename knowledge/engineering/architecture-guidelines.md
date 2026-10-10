# Architecture guidelines

- Prefer extending existing modules over introducing new services or frameworks.
- Every external dependency needs a justification in the ADR (license, maintenance, size).
- Health/readiness endpoints must be cheap, side-effect free and must not block on slow I/O.
- Design for failure: define timeouts, retries and what the caller sees on failure.
- Keep interfaces small and typed; document every new public API.
