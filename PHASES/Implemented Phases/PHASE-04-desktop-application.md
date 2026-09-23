# Phase 04 — Windows desktop application

**State:** implemented · commit `e2b4f4f`

A program with a title bar, not a page with a URL.

---

## Goal

Make Mapify a native Windows application while keeping the WebGL globe, which
is the only sensible way to render it.

## What was built

**`mapify/desktop.py`** opens a real window through pywebview (BSD-3) on the
Edge WebView2 runtime that ships with Windows 10 and 11. Real title bar, real
taskbar entry, native file dialogs, no URL shown anywhere.

**The analysis service moves behind the window.** Loopback only, on an
ephemeral port the OS picks at launch, behind a random per-session token that
is never written to disk and dies with the window.

**Native file handling.** Opening an image goes through the Windows dialog and
sends a *path*, so a 30 MB photograph is read once off disk instead of being
copied through the WebView. The service will only read paths the dialog handed
out — without that check it would be an arbitrary-file-read endpoint, token or
no token.

**Launchers.** `Mapify.bat` starts it with `pythonw` so no console flashes up;
`Mapify-console.bat` is the one to use when something is wrong.

## Verification

```
window title : "Mapify 0.1.0 — Image Geolocation Reconnaissance"

GET  /api/status          no token        -> 403
GET  /js/app.js           no token        -> 403
GET  /api/status          wrong token     -> 403
GET  /api/status          with token      -> 200
POST /api/analyse/local   unapproved path -> 403
```

## What went wrong

Two bugs, both mine, both found by running it rather than reading it:

- pywebview serialises the `js_api` object's attributes to expose them to
  JavaScript. Holding the `Window` on the bridge meant it walked
  `Window.native`, a .NET WinForms control — recursing until the stack gave out
  and touching COM interfaces from the wrong thread. The window reference now
  lives outside the bridge's attribute graph.
- The strict CSP blocked the inline import map. Rather than adding
  `'unsafe-inline'`, the server issues a per-response nonce and substitutes it
  into the page.

## Left open

Windows only. pywebview supports GTK and Cocoa, but nothing here has been
tested on either, and the packaging and setup scripts are both Windows-shaped.
