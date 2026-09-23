# Phase 20 — Installer

**State:** implementable · last, deliberately

---

## Why

`dist/Mapify/` is 1.1 GB of folder. Shippable, but "unzip this and find the
exe" is where non-technical users stop.

Last because an installer for a tool that is still changing is wasted work, and
because everything above it improves the product while this only improves its
delivery.

## Scope

- Inno Setup (open source) or WiX. Inno is simpler and sufficient.
- Start-menu entry, optional desktop shortcut, a real uninstaller.
- **The first-run experience is the actual content of this phase.** The
  application currently downloads 1.7 GB of weights and then encodes the GPS
  gallery for four minutes with nothing but a status line. That is fine for a
  developer and not fine for anyone else. It needs a proper first-run screen:
  progress, the size stated *before* the download starts, and the option to
  defer.
- Bundle the Natural Earth data and the textures; fetch the weights on first
  run.
- Detect the WebView2 runtime and point at Microsoft's installer if it is
  absent, rather than failing with a COM error.

## Code signing

Without a certificate, SmartScreen will warn. That warning is **correct** for
an unsigned binary and should not be worked around with a trick. Either obtain
a certificate, or document the warning honestly in the README and tell people
how to verify the download instead.

## Done when

- Installs and uninstalls cleanly on a machine that has never had Python.
- First run explains the download before starting it.
- A missing WebView2 runtime produces a sentence a person can act on rather
  than a stack trace.
