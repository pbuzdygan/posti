# ARCHITECTURE

## High-level flow

1. `posti_designer.py` is the GUI “Forge”. It lets you define profiles (targets), pre-flight checklists and ordered step lists.
2. The GUI serializes every profile into JSON and injects it into a CLI template (`POSTI_TEMPLATE` constant).
3. The rendered script (`posti.py`) is the operator-facing runner. It contains the full console UI, profile data, and command execution engine.
4. Optional: the designer can wrap that script with PyInstaller to emit a standalone binary (`posti_cli`).

## Core modules

### Data models

- `StepModel` holds the user-facing settings for one automation step (title, command string, confirmation flag, optional description, enabled flag). It exposes `from_dict`/`to_dict` helpers so data coming from JSON and GUI widgets stay in sync.
- `ProfileModel` wraps a set of steps plus profile metadata (key, label, description, pre-flight checklist). The designer keeps an ordered dict (`self.profiles` and `self.profile_order`) that mirrors the final serialized structure.

### Qt dialog helpers

- `ProfileDialog` is a modal editor for profile label/description/pre-flight checklist. It validates required fields before closing.
- Numerous Qt widgets (combo boxes, list views, buttons) are glued together inside `DesignerWindow._build_ui()`. Signals wire up to handler methods (`add_step`, `move_step`, `disable_selected_steps`, etc.).

### DesignerWindow responsibilities

| Area | Details |
| --- | --- |
| Profile orchestration | Adds, edits, deletes, and reorders profiles. `self.profile_combo` remains the source of truth for which profile is being edited. |
| Step editing | Step list sits in a `QListWidget`. Selection drives the form; updates propagate back into `ProfileModel.steps`. Bulk actions (clone, move, enable/disable) operate on the selected rows. |
| Persistence | `load_existing_file()` and `_extract_profile_data()` pull JSON out of an existing `posti.py` by scanning for `# === POSTI PROFILE DATA ...` markers, letting you tweak a previously exported script. |
| Script generation | `build_script()` dumps profiles to JSON (via `serialize_profiles()`) and swaps that straight into `POSTI_TEMPLATE`. Preview pane shows the final script; buttons save it to disk or clipboard. |
| Binary builds | `build_binary()` writes a temporary script, runs PyInstaller (`--onefile --name posti_cli`), streams progress through a status bar, and copies the result to the user-chosen location, adding executable bits on POSIX systems. |

## Generated CLI anatomy

When you export `posti.py`, the GUI injects profile JSON into `POSTI_TEMPLATE`. Key pieces of the runner:

- `ExecutionContext`: wraps `subprocess.run` with dry-run support, unified output formatting, and error propagation.
- Presentation helpers (`hacker_banner`, `panel`, `render_matrix`) render the CRT-style interface.
- `choose_profile` shows every profile, validates numeric input, and handles exit (“00”).
- `display_preflight` prints any checklist items before executing steps.
- `_split_subcommands` inspects the command string. If it contains `&&` (and no conflicting separators such as `|`, `;`, or literal newlines), it splits the command into sub-steps. Each sub-step becomes its own subprocess invocation with individual success/failure reporting. Otherwise the original string runs once.
- `run_steps` is the execution core. It iterates over the profile’s steps, renders panels, enforces confirmation prompts, honors disabled steps, and executes sub-commands sequentially. Operators can choose to continue/abort when a sub-command fails unless `--yes` is used (auto-confirm mode).
- `main()` wires up CLI arguments (`--profile`, `--dry-run`, `--yes`), prints the banner, selects a profile (either via menu or `--profile`), asks if dry-run mode should be enabled, and kicks off execution.

Because the template embeds the JSON payload directly, any edits made through the GUI are reflected instantly in both the preview and the exported script/binary.

## Command splitting and logging

- `&&` acts as a delimiter for “sub-steps.” During execution POSTI labels them `[1/n]`, `[2/n]`, etc., so operators know exactly which part failed.
- If any sub-command errors out, POSTI surfaces the exit code. In interactive mode it asks whether to continue with the remaining steps; in auto-confirm mode it treats the failure as fatal.
- Disabled steps stay in the profile data with their `enabled` flag set to `False`. The runner still shows them in the UI but skips execution, which is helpful for staging incomplete work.

## Export targets

- **Python script (`posti.py`)**: Portable text file containing profiles + runner logic. Requires Python 3.8+ and the shell tools you reference in steps.
- **Standalone binary (`posti_cli`)**: PyInstaller bundle that embeds Python and the script. It’s platform-specific (Linux vs. Windows) but removes the need for Python on the target. Build it via the GUI’s “Build standalone binary” button; the designer handles temporary project setup, log capture, and copying the resulting executable to a user-specified path.

This split between designer, template, and runner keeps the codebase small while still supporting fast iterations: all domain logic lives in Python, and user data (profiles/steps) gets injected on export without having to maintain multiple versions of the CLI.
