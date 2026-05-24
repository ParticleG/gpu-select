"""gpu-select: Per-app GPU assignment tool for Linux hybrid GPU laptops."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("gpu-select")
except PackageNotFoundError:
    __version__ = "0.0.0"  # fallback when not installed
