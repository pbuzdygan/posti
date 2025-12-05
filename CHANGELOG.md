# POSTI Forge – Changelog

## 2.0.1 – 2025-12-05

- Fixed the CLI banner in generated `posti.py` scripts by forcing a raw string literal to avoid Python escape warnings.
- Changed the default dry-run prompt to “No”, so hitting Enter now runs the selected profile immediately unless `--dry-run` is supplied.
- Added backend normalization to apply both fixes automatically when saving projects or building binaries, covering older frontend bundles without requiring a rebuild.

## 2.0 – 2025-12-05

- Rebuilt POSTI as a containerised web app: React/Vite frontend served by a FastAPI backend in a single Docker service.
- Introduced a new workspace layout: Profiles, Operations, banner, Steps and Step composer arranged in a full-width, responsive grid.
- Redesigned Profiles UX with custom dropdown, inline Add/Edit/Remove actions, confirmation flow for deletes and cleaned-up profile details.
- Simplified step management: compact list without drag-and-drop, multi-selection with Ctrl/Shift, bulk enable/disable and a Confirm toggle per step.
- Modernised theming and visuals: light/dark modes, styled inputs and dropdowns, refined scrollbars and a centred branding banner.
- Added a rich posti.py preview: Python syntax highlighting, inline comments in the generated script, and reliable Copy to clipboard support.
- Implemented project persistence: versioned `posti_vX.Y.py` files, server-side save endpoint (`/api/save-script`) and executable scripts stored under `data/projects`.
- Implemented binary builds in the backend: PyInstaller integration via `/api/build-binary`, versioned artifacts, executable bits and storage under `data/generated_binary`.
- Normalised data directory handling: `data/projects` and `data/generated_binary` bind-mounted from the host, with startup checks and non-fatal warnings instead of forced chmod.
- Added PWA support: manifest, icons, service worker with safe offline shell caching, plus an in-app install banner for Posti Forge.

## 1.1 – 2025-12-03

- Redesigned main UI layout (split view, clearer sections for profiles, steps and preview).
- Added drag-and-drop reordering of steps and keyboard shortcuts (Delete, Ctrl+↑/↓).
- Introduced dark/light themes with a toggle and consistent styling for menus and controls.
- Improved step list readability and command editor hints (including `&&` sub-step support).
- Unified saving flow into a single “Save changes to posti.py” action (new or existing file).
- Enhanced status messages with colored badges and clearer feedback for profile/step actions.
