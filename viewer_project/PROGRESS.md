# Viewer Project Progress Ledger

This is an append-only handoff record for the scoped local Gaussian-splat
viewer. It records project state; it does not replace `SPEC.md` or
`AGENTS.md`.

## Current status

- Last updated: 2026-08-30
- Current status: M0 complete; documentation independently reviewed; verdict
  **SHIP** for the M0 documentation milestone only.
- Last checkpoint commit: `99f2ef1` — `checkpoint: define viewer contract and
  progress ledger`. This worker created no commit.
- Current viewer files: `viewer_project/SPEC.md`,
  `viewer_project/AGENTS.md`, and this `viewer_project/PROGRESS.md` (the
  third file).
- M0 froze the logical scene ID, manifest/output boundaries, Z-up convention,
  `zup_aabb_v1`, pinned PlayCanvas `2.3.3` requirement, and acceptance gates.
- The full viewer remains unimplemented. No viewer code, dependency
  installation, generated capture/artifact, source mutation, push, or
  implementation commit exists yet.
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
- Fresh independent review: SHIP for M0 documentation; no implementation
  acceptance gate was represented as passed.

## Acceptance-gate boundary

The viewer-level authenticity, immutability fingerprint, coordinate,
reproducibility, output-hygiene, security, performance, research-boundary,
and real-scene verification gates are pending implementation. M0 only proves
that their contract and scope are documented; it does not prove rendering,
streaming, capture, or performance.

## Milestone updates (append only)

- 2026-08-30 — M0 — Contract and fixture review: complete and independently
  reviewed, verdict SHIP. No scene or code changes were part of M0.
- 2026-08-30 — M0 checkpoint `99f2ef1` — `checkpoint: define viewer contract
  and progress ledger`: documentation-only checkpoint; implementation remains
  pending.
- M1 — Authentic source manifest and bounded stream: pending.
- M2 — Thin interactive renderer: pending.
- M3 — Bird's-eye and capture reproducibility: pending.
- M4 — Hardening and handoff: pending.
- M5 — Explicitly separate later perception work: pending; the viewer remains
  RGB-only even if this later adapter is not implemented.

## Blockers

- None for the M0 documentation handoff.
- Before implementation, the primary agent must establish the real source
  fingerprint and confirm the official pinned PlayCanvas path within the
  stated memory and immutability constraints.

## Exact next action

Start M1: implement the allowlisted manifest and bounded compressed-source
stream, with tests for byte/digest identity, count reporting, loopback
confinement, traversal rejection, bounded response buffering, and source
immutability. Code must start from M1; do not redo M0 documentation review.

## Continuation checklist

1. Read the root `SPEC.md` and `AGENTS.md`, then
   `viewer_project/SPEC.md`, `viewer_project/AGENTS.md`, and this ledger.
2. Inspect `git status`, `git diff`, and `git log`; preserve the modified root
   `SPEC.md` and untracked `editing_agent/` without staging them.
3. Verify the immutable source's size, mtime where available, and SHA-256
   fingerprint before any viewer operation; use a separate output namespace.
4. Continue from the first incomplete milestone, which is M1, and update this
   ledger by appending a dated milestone/checkpoint entry.
