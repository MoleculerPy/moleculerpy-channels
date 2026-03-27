# MoleculerPy Channels — Pub/Sub Middleware

**Reliable pub/sub messaging for MoleculerPy microservices.**

**Depends on**: `moleculerpy>=0.14.1`

## Quick Start

```bash
pip install -e ".[all]"
docker compose up -d         # Start Redis + NATS
pytest                       # Run tests (155 tests, 100% pass)
mypy moleculerpy_channels/  # Type check
ruff check .                # Lint
```

## Architecture

| Component | File | Purpose |
|---|---|---|
| ChannelsMiddleware | middleware.py | Main integration (509 LOC) |
| RedisAdapter | adapters/redis.py | Redis Streams (~600 LOC) |
| NATSAdapter | adapters/nats.py | NATS JetStream (~566 LOC) |
| FakeAdapter | adapters/fake.py | Testing adapter |

## Performance

- End-to-end latency: **0.77ms** (vs 5ms Node.js — 85% faster)
- 98% feature parity with moleculer-channels

## Git Workflow

### Branches: main ← dev ← feat/*

```bash
git checkout dev && git pull origin dev
git checkout -b feat/task-name
git add file1 file2
git commit -m "feat(module): what was done"
git push origin feat/task-name -u
gh pr create --base dev
gh pr merge --merge --delete-branch=false
```

### Commits: `type(module): description`
Types: feat, fix, docs, test, refactor, chore

### Forbidden
- `git push --force`, `git reset --hard`, `git add .`
- Direct commits to main/dev

## Methodology: Route → Shape → Code → Evidence

1. **Route** — Tactical / Standard / Deep / Critical
2. **Shape** — PRD/RFC/ADR before coding (Standard+)
3. **Code** — every function = test immediately, `pytest` required
4. **Evidence** — confirm the result

## Enforcement Hooks

5 hooks in `.claude/hooks/`:

| Hook | Checks |
|---|---|
| forge-safety | Blocks dangerous commands |
| pr-todo-check | P0 checkboxes before PR |
| commit-test-check | Tests for new `def` |
| pre-code-check | Active PRD before coding |
| pre-commit-health | Blind spots |
