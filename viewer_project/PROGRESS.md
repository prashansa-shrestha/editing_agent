# Viewer Project Progress Ledger

This is an append-only handoff record for the scoped local Gaussian-splat
viewer. It records project state; it does not replace `SPEC.md` or
`AGENTS.md`.

## Current status

- Last updated: 2026-08-31
- Current status: M0, M1, and M2 complete. M2 was freshly independently
  reviewed; verdict **SHIP** for the authentic thin interactive RGB renderer
  milestone. M3 is the first incomplete milestone.
- Last completed checkpoint commit: `34e3a33` — `checkpoint: add safe viewer
  scene streaming`. The M0 documentation checkpoint `99f2ef1` remains in the
  milestone history. The M2 checkpoint commit is pending immediately after
  this ledger update; no M2 hash exists yet. This documentation update creates
  no commit.
- Current viewer files: `viewer_project/SPEC.md`,
  `viewer_project/AGENTS.md`, `viewer_project/PROGRESS.md`, the M1 files
  `src/scene_agent/viewer.py`, `src/scene_agent/webapp.py`, and
  `tests/test_viewer_api.py`, and the M2 integration/test files
  `src/scene_agent/web/index.html`, `src/scene_agent/web/styles.css`,
  `src/scene_agent/web/viewer.js`,
  `src/scene_agent/web/viewer-camera.js`,
  `src/scene_agent/web/viewer-lifecycle.js`,
  `src/scene_agent/webapp.py`, `tests/test_webapp.py`, and
  `tests/test_viewer_frontend.py`.
- M0 froze the logical scene ID, manifest/output boundaries, Z-up convention,
  `zup_aabb_v1`, pinned PlayCanvas `2.3.3` requirement, and acceptance gates.
- M1 establishes only the safe allowlisted manifest and bounded compressed-Ply
  transport: trusted `+Z` configuration, canonical pinned target,
  descriptor-relative `O_NOFOLLOW` traversal, one concurrent operation, reads
  bounded to `<=1 MiB`, and HTTP mutation truncation/validation.
- M2 connects the exact SHA-pinned same-origin vendor route to the official
  PlayCanvas `2.3.3` path `Asset(gsplat) -> GSplatResource -> instantiate`
  with decompression disabled. It is an RGB-only interactive renderer; M3
  bird's-eye framing and capture work remain unimplemented.
- The immutable source remains unchanged. No generated capture/artifact,
  source mutation, or push exists; M1 checkpoint `34e3a33` records the last
  completed implementation checkpoint, and the M2 checkpoint is pending.
- Preserve these unrelated pre-existing workspace changes; never stage them:
  root `SPEC.md` is modified, and root `editing_agent/` is untracked.
  Existing viewer specification files are also not to be overwritten.

## Completed checks

- Non-empty checks: `SPEC.md`, `AGENTS.md`, and this ledger were verified
  non-empty.
- Required headings and rules: the ledger records current status, checkpoint,
  completed checks, blockers, exact next action, M0-M5 milestones, acceptance
  gate boundary, safety/claim limits, and continuation steps.
- `git diff --check -- viewer_project/PROGRESS.md`: PASS.
- Trailing-whitespace scan for this file: PASS.
- M1 implementation files: `src/scene_agent/viewer.py`,
  `src/scene_agent/webapp.py`, and `tests/test_viewer_api.py`.
- Focused viewer and web tests: 113 passed in 49.17s.
- Full test suite: 146 passed in 52.98s.
- Python `py_compile`: PASS.
- M1-file `git diff --check`: PASS.
- Fresh isolated M1 reviewer: SHIP.
- Immutable source evidence: `/data/Desktop/Prashansa/major/scenebench/data/InteriorGS/0231_840445/3dgs_compressed.ply` remains byte-identical at 32,144,308 bytes, with 524,508 Gaussians, 2,049 chunks, and SHA-256
  `c82a07ca1f2d4502df9dfb83e0b26973392e5139f78d3fe1879427c272b426da`.
- Trusted renderer-expanded Z-up AABB: min
  `[-6.4937675035358176, -8.491147283351491, -5.602591798801276]`; max
  `[6.81342827358616, 8.76070222006658, 5.533811335966632]`.
- Measured isolated RSS delta: 4,673,536 bytes; configured ceiling: 32 MiB.
- M2 implementation files: modified `src/scene_agent/webapp.py`,
  `src/scene_agent/web/index.html`, `src/scene_agent/web/styles.css`, and
  `tests/test_webapp.py`; new `src/scene_agent/web/viewer.js`,
  `src/scene_agent/web/viewer-camera.js`,
  `src/scene_agent/web/viewer-lifecycle.js`, and
  `tests/test_viewer_frontend.py`.
- M2 focused frontend + viewer API + web tests: 143 passed in 58.64s.
- M2 full test suite: 176 passed in 65.16s.
- Python and JavaScript syntax checks plus the scoped diff check: PASS.
- Fresh isolated second M2 reviewer: SHIP. The review findings about hidden or
  0x0 readiness and aspect-aware portrait fitting were fixed before this
  verdict.
- Authentic browser acceptance: the real source reached `READY` with 524,508
  splats through the pinned official path, using `playcanvas_compressed_ply`,
  `+Z`, and the source digest prefix; the canvas visibly rendered non-background
  splats with zero console errors.
- Desktop visual QA: the canvas was 777x640; orbit and zoom changed the
  camera, while Reset and Fit returned exact deterministic states.
- Portrait visual QA: a 320x700 viewport produced a real 252x330 canvas with
  aspect 0.7636 and visible framed splats with margin. Reload reproduced the
  camera position `[27.03,-26.73,20.17]`, target `[0.16,0.13,-0.03]`, and
  `+Z` convention with zero errors.
- Deliberate renderer-error QA kept the canvas behind the error overlay with
  opacity 0, positive layout dimensions, and disabled controls; initial and
  reload-ready states had opacity 1. The rendered screenshot had 8,248 unique
  colors and 9,330 non-background pixels, with zero runtime or console errors.
- PlayCanvas module hash: `4b18241d684e3676109100f61aa3ad3488f8f95f632fdbb4433290a315dbc875`.
- Honest M2 limitations: browser/GPU memory, FPS, and cold/warm timing remain
  unmeasured, and no reusable browser harness was committed.

## Acceptance-gate boundary

M1 passes the safe manifest/transport boundary: the real source fingerprint,
Gaussian/chunk counts, trusted `+Z` bounds, loopback/path confinement,
descriptor-relative `O_NOFOLLOW` traversal, bounded reads, single-operation
concurrency, and request truncation/validation are covered by the recorded
implementation and tests. M2 now also passes the thin interactive renderer
boundary: the authentic 524,508-Gaussian compressed source renders through the
pinned PlayCanvas `2.3.3` chain with ordinary orbit/zoom/reset/Fit controls,
real source-backed readiness and failure states, and RGB-only output. The
vendor route is same-origin and SHA-pinned; no canonical decoded substitute is
used. M2 does not implement bird's-eye framing, capture, scene understanding,
segmentation, semantics, depth, Gaussian IDs, or editing. M3-M4 still need
deterministic bird's-eye framing, capture reproducibility, and final hardening
gates.

## Milestone updates (append only)

- 2026-08-30 — M0 — Contract and fixture review: complete and independently
  reviewed, verdict SHIP. No scene or code changes were part of M0.
- 2026-08-30 — M0 checkpoint `99f2ef1` — `checkpoint: define viewer contract
  and progress ledger`: documentation-only checkpoint; implementation remains
  pending.
- 2026-08-31 — M1 — Authentic source manifest and bounded stream: complete and
  freshly independently reviewed, verdict SHIP. M1 proves only safe
  manifest/transport.
- 2026-08-31 — M1 checkpoint `34e3a33` — `checkpoint: add safe viewer scene
  streaming`: implementation checkpoint; M2 is the next action.
- M1 — Authentic source manifest and bounded stream: complete.
- M2 — Thin interactive renderer: complete; see the dated SHIP entry appended
  below.
- M3 — Bird's-eye and capture reproducibility: pending.
- M4 — Hardening and handoff: pending.
- M5 — Explicitly separate later perception work: pending; the viewer remains
  RGB-only even if this later adapter is not implemented.
- 2026-08-31 — M2 — Thin interactive renderer: complete and freshly
  independently reviewed, verdict SHIP. The real 524,508-Gaussian compressed
  source reached authentic READY through the pinned PlayCanvas `2.3.3`
  `Asset(gsplat) -> GSplatResource -> instantiate` path with decompression
  disabled. Desktop and portrait visual checks covered non-background rendering,
  orbit/zoom, exact Reset/Fit behavior, reload camera stability, and honest
  renderer-error state behavior; no console or runtime errors were observed.
  The viewer remains RGB-only.
- 2026-08-31 — M2 checkpoint: pending immediately after this ledger update;
  no commit hash exists yet. Preserve the M0/M1 checkpoints and unrelated
  workspace changes when creating the M2 checkpoint.

## Blockers

- None blocking the M2 thin-renderer handoff; the fresh isolated reviewer
  returned SHIP.
- M3 is the first incomplete milestone and has not started. Its deterministic
  bird's-eye/capture work is the next boundary. Browser/GPU memory, FPS, and
  cold/warm timing remain unmeasured, and no reusable browser harness was
  committed; these are honest later hardening/measurement limitations, not
  evidence that M3 is complete.

## Exact next action

Start M3 (not yet started): implement deterministic `zup_aabb_v1` bird's-eye
framing plus capture reproducibility with the fixed 1280x720 framebuffer at
pixel ratio 1, camera JSON/PNG validation, and the safe
`outputs/viewer/<capture_id>/` namespace. Do not imply that M3 has started.

## Continuation checklist

1. Read the root `SPEC.md` and `AGENTS.md`, then
   `viewer_project/SPEC.md`, `viewer_project/AGENTS.md`, and this ledger.
2. Inspect `git status`, `git diff`, and `git log`; preserve the modified root
   `SPEC.md` and untracked `editing_agent/` without staging them.
3. Verify the immutable source's size, mtime where available, and SHA-256
   fingerprint before any viewer operation; use a separate output namespace.
4. Continue from the first incomplete milestone, which is M3, and update this
   ledger by appending a dated milestone/checkpoint entry.
