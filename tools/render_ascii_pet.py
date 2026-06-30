"""Render a buddy species' ASCII pose animations to Tidbyt WebPs.

Pulls poses with extract_buddies, lays each 5-row pose out in pixlet's
monospace `tom-thumb` font in the species body color, overlays a generic
per-state particle, and renders one animated 64x32 WebP per persona state to
`src/claude_buddy/tidbyt_buddy/<species>/<state>.webp`.

    uv run --with pillow python tools/render_ascii_pet.py src/buddies/capybara.cpp

Requires `pixlet` on PATH (or ~/.local/bin/pixlet).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
import extract_buddies  # noqa: E402

PIXLET = shutil.which("pixlet") or os.path.expanduser("~/.local/bin/pixlet")
OUT_ROOT = os.path.join(os.path.dirname(__file__), os.pardir,
                        "linux-bridge", "src", "claude_buddy", "tidbyt_buddy")

POSE_X, POSE_Y = 8, 1          # center ~48x30 art on the 64x32 panel
FIRMWARE_TICK_MS = 200         # the M5 ticks the pet every 200ms (main.cpp)
MAX_ANIM_MS = 14500            # pixlet/Tidbyt hard-cap animations at 15s


def _particle(state, i, n):
    """Generic per-state particle for frame i: (char, x, y, color) or None."""
    if state == "busy":                       # typing ticker, bottom center
        dots = ["   ", ".  ", ".. ", "...", ".. ", ".  "]
        return (dots[i % len(dots)], 30, 26, "#ffffff")
    if state == "attention":                  # blinking alert above the head
        return ("!", 31, 0, "#ffd000") if i % 2 == 0 else None
    if state == "celebrate":                  # sparkles drifting down
        spots = [(14, 2), (46, 4), (24, 0), (40, 1)]
        x, y0 = spots[i % len(spots)]
        ch = "*" if i % 2 == 0 else "."
        return (ch, x, (y0 + i * 2) % 12, "#ffd000")
    # idle: no particle — the blink/chew/look-around poses carry the liveness
    # (matching the firmware, where the z belongs to the sleep state).
    return None


def _frame_star(rows, particle, color):
    pose = "render.Padding(pad=(%d, %d, 0, 0), child=render.Column(children=[%s]))" % (
        POSE_X, POSE_Y,
        ", ".join('render.Text(content=%s, font="tom-thumb", color=%s)'
                  % (json.dumps(r), json.dumps(color)) for r in rows))
    children = [pose]
    if particle:
        ch, x, y, pcol = particle
        children.append(
            'render.Padding(pad=(%d, %d, 0, 0), child=render.Text(content=%s, '
            'font="tom-thumb", color=%s))' % (x, y, json.dumps(ch), json.dumps(pcol)))
    return "render.Stack(children=[%s])" % ", ".join(children)


def _star(state, data):
    frames = data["frames"]
    color = data["color"]
    body = ",\n        ".join(
        _frame_star(frames[i], _particle(state, i, len(frames)), color)
        for i in range(len(frames)))
    # Match the M5 (each pose holds `divisor` ticks of 200ms), but speed up just
    # enough to keep every pose under the 15s animation cap.
    n = len(frames)
    delay = min(data.get("divisor", 5) * FIRMWARE_TICK_MS, MAX_ANIM_MS // max(n, 1))
    return (
        'load("render.star", "render")\n'
        "def main(config):\n"
        "    return render.Root(delay=%d, child=render.Animation(children=[\n"
        "        %s,\n"
        "    ]))\n" % (delay, body))


def render_species(cpp_path):
    name = os.path.splitext(os.path.basename(cpp_path))[0]
    states = extract_buddies.extract(cpp_path)
    out_dir = os.path.join(OUT_ROOT, name)
    os.makedirs(out_dir, exist_ok=True)
    for state, data in states.items():
        if not data["frames"]:
            continue
        # pixlet treats the .star's directory as the app bundle and globs
        # sibling *.star files, so give each render its own clean dir.
        tmp = tempfile.mkdtemp()
        star_path = os.path.join(tmp, "app.star")
        with open(star_path, "w") as f:
            f.write(_star(state, data))
        out = os.path.join(out_dir, state + ".webp")
        try:
            subprocess.run([PIXLET, "render", star_path, "-o", out],
                           check=True, capture_output=True, text=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        print("%-10s %2d frames -> %s" % (state, len(data["frames"]), out))
    return out_dir


if __name__ == "__main__":
    for cpp in sys.argv[1:]:
        render_species(cpp)
