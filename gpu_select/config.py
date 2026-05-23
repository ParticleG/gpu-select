"""TOML configuration module for gpu-select.

Config files:
  User:   ~/.config/gpu-select/apps.toml
  System: /etc/gpu-select/apps.toml

User config takes precedence; rules are merged (user overrides system for same
match pattern).
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

import tomlkit
from tomlkit import TOMLDocument

# ---------------------------------------------------------------------------
# Template written on first use
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: str = """\
# gpu-select configuration
# Manage per-application GPU assignment for hybrid graphics systems.
#
# gpu values: "igpu" (full isolation), "igpu+accel" (iGPU render + dGPU accel), or "dgpu" (discrete)

[defaults]
# Fallback GPU when no rule matches.
gpu = "igpu"

# ---------------------------------------------------------------------------
# Rules — matched in order; first match wins.
# ---------------------------------------------------------------------------
# Each rule must have:
#   match  — process name, .desktop app-id, or glob pattern (fnmatch syntax)
#   gpu    — "igpu", "igpu+accel", or "dgpu"
# Optional:
#   [rules.env]  — extra environment variables injected alongside the prime
#                  variables (e.g. LIBVA_DRIVER_NAME, VDPAU_DRIVER …)
#
# Examples:
#
# [[rules]]
# match = "blender"
# gpu = "dgpu"
#
# [[rules]]
# match = "electron*"
# gpu = "igpu"
# env = { LIBVA_DRIVER_NAME = "iHD" }
"""

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_USER_CONFIG_DIR = Path.home() / ".config" / "gpu-select"
_SYSTEM_CONFIG_DIR = Path("/etc/gpu-select")
_CONFIG_FILENAME = "apps.toml"


def get_config_path(system: bool = False) -> Path:
    """Return the config file path for user (default) or system config."""
    base = _SYSTEM_CONFIG_DIR if system else _USER_CONFIG_DIR
    return base / _CONFIG_FILENAME


# ---------------------------------------------------------------------------
# Load / Save
# ---------------------------------------------------------------------------


def load_config(system: bool = False) -> TOMLDocument:
    """Load config from disk.

    Returns an empty ``TOMLDocument`` if the file does not exist.
    """
    path = get_config_path(system)
    if not path.exists():
        return tomlkit.document()
    return tomlkit.loads(path.read_text(encoding="utf-8"))


def save_config(doc: TOMLDocument, system: bool = False) -> None:
    """Write *doc* to the appropriate config file, creating dirs if needed."""
    path = get_config_path(system)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")


# ---------------------------------------------------------------------------
# Merge helpers
# ---------------------------------------------------------------------------


def _doc_to_plain(doc: TOMLDocument) -> dict[str, Any]:
    """Convert a TOMLDocument to a plain dict (unwraps tomlkit proxy objects)."""
    return dict(tomlkit.loads(tomlkit.dumps(doc)))  # simple round-trip unwrap


def load_merged_config() -> dict[str, Any]:
    """Return a plain dict with system and user configs merged.

    Merge strategy:
    - ``[defaults]`` — user value wins for each key.
    - ``[[rules]]``  — user rules with the same *match* pattern override the
                       corresponding system rule; otherwise both are kept.
                       User rules appear first (higher priority at lookup time).
    """
    sys_doc = _doc_to_plain(load_config(system=True))
    usr_doc = _doc_to_plain(load_config(system=False))

    # --- defaults ---
    merged_defaults: dict[str, Any] = {**sys_doc.get("defaults", {}), **usr_doc.get("defaults", {})}

    # --- rules ---
    sys_rules: list[dict[str, Any]] = sys_doc.get("rules", [])
    usr_rules: list[dict[str, Any]] = usr_doc.get("rules", [])

    usr_matches = {r["match"] for r in usr_rules if "match" in r}
    # Keep system rules whose match is not overridden by a user rule
    filtered_sys = [r for r in sys_rules if r.get("match") not in usr_matches]

    merged_rules = usr_rules + filtered_sys

    return {"defaults": merged_defaults, "rules": merged_rules}


# ---------------------------------------------------------------------------
# Rule helpers
# ---------------------------------------------------------------------------


def get_rule(match: str) -> dict[str, Any] | None:
    """Return the first rule whose *match* pattern matches *match*, or ``None``.

    Comparison is exact first; if no exact match is found, fnmatch glob
    matching is used.
    """
    config = load_merged_config()
    rules: list[dict[str, Any]] = config.get("rules", [])

    # Exact match first
    for rule in rules:
        if rule.get("match") == match:
            return rule

    # Glob / fnmatch
    for rule in rules:
        pattern = rule.get("match", "")
        if fnmatch.fnmatch(match, pattern):
            return rule

    return None


def set_rule(
    match: str,
    gpu: str,
    env: dict[str, str] | None = None,
    system: bool = False,
) -> None:
    """Add or update a rule in the specified config file.

    If a rule with the same *match* already exists in that file it is updated
    in-place (preserving surrounding comments).  Otherwise a new rule is
    appended.

    Parameters
    ----------
    match:
        The process name / app-id / glob pattern to match.
    gpu:
        ``"igpu"``, ``"igpu+accel"``, or ``"dgpu"``.
    env:
        Optional dict of extra environment variables.
    system:
        If ``True``, write to the system config; otherwise the user config.
    """
    if gpu not in {"igpu", "igpu+accel", "dgpu"}:
        raise ValueError(f"gpu must be 'igpu', 'igpu+accel', or 'dgpu', got {gpu!r}")

    path = get_config_path(system)

    # Bootstrap from template if file does not exist yet
    if not path.exists():
        doc: TOMLDocument = tomlkit.loads(DEFAULT_CONFIG)
    else:
        doc = tomlkit.loads(path.read_text(encoding="utf-8"))

    rules = doc.get("rules")  # type: ignore[assignment]
    if rules is None:
        rules = tomlkit.aot()  # Array of Tables
        doc.append("rules", rules)  # type: ignore[arg-type]

    # Look for an existing entry with the same match
    for item in rules:  # type: ignore[union-attr]
        if item.get("match") == match:
            item["gpu"] = gpu
            if env is not None:
                item["env"] = env
            elif "env" in item:
                del item["env"]
            save_config(doc, system=system)
            return

    # Append new rule
    rule_table = tomlkit.table()
    rule_table.append("match", match)
    rule_table.append("gpu", gpu)
    if env:
        rule_table.append("env", env)
    rules.append(rule_table)  # type: ignore[union-attr]

    save_config(doc, system=system)


def get_default_gpu() -> str:
    """Return the default GPU from the merged config (fallback: ``"igpu"``)."""
    config = load_merged_config()
    return config.get("defaults", {}).get("gpu", "igpu")


def list_rules() -> list[dict[str, Any]]:
    """Return all rules from both configs with a ``"source"`` annotation.

    Each returned dict has the same keys as the rule plus a ``"source"`` key
    set to ``"user"`` or ``"system"``.  User rules appear first.
    """
    sys_doc = _doc_to_plain(load_config(system=True))
    usr_doc = _doc_to_plain(load_config(system=False))

    sys_rules: list[dict[str, Any]] = sys_doc.get("rules", [])
    usr_rules: list[dict[str, Any]] = usr_doc.get("rules", [])

    usr_matches = {r["match"] for r in usr_rules if "match" in r}

    annotated: list[dict[str, Any]] = []
    for rule in usr_rules:
        annotated.append({**rule, "source": "user"})
    for rule in sys_rules:
        if rule.get("match") not in usr_matches:
            annotated.append({**rule, "source": "system"})

    return annotated
