# POSTI Forge – Changelog

## 2.1.0 – 2026-09-11

### Bug Fixes

- Saving a project no longer creates a growing collection of version-numbered files.
- Files saved in bind-mounted folders now belong to the UID and GID selected by
  the administrator instead of unexpectedly being owned by root.
- Requests can no longer use crafted paths to read files outside the public web
  assets directory.
- Profile content containing triple quotes no longer damages the generated Python
  script, and projects created by earlier releases remain importable.
- Project and binary files are replaced atomically, preventing partially written
  artifacts when a save or build is interrupted.

### Improvements

- Posti now opens behind a full-screen numeric PIN login, keeping both the designer
  and its project/build API unavailable until the user signs in.
- Each project now has one clear current file, while the previous ten saves are
  retained automatically for recovery without cluttering the project list.
- Posti now runs without root privileges and starts with a more restricted
  container environment, reducing the impact of an application failure or attack.
- The service listens on localhost by default, making accidental exposure to the
  local network or Internet less likely.
- Large, overlapping, or stalled build requests are rejected or stopped cleanly,
  keeping the interface responsive and protecting disk, CPU, memory, and process capacity.
- Invalid filenames and version values now produce predictable validation errors
  instead of unexpected filesystem failures.
- Startup reports unusable persistence permissions immediately, so an ownership or
  NAS ACL problem is visible before a user tries to save work.
- The application stack and build tools have been refreshed to supported releases,
  including a supported Node.js LTS line and a patched PyInstaller release.
- Automated tests, dependency checks, frontend builds, and update monitoring now
  run continuously to catch regressions and vulnerable packages earlier.
- Development releases now publish under separate image names, so testing a new
  version can no longer replace the production image used by regular deployments.
- Saved projects are now opened directly from Posti's project library instead of
  from unrelated browser downloads, keeping one clear source of truth.
- Profile editing now becomes available only after a project is safely created or
  loaded, making the intended order of work clear and preventing unnamed projects.
- Deployment, migration, security, and troubleshooting instructions now reflect
  the safer defaults introduced in this release.

### New Features

- **Undo last save** restores earlier project states, one save at a time, for up
  to the ten most recent changes.
- A project-name field above **Active profile** now creates the initial project
  file immediately and carries its name into readable scripts and binaries.
- **Load project** now presents the projects available on the server, including
  files saved by earlier Posti releases.
- Administrators can choose the owner of generated files with `POSTI_UID` and
  `POSTI_GID`, including deployments backed by NAS shares and bind mounts.
- Access to Posti now requires the numeric application PIN configured by the administrator.
- Administrators can configure script-size, build-time, queue-wait, temporary-file
  retention, and permitted cross-origin access limits for their environment.

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
