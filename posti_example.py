#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from typing import Dict, Iterable

try:
    import colorama

    colorama.init()
except Exception:
    colorama = None


# === POSTI PROFILE DATA START ===
PROFILE_DATA_JSON = r"""{
    "fedora-cli": {
        "label": "Fedora CLI (Server)",
        "description": "Minimal Fedora installation reachable over SSH.",
        "preflight": [
            "ssh-copy-id user@host",
            "scp ~/.ssh/id_rsa user@host:~/.ssh/",
            "scp this tool to the target host"
        ],
        "steps": [
            {
                "title": "Update system",
                "command": "sudo dnf update",
                "confirm": true,
                "enabled": true,
                "description": "Get Your system ready and up-to-date before next step"
            },
            {
                "title": "Add qemu agent",
                "command": "sudo dnf install qemu-guest-agent",
                "confirm": true,
                "enabled": true,
                "description": "if You are running this in VM consider installing Qemu guest agent"
            },
            {
                "title": "Additional software installation",
                "command": "sudo dnf install git stow micro fastfetch zsh ncdu",
                "confirm": true,
                "enabled": true,
                "description": "Install software defined by You"
            },
            {
                "title": "Update locate db",
                "command": "sudo updatedb",
                "confirm": true,
                "enabled": true,
                "description": "without this locate function is not able to catch current file state and some files/folders might not be findable by locate"
            },
            {
                "title": "Git clone repo",
                "command": "git clone repo",
                "confirm": true,
                "enabled": true,
                "description": "Clone repo with Your dotfiles"
            },
            {
                "title": "Stow setup preparation",
                "command": "mkdir ~/.config && mkdir ~/.config/git && mkdir ~/.config/micro && mkdir ~/.config/fastfetch",
                "confirm": true,
                "enabled": true,
                "description": "Prepare Your system for stow - create proper folders"
            },
            {
                "title": "Stow execute",
                "command": "stow -d source -t target",
                "confirm": true,
                "enabled": true,
                "description": "Link Your dotfiles from github to Your system using stow"
            },
            {
                "title": "Installation of oh-my-zsh",
                "command": "wget https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh && chmod +x ./install.sh && sed -i 's/exec zsh -l/echo Done installing oh-my-zsh/g' install.sh && ./install.sh && rm ./install.sh",
                "confirm": true,
                "enabled": true
            },
            {
                "title": "Installation of plugins oh-my-zsh",
                "command": "git clone https://github.com/zsh-users/zsh-autosuggestions ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/zsh-autosuggestions && git clone https://github.com/zsh-users/zsh-syntax-highlighting.git ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/zsh-syntax-highlighting",
                "confirm": true,
                "enabled": true
            },
            {
                "title": "Starship installation",
                "command": "curl -sS https://starship.rs/install.sh | sh",
                "confirm": true,
                "enabled": true
            }
        ]
    }
}"""
# === POSTI PROFILE DATA END ===
PROFILE_DATA = json.loads(PROFILE_DATA_JSON)


RESET = "\033[0m"
GREEN = "\033[92m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
SCREEN_WIDTH = 64
STEP_BORDER = "=" * (SCREEN_WIDTH + 10)


def supports_color() -> bool:
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stdout.isatty()


def colorize(text: str, color: str) -> str:
    if not supports_color():
        return text
    return f"{color}{text}{RESET}"


def prompt_bool(message: str, default: bool = True) -> bool:
    prompt = "[Y/n]" if default else "[y/N]"
    while True:
        response = input(colorize(f"{message} {prompt} >", MAGENTA) + " ").strip().lower()
        if not response:
            return default
        if response in {"y", "yes"}:
            return True
        if response in {"n", "no"}:
            return False
        print("Please answer yes or no.")


def hacker_banner() -> None:
    art = """
 ____    ___    ____   _____   ___ 
|  _ \\  / _ \\  / ___| |_   _| |_ _|
| |_) || | | | \\___ \\   | |    | | 
|  __/ | |_| |  ___) |  | |    | | 
|_|     \\___/  |____/   |_|   |___|
"""
    print(colorize(art, GREEN))
    print(colorize("POSTI :: Post install interactive orchestrator", BOLD))
    print(colorize("-" * SCREEN_WIDTH, MAGENTA))
    print(colorize("Booting CRT simulation... stand by", CYAN))
    print(colorize("-" * SCREEN_WIDTH, MAGENTA))


def panel(title: str, details: str | None = None, commands: list[str] | None = None) -> None:
    border = colorize("+" + "=" * SCREEN_WIDTH + "+", MAGENTA)
    print(border)
    print(colorize(f"|{title.center(SCREEN_WIDTH)}|", BOLD))
    if details:
        for line in textwrap.wrap(details, width=SCREEN_WIDTH):
            padded = f" {line}".ljust(SCREEN_WIDTH)
            print(colorize(f"|{padded}|", CYAN))
    if commands:
        print(colorize(f"|{'COMMANDS TO RUN:'.ljust(SCREEN_WIDTH)}|", CYAN))
        for idx, cmd in enumerate(commands, 1):
            label = f"[{idx}] " if len(commands) > 1 else ""
            wrapped = textwrap.wrap(label + cmd, width=SCREEN_WIDTH - 1) or [label + cmd]
            for line in wrapped:
                padded = f" {line}".ljust(SCREEN_WIDTH)
                print(colorize(f"|{padded}|", CYAN))
    print(border)


def render_matrix(keys, data) -> None:
    print(colorize("\n// TARGET MATRIX", BOLD))
    name_width = 24
    for idx, key in enumerate(keys, 1):
        profile = data[key]
        label = colorize(f"{idx:02d}", GREEN)
        name = profile["label"][:name_width]
        print(f"  [{label}] {name.ljust(name_width)} :: {profile['description']}")
    exit_name = "EXIT"
    print(f"  [{colorize('00', GREEN)}] {exit_name.ljust(name_width)} :: Abort mission")


@dataclass
class ExecutionContext:
    dry_run: bool

    def run(self, command: str, announce: bool = True) -> None:
        prefix = "[dry-run] " if self.dry_run else ""
        shell = os.environ.get("SHELL", "/bin/bash")
        if announce:
            print(f"        {colorize(prefix + command, CYAN)}")
        if self.dry_run:
            return
        result = subprocess.run(command, shell=True, executable=shell)
        if result.returncode != 0:
            raise RuntimeError(f"Command failed with exit code {result.returncode}")


def choose_profile(data: Dict[str, dict]) -> str:
    keys = list(data.keys())
    render_matrix(keys, data)
    while True:
        choice = input(colorize("» Select profile >", MAGENTA) + " ").strip()
        if choice in {"0", "00"}:
            print(colorize("Mission aborted. Powering down POSTI console.", YELLOW))
            sys.exit(0)
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(keys):
                return keys[idx - 1]
        print("Enter a valid number.")


def display_preflight(profile: dict) -> None:
    preflight = profile.get("preflight", [])
    if not preflight:
        return
    print(colorize("\n// PRE-FLIGHT CHECKLIST", BOLD))
    for item in preflight:
        print(f"  - {item}")
    print()


def _split_subcommands(command: str) -> list[str]:
    raw = command.strip()
    if not raw:
        return []
    if "&&" not in raw:
        return [raw]
    if any(sep in raw for sep in ["|", ";", "\n"]):
        return [raw]
    parts = [part.strip() for part in raw.split("&&")]
    parts = [part for part in parts if part]
    return parts if len(parts) > 1 else [raw]


def run_steps(profile: dict, ctx: ExecutionContext, auto_confirm: bool) -> None:
    steps = profile.get("steps", [])
    total_steps = len(steps)
    if not steps:
        panel("NO STEPS DEPLOYED", "Profile empty. Use POSTI Designer to add actions.")
        print(colorize(":: Nothing to execute for this profile ::", YELLOW))
        return
    for idx, step in enumerate(steps, 1):
        title = step.get("title", "Unnamed step")
        description = step.get("description")
        confirm = step.get("confirm", False)
        command = step.get("command", "")
        enabled = step.get("enabled", True)
        subcommands = _split_subcommands(command)
        step_header = f"[{idx}/{total_steps}] {title}"
        print(colorize(STEP_BORDER, MAGENTA))
        print(colorize(f">>> STEP {idx}/{total_steps}", GREEN))
        display_title = step_header if enabled else f"{step_header} [DISABLED]"
        panel(display_title, description, subcommands if subcommands else None)
        if not enabled:
            print(colorize("    Step is disabled – skipping execution.", YELLOW))
            print(colorize(f"<<< STEP {idx}/{total_steps} DISABLED", YELLOW))
            print(colorize(STEP_BORDER, MAGENTA))
            print()
            continue
        if not subcommands:
            continue
        if confirm and not auto_confirm:
            if not prompt_bool("Execute this step?", True):
                print(colorize("    Skipped by operator request.", YELLOW))
                continue
        if len(subcommands) == 1:
            try:
                ctx.run(subcommands[0])
            except RuntimeError as exc:
                print(colorize(f"    Error: {exc}", YELLOW))
                if auto_confirm:
                    raise
                if not prompt_bool("Continue with remaining steps?", default=False):
                    print(colorize("Halting on operator request.", YELLOW))
                    return
        else:
            failed = 0
            for pos, sub in enumerate(subcommands, 1):
                label = colorize(f"[{pos}/{len(subcommands)}]", GREEN)
                prefix = "[dry-run] " if ctx.dry_run else ""
                print(f"    {label} {prefix}{sub}")
                try:
                    ctx.run(sub, announce=False)
                    if ctx.dry_run:
                        print(colorize("        -> dry-run (skipped)", CYAN))
                    else:
                        print(colorize("        -> OK", GREEN))
                except RuntimeError as exc:
                    failed += 1
                    print(colorize(f"        -> {exc}", YELLOW))
            if failed:
                print(colorize(f"    Step finished with {failed} failed sub-command(s).", YELLOW))
                if not auto_confirm:
                    if not prompt_bool("Continue with remaining steps?", default=False):
                        print(colorize("Halting on operator request.", YELLOW))
                        return
        print(colorize(f"<<< STEP {idx}/{total_steps} COMPLETED", GREEN))
        print(colorize(STEP_BORDER, MAGENTA))
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="POSTI automation runner.")
    parser.add_argument("--profile", help="Profile key to run without interactive menu.")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing.")
    parser.add_argument("--yes", action="store_true", help="Auto approve step confirmations.")
    args = parser.parse_args()

    hacker_banner()
    data = PROFILE_DATA
    if args.profile:
        if args.profile not in data:
            print(f"Unknown profile '{args.profile}'. Available: {', '.join(sorted(data))}")
            sys.exit(1)
        profile_key = args.profile
    else:
        profile_key = choose_profile(data)

    profile = data[profile_key]
    print(colorize(f"\nLoaded profile: {profile['label']}", GREEN))
    display_preflight(profile)
    dry_run = args.dry_run or prompt_bool("Enable dry-run mode?", default=True)
    ctx = ExecutionContext(dry_run=dry_run)
    run_steps(profile, ctx, auto_confirm=args.yes)
    print(colorize("\nAll done. Consider launching zsh manually when ready.", GREEN))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
