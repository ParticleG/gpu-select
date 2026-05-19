# AGENTS.md

## Project

Python CLI tool for per-app GPU selection on Linux hybrid GPU laptops. Single package, no monorepo.

## Commands

```bash
# Install (editable, with dev deps)
pip install -e ".[dev]"

# Run directly
python -m gpu_select detect

# Lint
ruff check gpu_select/

# Format
ruff format gpu_select/

# Tests (pytest configured but no tests exist yet)
pytest
```

## Architecture

- Entrypoint: `gpu_select/__main__.py:main` (registered as `gpu-select` console script)
- CLI uses `argparse` with subcommands: `detect`, `list`, `set`, `run`, `apply`, `check`
- Lazy imports: each subcommand imports its module only when invoked (inside `cmd_*` functions)
- `detect.py` — parses `switcherooctl list` output to discover GPUs
- `config.py` — TOML config via `tomlkit` (comment-preserving); merges user (`~/.config/gpu-select/apps.toml`) over system (`/etc/gpu-select/apps.toml`)
- `desktop.py` — rewrites `.desktop` files with env var prefixes in `Exec=` lines
- `shell.py` — generates `~/.config/gpu-select/env.sh` with shell wrapper functions
- `compositor.py` — generates snippet files for niri/Hyprland/Sway (startup exec only, no per-window env injection)

## Key conventions

- Python 3.11+ required; uses `match` statements and modern type hints (`X | None`)
- Only runtime dependency: `tomlkit`
- External system dependency: `switcheroo-control` (provides `switcherooctl`)
- Rule matching uses `fnmatch` glob patterns; exact match takes priority over glob
- `cmd_run` calls `os.execvpe` — replaces the process, no return
- Config merge: user rules override system rules with same `match` pattern; user `[defaults]` keys override system

## Gotchas

- No test suite exists yet. If adding tests, mock `subprocess.run` for `switcherooctl` and filesystem writes for config/desktop/shell/compositor outputs.
- `compositor.py` generators expect rules with an `"app"` key (not `"match"`); the `cmd_apply` flow passes `list_rules()` output which uses `"match"`. This is a known inconsistency — compositor generators silently skip rules without `"app"`.
- `SNIPPET_DIR` and other paths use `Path.expanduser()` at module import time — tests must patch these or use `monkeypatch` on `Path.home()`.
