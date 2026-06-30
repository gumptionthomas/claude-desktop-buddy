# ASCII Pets on the Tidbyt Design

**Goal:** Render a config-selected ASCII species on the Tidbyt as actual text —
state-reflective, animated through its real pose sequences, with generic
per-state particles. Start with **capybara** (`SPECIES_TABLE[0]`, the M5 default);
the rest follow as a batch.

**Reuses the bufo orchestration (#26):** the daemon already pushes
`<state>.webp` per persona from an asset dir. We add a per-species asset dir and
let `tidbyt_pet` choose which dir. No new push/event logic.

## Components
1. **Extractor (`tools/extract_buddies.py`, build-time):** parse a species'
   `src/buddies/<name>.cpp` for each state function (`doIdle/doBusy/doAttention/
   doCelebrate`): the pose arrays (5-row art), the `SEQ` order, and the
   `buddyPrintSprite` body color (RGB565 → RGB888). Emit per state. Handles C++
   string escapes (`\\`, etc.).
2. **ASCII → WebP renderer (build-time):** render each pose to a 64x32 frame in a
   small monospace pixel font (the pet in its body color on black), overlay the
   generic particle layer for that state, assemble the `SEQ` into an animated
   WebP → `tidbyt_buddy/<species>/<state>.webp`. Bundled like the bufo webps.
3. **Generic particles (per state, not per species):** a shared overlay keyed on
   state — `idle` a drifting `z`/`Z`, `celebrate` a `*` sparkle burst,
   `attention` a blinking `!`. (Exact per-species particles — ~10-15 procedural
   draws x 18 species — are out of scope; "incredibly difficult".)
4. **Config:** `tidbyt_pet = "capybara"` selects the species asset dir; unset or
   `"bufo"` keeps the GIF buddy. Validated against the available dirs; unknown →
   fall back to bufo.
5. **Daemon:** the only change is choosing `asset_dir` from `tidbyt_pet`. Persona
   derive, idle rotation (single idle.webp per species here, so no rotation), and
   the haiku-event timer are unchanged.

## State mapping
persona `attention/busy/celebrate/idle` → the species' `doAttention/doBusy/
doCelebrate/doIdle`. (sleep/dizzy/heart unused for now.)

## Error handling
Best-effort: a missing species dir or asset → fall back to bufo / no-op; the M5
path is unaffected. The extractor/renderer are build-time, not in the daemon.

## Testing
- Extractor on capybara: pose counts, SEQ length, color parsed; C++ escapes
  unescaped correctly.
- Renderer: output WebP dims (64x32) and frame count match the SEQ.
- `tidbyt_pet` selects the right asset dir; unknown falls back to bufo.
- Persona mapping unchanged (covered by existing daemon tests).

## Scope / phases
- **Phase 1 (this spec):** capybara end-to-end (extractor + renderer +
  config + daemon selection), proving the pipeline and the look on hardware.
- **Phase 2 (follow-on, issue #27):** run the extractor/renderer over the other
  17 species; same code, just more assets.
