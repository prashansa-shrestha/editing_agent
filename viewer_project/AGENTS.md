# Viewer Project Instructions

These instructions apply to every file below `viewer_project/`. They are
scoped to the thin Gaussian-splat viewer and supplement the repository-level
`AGENTS.md`; when the two conflict, the user's request and the repository rules
take precedence.

## Communication

- Address the project owner as Prashansa.
- Before changing a repository file, explain what will change and why. Keep
  the explanation simple enough that Prashansa can understand the purpose of
  each change.
- Before running an unfamiliar command, explain in plain language what it
  does and why it is needed. Do not hide important side effects behind a
  one-line command.
- After any repository change, report every changed file, the verification
  performed, commits (if any), and remaining risks.
- When diagnosing a failure, identify the root cause and evidence before
  proposing or implementing a fix.

## Required reading and project boundary

- Read `viewer_project/SPEC.md` completely before planning, implementing,
  reviewing, or documenting viewer work. Read the repository-level `SPEC.md`
  and applicable `AGENTS.md` files as well.
- Keep changes minimal and specific to this viewer. Do not turn this subtree
  into a general Gaussian editor, a SuperSplat clone, or a second research
  pipeline.
- The viewer's first capability is authentic RGB rendering of the existing
  compressed PLY, deterministic Z-up bird's-eye framing, and deterministic
  bird's-eye reference camera/screenshot artifacts. Interactive current-view
  downloads are non-reference exports. Do not expand that scope silently.
- Reuse the official, pinned PlayCanvas Gaussian renderer where practical.
  Never reimplement splat rasterization, sorting, alpha compositing, or
  projection merely for convenience.

## Scientific and claim discipline

- Do not introduce task-specific training, fine-tuning, semantic-field
  optimization, or a new learned component unless Prashansa explicitly
  changes the research scope.
- Treat the viewer as an engineering visualization and reproducibility aid,
  not as a research contribution. Clearly separate engineering capability
  from research evidence in code, UI text, metadata, and reports.
- Interactive viewer RGB is not a research `RenderObservation`. Do not claim
  that the viewer supplies depth, alpha, Gaussian IDs, contribution weights,
  segmentation, object membership, scene graphs, or contribution-aware
  lifting. Those fields require a separately specified perception renderer.
- Never report a mock, placeholder, synthetic scene, oracle-assigned group, or
  user annotation as an authentic render or as a completed perception method.
  Label ground truth, heuristics, model predictions, oracle assistance, and
  user-provided annotations explicitly.
- Do not use `labels.json`, object boxes, or ground-truth assignments as hidden
  input to a claimed viewer or perception method. Geometry-only framing
  metadata is allowed only when it is identified and recorded as such.
- Record assumptions, failure cases, runtime, peak memory, renderer/browser,
  and experimental settings. Preserve reproducibility with fixed camera
  settings, stable JSON, and fixed seeds when randomness is introduced.

## Data safety and security

- Never modify, overwrite, recompress, or use as an output target the original
  Gaussian PLY or any source dataset file. The known compressed source is
  immutable and must be rendered directly where the official loader supports
  it.
- Store server-written reference screenshots and camera metadata only below
  the ignored `outputs/viewer/` namespace. A browser-side non-reference export
  may go only to the user's explicitly selected download destination and must
  never be treated as a server-written reference artifact. Do not commit
  generated screenshots, large scene files, decoded copies, model weights,
  credentials, or private data.
- Every reference capture must use the fixed orthographic `zup_aabb_v1`
  bird's-eye camera and validated server-side writer so the PNG/JSON pair is
  atomic, stays under `outputs/viewer/<capture_id>/`, and cannot overwrite an
  existing capture or source. An interactive current-view or perspective
  image is only a clearly labeled browser-side non-reference export; never
  send it to the reference route, store it in the reference namespace, or
  claim it under the reference capture contract.
- Keep every service loopback-only (`127.0.0.1`, `localhost`, or `::1`). Do not
  bind a viewer convenience mode to `0.0.0.0`, a LAN address, or a public
  interface.
- Use allowlisted logical scene IDs and safe output names. Reject traversal,
  absolute paths, NUL bytes, symlink escapes, directory reads, and arbitrary
  filesystem paths. Never add an arbitrary file-read endpoint.
- Stream the compressed source in bounded chunks. Do not read the whole
  approximately 32 MB PLY into a Python response buffer. Keep manifest and
  capture request sizes bounded, and validate source digest/size before saving
  an artifact.
- Preserve the existing local server's Host/client checks, restrictive CSP,
  same-origin policy, and no-remote-code posture. For every mutating capture
  request, require an exact active-origin `Origin` header plus an unguessable
  per-session CSRF/session token in a dedicated header; reject absent or
  mismatched values. Accept only the explicitly bounded JSON content type and
  body size defined in `SPEC.md`. Do not add permissive CORS, remote CDNs, or
  network model calls without explicit review.

## Development workflow

1. Read the full specifications and inspect relevant files, tests, package
   pins, and current Git status/diff before making a plan.
2. State the root cause when fixing a bug. Implement the smallest reversible
   change that addresses that cause; preserve unrelated user work.
3. Keep viewer code separate from research perception code. Prefer narrow
   adapters and explicit contracts over broad refactors.
4. Add deterministic unit/contract tests for path safety, source streaming,
   manifest/capture serialization, camera math, coordinate conventions, and
   source immutability. Add real-browser and visual checks for authentic
   rendering when the environment supports them.
5. Run relevant formatters, linters, type checks, unit tests, integration tests,
   and visual QA. Do not substitute import checks, a mock render, or an empty
   canvas for the real compressed-scene acceptance test.
6. Inspect the final diff and status. Report exact changed files, test commands
   and outcomes, source-integrity evidence, performance measurements, and
   remaining limitations.

Do not install dependencies or implement code for a documentation-only task.
When implementation is authorized, use the existing lockfile and pinned
PlayCanvas `2.3.3`; ask before adding a dependency that materially changes
memory, network, licensing, or privacy assumptions.

## Camera and renderer rules

- Carry an explicit coordinate-system value. For the known scene, use Z-up:
  +Z is height, XY is the floor plane, and the top-down camera looks along
  -Z with +X screen-right and +Y screen-up.
- Implement the documented `zup_aabb_v1` bird's-eye algorithm exactly or
  version any intentional change. Do not silently assume Y-up, meters, or
  hidden scene bounds.
- Before calculating AABB extents, require every bound to be finite and each
  minimum to be no greater than its matching maximum. Reject inverted bounds
  rather than clamping them. Reject a fully degenerate AABB; accept valid
  partially degenerate bounds only with the fixed epsilon-span and minimum-
  clearance policy documented in `SPEC.md`.
- Record source digest, Gaussian count, camera pose/target/view-up vector,
  projection, clipping, viewport, renderer version, and render settings in
  every reference capture. The `[0, 1, 0]` camera view-up vector must not be
  labeled world-up; world-up remains +Z. The initial reference schema accepts
  only orthographic `birdseye`/`zup_aabb_v1` metadata.
- Keep viewer RGB state semantically empty. A future `RenderObservation`
  adapter must be separate and must document alpha/depth/ID/contribution
  semantics before research use.

## Verification and acceptance

Before calling viewer work complete, verify as applicable that:

- the real 524,508-Gaussian compressed source renders through the official
  pinned PlayCanvas path;
- the source digest, bytes, and size remain unchanged;
- Z-up camera tests, deterministic reload checks on the exact metadata subset
  or digest (not volatile diagnostics), and edge-case bounds tests pass,
  including pre-calculation rejection of inverted/non-finite bounds, rejection
  of a fully degenerate AABB, and deterministic handling of valid partially
  degenerate bounds;
- every reference capture contains a valid fixed bird's-eye PNG and canonical
  camera JSON under ignored output; current-view exports are labeled
  non-reference and never use the reference route or namespace;
- loopback, path-allowlist, request-limit, bounded-stream, exact-Origin,
  CSRF/session-token, and capture-content-type tests pass;
- cold/warm latency, frame rate, RAM, and GPU-memory measurements are recorded
  or explicitly marked unmeasured; and
- visual QA confirms upright framing, visible splats, control behavior, and
  honest failure states.

If any gate fails, leave the work clearly marked incomplete and do not present
the result as a successful research method.

## Git policy

- Before any Git mutation, inspect `git status`, `git diff`, current branch,
  and configured remote. Stage only files belonging to the viewer task.
- Create a local checkpoint commit only after the relevant verification passes.
  Use a concise message beginning with `checkpoint:` and never include
  generated large artifacts, datasets, credentials, or unrelated user work.
- Do not push, force-push, amend, rebase shared history, delete branches/tags,
  or change remotes. A push is prohibited unless Prashansa explicitly
  requests it for the specific branch and remote.
- Never use destructive cleanup or reset commands merely for convenience.

## Stop conditions

Stop and ask Prashansa for direction if:

- a source would need to be overwritten, copied outside approved outputs, or
  exposed through an arbitrary path;
- the official renderer cannot consume the exact compressed source and the
  proposed workaround would change authenticity, memory, or research scope;
- the coordinate convention or bounds cannot be established safely;
- a dependency requires materially more RAM/VRAM, remote access, credentials,
  or a broader license/privacy decision;
- a proposed UI/API change would imply scene understanding or contribution
  evidence that the viewer does not implement; or
- acceptance tests cannot distinguish a genuine render from a mock or oracle
  result.
