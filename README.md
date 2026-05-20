# gpu-select

Per-app GPU selection tool for Linux hybrid GPU laptops (iGPU + dGPU).

## Problem

Linux lacks a unified, persistent per-app GPU selection mechanism. Users with hybrid GPU setups need to control which GPU each application uses, but existing solutions are either non-persistent (`prime-run`, env vars), binary (`.desktop` `PrefersNonDefaultGPU`), or DE-specific (KDE KMenuEdit).

## Solution

`gpu-select` provides a config-file-based mapping of app names/patterns → GPU, honored across launch methods (CLI, .desktop, compositor).

## Installation

```bash
pip install gpu-select
```

Or on Arch Linux (AUR):

```bash
paru -S gpu-select
```

Requires `switcheroo-control` for GPU detection:

```bash
sudo pacman -S switcheroo-control
sudo systemctl enable --now switcheroo-control.service
```

## Usage

```bash
# Detect available GPUs
gpu-select detect

# Set GPU preference for an app (writes to user config)
gpu-select set blender dgpu
gpu-select set "electron*" igpu

# Set a system-wide rule (writes to /etc/gpu-select/apps.toml)
gpu-select set blender dgpu --system

# Launch an app with its configured GPU
gpu-select run blender

# Show all configured rules (with source: user/system)
gpu-select list

# Regenerate all integration configs
gpu-select apply

# Regenerate only specific integration
gpu-select apply --desktop
gpu-select apply --shell
gpu-select apply --compositor

# Check for running apps configured to use dGPU
# Exits 0 if none running, 1 if dGPU apps are active (useful before GPU passthrough)
gpu-select check

# Show version
gpu-select --version
```

## Configuration

Config file: `~/.config/gpu-select/apps.toml`

```toml
[defaults]
gpu = "igpu"  # default for unlisted apps

[[rules]]
match = "blender"  # match by process name or .desktop app-id
gpu = "dgpu"

[[rules]]
match = "steam"
gpu = "dgpu"

[[rules]]
match = "electron*"  # glob pattern (fnmatch syntax)
gpu = "igpu"

[[rules]]
match = "code"
gpu = "igpu"
env = { LIBVA_DRIVER_NAME = "iHD" }  # optional extra env vars
```

System-wide config: `/etc/gpu-select/apps.toml` (user rules override system rules with the same `match` pattern; user `[defaults]` keys override system defaults).

## Integration Methods

`gpu-select apply` generates configurations for three integration methods. Use `--desktop`, `--shell`, or `--compositor` to regenerate only one.

### .desktop file overrides

Generates user-local `.desktop` overrides in `~/.local/share/applications/` that prepend GPU env vars to `Exec=` lines. Works with any desktop environment or app launcher.

Matching is done by `.desktop` filename stem and `Exec=` binary name, both via `fnmatch` glob.

### Shell wrapper functions

Generates `~/.config/gpu-select/env.sh` with shell functions that wrap commands with GPU env vars. Add to your shell rc:

```bash
source "$HOME/.config/gpu-select/env.sh"
```

Note: only rules with exact (non-glob) match names produce shell functions. Glob patterns are skipped since they cannot map to a single function name.

### Compositor startup rules

Generates snippet files for compositor-level integration:

| Compositor | Snippet | Include with |
|---|---|---|
| Hyprland | `~/.config/gpu-select/hyprland.conf` | `source = ~/.config/gpu-select/hyprland.conf` |
| Sway | `~/.config/gpu-select/sway.conf` | `include ~/.config/gpu-select/sway.conf` |
| niri | `~/.config/gpu-select/niri.kdl` | Manual copy into config (niri has no include directive) |

Note: compositor snippets only affect apps launched at compositor startup (via `exec` / `spawn-at-startup`). They do not inject env vars into apps launched later from a launcher or terminal — use `.desktop` overrides or shell functions for those.

## How It Works

1. `gpu-select detect` queries `switcherooctl list` to discover GPUs and their associated environment variables
2. Rules map app names (or glob patterns) to `igpu` or `dgpu`; exact match takes priority over glob
3. `gpu-select run <app>` resolves the rule, sets env vars, and `exec`s into the app (replaces the process)
4. `gpu-select apply` pre-generates static configs so apps launched by other means (DE launcher, systemd, compositor autostart) also get the correct GPU
5. `gpu-select check` scans `/proc/*/comm` against dGPU rules — useful for scripting (e.g. block GPU passthrough while dGPU apps are running)

## Dependencies

- Python 3.11+
- [tomlkit](https://github.com/python-poetry/tomlkit) — TOML read/write with comment preservation
- [switcheroo-control](https://gitlab.freedesktop.org/hadess/switcheroo-control) — GPU detection (`switcherooctl`)

## License

MIT
