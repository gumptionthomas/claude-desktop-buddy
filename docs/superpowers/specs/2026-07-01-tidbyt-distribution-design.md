# Tidbyt Distribution Polish Design

**Goal:** Make Familiar's Tidbyt companion a self-contained, hand-off-able local
tool: no `pixlet` dependency, runnable without an M5, and set up with one
`familiar init` command — all shipping under the project's real name, `familiar`.

**Target user:** anyone running Claude Code who owns a Tidbyt. Bar: `uv tool
install familiar` → `familiar init` → done. No `pixlet`, no M5 required.

**Non-goals:** a Tidbyt app-store/community app or any hosted service. The pet
reacts to *live, local* Claude Code activity (hooks on your machine), which a
server-side community app cannot see — so a local component is inherent, and we
keep it entirely local. (A hosted front-end could layer on later; out of scope
here.)

## Constraints (verified)

- **Direct HTTP push works** (verified against the live device): `POST
  https://api.tidbyt.com/v0/devices/{deviceID}/push`, header `Authorization:
  Bearer {apiKey}`, JSON body `{"image": "<base64 webp>", "installationID":
  "<id>", "background": false}`. Removal: `DELETE
  /v0/devices/{deviceID}/installations/{installationID}`. So `pixlet` is fully
  droppable.
- **15s animation length is a Tidbyt *device* limit**, not a `pixlet` limit
  (verified: a 24s test animation snapped back at the 15s mark on-device).
  Dropping `pixlet` buys no timing headroom; the existing "compress per-frame
  delay to fit 15s" logic in `render_ascii_pet.py` stays as-is and is optimal.

## Architecture

Everything lives in `linux-bridge/`, shipped as the `familiar` package — a
fork-only tree upstream doesn't have, so this has zero upstream-sync impact. The
C++ firmware keeps its "buddy" vocabulary untouched (clean firmware merges).

One command with subcommands:

```
familiar run            → the daemon (M5 and/or Tidbyt, from config)
familiar init           → write config + wire hooks (+ migrate old claude-buddy)
familiar hook <event>   → what Claude Code's hooks invoke
```

Three run configurations, all from `familiar run`: **M5 only** (BLE, as today),
**Tidbyt only** (no BLE), or **both**.

## Components

### 1. Direct HTTP push (`tidbyt.py`)

Replace the `pixlet push` subprocess with a stdlib `urllib` HTTPS POST to the
push endpoint above. `push_image(webp_path, ...)` reads the bytes, base64-encodes,
and POSTs with the Bearer header + `installationID`. Delete all pixlet-path
resolution and subprocess plumbing (and the systemd-PATH workaround). Keep the
best-effort contract: any error is caught and logged; the M5 path is never
disturbed. No new dependency for pushing.

### 2. Python haiku renderer (`haiku_render.py`, new)

Reproduce today's scrolling-haiku WebP (currently a Pixlet `.star`) in Pillow:

- **Font:** embed the tom-thumb bitmap glyphs (public-domain, ~95 printable
  ASCII chars) as a compact table — pixel-perfect match to the current render,
  no `.ttf` file to load. Blit glyphs directly.
- **Badge:** reuse the same 7×7 Claude sunburst bitmap the `.star` uses today,
  leading the first line.
- **Layout/animation:** word-wrap the three lines to width, render white text
  centered into a tall strip with the badge inline on line 1, then slice a
  vertical-scroll animation (hold ~2.5s at top → scroll up until off-screen)
  into a 64×32 animated WebP, total duration **≤15s** (device cap).
- Returns WebP bytes; `tidbyt.py` pushes them via the same HTTP push.

Remove `tidbyt_app.star` and `tidbyt_app.webp` from the package.

**Pillow** becomes a core runtime dependency (approved) — the one thing the
renderer needs; keeps setup to a single `uv tool install` with no extras flag.

### 3. Tidbyt-only run mode (`daemon.py`)

The run-path picker chooses by config:

- M5 `address` present → BLE path (`run_with_ble`), unchanged.
- No `address` but Tidbyt configured → **BLE-free path**: construct `Bridge`
  with a `NullTransport` (no-op `send`) and run `serve()` + `_push_loop` +
  `_haiku_loop`. The existing Tidbyt sync + haiku orchestration drive the
  display; no BLE.
- Neither → clear error ("configure an M5 address and/or Tidbyt keys").

### 4. `familiar init` (`init.py`, new)

Interactive by default; flags (`--yes`, `--tidbyt-device`, `--tidbyt-key`,
`--m5-address`, `--anthropic-key`, `--service`) for scripted/non-interactive
setup. Steps:

1. **Config** — ensure `~/.config/familiar/config.toml`. Prompt for the two
   Tidbyt keys (+ optional M5 address, Anthropic key, `owner`, `tidbyt_pet`);
   blank = skip. Don't overwrite existing values without confirming. Keys
   written quoted.
2. **Hooks** — merge the six hooks into `~/.claude/settings.json` as `familiar
   hook <event>`. Idempotent (detect + skip/replace Familiar's own hooks, no
   duplicates), backs up the file first (timestamped), preserves unrelated
   hooks, aborts cleanly on malformed JSON.
3. **Service** (opt-in: prompt / `--service`) — write + `enable --now` the
   `familiar.service` user unit (`ExecStart=%h/.local/bin/familiar run`).
4. **Migration** — detect an existing `claude-buddy` install and carry it over
   non-destructively: copy `~/.config/claude-buddy/config.toml` → the new path
   (only if the new one is absent), rewrite `claude-buddy-hook` hook commands to
   `familiar hook`, stop/disable `claude-buddy.service` and install
   `familiar.service`, and point the user at `uv tool uninstall claude-buddy`.
   Back up before edits; never delete the old config.

Whole command is safe to re-run.

### 5. Rename `claude_buddy` → `familiar`

- `linux-bridge/src/claude_buddy/` → `linux-bridge/src/familiar/`; update all
  imports and test imports.
- `pyproject.toml`: package name `claude-buddy` → `familiar`; replace the two
  console scripts with a single `familiar = "familiar.cli:main"`; hatchling
  `packages = ["src/familiar"]`.
- New `cli.py` dispatches `run` / `init` / `hook <event>`; bare `familiar`
  prints help.
- `config.py`: config dir `~/.config/claude-buddy/` → `~/.config/familiar/`.
- Package data (`tidbyt_buddy/` WebPs) moves with the directory.

## Data flow (unchanged in spirit)

Claude Code hooks → `familiar hook <event>` → Unix socket → `Bridge` updates
session state → `_push_loop` derives persona → pushes the pet WebP over HTTP;
turn-end → haiku composed (if Anthropic key) → `haiku_render` → HTTP push, then
reverts to the pet. Without an Anthropic key, the Tidbyt shows only the pet.

## Error handling

Best-effort throughout the Tidbyt path (push HTTP errors, render failures, no
network) — caught, logged, never disturbing the M5 path. `init` backs up before
any edit, aborts cleanly on unreadable/malformed `settings.json`, and is
idempotent. Migration copies-then-verifies and never deletes the old config.

## Testing

- **push** — mock `urlopen`; assert URL, Bearer header, base64 body,
  `installationID`; best-effort no-op on HTTP error.
- **haiku_render** — output is a valid 64×32 animated WebP, >1 frame, total
  ≤15s; the glyph table renders known ASCII (incl. wrap + badge).
- **Tidbyt-only mode** — run-path picker selects BLE-free when no `address`;
  `Bridge` drives Tidbyt through `NullTransport`, no BLE.
- **init** — hook-merge idempotency (run twice → identical file); backup
  created; unrelated hooks preserved; malformed JSON aborts; migration from a
  fake `claude-buddy` config dir yields the `familiar` config + rewired hooks.
- **rename** — existing suite passes under `familiar` imports.

## Docs

Update `linux-bridge/README.md` (the `familiar init` flow, `familiar run`,
Tidbyt-only setup, no pixlet) and the main `README.md` (command names, the
"install → init → done" path). Note the `claude-buddy → familiar` migration for
existing users.
