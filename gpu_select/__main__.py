"""gpu-select CLI entry point."""

from __future__ import annotations

import argparse
import os
import sys

from gpu_select import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gpu-select",
        description="Per-app GPU assignment for Linux hybrid GPU laptops",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    # detect
    sub.add_parser("detect", help="Auto-detect GPUs and show env vars for each")

    # list
    sub.add_parser("list", help="Show configured app → GPU rules")

    # set
    p_set = sub.add_parser("set", help="Set GPU preference for an app")
    p_set.add_argument("match", help="App name or glob pattern (e.g. 'blender', 'electron*')")
    p_set.add_argument("gpu", choices=["igpu", "dgpu"], help="GPU to use")
    p_set.add_argument("--system", action="store_true", help="Write to system config instead of user config")

    # run
    p_run = sub.add_parser("run", help="Launch an app with its configured GPU")
    p_run.add_argument("app", help="Application to launch")
    p_run.add_argument("args", nargs=argparse.REMAINDER, help="Arguments to pass to the app")

    # apply
    p_apply = sub.add_parser("apply", help="Regenerate .desktop overrides, shell aliases, and compositor rules")
    p_apply.add_argument("--desktop", action="store_true", help="Only generate .desktop overrides")
    p_apply.add_argument("--shell", action="store_true", help="Only generate shell aliases")
    p_apply.add_argument("--compositor", action="store_true", help="Only generate compositor rules")

    # check
    p_check = sub.add_parser("check", help="Check for running apps configured to use dGPU")

    args = parser.parse_args(argv)

    try:
        match args.command:
            case "detect":
                return cmd_detect()
            case "list":
                return cmd_list()
            case "set":
                return cmd_set(args.match, args.gpu, args.system)
            case "run":
                return cmd_run(args.app, args.args)
            case "apply":
                return cmd_apply(args.desktop, args.shell, args.compositor)
            case "check":
                return cmd_check()
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130

    return 0


def cmd_detect() -> int:
    from gpu_select.detect import detect_gpus

    gpus = detect_gpus()
    if not gpus:
        print("No GPUs detected via switcherooctl.")
        return 1

    for gpu in gpus:
        label = gpu.label.upper()
        default = " (default)" if gpu.is_default else ""
        print(f"[{label}] {gpu.name}{default}")
        print(f"  Device: {gpu.device}")
        if gpu.env:
            env_str = " ".join(f"{k}={v}" for k, v in gpu.env.items())
            print(f"  Env:    {env_str}")
        else:
            print("  Env:    (none — system default)")
        print()

    return 0


def cmd_list() -> int:
    from gpu_select.config import get_default_gpu, list_rules

    default = get_default_gpu()
    rules = list_rules()

    print(f"Default GPU: {default}")
    print()

    if not rules:
        print("No rules configured. Use 'gpu-select set <app> <gpu>' to add one.")
        return 0

    # Column formatting
    max_match = max(len(r["match"]) for r in rules)
    max_gpu = max(len(r["gpu"]) for r in rules)

    for r in rules:
        source = f"[{r['source']}]"
        extra = ""
        if r.get("env"):
            extra = f"  +env: {r['env']}"
        print(f"  {r['match']:<{max_match}}  → {r['gpu']:<{max_gpu}}  {source}{extra}")

    return 0


def cmd_set(match: str, gpu: str, system: bool) -> int:
    from gpu_select.config import set_rule

    set_rule(match, gpu, system=system)
    target = "system" if system else "user"
    print(f"Set {match} → {gpu} ({target} config)")
    return 0


def cmd_run(app: str, app_args: list[str]) -> int:
    from gpu_select.config import get_default_gpu, get_rule
    from gpu_select.detect import detect_gpus, get_env_for_gpu

    # Find matching rule
    rule = get_rule(app)
    gpu_label = rule["gpu"] if rule else get_default_gpu()

    gpus = detect_gpus()
    env_vars = get_env_for_gpu(gpus, gpu_label)

    # Merge rule-specific env vars
    if rule and rule.get("env"):
        env_vars.update(rule["env"])

    # Build environment
    run_env = os.environ.copy()
    run_env.update(env_vars)

    # exec into the app
    if env_vars:
        env_str = " ".join(f"{k}={v}" for k, v in env_vars.items())
        print(f"[gpu-select] Launching {app} with {gpu_label}: {env_str}", file=sys.stderr)
    else:
        print(f"[gpu-select] Launching {app} with {gpu_label} (default, no env override)", file=sys.stderr)

    os.execvpe(app, [app] + app_args, run_env)
    # unreachable
    return 0


def cmd_apply(desktop_only: bool, shell_only: bool, compositor_only: bool) -> int:
    from gpu_select.config import get_default_gpu, list_rules
    from gpu_select.detect import detect_gpus

    gpus = detect_gpus()
    rules = list_rules()
    default_gpu = get_default_gpu()

    # If no flags, do all
    do_all = not (desktop_only or shell_only or compositor_only)

    if do_all or desktop_only:
        from gpu_select.desktop import generate_desktop_overrides
        paths = generate_desktop_overrides(rules, gpus)
        print(f".desktop overrides: {len(paths)} files generated")
        for p in paths:
            print(f"  {p}")

    if do_all or shell_only:
        from gpu_select.shell import generate_shell_config, get_source_instruction
        path = generate_shell_config(rules, default_gpu, gpus)
        print(f"\nShell config: {path}")
        print(f"Add to your shell rc: {get_source_instruction()}")

    if do_all or compositor_only:
        from gpu_select.compositor import detect_compositor, generate_all_compositor_configs, get_source_instruction as comp_source

        configs = generate_all_compositor_configs(rules, default_gpu, gpus)
        if configs:
            for comp, path in configs.items():
                print(f"\n{comp} config: {path}")
                print(f"Add to config: {comp_source(comp)}")
        else:
            print("\nNo compositor detected. Generate manually with: gpu-select apply --compositor")

    return 0


def cmd_check() -> int:
    """Check for running processes that match dGPU rules."""
    import fnmatch

    from gpu_select.config import list_rules

    rules = list_rules()
    dgpu_rules = [r for r in rules if r["gpu"] == "dgpu"]

    if not dgpu_rules:
        print("No apps configured to use dGPU.")
        return 0

    # Get running process names
    try:
        pids = [d for d in os.listdir("/proc") if d.isdigit()]
    except OSError:
        print("Cannot read /proc", file=sys.stderr)
        return 1

    running_names: set[str] = set()
    for pid in pids:
        try:
            with open(f"/proc/{pid}/comm") as f:
                comm = f.read().strip()
            running_names.add(comm)
        except (OSError, PermissionError):
            continue

    # Check matches
    matches: list[tuple[str, str]] = []
    for rule in dgpu_rules:
        pattern = rule["match"]
        for name in running_names:
            if fnmatch.fnmatch(name, pattern):
                matches.append((name, pattern))

    if matches:
        print("⚠ Running processes configured to use dGPU:")
        for name, pattern in sorted(matches):
            print(f"  {name} (rule: {pattern})")
        return 1
    else:
        print("✓ No dGPU-configured apps are currently running.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
