# Tidbyt Distribution Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Familiar's Tidbyt companion a self-contained, pixlet-free, `familiar init`-onboarded local tool that runs without an M5, shipping as the `familiar` package.

**Architecture:** Everything lives in `linux-bridge/` (shipped as the `familiar` package). Drop the `pixlet` Go binary: push WebPs to the Tidbyt over its HTTP API (stdlib `urllib`), and render the scrolling haiku in pure Python (Pillow + a vendored tom-thumb BDF). Add a BLE-free run mode and a `familiar init` setup command that also migrates an existing `claude-buddy` install. Rename the package `claude_buddy → familiar` with a single `familiar` command (subcommands `run`/`init`/`hook`).

**Tech Stack:** Python 3.11+, asyncio, Pillow (new core dep), bleak (existing), hatchling, pytest, systemd user services, Tidbyt HTTP API.

## Global Constraints

- Package/import name is `familiar` (not `claude_buddy`); single console script `familiar = "familiar.cli:main"` with subcommands `run`, `init`, `hook`.
- Config dir is `~/.config/familiar/` (was `~/.config/claude-buddy/`).
- Tidbyt push endpoint: `POST https://api.tidbyt.com/v0/devices/{deviceID}/push`, header `Authorization: Bearer {apiKey}`, JSON body `{"image": "<base64 webp>", "installationID": "<id>", "background": false}`. Installation id is `claudebuddy` (must stay alphanumeric — the API 400s on hyphens; do not change it or existing installs orphan).
- Rendered WebPs are 64×32 and total ≤ 15s (hard Tidbyt device limit).
- All Tidbyt-side operations are best-effort: any error is caught and logged, never raised into the M5 path or a Claude Code hook.
- No `pixlet` anywhere; no new dependency except Pillow.
- TDD: failing test first. Run `uv run pytest` from `linux-bridge/`. Commit after each green task.
- Do not rename anything in the C++ firmware (`src/`, `characters/`) — it stays byte-aligned with upstream.

---

## File Structure

Under `linux-bridge/`:

- `src/familiar/` — the renamed package (was `src/claude_buddy/`).
- `src/familiar/cli.py` **(new)** — arg dispatch: `run` → `daemon.main`, `init` → `init.main`, `hook` → `hook.main`.
- `src/familiar/init.py` **(new)** — the `familiar init` command (config + hooks + service + migration).
- `src/familiar/tidbyt.py` — HTTP push (no pixlet); `push(lines)` now renders via `haiku_render`.
- `src/familiar/haiku_render.py` **(new)** — render 3 lines + Claude badge to a scrolling 64×32 WebP.
- `src/familiar/font.py` **(new)** — parse the vendored tom-thumb BDF into a glyph table + a `draw_text` blitter.
- `src/familiar/fonts/tom-thumb.bdf` **(new, vendored)** — public-domain 4×6 bitmap font.
- `src/familiar/transport.py` — add `NullTransport`.
- `src/familiar/daemon.py` — `_make_tidbyt` drops pixlet/app_path; add BLE-free run path.
- `src/familiar/config.py` — config dir → `~/.config/familiar/`.
- Delete `src/familiar/tidbyt_app.star`, `src/familiar/tidbyt_app.webp`.
- `pyproject.toml` — name `familiar`, single script, Pillow dep, hatchling `packages = ["src/familiar"]`.
- `tests/` — imports `familiar.*`; new `test_haiku_render.py`, `test_font.py`, `test_init.py`.

Deployment (`uv tool install`, running `familiar init` to migrate this machine) is done with the user after the branch is built — not part of these tasks.

---

### Task 1: Rename `claude_buddy` → `familiar` + CLI scaffold

**Files:**
- Move: `linux-bridge/src/claude_buddy/` → `linux-bridge/src/familiar/`
- Create: `linux-bridge/src/familiar/cli.py`
- Modify: `linux-bridge/pyproject.toml`, `linux-bridge/src/familiar/config.py`, all `linux-bridge/tests/*.py`
- Test: `linux-bridge/tests/test_cli.py` (new)

**Interfaces:**
- Produces: `familiar.cli.main(argv=None) -> int` dispatching `run`/`init`/`hook`; `familiar.daemon.main`, `familiar.hook.main` unchanged in signature; config dir `~/.config/familiar/`.

- [ ] **Step 1: Move the package and rewrite imports**

```bash
cd linux-bridge
git mv src/claude_buddy src/familiar
# rewrite intra-package + test imports
grep -rl 'claude_buddy' src tests | xargs sed -i 's/claude_buddy/familiar/g'
```

- [ ] **Step 2: Point the config dir at `~/.config/familiar/`**

In `src/familiar/config.py`, `_default_config_path`:

```python
def _default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "familiar" / "config.toml"
```

Also update `_default_socket` if it references the old name (it uses `claude-buddy.sock` — leave the socket filename as `claude-buddy.sock` is fine, but rename to `familiar.sock` for consistency):

```python
def _default_socket() -> str:
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return str(Path(base) / "familiar.sock")
```

And in `hook.py`, the env override `CLAUDE_BUDDY_SOCKET` → `FAMILIAR_SOCKET`.

- [ ] **Step 3: Write the failing CLI test**

Create `tests/test_cli.py`:

```python
from familiar import cli


def test_cli_dispatches_run(monkeypatch):
    called = {}
    monkeypatch.setattr(cli.daemon, "main", lambda argv: called.setdefault("run", argv) or 0)
    assert cli.main(["run", "--stdout"]) == 0
    assert called["run"] == ["--stdout"]


def test_cli_dispatches_hook(monkeypatch):
    called = {}
    monkeypatch.setattr(cli.hook, "main", lambda argv: called.setdefault("hook", argv) or 0)
    assert cli.main(["hook", "stop"]) == 0
    assert called["hook"] == ["familiar-hook", "stop"]   # hook.main reads argv[1:] as event


def test_cli_no_args_prints_help(capsys):
    assert cli.main([]) == 0
    assert "familiar" in capsys.readouterr().out.lower()
```

- [ ] **Step 4: Run it, expect failure**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL (`No module named 'familiar.cli'`).

- [ ] **Step 5: Write `cli.py`**

`src/familiar/cli.py`:

```python
"""Single entry point: `familiar run | init | hook <event>`."""
import sys

from . import daemon, hook, init

_HELP = """familiar — a desk buddy for Claude Code, on an M5 or a Tidbyt

usage:
  familiar run [--stdout]      run the daemon (M5 and/or Tidbyt, from config)
  familiar init [flags]        set up config + Claude Code hooks
  familiar hook <event>        (invoked by Claude Code's hooks)
"""


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        sys.stdout.write(_HELP)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "run":
        return daemon.main(rest)
    if cmd == "init":
        return init.main(rest)
    if cmd == "hook":
        # hook.main reads argv[1] as the event name, so prepend a dummy argv[0].
        return hook.main(["familiar-hook", *rest])
    sys.stderr.write(f"unknown command: {cmd}\n{_HELP}")
    return 2
```

Add a minimal `init.main` stub so imports resolve (fleshed out in Task 7). Create `src/familiar/init.py`:

```python
def main(argv=None) -> int:
    raise NotImplementedError("familiar init lands in Task 7")
```

Update `daemon.main`'s `ArgumentParser(prog="claude-buddy")` → `prog="familiar run"`, and its stderr banners `[claude-buddy]` → `[familiar]`.

- [ ] **Step 6: Update `pyproject.toml`**

```toml
[project]
name = "familiar"
version = "0.1.0"
description = "Familiar: a desk buddy for Claude Code, on an M5 or a Tidbyt"
requires-python = ">=3.11"
dependencies = ["bleak>=0.22", "pillow>=10"]

[project.scripts]
familiar = "familiar.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/familiar"]

[dependency-groups]
dev = ["pytest>=8"]
```

- [ ] **Step 7: Run the whole suite**

Run: `uv run pytest -v`
Expected: PASS (all existing tests under `familiar` imports + the 3 new CLI tests). Fix any missed `claude_buddy` references the grep didn't catch (e.g. in comments/docstrings that assert on names — check `test_daemon.py` prog strings).

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor: rename claude_buddy package to familiar + single CLI"
```

---

### Task 2: Direct HTTP Tidbyt push (drop pixlet from pushing)

**Files:**
- Modify: `linux-bridge/src/familiar/tidbyt.py`
- Test: `linux-bridge/tests/test_tidbyt.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `async def push_image(webp_bytes: bytes, *, device_id, api_token, installation_id="claudebuddy", poster=None) -> bool`. `poster` is an injectable `def(url, data, headers) -> int` (returns HTTP status) for tests; defaults to a real `urllib` POST run in a thread.

- [ ] **Step 1: Write the failing test**

Replace the pixlet-based push tests in `tests/test_tidbyt.py` with:

```python
import asyncio
import base64
import json
from familiar import tidbyt


def test_push_image_posts_base64_webp():
    calls = []
    def poster(url, data, headers):
        calls.append((url, json.loads(data), headers))
        return 200
    ok = asyncio.run(tidbyt.push_image(b"WEBPDATA", device_id="dev1",
                                       api_token="tok", poster=poster))
    assert ok is True
    url, body, headers = calls[0]
    assert url == "https://api.tidbyt.com/v0/devices/dev1/push"
    assert base64.b64decode(body["image"]) == b"WEBPDATA"
    assert body["installationID"] == "claudebuddy"
    assert body["background"] is False
    assert headers["Authorization"] == "Bearer tok"


def test_push_image_missing_config_is_false():
    assert asyncio.run(tidbyt.push_image(b"x", device_id="", api_token="t")) is False


def test_push_image_http_error_is_false():
    def poster(url, data, headers):
        return 500
    assert asyncio.run(tidbyt.push_image(b"x", device_id="d", api_token="t",
                                         poster=poster)) is False


def test_push_image_poster_raises_is_false():
    def poster(url, data, headers):
        raise OSError("network down")
    assert asyncio.run(tidbyt.push_image(b"x", device_id="d", api_token="t",
                                         poster=poster)) is False
```

- [ ] **Step 2: Run it, expect failure**

Run: `uv run pytest tests/test_tidbyt.py -v`
Expected: FAIL (`push_image` still takes a file path / uses pixlet).

- [ ] **Step 3: Rewrite `tidbyt.py` push**

Replace the module's push/render internals with:

```python
"""Push the buddy pet + haiku to a Tidbyt 64x32 over the HTTP API.

Best-effort: any failure (no config, network, non-200) is swallowed so it never
disturbs the M5 path. `poster` is injectable for tests.
"""
import asyncio
import base64
import json
import urllib.request

PUSH_URL = "https://api.tidbyt.com/v0/devices/%s/push"


def _post(url, data, headers) -> int:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


async def push_image(webp_bytes, *, device_id, api_token,
                     installation_id="claudebuddy", poster=None) -> bool:
    if not (device_id and api_token and webp_bytes):
        return False
    post = poster or _post
    body = json.dumps({
        "image": base64.b64encode(webp_bytes).decode(),
        "installationID": installation_id,
        "background": False,
    }).encode()
    headers = {"Authorization": "Bearer " + api_token,
               "Content-Type": "application/json"}
    url = PUSH_URL % device_id
    try:
        status = await asyncio.get_event_loop().run_in_executor(
            None, post, url, body, headers)
        return status == 200
    except Exception:
        return False
```

Leave the `push(lines, ...)` haiku function temporarily calling the old path only if present; it is fully replaced in Task 5. If the current `push(...)` references removed pixlet helpers and breaks import, stub it to `return False` for now with a `# replaced in Task 5` note.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_tidbyt.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/familiar/tidbyt.py tests/test_tidbyt.py
git commit -m "feat: push to Tidbyt over HTTP instead of pixlet"
```

---

### Task 3: tom-thumb font loader

**Files:**
- Create: `linux-bridge/src/familiar/fonts/tom-thumb.bdf` (vendored), `linux-bridge/src/familiar/font.py`
- Test: `linux-bridge/tests/test_font.py`

**Interfaces:**
- Produces: `font.text_width(s: str) -> int`; `font.draw_text(draw, xy, s, fill)` blits `s` at pixel `xy=(x,y)` onto a `PIL.ImageDraw.Draw` using the 4×6 tom-thumb glyphs; `font.CHAR_W == 4`, `font.CHAR_H == 6`.

- [ ] **Step 1: Vendor the font**

Add the public-domain **Tom Thumb** 4×6 BDF (by Robey Pointer) at `src/familiar/fonts/tom-thumb.bdf`. Source it from the pixlet/tinygo font set or https://robey.lag.net/2010/01/23/tiny-monospace-font.html . It is git-tracked package data (hatchling ships it by default — do NOT force-include).

- [ ] **Step 2: Write the failing test**

`tests/test_font.py`:

```python
from PIL import Image, ImageDraw
from familiar import font


def test_dimensions():
    assert font.CHAR_W == 4 and font.CHAR_H == 6


def test_known_glyph_loaded():
    # 'A' must exist and have some set pixels
    assert "A" in font.GLYPHS
    assert any(any(row) for row in font.GLYPHS["A"])


def test_text_width_is_monospace():
    assert font.text_width("abc") == 3 * font.CHAR_W


def test_draw_text_sets_pixels():
    im = Image.new("RGB", (32, 8), (0, 0, 0))
    d = ImageDraw.Draw(im)
    font.draw_text(d, (0, 0), "A", (255, 255, 255))
    assert im.getcolors()[0][1] != (255, 255, 255)  # not entirely white
    assert (255, 255, 255) in [c for _, c in im.getcolors()]  # some white drawn
```

- [ ] **Step 3: Run it, expect failure**

Run: `uv run pytest tests/test_font.py -v`
Expected: FAIL (`No module named 'familiar.font'`).

- [ ] **Step 4: Write `font.py`**

```python
"""Tom Thumb 4x6 bitmap font, parsed from the vendored BDF, for the haiku render.

GLYPHS[char] is a list of CHAR_H rows, each a list of CHAR_W ints (1 = ink).
"""
import os

CHAR_W, CHAR_H = 4, 6
_BDF = os.path.join(os.path.dirname(__file__), "fonts", "tom-thumb.bdf")


def _parse_bdf(path):
    glyphs, cur, code, bbx = {}, None, None, None
    reading = False
    for line in open(path):
        p = line.split()
        if not p:
            continue
        if p[0] == "ENCODING":
            code = int(p[1])
        elif p[0] == "BBX":
            bbx = tuple(int(x) for x in p[1:5])          # w, h, xoff, yoff
        elif p[0] == "BITMAP":
            reading, cur = True, []
        elif p[0] == "ENDCHAR":
            reading = False
            if code is not None and 32 <= code < 127:
                glyphs[chr(code)] = _normalize(cur, bbx)
            cur = code = bbx = None
        elif reading:
            cur.append(int(p[0], 16))
    return glyphs


def _normalize(rows, bbx):
    # BDF rows are MSB-left hex; place into a CHAR_W x CHAR_H cell using the
    # glyph bbx offsets so every glyph shares one baseline/grid.
    w, h, xoff, yoff = bbx
    cell = [[0] * CHAR_W for _ in range(CHAR_H)]
    top = CHAR_H - h - (1 + yoff)                        # tom-thumb descent = 1
    for r, val in enumerate(rows):
        y = top + r
        if not (0 <= y < CHAR_H):
            continue
        for c in range(w):
            bit = (val >> (8 * ((w + 7) // 8) - 1 - c)) & 1
            x = xoff + c
            if 0 <= x < CHAR_W and bit:
                cell[y][x] = 1
    return cell


GLYPHS = _parse_bdf(_BDF)
_BLANK = [[0] * CHAR_W for _ in range(CHAR_H)]


def text_width(s: str) -> int:
    return len(s) * CHAR_W


def draw_text(draw, xy, s, fill):
    x0, y0 = xy
    for i, ch in enumerate(s):
        cell = GLYPHS.get(ch, _BLANK)
        for ry, row in enumerate(cell):
            for rx, on in enumerate(row):
                if on:
                    draw.point((x0 + i * CHAR_W + rx, y0 + ry), fill=fill)
```

If `test_known_glyph_loaded`/`test_draw_text_sets_pixels` fail because the specific BDF's descent/offset differs, adjust the `top` computation in `_normalize` until 'A' renders upright and within the cell (verify by eye with a scratch render); the ≤1px baseline nudge is the only thing that varies between tom-thumb BDF copies.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_font.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/familiar/fonts/tom-thumb.bdf src/familiar/font.py tests/test_font.py
git commit -m "feat: tom-thumb bitmap font loader for the haiku render"
```

---

### Task 4: Python haiku renderer

**Files:**
- Create: `linux-bridge/src/familiar/haiku_render.py`
- Test: `linux-bridge/tests/test_haiku_render.py`

**Interfaces:**
- Consumes: `font.draw_text`, `font.text_width`, `font.CHAR_W/CHAR_H`.
- Produces: `haiku_render.render(lines: list[str]) -> bytes` — a 64×32 animated WebP (bytes) of the badge + wrapped white lines scrolling up, total ≤ 15s. `haiku_render.BADGE` is the 7×7 RGBA Claude sunburst.

- [ ] **Step 1: Write the failing test**

`tests/test_haiku_render.py`:

```python
import io
import struct
from PIL import Image
from familiar import haiku_render


def _durations(b):
    durs, i = [], 12
    while i < len(b) - 8:
        tag = b[i:i+4]; size = struct.unpack("<I", b[i+4:i+8])[0]
        if tag == b"ANMF":
            p = b[i+8:]; durs.append(p[12] | (p[13] << 8) | (p[14] << 16))
        i += 8 + size + (size & 1)
    return durs


def test_render_returns_64x32_animation():
    out = haiku_render.render(["morning tokens flow",
                               "a capybara dreams in brown",
                               "the cursor blinks on"])
    im = Image.open(io.BytesIO(out))
    assert im.size == (64, 32)
    assert im.n_frames > 1


def test_render_under_15s():
    out = haiku_render.render(["one two three four five",
                               "six seven eight nine ten now",
                               "eleven twelve done"])
    assert sum(_durations(out)) <= 15000


def test_render_empty_lines_ok():
    out = haiku_render.render(["", "", ""])
    assert Image.open(io.BytesIO(out)).size == (64, 32)
```

- [ ] **Step 2: Run it, expect failure**

Run: `uv run pytest tests/test_haiku_render.py -v`
Expected: FAIL (`No module named 'familiar.haiku_render'`).

- [ ] **Step 3: Write `haiku_render.py`**

```python
"""Render a haiku to a scrolling 64x32 WebP (replaces the Pixlet .star)."""
import base64
import io

from PIL import Image, ImageDraw

from . import font

W, H = 64, 32
FRAME_MS = 180          # matches the old Root(delay=180)
TOP_HOLD_FRAMES = 14    # ~2.5s hold at the top (old Marquee delay=14)
MAX_MS = 14500          # under the 15s device cap
GAP = 2                 # blank rows between lines

# 7x7 Claude coral sunburst (same asset the old .star used).
BADGE = Image.open(io.BytesIO(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAcAAAAHCAYAAADEUlfTAAAANUlEQVR4nGO4WR7+n"
    "wEKYGxkMRRBZAkmBgYGBvXOlYwoqqEAqyBcEsMOqEkM6BJYHYQuCQMA5wwlJ5fGpP"
    "oAAAAASUVORK5CYII="))).convert("RGBA")


# Fold typographic punctuation the tom-thumb (ASCII-only) glyphs can't draw.
_SUBS = {"—": "-", "–": "-", "‒": "-", "‘": "'",
         "’": "'", "“": '"', "”": '"', "…": "...",
         " ": " "}


def _ascii(s):
    for k, v in _SUBS.items():
        s = s.replace(k, v)
    return s.encode("ascii", "ignore").decode("ascii")


def _wrap(s, max_chars):
    words, lines, cur = s.split(), [], ""
    for w in words:
        cand = (cur + " " + w).strip()
        if font.text_width(cand) > max_chars * font.CHAR_W and cur:
            lines.append(cur); cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines or [""]


def _strip(lines):
    """Tall RGB image: centered badge, then each wrapped line centered."""
    max_chars = W // font.CHAR_W
    rendered = [row for ln in lines if ln for row in _wrap(ln, max_chars)]
    height = 7 + GAP + sum(font.CHAR_H + GAP for _ in rendered) + H  # trailing pad = one screen
    img = Image.new("RGB", (W, max(height, H)), (0, 0, 0))
    d = ImageDraw.Draw(img)
    img.paste(BADGE, ((W - 7) // 2, 0), BADGE)
    y = 7 + GAP
    for row in rendered:
        x = (W - font.text_width(row)) // 2
        font.draw_text(d, (x, y), row, (255, 255, 255))
        y += font.CHAR_H + GAP
    return img


def render(lines) -> bytes:
    strip = _strip([_ascii(str(x)) for x in lines])
    travel = max(0, strip.height - H)                  # px to scroll
    steps = [0] * TOP_HOLD_FRAMES + list(range(1, travel + 1))
    if len(steps) * FRAME_MS > MAX_MS:                 # fit the device cap
        steps = steps[:max(1, MAX_MS // FRAME_MS)]
    frames = [strip.crop((0, off, W, off + H)) for off in steps]
    buf = io.BytesIO()
    frames[0].save(buf, format="WEBP", save_all=True, append_images=frames[1:],
                   duration=[FRAME_MS] * len(frames), loop=0, lossless=True)
    return buf.getvalue()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_haiku_render.py -v`
Expected: PASS.

- [ ] **Step 5: Eyeball a sample (manual, not committed)**

```bash
cd linux-bridge
uv run python -c "from familiar import haiku_render; open('/tmp/h.webp','wb').write(haiku_render.render(['silent functions wait','a haiku drifts up the screen','poetry returns']))"
```
Open `/tmp/h.webp`; confirm the badge leads, text is crisp white, it scrolls once and holds at top. Adjust `GAP`/`TOP_HOLD_FRAMES` only if visibly off.

- [ ] **Step 6: Commit**

```bash
git add src/familiar/haiku_render.py tests/test_haiku_render.py
git commit -m "feat: render the haiku scroll in Python (Pillow), no pixlet"
```

---

### Task 5: Wire the renderer in + delete pixlet/.star

**Files:**
- Modify: `linux-bridge/src/familiar/tidbyt.py`, `linux-bridge/src/familiar/daemon.py`
- Delete: `linux-bridge/src/familiar/tidbyt_app.star`, `linux-bridge/src/familiar/tidbyt_app.webp`
- Test: `linux-bridge/tests/test_tidbyt.py`, `linux-bridge/tests/test_daemon.py`

**Interfaces:**
- Consumes: `haiku_render.render`, `tidbyt.push_image`.
- Produces: `async def tidbyt.push(lines, *, device_id, api_token, installation_id="claudebuddy", renderer=None, poster=None) -> bool` — renders the haiku and pushes it. The `_make_tidbyt` dict no longer has `pixlet` or `app_path` keys.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_tidbyt.py`:

```python
def test_push_renders_then_pushes(monkeypatch):
    monkeypatch.setattr(tidbyt.haiku_render, "render", lambda lines: b"RENDERED")
    sent = {}
    def poster(url, data, headers):
        import json, base64
        sent["img"] = base64.b64decode(json.loads(data)["image"]); return 200
    ok = asyncio.run(tidbyt.push(["a", "b", "c"], device_id="d", api_token="t",
                                 poster=poster))
    assert ok is True and sent["img"] == b"RENDERED"


def test_push_empty_lines_is_false():
    assert asyncio.run(tidbyt.push(["", "", ""], device_id="d", api_token="t")) is False
```

- [ ] **Step 2: Run it, expect failure**

Run: `uv run pytest tests/test_tidbyt.py::test_push_renders_then_pushes -v`
Expected: FAIL.

- [ ] **Step 3: Implement `push` and delete the star**

In `tidbyt.py` add the import `from . import haiku_render` and:

```python
async def push(lines, *, device_id, api_token, installation_id="claudebuddy",
               renderer=None, poster=None) -> bool:
    if not any(lines):
        return False
    render = renderer or haiku_render.render
    try:
        webp = render([str(x) for x in lines][:3])
    except Exception:
        return False
    return await push_image(webp, device_id=device_id, api_token=api_token,
                            installation_id=installation_id, poster=poster)
```

(Punctuation folding now lives in `haiku_render._ascii`, so `push` just forwards the raw lines.) Then remove the old Pixlet app:

```bash
git rm src/familiar/tidbyt_app.star src/familiar/tidbyt_app.webp
```

- [ ] **Step 4: Update the daemon's tidbyt dict + call sites**

In `daemon.py` `_make_tidbyt`, drop `pixlet` and `app_path`; the dict keeps `device_id`, `api_token`, `asset_dir`, `idle_assets`. Remove the `shutil.which("pixlet")` line (and the `shutil` import if now unused). In `_tidbyt_haiku`, call:

```python
ok = await tidbyt.push(lines, device_id=tb["device_id"], api_token=tb["api_token"])
```

In `_tidbyt_sync`, `push_image` now takes bytes — read the file and pass bytes:

```python
with open(path, "rb") as f:
    await tidbyt.push_image(f.read(), device_id=tb["device_id"],
                            api_token=tb["api_token"])
```

Fix `tests/test_daemon.py`: `_bridge_tb()` drops `pixlet`/`app_path` from the tb dict; any test asserting on `app_path` is removed.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: PASS. Grep to confirm no pixlet remains: `! grep -rn pixlet src/familiar` (must print nothing).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: wire Python haiku render into the daemon, drop pixlet + .star"
```

---

### Task 6: Tidbyt-only run mode (no M5/BLE)

**Files:**
- Modify: `linux-bridge/src/familiar/transport.py`, `linux-bridge/src/familiar/daemon.py`
- Test: `linux-bridge/tests/test_daemon.py`, `linux-bridge/tests/test_transport.py`

**Interfaces:**
- Produces: `transport.NullTransport` (`async send` no-op); `daemon._run_mode(cfg) -> "ble" | "tidbyt" | "none"`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_transport.py`:

```python
import asyncio
from familiar.transport import NullTransport

def test_null_transport_send_is_noop():
    asyncio.run(NullTransport().send(b"anything"))   # no error, no output
```

Add to `tests/test_daemon.py`:

```python
from familiar.config import Config

def test_run_mode_selects_ble_when_address():
    assert daemon._run_mode(Config(address="AA:BB", tidbyt_device_id="d",
                                   tidbyt_api_key="k")) == "ble"

def test_run_mode_tidbyt_only_without_address():
    assert daemon._run_mode(Config(address=None, tidbyt_device_id="d",
                                   tidbyt_api_key="k")) == "tidbyt"

def test_run_mode_none_when_unconfigured():
    assert daemon._run_mode(Config(address=None)) == "none"
```

- [ ] **Step 2: Run them, expect failure**

Run: `uv run pytest tests/test_transport.py tests/test_daemon.py -k "null_transport or run_mode" -v`
Expected: FAIL.

- [ ] **Step 3: Add `NullTransport`**

In `transport.py`:

```python
class NullTransport:
    async def send(self, data: bytes) -> None:
        return None
```

- [ ] **Step 4: Add `_run_mode` and the BLE-free path**

In `daemon.py`:

```python
def _run_mode(cfg) -> str:
    if cfg.address:
        return "ble"
    if cfg.tidbyt_device_id and cfg.tidbyt_api_key:
        return "tidbyt"
    return "none"
```

Refactor `main` to branch on `_run_mode`. For `"tidbyt"`, run a BLE-free bridge (reuse existing loops):

```python
from .transport import NullTransport, StdoutTransport

def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    ap = argparse.ArgumentParser(prog="familiar run")
    ap.add_argument("--stdout", action="store_true")
    args = ap.parse_args(argv)
    cfg = load()
    compose = _make_compose(cfg)
    tidbyt_cfg = _make_tidbyt(cfg)
    store = SessionStore(haiku_mode=compose is not None)
    mode = _run_mode(cfg)
    if mode == "none":
        print("[familiar] nothing configured — set an M5 `address` and/or "
              "Tidbyt keys (try `familiar init`)", file=sys.stderr)
        return 1
    if compose is not None:
        print("[familiar] haiku mode on", file=sys.stderr)
    if tidbyt_cfg is not None:
        print("[familiar] tidbyt on", file=sys.stderr)

    if mode == "tidbyt" or args.stdout:
        transport = StdoutTransport() if args.stdout else NullTransport()
        bridge = Bridge(store, transport, cfg.socket_path,
                        compose=compose, tidbyt=tidbyt_cfg)
        print(f"[familiar] {'dry-run' if args.stdout else 'tidbyt-only'}; "
              f"socket={cfg.socket_path}", file=sys.stderr)
        try:
            asyncio.run(bridge.run())
        except KeyboardInterrupt:
            pass
        return 0

    from .ble import run_with_ble
    try:
        asyncio.run(run_with_ble(cfg, store, _on_connect,
                                 compose=compose, tidbyt=tidbyt_cfg))
    except KeyboardInterrupt:
        pass
    return 0
```

(This preserves the old `--stdout` dry-run behavior and adds the tidbyt-only path.)

- [ ] **Step 5: Run the suite**

Run: `uv run pytest -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: Tidbyt-only run mode (no M5/BLE) via NullTransport"
```

---

### Task 7: `familiar init` (config + hooks + service + migration)

**Files:**
- Modify: `linux-bridge/src/familiar/init.py`
- Test: `linux-bridge/tests/test_init.py`

**Interfaces:**
- Consumes: `config._default_config_path`.
- Produces: `init.main(argv=None) -> int`; helpers `init.merge_hooks(settings: dict) -> dict` (pure, idempotent) and `init.migrate(old_cfg_dir, new_cfg_dir, settings_path)` used by `main`.

- [ ] **Step 1: Write failing tests for the pure pieces**

`tests/test_init.py`:

```python
import json
from familiar import init

EVENTS = {"SessionStart": "session-start", "UserPromptSubmit": "prompt-submit",
          "PostToolUse": "post-tool", "Notification": "notification",
          "Stop": "stop", "SessionEnd": "session-end"}


def test_merge_hooks_adds_all_six_events():
    out = init.merge_hooks({})
    for evt, name in EVENTS.items():
        cmds = [h["command"] for grp in out["hooks"][evt] for h in grp["hooks"]]
        assert f"familiar hook {name}" in cmds


def test_merge_hooks_is_idempotent():
    once = init.merge_hooks({})
    twice = init.merge_hooks(once)
    assert once == twice


def test_merge_hooks_preserves_foreign_hooks():
    existing = {"hooks": {"Stop": [{"hooks": [{"type": "command",
                                               "command": "other-tool ping"}]}]}}
    out = init.merge_hooks(existing)
    cmds = [h["command"] for grp in out["hooks"]["Stop"] for h in grp["hooks"]]
    assert "other-tool ping" in cmds and "familiar hook stop" in cmds


def test_merge_hooks_rewrites_claude_buddy():
    existing = {"hooks": {"Stop": [{"hooks": [{"type": "command",
                                              "command": "claude-buddy-hook stop"}]}]}}
    out = init.merge_hooks(existing)
    cmds = [h["command"] for grp in out["hooks"]["Stop"] for h in grp["hooks"]]
    assert "familiar hook stop" in cmds
    assert "claude-buddy-hook stop" not in cmds


def test_migrate_copies_config(tmp_path):
    old = tmp_path / "claude-buddy"; old.mkdir()
    (old / "config.toml").write_text('owner = "x"\n')
    new = tmp_path / "familiar"
    settings = tmp_path / "settings.json"; settings.write_text("{}")
    init.migrate(str(old), str(new), str(settings))
    assert (new / "config.toml").read_text() == 'owner = "x"\n'
```

- [ ] **Step 2: Run them, expect failure**

Run: `uv run pytest tests/test_init.py -v`
Expected: FAIL (`merge_hooks`/`migrate` not defined; `init.main` still raises).

- [ ] **Step 3: Implement `init.py`**

```python
"""`familiar init` — write config, wire Claude Code hooks, optional service,
and migrate an existing claude-buddy install. Interactive by default."""
import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

from .config import _default_config_path

EVENTS = {"SessionStart": "session-start", "UserPromptSubmit": "prompt-submit",
          "PostToolUse": "post-tool", "Notification": "notification",
          "Stop": "stop", "SessionEnd": "session-end"}
_MATCHER = {"PostToolUse": "*"}


def _entry(evt, name):
    grp = {"hooks": [{"type": "command", "command": f"familiar hook {name}"}]}
    if evt in _MATCHER:
        grp["matcher"] = _MATCHER[evt]
    return grp


def merge_hooks(settings: dict) -> dict:
    out = dict(settings)
    hooks = {k: list(v) for k, v in out.get("hooks", {}).items()}
    for evt, name in EVENTS.items():
        groups = hooks.get(evt, [])
        # drop any prior familiar/claude-buddy group for this event
        kept = []
        for grp in groups:
            cmds = [h.get("command", "") for h in grp.get("hooks", [])]
            if any(c.startswith("familiar hook ") or c.startswith("claude-buddy-hook")
                   for c in cmds):
                continue
            kept.append(grp)
        kept.append(_entry(evt, name))
        hooks[evt] = kept
    out["hooks"] = hooks
    return out


def _settings_path() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(base) / "settings.json"


def _write_hooks(settings_path: Path):
    if settings_path.exists():
        cur = json.loads(settings_path.read_text() or "{}")
        settings_path.with_suffix(f".json.bak.{int(time.time())}").write_text(
            settings_path.read_text())
    else:
        cur = {}
        settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(merge_hooks(cur), indent=2) + "\n")


def migrate(old_cfg_dir, new_cfg_dir, settings_path):
    old_cfg_dir, new_cfg_dir = Path(old_cfg_dir), Path(new_cfg_dir)
    old_toml = old_cfg_dir / "config.toml"
    new_toml = new_cfg_dir / "config.toml"
    if old_toml.exists() and not new_toml.exists():
        new_cfg_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old_toml, new_toml)
    sp = Path(settings_path)
    if sp.exists():
        _write_hooks(sp)   # merge_hooks rewrites claude-buddy-hook -> familiar hook


def _prompt(label, default=""):
    v = input(f"{label}{f' [{default}]' if default else ''}: ").strip()
    return v or default


def _write_config(cfg_path: Path, values: dict):
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    existing = cfg_path.read_text() if cfg_path.exists() else ""
    lines = [existing.rstrip()] if existing else []
    for k, v in values.items():
        if v and f"{k} " not in existing and f"{k}=" not in existing:
            lines.append(f'{k} = "{v}"')
    cfg_path.write_text("\n".join(l for l in lines if l) + "\n")


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    ap = argparse.ArgumentParser(prog="familiar init")
    ap.add_argument("--yes", action="store_true", help="non-interactive")
    ap.add_argument("--tidbyt-device"); ap.add_argument("--tidbyt-key")
    ap.add_argument("--m5-address"); ap.add_argument("--anthropic-key")
    ap.add_argument("--owner"); ap.add_argument("--service", action="store_true")
    a = ap.parse_args(argv)

    cfg_path = _default_config_path()
    old_cfg = Path(str(cfg_path.parent).replace("familiar", "claude-buddy"))
    if old_cfg.exists() and old_cfg != cfg_path.parent:
        print(f"Migrating existing claude-buddy setup from {old_cfg} ...")
        migrate(str(old_cfg), str(cfg_path.parent), str(_settings_path()))
        print("Migrated. You can `uv tool uninstall claude-buddy` when ready.")
        return 0

    interactive = not (a.yes or a.tidbyt_device)
    values = {
        "tidbyt_device_id": a.tidbyt_device or (_prompt("Tidbyt device id") if interactive else ""),
        "tidbyt_api_key": a.tidbyt_key or (_prompt("Tidbyt API key") if interactive else ""),
        "address": a.m5_address or (_prompt("M5 BLE address (blank if none)") if interactive else ""),
        "api_key": a.anthropic_key or (_prompt("Anthropic API key (blank to skip haikus)") if interactive else ""),
        "owner": a.owner or (_prompt("Your name", os.environ.get("USER", "")) if interactive else ""),
    }
    _write_config(cfg_path, values)
    _write_hooks(_settings_path())
    print(f"Wrote {cfg_path} and merged hooks into {_settings_path()}.")
    if a.service or (interactive and _prompt("Install systemd service? (y/N)").lower() == "y"):
        _install_service()
    print("Done. Start with `familiar run` (or the service).")
    return 0


def _install_service():
    unit = Path.home() / ".config/systemd/user/familiar.service"
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(
        "[Unit]\nDescription=Familiar desk buddy\nAfter=bluetooth.target\n\n"
        "[Service]\nExecStart=%h/.local/bin/familiar run\nRestart=on-failure\n"
        "RestartSec=3\nEnvironment=PYTHONUNBUFFERED=1\n\n"
        "[Install]\nWantedBy=default.target\n")
    os.system("systemctl --user daemon-reload && "
              "systemctl --user enable --now familiar.service")
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_init.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/familiar/init.py tests/test_init.py
git commit -m "feat: familiar init — config + hooks + service + claude-buddy migration"
```

---

### Task 8: Docs

**Files:**
- Modify: `linux-bridge/README.md`, `README.md`, `linux-bridge/hooks-settings.example.json`

- [ ] **Step 1: Update the example hooks file**

Rewrite `linux-bridge/hooks-settings.example.json` commands from `claude-buddy-hook <event>` to `familiar hook <event>` (all six).

- [ ] **Step 2: Rewrite `linux-bridge/README.md`**

- Title/intro: the tool is `familiar`; setup is `uv tool install .` (or `familiar` once published) → `familiar init` → `familiar run`.
- Replace the manual "pair / configure / install hooks / run" steps with the `familiar init` flow; keep pairing as a step only for M5 users.
- Tidbyt section: no `pixlet`; note the pet needs only the two Tidbyt keys (haiku needs the Anthropic key); Tidbyt-only mode = leave `address` unset.
- Add a short "Migrating from claude-buddy" note: `uv tool install .` then `familiar init` migrates config + hooks + service; then `uv tool uninstall claude-buddy`.
- Service unit: `familiar.service`, `ExecStart=%h/.local/bin/familiar run`.

- [ ] **Step 3: Update `README.md`**

- The three-feature banner: bridge/haiku/Tidbyt wording stays, but any command references become `familiar`.
- Project layout: `linux-bridge/` ships the `familiar` package; note `familiar init`.
- The Tidbyt companion section: drop the `pixlet` requirement line; setup is `familiar init`.

- [ ] **Step 4: Verify no stale references**

Run: `! grep -rniE 'pixlet|claude-buddy-hook|claude_buddy' README.md linux-bridge/README.md linux-bridge/hooks-settings.example.json`
Expected: prints nothing (all updated). (`claude-buddy` may remain only in the explicit "migrating from" note.)

- [ ] **Step 5: Commit**

```bash
git add README.md linux-bridge/README.md linux-bridge/hooks-settings.example.json
git commit -m "docs: familiar init flow, no pixlet, claude-buddy migration"
```

---

## Post-plan rollout (with the user, not a task)

After all tasks: `uv tool install . --force --no-cache` from `linux-bridge/`, run `familiar init` (it migrates this machine's `~/.config/claude-buddy` + hooks + service), verify the cactus + a haiku render on the Tidbyt, `uv tool uninstall claude-buddy`, then open the PR.
