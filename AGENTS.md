# Agents

Any coding agent (Claude Code, Codex, Cursor, …) working in this repository
follows [RULES.md](RULES.md) and starts each session from
[HANDOFF.md](HANDOFF.md). This file defines the subagent roles a main agent
may deploy, and how to brief them.

## Roles

| Role | Use it for | Tools / isolation | Output |
|---|---|---|---|
| **Explorer** | "Where is X handled?", sweeping many files, tracing a field through collect → index → annotate → export | read-only | file:line pointers + a short conclusion |
| **Implementer** | One well-scoped change that runs in parallel with other work (e.g. a new source adapter while the main agent does a filter) | `isolation: "worktree"` | diff summary + test results; **no commit** |
| **Test writer** | Offline tests for a finished change, especially the §4 invariants in RULES.md | worktree or main tree if idle | new test file(s) + pass/fail output |
| **Reviewer** | Second opinion on a diff before commit: correctness, privacy leaks, broken invariants | read-only | findings ranked by severity |
| **Data auditor** | Read-only stats over `data/` and `data/db/*.sqlite3`: noise rates, label distributions, date spans, temporal-context coverage | read-only, **never writes data** | numbers + the query/script used |

Use the Plan agent type for design questions with real trade-offs (e.g. how
to restructure the context builder). Don't use it for small edits.

## Briefing template

Every subagent prompt includes:

```
Repo: uyam (C:\Users\Gio\Documents\GitHub\uyam) — Taglish sarcasm dataset pipeline.
Follow RULES.md. In particular:
- work offline; tests use fixtures / fake HTTP / tmp_path only
- never print, store, or log plaintext Reddit usernames
- never write to data/ or data/db/*.sqlite3 (copy to a scratch dir if you must)
- do NOT commit or push; report back instead
Task: <one paragraph: goal, files in scope, done-criteria>
Report: <what the final message must contain>
```

## Hand-back checklist (main agent)

1. Read the subagent's diff yourself. Don't merge on its summary.
2. Re-run `python -m pytest -q`, `ruff check src tests`, `mypy src/uyam/`.
3. Commit with a Conventional Commit message, with no AI attribution.
4. Note what the subagent did in HANDOFF.md's session entry.
