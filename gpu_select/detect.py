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
    """Get environment variables for a GPU mode.

    Supported labels:
      - 'igpu': Full iGPU isolation — blocks all NVIDIA library loading
      - 'igpu+accel': iGPU rendering, but allows dGPU acceleration (video decode, etc.)
      - 'dgpu': Full dGPU rendering via NVIDIA PRIME offload
    """
    if label == "igpu":
        # Full isolation: render on iGPU AND block all NVIDIA library paths
        igpu = _find_gpu_by_label(gpus, "igpu")
        env = dict(igpu.env) if igpu else {}
        # Add NVIDIA isolation vars
        env.update(_nvidia_isolation_env())
        return env

    if label == "igpu+accel":
        # iGPU rendering only (DRI_PRIME), but allow NVIDIA libs to load
        igpu = _find_gpu_by_label(gpus, "igpu")
        return dict(igpu.env) if igpu else {}

    if label == "dgpu":
        dgpu = _find_gpu_by_label(gpus, "dgpu")
        if dgpu:
            return dict(dgpu.env)
        raise ValueError(f"No dGPU found. Available: {[g.label for g in gpus]}")

    raise ValueError(f"Unknown GPU mode '{label}'. Use 'igpu', 'igpu+accel', or 'dgpu'.")


def _find_gpu_by_label(gpus: list[GPU], label: str) -> GPU | None:
    """Find first GPU matching the given label."""
    for gpu in gpus:
        if gpu.label == label:
            return gpu
    return None


def _nvidia_isolation_env() -> dict[str, str]:
    """Return env vars that completely prevent NVIDIA GPU access.

    This blocks:
    - EGL: only load Mesa ICD
    - GLX: force Mesa
    - Vulkan: disable NVIDIA ICD and layers
    - VA-API: force radeonsi driver
    - DRI: force device 0 (iGPU)
    """
    from pathlib import Path

    env: dict[str, str] = {}

    # EGL: only load Mesa vendor library
    mesa_egl = Path("/usr/share/glvnd/egl_vendor.d/50_mesa.json")
    if mesa_egl.exists():
        env["__EGL_VENDOR_LIBRARY_FILENAMES"] = str(mesa_egl)

    # GLX: force Mesa
    env["__GLX_VENDOR_LIBRARY_NAME"] = "mesa"

    # Vulkan: use only AMD/radeon ICD, disable NVIDIA ICD and layers
    radeon_icd = Path("/usr/share/vulkan/icd.d/radeon_icd.json")
    if radeon_icd.exists():
        env["VK_DRIVER_FILES"] = str(radeon_icd)
    env["VK_LOADER_DRIVERS_DISABLE"] = "nvidia*"
    env["VK_LOADER_LAYERS_DISABLE"] = "VK_LAYER_NV_*"

    # VA-API: force radeonsi
    env["LIBVA_DRIVER_NAME"] = "radeonsi"

    # DRI: force iGPU
    env["DRI_PRIME"] = "0"

    return env
