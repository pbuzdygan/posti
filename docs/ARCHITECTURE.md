# Posti Forge Architecture

This document explains how the Posti Forge project is structured, how the major components interact, and what their responsibilities are. It is intended as a reference for contributors and advanced users who want to understand how the system works end‑to‑end.

---

## High-level overview

Posti Forge is a full-stack web application packaged as a single Docker image. The container exposes:

- A **React + Vite** single-page application (SPA) that provides the designer UI.
- A **FastAPI** backend that:
  - Serves the built frontend assets.
  - Persists projects (`posti_vX.Y.py`) and compiled binaries.
  - Exposes REST endpoints used by the frontend to save and build.
  - Provides a health check endpoint (`/api/healthz`).

Persistent data is stored outside the container in a bind-mounted `data/` directory:

- `data/projects` – Versioned posti.py scripts saved via “Save project”.
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

- **React 18** with functional components and hooks.
- **Vite** for development and production builds.
- **TypeScript** for type safety in the SPA.
- CSS modules collected in `src/styles.css`.

### Key modules

- `src/App.tsx` – Main React component. Handles:
  - Profile CRUD (state management for profiles and steps).
  - Step composer state, multi-selection logic and bulk actions.
  - Operations panel actions (New project, Load/Save, Build Binary).
  - posti.py preview generation, copy-to-clipboard, syntax highlighting.
  - PWA install banner and theme toggle.
  - UI layout (Profiles, Operations, Step composer, Steps, Preview).
- `src/postiTemplate.ts` – Defines the template for the generated `posti.py`. Provides helper functions:
  - `buildScript` (serialise profiles to Python).
  - `extractProfilesFromScript` (import an existing posti.py back into the designer).
- `src/data/content.ts` – Static data for marketing copy / placeholders.
- `src/main.tsx` – Entry point registering the service worker and mounting `<App />`.
- `public/manifest.webmanifest` – PWA manifest.
- `public/sw.js` – Service worker handling offline shell caching (navigation fallback to `/index.html` only).
- `public/posti_banner_*.png` etc. – Branding assets reused in the UI.

### Frontend → Backend interactions

- `POST /api/save-script`
  - Body: `{ script: string, version: string, filename?: string }`.
  - Saves the `posti.py` on the server (if possible) and streams it back.
- `POST /api/build-binary`
  - Body: `{ script: string, version?: string, filename?: string }`.
  - Runs PyInstaller and streams the resulting binary.
- `GET /api/healthz`
  - Used for monitoring / readiness if needed.

Internally the frontend uses helper functions for versioning (e.g. bumping `1.3 → 1.4`), file-name sanitisation, and step/profile serialization.

---

## Backend details (`builder_service/main.py`)

### Runtime

- **FastAPI** application served by **uvicorn**.
- Single module `builder_service/main.py` (copied as `main.py` in the container).
- Python dependencies listed in `builder_service/requirements.txt` (FastAPI, Uvicorn, PyInstaller).

### Responsibilities

1. **Static file serving**
   - Serves the built frontend (`/app/static`).
   - Any non-`/api/` path returns the SPA entry (`index.html`).

2. **API endpoints**
   - `GET /api/healthz` – returns `{ "status": "ok" }`.
   - `POST /api/save-script`
     - Writes the provided script to `PROJECT_ROOT` (`/app/data/projects`).
     - Sets executable mode (`0755`) if possible.
     - Returns the saved file as a streamed response with headers describing the filename and relative path.
   - `POST /api/build-binary`
     - Creates a temporary build directory under `BINARY_ROOT`.
     - Runs `pyinstaller --onefile` to build `posti_cli`.
     - Copies the resulting binary to `generated_binary`, sets executable bits, and streams it back.

3. **Data directory handling**
   - `ensure_dir` attempts to create `data/`, `projects` and `generated_binary` but ignores permission errors (allowing host-managed directories).
   - At startup, `_check_data_dir` logs warnings if the directories are missing or not writable, without crashing the app.

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

1. **Frontend stage (Node 20 alpine)**
   - Installs frontend dependencies (`npm ci`) and runs `npm run build`.
   - Outputs a static bundle under `/web/dist`.

2. **Runtime stage (Python 3.11 slim)**
   - Installs system dependencies (`build-essential` for PyInstaller).
   - Copies backend requirements and installs them with `pip`.
   - Copies `builder_service/main.py` as `/app/main.py`.
   - Copies built frontend (`/web/dist`) into `/app/static`.
   - Ensures `/app/data` exists (host bind mount provides actual storage).
   - Starts FastAPI via `uvicorn main:app`.

---

## Docker Compose (`docker-compose.yml`)

Minimal setup:

```yaml
services:
  posti:
    build: .
    container_name: posti
    ports:
      - "8012:8000"
    volumes:
      - ./data:/app/data
```

Notes:

- Port 8012 on the host maps to FastAPI port 8000.
- `./data` on the host is bind-mounted to `/app/data`.
- You can set the `user:` key if you need the container to run as a specific UID/GID (e.g. to match NAS ACLs).
- To use a prebuilt image from GHCR, replace `build: .` with `image: ghcr.io/<OWNER>/<REPO>:latest`.

---

## CI/CD

Located at `.github/workflows/docker-image.yml`:

- Builds the Docker image on pushes to `main` and on GitHub releases.
- Publishes images to GitHub Container Registry with tags:
  - `ghcr.io/<OWNER>/<REPO>:latest` (main branch)
  - `ghcr.io/<OWNER>/<REPO>:<tag>` (for tags/releases)
  - `ghcr.io/<OWNER>/<REPO>:edge` (for non-tag pushes if desired via the logic in the workflow)

The workflow uses `docker/login-action`, `docker/setup-buildx-action`, and `docker/build-push-action`.

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

1. **User interacts with SPA**: builds profiles, steps, and previews.
2. **Save project**:
   - Frontend serializes state to a Python script.
   - Sends it to `/api/save-script`.
   - Backend writes to `./data/projects` and streams the file back.
   - Frontend downloads the file and shows a success banner.
3. **Build binary**:
   - Frontend sends script, version, and base filename to `/api/build-binary`.
   - Backend runs PyInstaller in a temp dir and stores the binary under `./data/generated_binary`.
   - Binary is streamed back; frontend downloads it.
4. **Persistence**:
   - If `./data/projects` or `./data/generated_binary` are missing or read-only on the host, the backend logs warnings but still allows browser-only downloads.

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
  - The backend will not change permissions; it only logs warnings if it cannot write.
- **PWA/Offline customization**:
  - Adjust `public/sw.js` if additional assets need caching.
  - Update `manifest.webmanifest` with new icons or metadata.

---

## Troubleshooting

- **Container logs show `[DATA] … is not writable`**:
  - The backend cannot write to the bind-mounted directory. Check host/NAS permissions.
  - Downloads in the browser still work, but nothing is persisted server-side.
- **“Saved posti_vX.Y.py” banner but file not on host**:
  - Same as above: the file was created inside `/app/data/projects` but the host mount is read-only or absent.
  - Ensure `./data/projects` exists and is writable before running the container.
- **White screen after refresh / MIME errors**:
  - Clear the browser’s service worker (DevTools → Application → Service Workers → Unregister) so the latest `sw.js` is used.
- **PyInstaller failures**:
  - Check backend logs for stderr from PyInstaller (e.g. missing dependencies).
  - Ensure the image has access to required system packages (already includes `build-essential`).

---

## Conclusion

Posti Forge combines a modern web UI with a Python-based builder service to streamline the creation of `posti.py` scripts and standalone binaries. The Dockerised architecture keeps deployment simple: bind-mount a `data/` directory, expose one port, and everything else happens inside the container. This document should give you the context needed to explore, extend, or debug the system. Happy hacking!

