# Security policy (excerpt)

- Secrets come from the environment or a secret manager, never from source code or prompts.
- No production credentials on development or coding workers.
- Inputs crossing a trust boundary are validated; file paths are confined to their root.
- New network listeners bind to localhost unless explicitly required otherwise.
- Dependencies must be pinned and license-compatible; report CVEs of severity high or critical.
