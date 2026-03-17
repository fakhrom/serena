purpose={enables:"behavioral corrections from user — apply in all future work"}
related=[discipline,design-principles]
updated=2026-03-17

## No Legacy Fallbacks (2026-03-17)
Never add legacy fallbacks or shadow-writes when migrating code. Clean cut only.
**Why:** User considers fallbacks bloat. Code must be production-grade.
**How to apply:** Delete old code entirely on migration. No dual-write patterns.

## No Hedging Questions (2026-03-17)
Never ask "should I", "want me to", "let me know if" unless destructive/irreversible/money.
**Why:** User wants autonomous action, not permission-seeking.
**How to apply:** Research context, evaluate options, pick best approach, act.

## Clean Code Only (2026-03-17)
All code must be production-grade. No bloat, no unused imports, no dead code paths.
**Why:** Zero tolerance for code bloat.
**How to apply:** Run ruff after every edit. Remove unused imports immediately.

## Maximize Parallel Serena Tool Calls (2026-03-17)
Fire multiple independent Serena tool calls per message. Don't serialize what can be parallel.
**Why:** User wants Serena's execution queue active and busy.
**How to apply:** Send independent operations in the same message.

## No Unnecessary Internet (2026-03-17)
All software on this machine must be offline-first. Internet only for: Claude API, trading bot RPC/CEX, GitHub push/pull, Windows Update (manual), Chrome browsing (manual). No CDN, no analytics, no telemetry, no phoning home.
**Why:** User wants full control of all internet usage. Nothing should access the internet without explicit user intent.
**How to apply:** Bundle all dependencies locally. Disable analytics/telemetry. Block auto-update fetches. Only user-initiated internet usage.