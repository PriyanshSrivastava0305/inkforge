# Working on InkForge

Personal project; these are notes to self.

1. **Git is local only.** No remote, no pushes (Q10).
2. **ADR first** for any non-obvious choice: copy `docs/adr/0000-template.md`.
3. **Fixtures before features:** extend `tests/golden/` first.
4. **Model ladder:** Fake adapter for tests → `llama3.2:1b` for a quick real smoke → `llama3.1:8b-instruct-q4_K_M` at the end (long runs in the background / overnight). No other models (D16).
5. **Definition of done:** code + tests + the relevant `docs/` page + CHANGELOG entry; `uv run pre-commit run --all-files` clean; works on Windows.
6. Write files as UTF-8 explicitly; never rely on the console code page.
