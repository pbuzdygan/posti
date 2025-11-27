#!/usr/bin/env python3
"""
POSTI Forge – profile-aware automation builder.

Features:
* Edit per-profile step sequences (Fedora CLI/Desktop, Ubuntu, Raspbian).
* Load an existing builder-generated posti.py, tweak steps, and overwrite it.
* Export a brand-new posti.py that ships the full console UI plus your steps.
"""

from __future__ import annotations

import json
import traceback
import os
import re
import sys
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List
import shutil
import stat
import subprocess
import tempfile

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)



POSTI_TEMPLATE = r'''#!/usr/bin/env python3
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
PROFILE_DATA_JSON = r"""__PROFILE_DATA__"""
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
'''


START_MARKER = "# === POSTI PROFILE DATA START ==="
END_MARKER = "# === POSTI PROFILE DATA END ==="


@dataclass
class StepModel:
    title: str
    command: str
    confirm: bool = False
    description: str = ""
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "StepModel":
        return cls(
            title=data.get("title", "Unnamed step"),
            command=data.get("command", ""),
            confirm=bool(data.get("confirm", False)),
            description=data.get("description", ""),
            enabled=bool(data.get("enabled", True)),
        )

    def to_dict(self) -> dict:
        payload = {
            "title": self.title,
            "command": self.command,
            "confirm": self.confirm,
            "enabled": self.enabled,
        }
        if self.description:
            payload["description"] = self.description
        return payload


@dataclass
class ProfileModel:
    key: str
    label: str
    description: str
    preflight: List[str] = field(default_factory=list)
    steps: List[StepModel] = field(default_factory=list)

    @classmethod
    def from_dict(cls, key: str, data: dict) -> "ProfileModel":
        return cls(
            key=key,
            label=data.get("label", key),
            description=data.get("description", ""),
            preflight=list(data.get("preflight", [])),
            steps=[StepModel.from_dict(step) for step in data.get("steps", [])],
        )

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "description": self.description,
            "preflight": self.preflight,
            "steps": [step.to_dict() for step in self.steps],
        }


class ProfileDialog(QDialog):
    def __init__(
        self,
        title: str,
        *,
        label: str = "",
        description: str = "",
        preflight: list[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.label_edit = QLineEdit(label)
        form.addRow("Profile label", self.label_edit)
        self.description_edit = QTextEdit()
        self.description_edit.setPlainText(description)
        form.addRow("Description", self.description_edit)
        self.preflight_edit = QPlainTextEdit("\n".join(preflight or []))
        form.addRow("Pre-flight checklist", self.preflight_edit)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:
        if not self.label_edit.text().strip():
            QMessageBox.warning(self, "Missing name", "Profile label cannot be empty.")
            return
        super().accept()

    def get_data(self) -> tuple[str, str, list[str]]:
        label = self.label_edit.text().strip()
        description = self.description_edit.toPlainText().strip()
        preflight = [
            line.strip()
            for line in self.preflight_edit.toPlainText().splitlines()
            if line.strip()
        ]
        return label, description, preflight


class DesignerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("POSTI Forge")
        self.resize(1200, 780)
        self.current_file: Path | None = None
        self.profiles: Dict[str, ProfileModel] = {}
        self.profile_order: List[str] = []
        self._build_ui()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        banner = QLabel("POSTI // FORGE YOUR AUTOMATION")
        banner.setAlignment(Qt.AlignCenter)
        banner.setStyleSheet(
            "color: #39ff14; font-size: 26px; font-family: 'JetBrains Mono', monospace; font-weight: bold;"
        )
        layout.addWidget(banner)

        subtitle = QLabel("Plan per-profile steps. Export a full posti.py with all menus baked in.")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: #0ff; font-size: 14px; font-family: 'JetBrains Mono', monospace;")
        layout.addWidget(subtitle)

        toolbar = QHBoxLayout()
        layout.addLayout(toolbar)

        load_btn = QPushButton("Load POSTI export")
        load_btn.clicked.connect(self.load_existing_file)
        toolbar.addWidget(load_btn)

        new_btn = QPushButton("Reset to blank project")
        new_btn.clicked.connect(self.reset_project)
        toolbar.addWidget(new_btn)

        toolbar.addStretch(1)
        self.file_label = QLabel("No file loaded")
        self.file_label.setStyleSheet("color: #ccc; font-family: 'JetBrains Mono';")
        toolbar.addWidget(self.file_label)

        profile_row = QHBoxLayout()
        layout.addLayout(profile_row)
        profile_row.addWidget(QLabel("Profile"))
        self.profile_combo = QComboBox()
        self.profile_combo.currentIndexChanged.connect(self.switch_profile)
        profile_row.addWidget(self.profile_combo, 1)
        add_profile_btn = QPushButton("Add profile")
        add_profile_btn.clicked.connect(self.add_profile)
        profile_row.addWidget(add_profile_btn)
        edit_profile_btn = QPushButton("Edit profile")
        edit_profile_btn.clicked.connect(self.edit_profile)
        profile_row.addWidget(edit_profile_btn)
        remove_profile_btn = QPushButton("Remove profile")
        remove_profile_btn.clicked.connect(self.remove_profile)
        profile_row.addWidget(remove_profile_btn)
        layout.addSpacing(4)

        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter, 1)

        self.steps_list = QListWidget()
        self.steps_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.steps_list.itemSelectionChanged.connect(self.populate_form_from_selection)
        splitter.addWidget(self.steps_list)

        form = QWidget()
        form_layout = QVBoxLayout(form)
        splitter.addWidget(form)

        form_layout.addWidget(QLabel("Step title"))
        self.title_input = QLineEdit()
        form_layout.addWidget(self.title_input)

        form_layout.addWidget(QLabel("Step description (optional)"))
        self.description_input = QLineEdit()
        form_layout.addWidget(self.description_input)

        form_layout.addWidget(QLabel("Command"))
        self.command_input = QTextEdit()
        self.command_input.setPlaceholderText("Shell command to execute for this step.")
        self.command_input.setFixedHeight(140)
        form_layout.addWidget(self.command_input)

        self.confirm_check = QCheckBox("Require confirmation before executing")
        form_layout.addWidget(self.confirm_check)

        button_row = QHBoxLayout()
        form_layout.addLayout(button_row)
        add_btn = QPushButton("Add step")
        add_btn.clicked.connect(self.add_step)
        button_row.addWidget(add_btn)
        update_btn = QPushButton("Update")
        update_btn.clicked.connect(self.update_step)
        button_row.addWidget(update_btn)
        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self.remove_step)
        button_row.addWidget(remove_btn)

        move_row = QHBoxLayout()
        form_layout.addLayout(move_row)
        up_btn = QPushButton("Move up")
        up_btn.clicked.connect(lambda: self.move_step(-1))
        move_row.addWidget(up_btn)
        down_btn = QPushButton("Move down")
        down_btn.clicked.connect(lambda: self.move_step(1))
        move_row.addWidget(down_btn)
        clone_btn = QPushButton("Clone step")
        clone_btn.clicked.connect(self.clone_step)
        move_row.addWidget(clone_btn)
        self.disable_btn = QPushButton("Disable selected")
        self.disable_btn.clicked.connect(self.disable_selected_steps)
        move_row.addWidget(self.disable_btn)
        self.enable_btn = QPushButton("Enable selected")
        self.enable_btn.clicked.connect(self.enable_selected_steps)
        move_row.addWidget(self.enable_btn)
        self.disable_btn.setEnabled(False)
        self.enable_btn.setEnabled(False)

        form_layout.addStretch(1)
        status_row = QHBoxLayout()
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #0ff; font-style: italic;")
        status_row.addWidget(self.status_label)
        self.status_progress = QProgressBar()
        self.status_progress.setTextVisible(False)
        self.status_progress.setRange(0, 1)
        self.status_progress.hide()
        status_row.addWidget(self.status_progress)
        form_layout.addLayout(status_row)
        self._status_timer: QTimer | None = None

        preview_label = QLabel("posti.py preview")
        layout.addWidget(preview_label)
        self.preview = QPlainTextEdit()
        self.preview.setFont(QFont("JetBrains Mono", 10))
        self.preview.setReadOnly(True)
        self.preview.setFixedHeight(220)
        layout.addWidget(self.preview)

        preview_buttons = QHBoxLayout()
        layout.addLayout(preview_buttons)
        gen_btn = QPushButton("Generate preview")
        gen_btn.clicked.connect(self.generate_script)
        preview_buttons.addWidget(gen_btn)

        copy_btn = QPushButton("Copy to clipboard")
        copy_btn.clicked.connect(self.copy_preview)
        preview_buttons.addWidget(copy_btn)

        save_as_btn = QPushButton("Save as new posti.py")
        save_as_btn.clicked.connect(self.save_as_new)
        preview_buttons.addWidget(save_as_btn)

        save_btn = QPushButton("Overwrite loaded posti.py")
        save_btn.clicked.connect(self.save_over_existing)
        preview_buttons.addWidget(save_btn)

        binary_btn = QPushButton("Build standalone binary")
        binary_btn.clicked.connect(self.build_binary)
        preview_buttons.addWidget(binary_btn)
        preview_buttons.addStretch(1)

        self.refresh_profile_combo()
        self.switch_profile(0)

    # -- profile utilities ------------------------------------------------
    def current_profile_key(self) -> str:
        key = self.profile_combo.currentData()
        if key:
            return key
        return self.profile_order[0] if self.profile_order else ""

    def current_profile(self) -> ProfileModel:
        key = self.current_profile_key()
        if not key:
            raise RuntimeError("No profiles configured.")
        return self.profiles[key]

    def refresh_profile_combo(self) -> None:
        current_key = self.current_profile_key() if self.profile_order else None
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for key in self.profile_order:
            self.profile_combo.addItem(self.profiles[key].label, userData=key)
        self.profile_combo.blockSignals(False)
        if self.profile_order:
            if current_key and current_key in self.profile_order:
                self.profile_combo.setCurrentIndex(self.profile_order.index(current_key))
            else:
                self.profile_combo.setCurrentIndex(0)
        else:
            self.profile_combo.setCurrentIndex(-1)
        self._update_step_buttons()

    def refresh_steps_list(self) -> None:
        selected_rows = sorted({index.row() for index in self.steps_list.selectedIndexes()})
        self.steps_list.blockSignals(True)
        self.steps_list.clear()
        if not self.profile_order:
            self.steps_list.blockSignals(False)
            self.populate_form(None)
            self._update_step_buttons()
            return
        profile = self.current_profile()
        for idx, step in enumerate(profile.steps, 1):
            base = f"{idx:02d}. {step.title}"
            if step.confirm:
                base += " [confirm]"
            label = base
            if not step.enabled:
                label = f"{base:<70}[DISABLED]"
            item = QListWidgetItem(label)
            self.steps_list.addItem(item)
        for row in selected_rows:
            if 0 <= row < self.steps_list.count():
                self.steps_list.item(row).setSelected(True)
        if selected_rows:
            self.steps_list.setCurrentRow(selected_rows[0])
        else:
            self.steps_list.setCurrentRow(-1)
        self.steps_list.blockSignals(False)
        self.populate_form_from_selection()

    # -- event handlers ---------------------------------------------------
    def switch_profile(self, index: int) -> None:
        if not self.profile_order:
            self.steps_list.clear()
            self.populate_form(None)
            self._update_step_buttons()
            return
        self.populate_form(None)
        self.refresh_steps_list()

    def populate_form_from_selection(self) -> None:
        if not self.profile_order:
            self.populate_form(None)
            self._update_step_buttons()
            return
        indexes = self.steps_list.selectedIndexes()
        if not indexes:
            self.populate_form(None)
            self._update_step_buttons()
            return
        index = indexes[0].row()
        profile = self.current_profile()
        if index >= len(profile.steps):
            self.populate_form(None)
            self._update_step_buttons()
            return
        step = profile.steps[index]
        self.populate_form(step)
        self._update_step_buttons()

    def populate_form(self, step: StepModel | None) -> None:
        if not step:
            self.title_input.clear()
            self.description_input.clear()
            self.command_input.clear()
            self.confirm_check.setChecked(False)
            return
        self.title_input.setText(step.title)
        self.description_input.setText(step.description)
        self.command_input.setPlainText(step.command)
        self.confirm_check.setChecked(step.confirm)

    def add_step(self) -> None:
        if not self.ensure_profile_available():
            return
        command = self.command_input.toPlainText().strip()
        if not command:
            QMessageBox.warning(self, "Missing command", "Provide a shell command for the step.")
            return
        title = self.title_input.text().strip() or f"Step {len(self.current_profile().steps) + 1}"
        description = self.description_input.text().strip()
        step = StepModel(title=title, command=command, confirm=self.confirm_check.isChecked(), description=description)
        self.current_profile().steps.append(step)
        self.refresh_steps_list()
        self.populate_form(None)
        self.flash_status("Step added.")

    def update_step(self) -> None:
        if not self.ensure_profile_available():
            return
        index = self.steps_list.currentRow()
        if index < 0:
            return
        command = self.command_input.toPlainText().strip()
        if not command:
            QMessageBox.warning(self, "Missing command", "Provide a shell command for the step.")
            return
        title = self.title_input.text().strip() or f"Step {index + 1}"
        description = self.description_input.text().strip()
        profile = self.current_profile()
        profile.steps[index].title = title
        profile.steps[index].command = command
        profile.steps[index].confirm = self.confirm_check.isChecked()
        profile.steps[index].description = description
        self.refresh_steps_list()
        self.steps_list.setCurrentRow(index)
        self.flash_status("Step updated.")

    def remove_step(self) -> None:
        if not self.ensure_profile_available():
            return
        index = self.steps_list.currentRow()
        if index < 0:
            return
        profile = self.current_profile()
        del profile.steps[index]
        self.refresh_steps_list()
        self.populate_form(None)
        self.flash_status("Step removed.")

    def move_step(self, delta: int) -> None:
        if not self.ensure_profile_available():
            return
        index = self.steps_list.currentRow()
        if index < 0:
            return
        profile = self.current_profile()
        new_index = index + delta
        if not (0 <= new_index < len(profile.steps)):
            return
        profile.steps[index], profile.steps[new_index] = profile.steps[new_index], profile.steps[index]
        self.refresh_steps_list()
        self.steps_list.setCurrentRow(new_index)

    def clone_step(self) -> None:
        if not self.ensure_profile_available():
            return
        index = self.steps_list.currentRow()
        if index < 0:
            return
        profile = self.current_profile()
        step = profile.steps[index]
        clone = StepModel(
            title=f"{step.title} (copy)",
            command=step.command,
            confirm=step.confirm,
            description=step.description,
            enabled=step.enabled,
        )
        profile.steps.insert(index + 1, clone)
        self.refresh_steps_list()
        self.steps_list.setCurrentRow(index + 1)
        self.flash_status("Step cloned.")

    def _update_step_buttons(self) -> None:
        if not self.profile_order:
            self.disable_btn.setEnabled(False)
            self.enable_btn.setEnabled(False)
            return
        profile = self.current_profile()
        rows = [index.row() for index in self.steps_list.selectedIndexes()]
        if not rows:
            self.disable_btn.setEnabled(False)
            self.enable_btn.setEnabled(False)
            return
        any_enabled = any(0 <= r < len(profile.steps) and profile.steps[r].enabled for r in rows)
        any_disabled = any(0 <= r < len(profile.steps) and not profile.steps[r].enabled for r in rows)
        self.disable_btn.setEnabled(any_enabled)
        self.enable_btn.setEnabled(any_disabled)

    def disable_selected_steps(self) -> None:
        if not self.ensure_profile_available():
            return
        indexes = self.steps_list.selectedIndexes()
        if not indexes:
            return
        profile = self.current_profile()
        for index in indexes:
            row = index.row()
            if 0 <= row < len(profile.steps):
                profile.steps[row].enabled = False
        self.refresh_steps_list()
        self.flash_status("Selected steps disabled.")
        self._update_step_buttons()

    def enable_selected_steps(self) -> None:
        if not self.ensure_profile_available():
            return
        indexes = self.steps_list.selectedIndexes()
        if not indexes:
            return
        profile = self.current_profile()
        for index in indexes:
            row = index.row()
            if 0 <= row < len(profile.steps):
                profile.steps[row].enabled = True
        self.refresh_steps_list()
        self.flash_status("Selected steps enabled.")
        self._update_step_buttons()

    def flash_status(self, message: str, timeout: int = 2000) -> None:
        self.hide_progress()
        self.status_label.setText(message)
        if self._status_timer is None:
            self._status_timer = QTimer(self)
            self._status_timer.setSingleShot(True)
            self._status_timer.timeout.connect(lambda: self.status_label.setText(""))
        self._status_timer.stop()
        self._status_timer.start(timeout)

    def show_progress(self, message: str) -> None:
        if self._status_timer:
            self._status_timer.stop()
        self.status_label.setText(message)
        self.status_progress.setRange(0, 0)
        self.status_progress.show()
        QApplication.processEvents()

    def hide_progress(self) -> None:
        self.status_progress.hide()
        self.status_progress.setRange(0, 1)

    # -- file handling ----------------------------------------------------
    def reset_project(self) -> None:
        if QMessageBox.question(self, "Reset", "Reset to blank project? Unsaved changes will be lost.") != QMessageBox.Yes:
            return
        self.profiles.clear()
        self.profile_order.clear()
        self.refresh_profile_combo()
        self.steps_list.clear()
        self.populate_form(None)
        self.preview.clear()
        self.current_file = None
        self.file_label.setText("No file loaded")
        self._update_step_buttons()
        self.preview.clear()
        self.current_file = None
        self.file_label.setText("No file loaded")

    def load_existing_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open POSTI export",
            str(Path.home()),
            "Python files (*.py);;All files (*.*)",
        )
        if not path:
            return
        data = self._extract_profile_data(Path(path))
        if data is None:
            QMessageBox.warning(
                self,
                "Unsupported file",
                "Could not find embedded profile data markers. Was this file generated by POSTI Forge?",
            )
            return
        self._apply_profile_data(data)
        self.current_file = Path(path)
        self.file_label.setText(f"Loaded: {path}")
        self._update_step_buttons()

    def _extract_profile_data(self, path: Path) -> Dict[str, dict] | None:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return None
        pattern = re.compile(
            r"# === POSTI PROFILE DATA START ===\s*(.*?)# === POSTI PROFILE DATA END ===",
            re.DOTALL,
        )
        match = pattern.search(text)
        if not match:
            return None
        blob = self._extract_json_blob(match.group(1))
        if not blob:
            return None
        try:
            return json.loads(blob)
        except json.JSONDecodeError:
            return None

    def add_profile(self) -> None:
        dialog = ProfileDialog("Add profile", parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        label, description, preflight = dialog.get_data()
        key = self._slugify(label)
        self.profiles[key] = ProfileModel(key=key, label=label, description=description, preflight=preflight, steps=[])
        self.profile_order.append(key)
        self.refresh_profile_combo()
        idx = self.profile_order.index(key)
        self.profile_combo.setCurrentIndex(idx)
        self.switch_profile(idx)

    def edit_profile(self) -> None:
        if not self.ensure_profile_available():
            return
        profile = self.current_profile()
        dialog = ProfileDialog(
            "Edit profile",
            parent=self,
            label=profile.label,
            description=profile.description,
            preflight=profile.preflight,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        label, description, preflight = dialog.get_data()
        profile.label = label
        profile.description = description
        profile.preflight = preflight
        self.refresh_profile_combo()

    def remove_profile(self) -> None:
        if not self.ensure_profile_available():
            return
        profile = self.current_profile()
        if QMessageBox.question(
            self,
            "Remove profile",
            f"Remove profile '{profile.label}'?",
        ) != QMessageBox.Yes:
            return
        key = profile.key
        self.profile_order = [k for k in self.profile_order if k != key]
        self.profiles.pop(key, None)
        self.refresh_profile_combo()
        if self.profile_order:
            self.switch_profile(0)
        else:
            self.steps_list.clear()
            self.populate_form(None)
            self._update_step_buttons()

    def _slugify(self, text_value: str) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", text_value.lower()).strip("-")
        if not base:
            base = "profile"
        candidate = base
        counter = 1
        while candidate in self.profiles:
            candidate = f"{base}-{counter}"
            counter += 1
        return candidate

    def ensure_profile_available(self) -> bool:
        if not self.profile_order:
            QMessageBox.information(self, "No profiles", "Create or load a profile first.")
            return False
        return True

    def _extract_json_blob(self, block: str) -> str | None:
        match = re.search(r'PROFILE_DATA_JSON\s*=\s*r"""(.*?)"""', block, re.DOTALL)
        if match:
            return match.group(1)
        return None

    def _apply_profile_data(self, data: Dict[str, dict]) -> None:
        self.profiles = {key: ProfileModel.from_dict(key, payload) for key, payload in data.items()}
        self.profile_order = list(data.keys())
        self.refresh_profile_combo()
        if self.profile_order:
            self.switch_profile(0)
        else:
            self.steps_list.clear()
            self.populate_form(None)
            self._update_step_buttons()

    # -- preview/save -----------------------------------------------------
    def serialize_profiles(self) -> Dict[str, dict]:
        return {key: self.profiles[key].to_dict() for key in self.profile_order}

    def build_script(self) -> str:
        profile_json = json.dumps(self.serialize_profiles(), indent=4, ensure_ascii=False)
        return POSTI_TEMPLATE.replace("__PROFILE_DATA__", profile_json)

    def generate_script(self) -> None:
        script = self.build_script()
        self.preview.setPlainText(script)

    def save_as_new(self) -> None:
        self.generate_script()
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save posti.py",
            str(Path.home() / "posti.py"),
            "Python files (*.py)",
        )
        if not path:
            return
        Path(path).write_text(self.preview.toPlainText(), encoding="utf-8")
        self.flash_status(f"Script written to {path}")

    def save_over_existing(self) -> None:
        if not self.current_file:
            QMessageBox.information(self, "No file", "Load a posti.py first or use 'Save as new'.")
            return
        self.generate_script()
        self.current_file.write_text(self.preview.toPlainText(), encoding="utf-8")
        self.flash_status(f"Updated {self.current_file}")

    def copy_preview(self) -> None:
        script = self.preview.toPlainText().strip()
        if not script:
            QMessageBox.information(self, "No script", "Generate the script before copying.")
            return
        QApplication.clipboard().setText(script)
        self.flash_status("Script copied to clipboard.")

    def build_binary(self) -> None:
        pyinstaller = shutil.which("pyinstaller") or shutil.which(str(Path.home() / ".local/bin/pyinstaller"))
        if not pyinstaller:
            QMessageBox.warning(
                self,
                "PyInstaller missing",
                "PyInstaller is required to build a standalone binary.\nInstall it with: python -m pip install pyinstaller",
            )
            return
        self.show_progress("Building standalone POSTI binary…")
        try:
            script = self.build_script()
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_path = Path(tmpdir)
                source = tmp_path / "posti_cli.py"
                source.write_text(script, encoding="utf-8")
                log_path = tmp_path / "pyinstaller.log"
                with log_path.open("w", encoding="utf-8") as log_handle:
                    proc = subprocess.Popen(
                        [pyinstaller, "--onefile", "--name", "posti_cli", str(source)],
                        cwd=tmpdir,
                        stdout=log_handle,
                        stderr=subprocess.STDOUT,
                        text=True,
                    )
                    while True:
                        QApplication.processEvents()
                        if proc.poll() is not None:
                            break
                        time.sleep(0.1)
                if proc.returncode != 0:
                    log_snippet = log_path.read_text(encoding="utf-8", errors="ignore")[-2000:]
                    QMessageBox.critical(self, "PyInstaller failed", f"PyInstaller reported an error:\n{log_snippet}")
                    return
                binary = tmp_path / "dist" / ("posti_cli.exe" if sys.platform.startswith("win") else "posti_cli")
                if not binary.exists():
                    QMessageBox.critical(self, "Binary missing", "PyInstaller finished but no binary was produced.")
                    return
                dest, _ = QFileDialog.getSaveFileName(
                    self,
                    "Save standalone binary",
                    str(Path.home() / binary.name),
                    "Executable files (*)",
                )
                if not dest:
                    return
                target = Path(dest)
                shutil.copy2(binary, target)
                current_mode = target.stat().st_mode
                target.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                self.flash_status(f"Standalone POSTI saved to {dest}")
        finally:
            self.hide_progress()


def main() -> None:
    app = QApplication(sys.argv)
    try:
        window = DesignerWindow()
        window.show()
        sys.exit(app.exec())
    except Exception as exc:
        error = "".join(traceback.format_exception(exc))
        QMessageBox.critical(None, "POSTI Designer failed to start", error)
        sys.exit(1)


if __name__ == "__main__":
    main()
