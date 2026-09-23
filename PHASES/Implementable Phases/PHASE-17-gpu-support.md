# Phase 17 — GPU support

**State:** implementable

---

## Why

2.7 seconds per image is dominated by the CLIP vision tower on CPU. On a
consumer GPU that is a fraction of a second, and the one-off gallery encoding
drops from four minutes to seconds.

## Scope

- `device = "cuda"` already exists in the config and is plumbed through. What
  is missing is detection, a CUDA install path, and a clean fallback.
- Detect at startup and report the device in the status bar, which already has
  a slot for it.
- **Keep CPU the default.** The setup script installs CPU wheels deliberately:
  a CUDA install is several gigabytes and most machines running this will not
  have a supported card.
- A separate `scripts/setup_env_cuda.ps1` rather than branching the main one
  with a flag nobody reads.
- The float16 gallery cache is device-independent and must stay loadable across
  a CPU/GPU switch. That needs a test, because a cache silently rebuilt on
  every device change would waste four minutes each time.

## Done when

- The same photograph gives the same coordinate on CPU and GPU, within
  floating-point tolerance, asserted.
- A machine with no CUDA falls back silently and correctly.
- Timings for both are in the README, measured rather than estimated.
