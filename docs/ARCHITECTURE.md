# Posti Forge Architecture

This document explains how the Posti Forge project is structured, how the major components interact, and what their responsibilities are. It is intended as a reference for contributors and advanced users who want to understand how the system works end‑to‑end.

---

## High-level overview

Posti Forge is a full-stack web application packaged as a single Docker image. The container exposes:

- A **React + Vite** single-page application (SPA) that provides the designer UI.
- A **FastAPI** backend that:
  - Serves the built frontend assets.
  - Persists named projects (`project-name_posti.py`), their limited history, and compiled binaries.
  - Exposes REST endpoints used by the frontend to list, load, save and build.
  - Provides a health check endpoint (`/api/healthz`).

Persistent data is stored outside the container in a bind-mounted `data/` directory:

- `data/projects` – Server-side project library; `.history` retains up to ten earlier saves per project.
- `data/generated_binary` – PyInstaller binaries produced via “Build Binary”.

---

## Repository layout

```
.
├── builder_service/
│   ├── main.py           # FastAPI app (copied to /app/main.py in the image)
│   └── requirements.txt  # Backend Python dependencies
├── frontend/
│   ├── src/              # React components, styles and logic
│   ├── public/           # Static assets (icons, manifest, service worker, etc.)
│   └── package.json      # Frontend npm dependencies and scripts
├── data/                 # Host-side persistence (bind-mount target)
├── Dockerfile            # Multi-stage build (frontend+backend) into one image
├── docker-compose.yml    # Local dev / deployment example
├── docs/
│   └── ARCHITECTURE.md   # (this file)
├── README.md             # User-facing overview and run instructions
└── .github/workflows/    # CI workflows (build/publish image)
```

---

## Frontend details (`frontend/`)

### Stack

- **React 19** with functional components and hooks.
- **Vite** for development and production builds.
- **TypeScript** for type safety in the SPA.
- CSS modules collected in `src/styles.css`.

### Key modules

- `src/App.tsx` – Main React component. Handles:
  - Profile CRUD (state management for profiles and steps).
  - Step composer state, multi-selection logic and bulk actions.
  - Named project creation, locked/unlocked profile state, and operations actions
    (New project, Load/Save, Build Binary).
  - posti.py preview generation, copy-to-clipboard, syntax highlighting.
  - PWA install banner and theme toggle.
  - UI layout (Profiles, Operations, Step composer, Steps, Preview).
- `src/postiTemplate.ts` – Defines the template for the generated `posti.py`. Provides helper functions:
  - `buildScript` (serialise profiles to Python).
  - `extractProfilesFromScript` (import an existing posti.py back into the designer).
- `src/projectNames.ts` – Normalises project names and reads them from current or legacy filenames.
- `src/data/content.ts` – Static data for marketing copy / placeholders.
- `src/main.tsx` – Entry point registering the service worker and mounting `<App />`.
- `public/manifest.webmanifest` – PWA manifest.
- `public/sw.js` – Service worker handling offline shell caching (navigation fallback to `/index.html` only).
- `public/posti_banner_*.png` etc. – Branding assets reused in the UI.

### Frontend → Backend interactions

- `POST /api/save-script`
  - Body: `{ script: string, version: string, filename?: string }`.
  - Saves the named `posti.py` in the server-side project library.
- `GET /api/projects`
  - Returns the project files available in the server-side library.
- `GET /api/projects/{filename}`
  - Returns the selected project after validating that its path remains inside the library.
- `POST /api/projects/{filename}/undo`
  - Restores and consumes the newest retained save for the selected project.
- `POST /api/build-binary`
  - Body: `{ script: string, version?: string, filename?: string }`.
  - Runs PyInstaller and streams the resulting binary.
- `GET /api/healthz`
  - Used for monitoring / readiness if needed.

Internally the frontend uses helpers for file-name sanitisation and step/profile serialization.

---

## Backend details (`builder_service/main.py`)

### Runtime

- **FastAPI** application served by **uvicorn**.
- A successful `POSTI_APP_PIN` login creates a random, expiring HTTP-only session cookie.
- Protected project and build endpoints reject requests without that session.
- Single module `builder_service/main.py` (copied as `main.py` in the container).
- Python dependencies listed in `builder_service/requirements.txt` (FastAPI, Uvicorn, PyInstaller).

### Responsibilities

1. **Static file serving**
   - Serves the built frontend (`/app/static`).
   - Any non-`/api/` path returns the SPA entry (`index.html`).

2. **API endpoints**
   - `GET /api/healthz` – returns `{ "status": "ok" }`.
   - `POST /api/save-script`
     - Requires a PIN-authenticated session, validates the input and writes atomically to
       `PROJECT_ROOT` (`/app/data/projects`).
     - Sets executable mode (`0755`).
     - Returns the saved file as a streamed response with headers describing the filename and relative path.
   - `GET /api/projects` and `GET /api/projects/{filename}`
     - Require a PIN-authenticated session and provide the project library list and contents.
   - `POST /api/build-binary`
     - Requires a PIN-authenticated session.
     - Creates a temporary build directory under `BINARY_ROOT`.
     - Runs one PyInstaller job at a time in a worker thread with a timeout.
     - Atomically persists the binary, streams an isolated copy, and removes stale temporary builds.

3. **Data directory handling**
   - `ensure_dir` creates `data/`, `projects` and `generated_binary` when permitted.
   - Startup fails if persistence is not writable by the configured UID/GID.

4. **Logging**
   - Uses `logging.getLogger("posti.builder")`.
   - Logs key events such as saves, binary builds and directory status warnings.

### Persistence paths

- `DATA_ROOT = Path("/app/data")` (overrides via `POSTI_DATA_ROOT` env var if needed).
- `PROJECT_ROOT = DATA_ROOT / "projects"`.
- `BINARY_ROOT = DATA_ROOT / "generated_binary"`.

Both directories are expected to be bind-mounted from the host using Docker (see `docker-compose.yml`).

---

## Docker build (`Dockerfile`)

Multi-stage build:

1. **Frontend stage (Node 24 alpine)**
   - Installs frontend dependencies (`npm ci`) and runs `npm run build`.
   - Outputs a static bundle under `/web/dist`.

2. **Runtime stage (Python 3.11 slim)**
   - Installs only `binutils`, required by PyInstaller on Linux.
   - Copies backend requirements and installs them with `pip`.
   - Copies `builder_service/main.py` as `/app/main.py`.
   - Copies built frontend (`/web/dist`) into `/app/static`.
   - Ensures `/app/data` exists (host bind mount provides actual storage).
   - Runs as non-root UID/GID `1000:1000` by default.
   - Starts Uvicorn with connection and keep-alive limits.

---

## Docker Compose (`docker-compose.yml`)

Minimal setup:

```yaml
services:
  posti:
    build: .
    container_name: posti
    user: "${POSTI_UID:-1000}:${POSTI_GID:-1000}"
    environment:
      HOME: /tmp
      POSTI_APP_PIN: "${POSTI_APP_PIN:?Set POSTI_APP_PIN in .env}"
    ports:
      - "127.0.0.1:8012:8000"
    volumes:
      - ./data:/app/data
```

Notes:

- Port 8012 on localhost maps to FastAPI port 8000.
- `./data` on the host is bind-mounted to `/app/data`.
- `user:` determines ownership of files on the bind mount; the host directories
  must already be writable by the selected numeric UID/GID.
- Compose uses a read-only root filesystem, drops all Linux capabilities, enables
  `no-new-privileges`, and applies process, CPU and memory limits.
- To use a prebuilt image from GHCR, replace `build: .` with `image: ghcr.io/<OWNER>/<REPO>:latest`.

---

## CI/CD

- `.github/workflows/ci.yml` runs backend/frontend tests, audits, type checking,
  and the frontend build for pushes to `main` and `dev` and for pull requests.
  It does not build or publish a container image.
- `.github/workflows/docker-image.yml` builds and publishes a container only
  when a GitHub Release is published:
  - releases targeting `main` use `x.x.x` and publish `latest` plus `x.x.x`;
  - releases targeting `dev` use `devx.x.x` and publish `dev_latest` plus `devx.x.x`.

The publishing workflow validates the release target, tag format, and branch
ancestry before logging in to GHCR and running the Docker build and pushes.

---

## PWA & service worker

- `public/manifest.webmanifest` describes the app, icons and colors.
- The service worker (`public/sw.js`) pre-caches the SPA shell (`/index.html`) and intercepts navigation requests:
  - `fetch` handler only applies to navigations/documents (avoids returning HTML for JS/CSS).
  - Offline fallback serves `/index.html` from cache.
  - The SW is registered in `src/main.tsx`.
- The frontend shows an install banner when `beforeinstallprompt` fires and falls back to a manual hint when the prompt isn’t available.

---

## Data flow summary

1. **Create or load project**:
   - Confirming the inline project name persists the initial canonical project script.
   - Profile editing remains locked until that save succeeds or a library file is loaded.
2. **User interacts with SPA**: builds profiles, steps, and previews.
3. **Save project**:
   - Frontend serializes state to a Python script.
   - Sends it to `/api/save-script`.
   - Backend archives the previous state and atomically replaces the named file in `./data/projects`.
   - Frontend treats the server-side library as the source of truth and shows a success banner.
4. **Load project**:
   - Frontend lists `/api/projects` and requests the selected file from `/api/projects/{filename}`.
   - Existing scripts from older releases remain importable.
5. **Build binary**:
   - Frontend sends the script and project name to `/api/build-binary`.
   - Backend runs PyInstaller in a temp dir and stores the binary under `./data/generated_binary`.
   - Binary is streamed back; frontend downloads it.
6. **Persistence**:
   - If `./data/projects` or `./data/generated_binary` are not writable, startup
     fails so an ownership or ACL error cannot be mistaken for successful persistence.

---

## Extending the project

- **Adding new backend endpoints**:
  - Extend `builder_service/main.py`.
  - Update `builder_service/requirements.txt` if new dependencies are needed.
  - Rebuild the Docker image.
- **Modifying the frontend**:
  - Work inside `frontend/src/`.
  - Use `npm run dev` for rapid iteration; set `VITE_BUILDER_URL` to point at your backend.
  - Run `npm run build` to produce the bundle copied into the container.
- **Persistent storage tweaks**:
  - If running on NAS/Synology, create `data/projects` and `data/generated_binary` with the desired ACLs before starting the container.
  - The backend does not change host ownership and fails startup if it cannot write.
- **PWA/Offline customization**:
  - Adjust `public/sw.js` if additional assets need caching.
  - Update `manifest.webmanifest` with new icons or metadata.

---

## Troubleshooting

- **Container logs show `[DATA] … is not writable`**:
  - The backend cannot write to the bind-mounted directory and exits intentionally.
  - Match host/NAS ownership or ACLs to `POSTI_UID` and `POSTI_GID`.
- **A saved project is missing from the project library**:
  - Ensure both data subdirectories exist and are writable before starting the container.
- **HTTP 401 from project/build APIs**:
  - Sign in on the PIN screen again; sessions expire and are cleared when the backend restarts.
- **White screen after refresh / MIME errors**:
  - Clear the browser’s service worker (DevTools → Application → Service Workers → Unregister) so the latest `sw.js` is used.
- **PyInstaller failures**:
  - Check backend logs for stderr from PyInstaller (e.g. missing dependencies).
  - Increase the configured timeout/resources if needed; the image includes `binutils`.

---

## Conclusion

Posti Forge combines a modern web UI with a Python-based builder service to streamline the creation of `posti.py` scripts and standalone binaries. The Dockerised architecture keeps deployment simple: bind-mount a `data/` directory, expose one port, and everything else happens inside the container. This document should give you the context needed to explore, extend, or debug the system. Happy hacking!
