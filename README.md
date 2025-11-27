# POSTI

POSTI is a “post-install interactive orchestrator” that lets you script complete bring-up flows for fresh Linux systems. You design per-profile step sequences in a GUI and export a runnable CLI (`posti.py` or a PyInstaller-built binary) that guides operators through the exact commands needed after installing an OS.

## What you can do with POSTI

- Model multiple targets (Fedora Desktop, Ubuntu server, Raspbian, etc.) in one project.
- Add pre-flight checklists plus rich descriptions so operators know the context before running commands.
- Compose step lists where every row has a title, optional confirmation gate, arbitrary shell command and enable/disable flags.
- Load an existing `posti.py`, tweak steps, and overwrite it without rebuilding everything manually.
- Preview or export a ready-to-run CLI script, or bake it into a standalone binary for “double click and run” deployments.
- Run generated scripts interactively, non-interactively (`--profile`, `--yes`), or in dry-run mode to audit commands.

## Requirements & setup

### Designer (posti_designer.py)

- Python 3.9 or newer.
- Install [PySide6](https://pypi.org/project/PySide6/) for the Qt GUI: `python3 -m pip install PySide6`.
- (Optional) Install [PyInstaller](https://pyinstaller.org/) if you plan to build standalone binaries: `python3 -m pip install pyinstaller`.

Launch the designer from a terminal (or double-click it on desktop environments that associate `.py` with Python 3):

```bash
python3 posti_designer.py
```

### Generated runner (posti.py)

- Python 3.8+ on the target machine (`sudo apt install python3`, `sudo dnf install python3`, etc.).
- Optional but recommended: `colorama` for ANSI colors (`python3 -m pip install colorama`).
- Shell utilities referenced in your step commands (e.g., `apt`, `dnf`, `git`, `ansible`) must already be installed.

Run it locally or on a remote server:

```bash
python3 posti.py                # interactive profile selector
python3 posti.py --dry-run      # print commands only
python3 posti.py --profile fedora-cli --yes   # auto-run a known profile
```

On desktop systems you can mark the file as executable (`chmod +x posti.py`) and double-click it, but it still relies on the system Python runtime and the dependencies listed above.

### Standalone binary (posti_cli)

- Requires PyInstaller at build time (see Designer requirements).
- The resulting executable embeds Python, so the target machine does **not** need Python, PySide, or colorama—only the commands you call in your steps.
- Build directly from the designer (“Build standalone binary”) or via CLI:

```bash
python3 posti_designer.py   # generate posti.py
python3 -m pip install pyinstaller
pyinstaller --onefile --name posti_cli posti.py
```

Copy `dist/posti_cli` (or `posti_cli.exe` on Windows) to the server and run it like any other binary: `./posti_cli`.

### .py vs. binary – when to use what?

| Aspect | `posti.py` | `posti_cli` binary |
| --- | --- | --- |
| Runtime dependencies | Needs Python 3.8+, optional `colorama`, plus whatever commands your steps reference | Self-contained – no Python packages required on the target |
| Portability | Same script runs on every platform with Python installed | Binary is OS/architecture-specific; rebuild per target |
| Transparency | Easy to inspect/edit with a text editor | Harder to audit (compiled); mainly for operators who just double-click |
| Size | Small text file | Tens of MB due to embedded interpreter |

Pick the `.py` script when you want full transparency and already have Python on the machine; pick the binary when you need a turnkey executable for ops teams without Python preinstalled.

## Running POSTI on a server

1. Export `posti.py` (or `posti_cli`) from the designer.
2. Copy it to the server via `scp`, `rsync`, etc.
3. For `.py`: make sure Python and any command-line tools used in the steps are installed, then run `python3 posti.py`.
4. For the binary: mark it executable (`chmod +x posti_cli`) and launch `./posti_cli` directly—even on minimal images without Python.

## License

See [LICENSE](LICENSE) for details.
