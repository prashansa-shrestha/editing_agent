# Viewer Project Progress Ledger

This is an append-only handoff record for the scoped local Gaussian-splat
viewer. It records project state; it does not replace `SPEC.md` or
`AGENTS.md`.

## Current status

- Last updated: 2026-08-31
- Current status: M0 and M1 complete; M1 was freshly independently reviewed;
  verdict **SHIP** for the M1 authentic source manifest and bounded transport
  milestone.
- Last completed checkpoint commit: `99f2ef1` — `checkpoint: define viewer
  contract and progress ledger`. The new M1 checkpoint is pending creation
  immediately after this ledger update; no M1 hash exists yet, and this worker
  creates no commit.
- Current viewer files: `viewer_project/SPEC.md`,
  `viewer_project/AGENTS.md`, `viewer_project/PROGRESS.md`, and the M1 files
  `src/scene_agent/viewer.py`, `src/scene_agent/webapp.py`, and
  `tests/test_viewer_api.py`.
- M0 froze the logical scene ID, manifest/output boundaries, Z-up convention,
  `zup_aabb_v1`, pinned PlayCanvas `2.3.3` requirement, and acceptance gates.
- M1 establishes only the safe allowlisted manifest and bounded compressed-Ply
  transport: trusted `+Z` configuration, canonical pinned target,
  descriptor-relative `O_NOFOLLOW` traversal, one concurrent operation, reads
  bounded to `<=1 MiB`, and HTTP mutation truncation/validation. The browser
  renderer and capture work remain unimplemented.
- The immutable source remains unchanged. No generated capture/artifact,
  source mutation, push, or M1 checkpoint commit exists yet.
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

## Acceptance-gate boundary

M1 passes the safe manifest/transport boundary: the real source fingerprint,
Gaussian/chunk counts, trusted `+Z` bounds, loopback/path confinement,
descriptor-relative `O_NOFOLLOW` traversal, bounded reads, single-operation
concurrency, and request truncation/validation are covered by the recorded
implementation and tests. M2-M4 still need browser rendering, interactive
controls, real-scene visual verification, bird's-eye framing, capture
reproducibility, and final hardening gates. M1 proves only safe
manifest/transport; it does not prove an interactive renderer, bird's-eye
view, capture, scene understanding, segmentation, semantics, depth, Gaussian
IDs, or editing.

## Milestone updates (append only)

- 2026-08-30 — M0 — Contract and fixture review: complete and independently
  reviewed, verdict SHIP. No scene or code changes were part of M0.
- 2026-08-30 — M0 checkpoint `99f2ef1` — `checkpoint: define viewer contract
  and progress ledger`: documentation-only checkpoint; implementation remains
  pending.
- 2026-08-31 — M1 — Authentic source manifest and bounded stream: complete and
  freshly independently reviewed, verdict SHIP. M1 proves only safe
  manifest/transport; its checkpoint is pending creation immediately after
  this ledger update.
- M1 — Authentic source manifest and bounded stream: complete.
- M2 — Thin interactive renderer: pending.
- M3 — Bird's-eye and capture reproducibility: pending.
- M4 — Hardening and handoff: pending.
- M5 — Explicitly separate later perception work: pending; the viewer remains
  RGB-only even if this later adapter is not implemented.

## Blockers

- None for the M1 manifest/transport handoff.
- M2 is the first incomplete milestone; no browser renderer, interactive
  controls, or visual-render acceptance is represented as passed.

## Exact next action

Start M2: implement a thin browser page using pinned PlayCanvas `2.3.3` to
fetch the M1 manifest and compressed source, then authentically render the
compressed splat with orbit, pan, zoom, and Fit Scene. Do not implement the
bird's-eye view or capture work until M3.

## Continuation checklist

1. Read the root `SPEC.md` and `AGENTS.md`, then
   `viewer_project/SPEC.md`, `viewer_project/AGENTS.md`, and this ledger.
2. Inspect `git status`, `git diff`, and `git log`; preserve the modified root
   `SPEC.md` and untracked `editing_agent/` without staging them.
3. Verify the immutable source's size, mtime where available, and SHA-256
   fingerprint before any viewer operation; use a separate output namespace.
4. Continue from the first incomplete milestone, which is M2, and update this
   ledger by appending a dated milestone/checkpoint entry.
