purpose={enables:"tracking Serena plugin patches and issues"}
updated=2026-03-17

## uv Cache Patches (session 207 — take effect after /clear)
Location: `C:/Users/admin/AppData/Local/uv/cache/archive-v0/re0fNZOFHI9dnrOoTFokh/Lib/site-packages/serena/`

### WebSocket Dashboard (replaces ALL polling)
Files patched: dashboard.py, task_executor.py, agent.py, dashboard.js, index.html
Dependencies added: flask_socketio/, socketio/ (copied from system Python)
Effect: bidirectional WebSocket, zero polling, real-time task/log/config updates
Also: chart.min.js + chartjs-plugin-datalabels.min.js bundled locally, banner fetch disabled

### shell.py — bash instead of cmd.exe
Uses `[bash, "-c", command]` with `shell=False`. Fixes heredoc/multiline commands on Windows.

### Re-apply if Serena updates
All patches are in the uv cache. A `serena@claude-plugins-official` update will overwrite them.
Re-apply by copying from the fork: `C:/Users/admin/Documents/GitHub/serena/src/serena/`

## Known Limitations
### insert_after_symbol / replace_symbol_body
Corrupt adjacent functions. Use `replace_content` with literal mode instead.

### HTML Window Closes Unexpectedly (Session 83)
Cause unknown. serena.exe stays running.