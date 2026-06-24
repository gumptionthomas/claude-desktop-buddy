# claude-buddy — Linux BLE bridge for Claude Code

Feeds an M5StickC Plus running the buddy firmware with live Claude Code
activity over BLE. Ambient display only (one-way).

## Install

```bash
cd linux-bridge
uv tool install .
```
This puts `claude-buddy` and `claude-buddy-hook` on your PATH.

## 1. Pair the stick (one-time)

The firmware requires an encrypted, bonded link. Pair via bluetoothctl:

```bash
bluetoothctl
  scan on                 # wait for "Claude-XXXX", note its MAC
  pair AA:BB:CC:DD:EE:FF  # type the 6-digit code shown on the stick
  trust AA:BB:CC:DD:EE:FF
  scan off
  exit
```

## 2. Configure

`~/.config/claude-buddy/config.toml`:
```toml
address = "AA:BB:CC:DD:EE:FF"
owner   = "YourName"
```

## 3. Install the hooks

Merge `hooks-settings.example.json` into `~/.claude/settings.json` (user
scope, so all Claude Code sessions feed the buddy).

## 4. Run

```bash
claude-buddy            # connects over BLE
claude-buddy --stdout   # dry run: prints heartbeats, no BLE
```

## How it maps

| Claude Code | Pet |
|---|---|
| actively working (running) | busy |
| permission prompt / notification | attention (LED blinks) |
| turn finished | celebrate |
| quiet | idle / sleep |
