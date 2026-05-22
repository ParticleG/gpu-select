"""GPU detection via switcherooctl."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field


@dataclass
class GPU:
    """Represents a detected GPU."""
    name: str
    device: str
    is_default: bool
    is_discrete: bool | None = None  # None = field not present in output
    env: dict[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        # Prefer Discrete field (more reliable on modern systems)
        if self.is_discrete is not None:
            return "dgpu" if self.is_discrete else "igpu"
        # Fallback: default GPU is typically the iGPU
        return "igpu" if self.is_default else "dgpu"


def detect_gpus() -> list[GPU]:
    """Detect GPUs using switcherooctl.

    Returns a list of GPU objects, sorted so the default (iGPU) comes first.
    """
    try:
        result = subprocess.run(
            ["switcherooctl", "list"],
            capture_output=True, text=True, check=True,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "switcherooctl not found. Install and enable switcheroo-control:\n"
            "  sudo pacman -S switcheroo-control\n"
            "  sudo systemctl enable --now switcheroo-control.service"
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip()
        if "No GPUs" in stderr or "not available" in stderr.lower() or e.returncode != 0:
            # Check if the service is running
            svc = subprocess.run(
                ["systemctl", "is-active", "switcheroo-control.service"],
                capture_output=True, text=True,
            )
            if svc.stdout.strip() != "active":
                raise RuntimeError(
                    "switcheroo-control.service is not running. Enable it:\n"
                    "  sudo systemctl enable --now switcheroo-control.service"
                )
        raise RuntimeError(f"switcherooctl failed: {stderr}")

    return _parse_switcherooctl(result.stdout)


def _parse_switcherooctl(output: str) -> list[GPU]:
    """Parse switcherooctl list output.

    Example output:
        Device: 0
        Name:        Intel® Graphics (ADL GT2)
        Default:     yes
        Discrete:    no
        Environment: DRI_PRIME=pci-0000_00_02_0

        Device: 1
        Name:        NVIDIA GeForce RTX 3060 Laptop GPU
        Default:     no
        Discrete:    yes
        Environment: __NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia __VK_LAYER_NV_optimus=NVIDIA_only
    """
    gpus: list[GPU] = []
    current: dict = {}

    for line in output.splitlines():
        line = line.strip()
        if not line:
            if current:
                gpus.append(_build_gpu(current))
                current = {}
            continue

        if line.startswith("Device:"):
            if current:
                gpus.append(_build_gpu(current))
            current = {"device": line.split(":", 1)[1].strip()}
        elif line.startswith("Name:"):
            current["name"] = line.split(":", 1)[1].strip()
        elif line.startswith("Default:"):
            current["default"] = line.split(":", 1)[1].strip().lower() == "yes"
        elif line.startswith("Discrete:"):
            current["discrete"] = line.split(":", 1)[1].strip().lower() == "yes"
        elif line.startswith("Environment:"):
            env_str = line.split(":", 1)[1].strip()
            current["env"] = _parse_env(env_str)

    if current:
        gpus.append(_build_gpu(current))

    # Sort: iGPU first (non-discrete before discrete)
    gpus.sort(key=lambda g: (g.is_discrete if g.is_discrete is not None else not g.is_default, g.device))
    return gpus


def _build_gpu(data: dict) -> GPU:
    return GPU(
        name=data.get("name", "Unknown"),
        device=data.get("device", "?"),
        is_default=data.get("default", False),
        is_discrete=data.get("discrete"),  # None if not present
        env=data.get("env", {}),
    )


def _parse_env(env_str: str) -> dict[str, str]:
    """Parse 'KEY=VALUE KEY2=VALUE2' into a dict."""
    env: dict[str, str] = {}
    for part in env_str.split():
        if "=" in part:
            k, v = part.split("=", 1)
            env[k] = v
    return env


def get_env_for_gpu(gpus: list[GPU], label: str) -> dict[str, str]:
    """Get environment variables for a GPU label ('igpu' or 'dgpu')."""
    for gpu in gpus:
        if gpu.label == label:
            return dict(gpu.env)
    raise ValueError(f"No GPU found with label '{label}'. Available: {[g.label for g in gpus]}")
