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

Requires `switcheroo-control` for GPU detection:

```bash
sudo pacman -S switcheroo-control
sudo systemctl enable --now switcheroo-control.service
```

## Usage

```bash
# Detect available GPUs
gpu-select detect

# Set GPU preference for an app
gpu-select set blender dgpu
gpu-select set "electron*" igpu

# Launch an app with its configured GPU
gpu-select run blender

# Show all configured rules
gpu-select list

# Regenerate .desktop overrides, shell aliases, and compositor rules
gpu-select apply

# Check for running apps configured to use dGPU (useful before GPU passthrough)
gpu-select check
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
match = "electron*"  # glob pattern
gpu = "igpu"

[[rules]]
match = "code"
gpu = "igpu"
env = { LIBVA_DRIVER_NAME = "iHD" }  # optional extra env vars
```

System-wide config: `/etc/gpu-select/apps.toml` (user config takes precedence).

## Integration Methods

`gpu-select apply` generates configurations for three integration methods:

### .desktop file overrides

Generates user-local `.desktop` overrides in `~/.local/share/applications/` that prepend GPU env vars to `Exec=` lines. Works with any desktop environment or app launcher.

### Shell aliases

Generates `~/.config/gpu-select/env.sh` with shell functions that wrap commands with GPU env vars. Add to your shell rc:

```bash
source "$HOME/.config/gpu-select/env.sh"
```

### Compositor rules

Generates snippet files for compositor-level integration:

| Compositor | Snippet | Include with |
|---|---|---|
| Hyprland | `~/.config/gpu-select/hyprland.conf` | `source = ~/.config/gpu-select/hyprland.conf` |
| Sway | `~/.config/gpu-select/sway.conf` | `include ~/.config/gpu-select/sway.conf` |
| niri | `~/.config/gpu-select/niri.kdl` | Manual copy into config (niri has no include) |

## How It Works

1. `gpu-select detect` queries `switcherooctl list` to discover GPUs and their associated environment variables
2. Rules map app names (or glob patterns) to `igpu` or `dgpu`
3. `gpu-select run <app>` resolves the rule, sets env vars, and `exec`s into the app
4. `gpu-select apply` pre-generates static configs so apps launched by other means (DE launcher, systemd, etc.) also get the correct GPU

## Dependencies

- Python 3.11+
- [tomlkit](https://github.com/python-poetry/tomlkit) — TOML read/write with comment preservation
- [switcheroo-control](https://gitlab.freedesktop.org/hadess/switcheroo-control) — GPU detection

## License

MIT
