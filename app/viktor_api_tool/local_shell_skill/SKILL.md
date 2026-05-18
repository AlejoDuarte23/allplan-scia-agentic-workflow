# VIKTOR Local Shell

Use this skill to inspect VIKTOR apps and draft or test bridge code through the local shell tool.

Rules:

- Use `python -c "..."` for API calls and JSON processing.
- `TOKEN_VK_APP` is available in the process environment. Never print it.
- The executor only allows `python`, `python3`, and `curl`; it blocks shell pipes, redirects, and common filesystem commands.
- Network calls from Python are intended only for allowed VIKTOR domains such as `demo.viktor.ai`.
- Return final answers as compact JSON when the task asks for API results or entity creation details.
- For VIKTOR entity reads, use query flags `properties=true`, `clean_params=true`, and `param_types=true` when needed.
- For VIKTOR methods, create jobs with `poll_result=false`, poll the returned job URL until `success` or `failed`, then inspect `result`.
- For entity creation, prefer creating a sibling of a source entity by checking `/parent/`; if no parent is available, create at workspace root.
