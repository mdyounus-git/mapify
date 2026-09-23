# Phase 18 — Session history

**State:** implementable

---

## Why

Analyses are ephemeral. Close the window and the reasoning is gone unless it
was exported. For a tool whose output is an argument, being unable to return to
a previous argument is a real gap.

## Scope

- A local SQLite history: timestamp, image hash, the report, the file name.
  **Not the image itself** — the privacy stance is that the image is analysed
  in memory and dropped, and history must not quietly break that.
- A history rail: re-open a past analysis, restore its globe state, compare two
  results side by side.
- **Off by default**, with an explicit switch, because "this tool keeps a
  record of every photograph you analysed" is a decision the user should make
  rather than discover.
- A one-click clear, and a stated retention limit.

## The trap

An image hash is not anonymous. It identifies a specific photograph, and a
history file is a record of what someone was investigating and when.

Design it as though it will one day be read by someone the user did not intend
to read it — because for a tool used by journalists and researchers, that is a
realistic threat rather than a hypothetical one.

## Done when

- History is off by default and the setting is discoverable rather than buried.
- Clearing it actually removes the rows, verified by reading the file back
  rather than trusting the delete.
- No image bytes reach disk in any mode except the existing debug
  `keep_uploads`, asserted.
