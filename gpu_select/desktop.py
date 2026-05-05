"""Generate and manage .desktop file overrides for gpu-select.

Overrides are written to ~/.local/share/applications/ and prepend
``env KEY=VALUE ...`` to ``Exec=`` lines so applications launch on the
correct GPU.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

from gpu_select.detect import GPU

if TYPE_CHECKING:
    pass  # gpu_select.config imported lazily where needed

# Marker written as the first line of every override file.
_MARKER = "# Modified by gpu-select"

# Directories that contain .desktop files (system-wide then user-local).
_SYSTEM_APPS_DIR = Path("/usr/share/applications")
_USER_APPS_DIR = Path(os.path.expanduser("~/.local/share/applications"))

# ──────────────────────────────────────────────────────────────────────────────
# Internal parsing helpers
# ──────────────────────────────────────────────────────────────────────────────

def _parse_desktop_file(path: Path) -> list[tuple[str, str | None]]:
    """Parse a .desktop file into an ordered list of (line_type, value) tuples.

    Each element is one of:
      - ("comment", raw_line)   — blank lines and lines starting with ``#``
      - ("section", section_name)  — e.g. "Desktop Entry" or "Desktop Action Foo"
      - ("key", "KEY=raw_value")   — a key=value pair (raw, unsplit)

    We preserve order and blank lines so we can reconstruct the file faithfully.
    """
    entries: list[tuple[str, str | None]] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True):
        stripped = raw.rstrip("\n\r")
        if stripped.startswith("[") and stripped.endswith("]"):
            entries.append(("section", stripped[1:-1]))
        elif stripped.startswith("#") or stripped == "":
            entries.append(("comment", stripped))
        elif "=" in stripped:
            entries.append(("key", stripped))
        else:
            # Continuation lines or unknown — preserve verbatim
            entries.append(("comment", stripped))
    return entries


def _get_desktop_value(entries: list[tuple[str, str | None]], key: str) -> str | None:
    """Return the first value for *key* in the [Desktop Entry] section, or None."""
    in_entry = False
    for kind, value in entries:
        if kind == "section":
            in_entry = value == "Desktop Entry"
            continue
        if not in_entry:
            continue
        if kind == "key" and value is not None:
            k, _, v = value.partition("=")
            if k.strip() == key:
                return v.strip()
    return None


def _exec_basename(exec_line: str) -> str:
    """Return the basename of the executable in an Exec= value.

    Handles ``env VAR=VAL cmd``, absolute paths, and simple names.
    """
    parts = shlex.split(exec_line)
    # Skip leading 'env' and any VAR=VALUE tokens
    for part in parts:
        if part == "env":
            continue
        if "=" in part and not part.startswith("/"):
            continue
        # First real token
        return Path(part).name
    return ""


# ──────────────────────────────────────────────────────────────────────────────
# Rule matching
# ──────────────────────────────────────────────────────────────────────────────

def _match_desktop_to_rule(
    desktop_path: Path,
    rules: list[dict],
) -> dict | None:
    """Return the first rule that matches *desktop_path*, or None.

    Matching strategy (in order):
    1. Rule ``app`` key matched against the .desktop basename without extension
       using ``fnmatch.fnmatch`` (case-insensitive).
    2. Rule ``app`` key matched against the executable basename from ``Exec=``
       using ``fnmatch.fnmatch`` (case-insensitive).
    """
    stem = desktop_path.stem.lower()

    # Lazily parse only when we have at least one rule to check
    entries: list[tuple[str, str | None]] | None = None

    for rule in rules:
        app_pattern = rule.get("app", "")
        if not app_pattern:
            continue
        pattern_lower = app_pattern.lower()

        # Match against .desktop filename stem
        if fnmatch.fnmatch(stem, pattern_lower):
            return rule

        # Match against executable name from Exec=
        if entries is None:
            try:
                entries = _parse_desktop_file(desktop_path)
            except OSError:
                return None

        exec_value = _get_desktop_value(entries, "Exec")
        if exec_value:
            exec_bin = _exec_basename(exec_value).lower()
            if exec_bin and fnmatch.fnmatch(exec_bin, pattern_lower):
                return rule

    return None


# ──────────────────────────────────────────────────────────────────────────────
# Exec= rewriting
# ──────────────────────────────────────────────────────────────────────────────

_FIELD_CODE_RE = re.compile(r"%[fFuUdDnNickvm%]")


def _prepend_env_to_exec(exec_line: str, env: dict[str, str]) -> str:
    """Prepend ``env KEY=VALUE ...`` to an Exec= value.

    Field codes (``%f``, ``%u``, ``%F``, ``%U``, etc.) are preserved in their
    original positions.  The function avoids double-wrapping if the line
    already begins with ``env ``.

    Example::

        "firefox %u"  →  "env __NV_PRIME_RENDER_OFFLOAD=1 firefox %u"
    """
    if not env:
        return exec_line

    # Extract and temporarily remove field codes so shlex doesn't choke on them
    field_codes: list[tuple[int, str]] = []
    placeholder_base = "\x00FC\x00"

    def replace_fc(m: re.Match) -> str:  # type: ignore[type-arg]
        idx = len(field_codes)
        field_codes.append((idx, m.group()))
        return f"{placeholder_base}{idx}\x00"

    scrubbed = _FIELD_CODE_RE.sub(replace_fc, exec_line)

    # Build the env prefix
    env_prefix = "env " + " ".join(f"{k}={v}" for k, v in env.items())

    # Reconstruct: if the original already starts with "env ", we just insert
    # the new vars after "env "
    stripped = scrubbed.strip()
    if stripped.startswith("env "):
        # Insert new vars right after "env "
        rest = stripped[4:]
        new_exec = f"env {env_prefix[4:]} {rest}"
    else:
        new_exec = f"{env_prefix} {stripped}"

    # Re-insert field codes
    for idx, code in field_codes:
        new_exec = new_exec.replace(f"{placeholder_base}{idx}\x00", code)

    return new_exec


# ──────────────────────────────────────────────────────────────────────────────
# Override generation
# ──────────────────────────────────────────────────────────────────────────────

def _collect_desktop_files() -> list[Path]:
    """Return all .desktop files from system and user application directories."""
    seen: set[str] = set()
    files: list[Path] = []

    for directory in (_SYSTEM_APPS_DIR, _USER_APPS_DIR):
        if not directory.is_dir():
            continue
        for p in sorted(directory.glob("*.desktop")):
            if p.name not in seen:
                seen.add(p.name)
                files.append(p)

    return files


def _resolve_gpu(rule: dict, gpus: list[GPU]) -> GPU | None:
    """Return the GPU that satisfies *rule*, or None if not found."""
    label = rule.get("gpu", "")
    for gpu in gpus:
        if gpu.label == label:
            return gpu
    return None


def _build_override_content(
    entries: list[tuple[str, str | None]],
    env: dict[str, str],
    app_name: str,
    gpu_label: str,
) -> str:
    """Reconstruct a .desktop file with Exec= lines rewritten."""
    lines: list[str] = [
        _MARKER,
        f"# gpu-select: app={app_name} gpu={gpu_label}",
        "",
    ]

    for kind, value in entries:
        if kind == "section":
            lines.append(f"[{value}]")
        elif kind == "comment":
            lines.append(value or "")
        elif kind == "key" and value is not None:
            k, _, v = value.partition("=")
            key = k.strip()
            if key == "Exec":
                new_v = _prepend_env_to_exec(v.strip(), env)
                lines.append(f"Exec={new_v}")
            else:
                # TryExec and everything else — preserve verbatim
                lines.append(value)
        else:
            lines.append(value or "")

    return "\n".join(lines) + "\n"


def generate_desktop_overrides(
    rules: list[dict],
    gpus: list[GPU],
) -> list[Path]:
    """Generate user-local .desktop overrides for all matching applications.

    For every ``.desktop`` file found in the system and user application
    directories, checks whether it matches a rule.  If it does, writes an
    override to ``~/.local/share/applications/`` that prepends the GPU's
    environment variables to every ``Exec=`` line.

    Parameters
    ----------
    rules:
        List of rule dicts, each with at minimum ``app`` (pattern) and
        ``gpu`` (label, e.g. ``"dgpu"``).
    gpus:
        List of detected :class:`~gpu_select.detect.GPU` instances as
        returned by :func:`~gpu_select.detect.detect_gpus`.

    Returns
    -------
    list[Path]
        Paths of override files that were written (created or updated).
    """
    _USER_APPS_DIR.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for desktop_path in _collect_desktop_files():
        # Skip files already managed by gpu-select to avoid re-processing
        # overrides on top of overrides.
        try:
            first_line = desktop_path.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0]
            if first_line.strip() == _MARKER:
                continue
        except OSError:
            continue

        rule = _match_desktop_to_rule(desktop_path, rules)
        if rule is None:
            continue

        gpu = _resolve_gpu(rule, gpus)
        if gpu is None:
            continue

        try:
            entries = _parse_desktop_file(desktop_path)
        except OSError:
            continue

        app_name = _get_desktop_value(entries, "Name") or desktop_path.stem
        content = _build_override_content(entries, gpu.env, app_name, gpu.label)

        out_path = _USER_APPS_DIR / desktop_path.name
        out_path.write_text(content, encoding="utf-8")
        written.append(out_path)

    return written


# ──────────────────────────────────────────────────────────────────────────────
# Override removal / listing
# ──────────────────────────────────────────────────────────────────────────────

def _is_gpu_select_override(path: Path) -> bool:
    """Return True if *path* is a gpu-select generated override."""
    try:
        first_line = path.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0]
        return first_line.strip() == _MARKER
    except OSError:
        return False


def remove_desktop_overrides() -> list[Path]:
    """Remove all gpu-select generated overrides from the user application dir.

    Returns
    -------
    list[Path]
        Paths of the files that were deleted.
    """
    removed: list[Path] = []

    if not _USER_APPS_DIR.is_dir():
        return removed

    for path in sorted(_USER_APPS_DIR.glob("*.desktop")):
        if _is_gpu_select_override(path):
            try:
                path.unlink()
                removed.append(path)
            except OSError:
                pass

    return removed


def list_desktop_overrides() -> list[tuple[str, str]]:
    """Return ``(app_name, gpu_label)`` for every existing gpu-select override.

    The ``app_name`` is read from the ``Name=`` key in the ``[Desktop Entry]``
    section of the override file.  The ``gpu_label`` is read from the metadata
    comment written during generation (``# gpu-select: app=... gpu=...``).

    Returns
    -------
    list[tuple[str, str]]
        Sorted list of ``(app_name, gpu_label)`` pairs.
    """
    results: list[tuple[str, str]] = []

    if not _USER_APPS_DIR.is_dir():
        return results

    meta_re = re.compile(r"^#\s*gpu-select:\s*app=(?P<app>.+?)\s+gpu=(?P<gpu>\S+)\s*$")

    for path in sorted(_USER_APPS_DIR.glob("*.desktop")):
        if not _is_gpu_select_override(path):
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        app_name: str = path.stem
        gpu_label: str = "unknown"

        for line in text.splitlines():
            m = meta_re.match(line)
            if m:
                app_name = m.group("app")
                gpu_label = m.group("gpu")
                break

        results.append((app_name, gpu_label))

    return sorted(results)
