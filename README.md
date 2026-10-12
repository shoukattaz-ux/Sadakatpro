<p align="center"><img src="flowpilot/resources/icons/flowpilot-256.png" width="128" alt="FlowPilot logo"></p>

# FlowPilot

**No-code browser automation for anti-detect browser profiles (Windows desktop app).**

### ⬇ [Download FlowPilot for Windows (FlowPilot-Setup.exe)](https://github.com/shoukattaz-ux/Sadakatpro/releases/latest/download/FlowPilot-Setup.exe)

Latest version, release notes and a portable zip: [Releases page](https://github.com/shoukattaz-ux/Sadakatpro/releases/latest).
FlowPilot updates itself when a new version is published.

FlowPilot connects to your anti-detect browser (AdsPower, GoLogin, Dolphin Anty, Multilogin X,
Octo Browser — or any other browser through the *Custom* connection), opens your profiles, and
repeats the tasks you recorded on as many profiles as you like — with human-like mouse movement,
typing and pauses.

You never write code: you **record** what you do in the browser, FlowPilot turns it into a list
of steps you can edit, and you **run** it on your accounts.

---

## 1. Install

### Option A — the installer (recommended)
1. Download **[FlowPilot-Setup.exe](https://github.com/shoukattaz-ux/Sadakatpro/releases/latest/download/FlowPilot-Setup.exe)**.
2. Double-click it and follow the steps (no administrator rights needed). FlowPilot appears in the Start menu.
   *Windows SmartScreen may say “Windows protected your PC” because the app is new and not code-signed yet:
   click **More info → Run anyway**.*
3. **Updates are automatic:** when a new version is out, a bar at the top of FlowPilot says so — click
   **Update now**. Your flows, accounts and settings are kept.

### Option B — run from the source code
1. Install **Python 3.11 or newer** from [python.org](https://www.python.org/downloads/)
   (tick *“Add Python to PATH”* during setup).
2. Double-click **`run.bat`**. The first start installs everything it needs (takes a few minutes).

To build `FlowPilot.exe` yourself, double-click **`build.bat`**. The app appears in
`dist\FlowPilot\`.

> FlowPilot does **not** download its own browsers: it controls the browser profiles of your
> anti-detect browser. To try it without an anti-detect browser, use the connection type
> **Local Chrome (testing)**, which uses Chrome or Edge installed on your PC.

---

## 2. First start (2 minutes)

A setup assistant opens on the first start:

1. **Connect your anti-detect browser.** Open your anti-detect browser and turn on its *Local API*.
   Choose its name in the list, check the API address (filled in for you) and paste the API key
   if your browser needs one. Press **Test connection** — you should see a green ✔.
2. **Load your profiles.** Press **Sync profiles now**. Every browser profile becomes an
   *account* in FlowPilot.
3. **Record your first flow** on the *Flows* page (see below).

You can open the assistant again at any time with the **?** button at the bottom of the sidebar.

| Browser | Where to find the address / key |
|---|---|
| AdsPower | AdsPower → *API*. Default address `http://local.adspower.net:50325`. The key is only needed if “API security verification” is on. |
| GoLogin | Keep the GoLogin app open. API token: GoLogin → *Settings → API*. |
| Dolphin Anty | Keep Dolphin open. API token: your Dolphin profile → *API*. |
| Multilogin X | Keep the Multilogin agent running. Needs a token and the **Folder ID** (extra settings). |
| Octo Browser | Keep Octo open. API token: Octo → *Settings → Additional*. |
| Custom | For any other browser (or your own): enter where the start/stop/list endpoints are and where the debugging address is in the answer (see *Custom connections* below). |

---

## 3. The pages

### Accounts
Your browser profiles.
- **Sync profiles** loads them from the selected connection (new profiles are added, names updated).
- **Add** / **Import** (CSV or Excel, with a column-matching assistant).
- **Groups** (left): create, rename, colour, drag to reorder. **Drag accounts onto a group** to move them.
- **Edit / variables**: each account can have its own values, e.g. `email`, `password`, `username`,
  `search_keyword`. In steps you use them as `{{account.email}}`. Passwords are stored encrypted and
  shown as `••••` until you press *Show secrets*.
- Search, filter by result or tag, enable/disable many accounts at once.

### Flows — build and record
- **Record**: choose a *Test profile* at the top, press **Record** and use the website normally.
  Each click, typed text, dropdown choice, tick box and Enter becomes a step — live.
  A floating toolbar lets you **pause**, add a **wait**, a **screenshot** or a **Close browser** step.
  Passwords are recorded as `{{account.password}}` automatically, so every account types its own.
- **Edit** steps: select a step to change it on the right. Drag steps to reorder, **Ctrl+D** duplicates,
  **Del** deletes, **Ctrl+Z / Ctrl+Y** undo/redo, right-click for more (e.g. *Turn this text into a variable*).
- **Pick element**: select a step, press **Pick** next to *Element* and click the element in the browser.
  FlowPilot saves a robust selector plus 2–3 backups.
- Steps that still need something (for example an element) are shown **in red** with an explanation.
- **Test flow (F5)**, **From here** and **This step** run on the test profile and highlight each element.
- **AI build**: describe the task in your own words (“log in, then like the first 3 posts”) and FlowPilot
  drafts the steps. With the test browser open, the AI sees the page's buttons and fields and uses real
  selectors. You preview the steps before they are added (Ctrl+Z undoes it). Needs an Anthropic API key in
  *Settings → AI* (you pay Anthropic per use; a draft costs a few cents). Without a key, simple requests are
  drafted offline.
- **List / Flowchart**: switch to the flowchart to see If/Else branches side by side and loops with their
  “repeat” arrow. Click a box to edit that step.
- **Self-healing**: if a website changes and a step's element can't be found, FlowPilot finds it again by its
  text, role and position and continues. A yellow **Review** bar then lets you save the new selector.
- **Ctrl+S** keeps a version; **History** restores one of the last 10 versions. Changes are saved automatically.
- **Import** a flow `.json` or a **Chrome DevTools Recorder** export; **Export** shares a flow.

### Dashboard — run on many accounts
1. Choose the **flow** and **who to run on** (all accounts, ticked groups, or hand-picked accounts).
2. **Browsers at once**: how many profiles run at the same time (use **+** / **−**).
3. **Delay between browser launches**: e.g. 10–20 s → browser 1 starts, a random 10–20 s later
   browser 2 starts, and so on.
4. **Delay between groups** and **Group order** (drag): group 2 starts only after group 1 has finished.
5. **Test run**: only the first account, slowly, highlighting every element — try this before a big batch.
6. Press **Start**. Every profile gets a live card with its status, current step, progress, time and the
   latest screenshot. **Pause all / Resume / Stop all** (clean) / **Force stop** (immediate).
   Each card has its own pause, stop, log and *bring browser to front* buttons. If a flow asks for your
   help (*Pause for manual action*, e.g. a captcha) the card shows a **Continue** button.

### Runs & Logs
Every run with its accounts. Select an account to see each step, how long it took and what went wrong
— in plain language (“Could not find the “Log in” button after 15 seconds”). Screenshots and the page
HTML of every failure are saved; **Export** creates an Excel or CSV file with the results
(including values saved with *Save value* steps).

### Scheduler
Run a flow once, every day, on weekdays or every N hours, with an optional random start delay.
Turn on **Settings → Behavior → Keep running in the tray** so schedules also run when the window is closed.

### Settings
Connections · default timeout/retries/delays · human-like behaviour presets (Fast / Normal / Careful /
custom) · what to save on errors · new tabs and pop-up handling · theme (light / dark / Windows) and
text size · data folder, **backup & restore**, clean-up of old runs · desktop and **Telegram**
notifications · version and license.

---

## 4. Step reference

| Category | Steps |
|---|---|
| Navigation | Open URL · Go back · Go forward · Reload · Open new tab · Switch to tab (number / title / address) · Close tab · Wait for page load |
| Interaction | Click · Double click · Right click · Hover · Type text · Clear field · Press key / combination · Select dropdown option · Check · Uncheck · Upload file · Scroll (to element / by amount / to bottom / to top) · Drag and drop · Focus |
| Waiting | Wait · Wait random time · Wait for element (visible/hidden) · Wait for URL · Wait for text on page · Wait for network idle |
| Data & variables | Extract text/attribute → variable · Set variable · Random item from list · Random number · Read next row from CSV · Save value to results/CSV · Get current URL |
| Logic | If element exists · If text contains · If variable equals … Else … End If · Repeat N times · Loop over list / CSV rows … End loop · Exit loop · Label · Go to label · Stop flow · Try … On error … End try |
| Utilities | Take screenshot · Log message · Pause for manual action · Run JavaScript *(advanced)* · Play sound / notify |
| Ending / Browser | Close browser (also stops the profile in your anti-detect browser) · Mark account as done · Mark account as failed |

**Every step also has** (under *More options*): backup selectors, timeout, retries, what to do if it
still fails (*stop*, *skip*, *go to a label*, *mark failed and close the browser*), random waits
before/after, human-like behaviour on/off, a label and a note.

**Variables:** `{{account.xxx}}` (account values), `{{name}}` (values set by steps),
`{{loop.item}}` / `{{loop.index}}` inside loops, `{{row.column}}` from CSV rows,
`{{error.message}}` inside *On error*, and `{{now}}`, `{{today}}`, `{{random_int}}`, `{{uuid}}`.

---

## 5. Custom connections

For a browser that is not in the list, choose **Custom** and fill in the extra settings:

- **Start URL** e.g. `/api/v1/browser/start?user_id={id}` (`{id}` = profile ID), method GET/POST and an
  optional JSON body such as `{"profileId": "{id}"}`.
- **JSON path to WebSocket/CDP address**: where the start answer contains the debugging address,
  e.g. `data.ws.puppeteer`. If the answer only contains a port number, FlowPilot uses `http://127.0.0.1:<port>`.
- **Stop URL**, **List profiles URL** + the paths to the list, the ID and the name inside each item.
- **Where the API key goes**: header (name + prefix such as `Bearer `), query parameter, or none.
- Optional **success field**, e.g. `code` = `0`.

---

## 6. Troubleshooting

| Problem | What to do |
|---|---|
| “Could not reach … Is the anti-detect browser app running?” | Start the anti-detect browser and enable its Local API; check the address in Settings → Connections. |
| “The browser API refused the request … Check the API key.” | Paste the API key again (Settings → Connections → Edit). |
| AdsPower is “busy (rate limit)” | Increase *Delay between browser launches* (AdsPower allows about 1 API call per second). |
| “Could not find … on the page” | Use **Pick element** again, add backup selectors, or raise the step timeout. A screenshot of the page is in *Runs & Logs*. |
| A popup or cookie banner covers a button | Add a *Click* step for the banner (with *If it still fails → skip*). |
| The PC becomes slow | Lower *Browsers at once*; keep *Settings → Behavior → Wait when the PC is busy* on. |
| FlowPilot was closed during a run | On the next start FlowPilot offers to **continue** the unfinished accounts. |

Logs are in the data folder (`%APPDATA%\FlowPilot\logs`), reachable via *Settings → About → Open logs folder*.

---

## 7. Publishing an update (for the owner)

1. Change the code, bump `__version__` in `flowpilot/__init__.py` (e.g. `0.3.0`) and add a `## [0.3.0]`
   section to `CHANGELOG.md` (it becomes the release notes).
2. Commit and push, then on GitHub open **Actions → Release → Run workflow** (or push a tag:
   `git tag v0.3.0 && git push origin v0.3.0`).
3. GitHub Actions (`.github/workflows/release.yml`) runs all tests on Windows, builds `FlowPilot.exe`,
   self-checks it with a real browser, builds `FlowPilot-Setup.exe` with Inno Setup and publishes a GitHub
   Release with the installer, its SHA-256 checksum and a portable zip.
4. Every installed FlowPilot sees the new release (on start, or *Settings → About → Check for updates*),
   downloads it, verifies the checksum and updates itself.

The download link above always points to the newest release.

## 8. For developers

```
flowpilot/
  app.py                 entry point (logging, qasync loop, --smoke-test)
  config.py              data folder (%APPDATA%/FlowPilot), settings (pydantic)
  security.py            DPAPI (Windows) / Fernet encryption of secrets
  adapters/              BrowserAdapter interface + AdsPower, GoLogin, Dolphin, Multilogin, Octo, Custom, Local
  core/
    steps/               58 step types (pydantic, registered in StepRegistry)
    runner.py            FlowRunner: flat step list + jump tables (If/Else, loops, Go to, Try), error policies
    engine.py            RunEngine: semaphore concurrency, staggered launches, sequential groups, watchdog
    session.py           adapter start → Playwright connect_over_cdp → guaranteed cleanup
    recorder.py/.js      recorder (init script + exposed binding), Pick element, DevTools import
    selectors.py         selector ranking + fallback resolution
    humanize.py          Bezier mouse paths, typing rhythm, scrolling
    variables.py         {{variable}} templating
    flow_document.py     editor model with undo/redo
  storage/               SQLAlchemy models, migrations, repositories
  services/              scheduler, notifications, reporting/retention, backup, profile sync, licensing hook
  ui/                    PySide6 UI: theme (palette, QSS, clay shadow painter), widgets, pages, dialogs
tests/                   unit tests (mocked HTTP) + integration tests on real Chromium
```

```bash
pip install -r requirements.txt pytest pytest-asyncio
python -m pytest            # ~210 tests; browser tests need Chrome/Chromium/Edge (skipped otherwise)
python run_flowpilot.py     # start the app
python run_flowpilot.py --smoke-test --with-browser   # headless self-check (also used by build.bat)
```

- **Adding a step type:** subclass `StepBase` in `core/steps/`, give it a literal `type`, fields with
  `title`/`description` (the editor form is generated from them) and an `async run(ctx)`; decorate with
  `@register`. Add it to `tests/test_steps.py`.
- **Adding an adapter:** implement the `BrowserAdapter` protocol (endpoints in one constants block),
  register it in `adapters/__init__.py`, add a mocked test in `tests/test_adapters.py`.
- **Schema changes:** append a function to `MIGRATIONS` in `storage/db.py`.
- **Licensing:** replace `services/licensing.check_license()`.
- Vendor endpoints are taken from the vendors' local API documentation; if a vendor changes them, edit the
  constants block at the top of that adapter.

### Credits
Inter font © The Inter Project Authors (SIL Open Font License 1.1, see `flowpilot/resources/fonts`).
Icons are drawn in the style of [Lucide](https://lucide.dev) (ISC License).
