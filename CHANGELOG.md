# Changelog

All notable changes to FlowPilot are listed here.

## [0.2.0] — 2026-10-12

### New
- **AI step builder** — click *AI build* on the Flows page and describe the task in your own words
  (“log in, then like the first 3 posts”). FlowPilot drafts the steps with Claude, using the elements of the
  page open in the test browser for accurate selectors; you preview them before they are added.
  Add your Anthropic API key in *Settings → AI*. Without a key, a simple offline parser drafts common steps.
- **Self-healing selectors** — when a website changes and a recorded element can't be found, FlowPilot finds it
  again by its text, role, attributes and position, continues the run, and shows a *Review* bar in the editor
  to save the new selector (the old one is kept as a backup). Can be turned off in *Settings → Behavior*.
- **Flowchart view** — switch the step editor between *List* and *Flowchart*: If/Else branches side by side,
  loops with a “repeat” arrow, Try with its “On error” branch. Click a box to edit the step; Ctrl + wheel zooms.
- **Automatic updates** — FlowPilot checks GitHub for new versions, shows an “update available” bar, downloads
  the installer, verifies its SHA-256 checksum and installs it silently, then starts again.
  *Settings → About → Check for updates* checks on demand.
- **Windows installer** (`FlowPilot-Setup.exe`) — per-user install, no admin rights needed, Start-menu and
  desktop shortcuts, clean upgrades; built and published automatically by GitHub Actions.
- New app logo and icon.

## [0.1.0] — 2026-10-12

First complete version, covering all eight build phases.

### Phase 1 — Foundation
- Project structure, SQLite database (SQLAlchemy 2) with versioned migrations.
- Claymorphism theme: light/dark/system palettes, cached double-shadow painter, custom painted
  `ClayCard`, `ClayButton`, `ClayToggle`, `ClaySpinBox` (+/−), `StatusPill`, `HelpIcon`; bundled Inter font.
- Sidebar navigation with Dashboard, Accounts, Flows, Runs & Logs, Scheduler, Settings.

### Phase 2 — Connections & Accounts
- `BrowserAdapter` interface; AdsPower, GoLogin, Dolphin Anty, Multilogin X, Octo Browser, Custom
  (configurable endpoints and JSON paths) and Local Chrome adapters, with retry/backoff and friendly errors.
- Connections editor with *Test connection*; API keys and account variables encrypted (DPAPI / Fernet).
- Accounts page: profile sync, CSV/Excel import wizard, groups (colour, order, drag & drop), variables,
  search and filters, bulk actions, “skip accounts that already succeeded today”.

### Phase 3 — Runner
- 58 step types as pydantic models in a `StepRegistry`; `{{variable}}` templating.
- Robust selectors with fallbacks; per-step timeout, retries, delays, humanize override and error policy.

### Phase 4 — Flow builder & recorder
- Step editor: clay step cards, drag & drop, multi-select, undo/redo, duplicate, enable/disable,
  collapsible blocks, labels and notes, validation in red, generated property forms.
- Recorder (injected script + exposed binding) with floating toolbar, smart selector ranking,
  automatic `{{account.password}}`, clean-up of recordings, Pick element, DevTools Recorder import.
- Flow templates, import/export, version history (last 10), autosave.

### Phase 5 — Run engine & dashboard
- Semaphore concurrency, staggered launches, sequential groups with group delay.
- Pause/resume/stop (global and per account), force stop, guaranteed cleanup (Playwright + `stop_profile`).
- Live run cards with status, step, progress, elapsed time, screenshot, manual-action Continue button.

### Phase 6 — Humanization & logic
- Bezier mouse movement with overshoot, random click points, typing rhythm with optional typos,
  wheel-like scrolling, idle jitter, presets (Fast/Normal/Careful/custom).
- If/Else, Repeat, Loop over list/CSV, Exit loop, Label/Go to, Stop flow, Try/On error.

### Phase 7 — Runs, scheduler, notifications, backup
- Runs & Logs with step timelines, plain-language errors, screenshots, HTML snapshots, traces, export.
- APScheduler schedules (once/daily/weekdays/every N hours + random offset), tray mode.
- Desktop and Telegram notifications, backup/restore, log retention, watchdog, resource guard,
  resume of interrupted batches after a restart.

### Phase 8 — Polish & packaging
- First-launch setup wizard, tooltips, empty states, confirmations, keyboard shortcuts.
- PyInstaller spec + `build.bat`, `--smoke-test [--with-browser]` self-check, user README.
