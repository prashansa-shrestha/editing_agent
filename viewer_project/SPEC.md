# Scoped Gaussian-Splat Viewer Specification

Status: proposed engineering specification; implementation is intentionally pending
Owner: Prashansa
Scope: `viewer_project/` and the smallest integration surface needed to serve it

## 1. Purpose and scope

This project defines a thin, local-first viewer for inspecting the existing 3D
Gaussian Splatting scene used by the Scene Agent project. It gives a researcher
an authentic interactive RGB view, a deterministic Z-up bird's-eye view, and
reproducible bird's-eye reference camera and screenshot artifacts. It is an
inspection and reproducibility aid around the scene I/O work; it is not a new
renderer or a research contribution on its own.

The first supported scene is the immutable compressed file:

```text
/data/Desktop/Prashansa/major/scenebench/data/InteriorGS/0231_840445/3dgs_compressed.ply
```

The known source has 524,508 Gaussians, is approximately 32.1 MB on disk, uses
the PlayCanvas/splat-transform compressed PLY layout with 2,049 quantization
chunks and 45 higher-order spherical-harmonic fields, and is known to be Z-up:
X/Y span the floor plane and Z is height. The viewer must render this source
directly where the official PlayCanvas loader supports it. A canonical decoded
copy (about 124 MB for this scene) is not a prerequisite for viewing and should
not be created merely to make the viewer work.

This specification covers the viewer's contract, not its implementation. No
code, dependency installation, scene conversion, screenshot, or generated
artifact is required by this document-only milestone.

## 2. Status and claim boundary

### 2.1 What the viewer may claim

After the acceptance gates pass, the project may claim that it can:

- stream the supported compressed source from a loopback-only local service;
- display that source using the pinned official PlayCanvas Gaussian renderer;
- provide ordinary orbit, pan, zoom, reset, and inspection controls;
- generate a reproducible Z-up bird's-eye camera from recorded scene bounds; and
- save a deterministic bird's-eye reference screenshot together with enough
  camera, source, and renderer metadata to reproduce the capture within
  documented visual tolerance.

These are engineering capabilities. A screenshot demonstrates that a view was
captured; it does not demonstrate object-group quality, edit quality, or a
scientific result.

### 2.2 What the viewer must not claim

The initial viewer is RGB-only interactive visualization. It must not present
an RGB framebuffer as a `RenderObservation` or imply that it supplies any of
the following:

- per-pixel depth or alpha;
- Gaussian IDs, rasterization visibility, or contribution weights;
- 2D segmentation masks or 3D membership scores;
- object categories, instances, groups, or scene-graph nodes;
- object poses, relations, support, collision, or physical affordances; or
- contribution-aware lifting from images to Gaussians.

The later research renderer may produce a separate `RenderObservation` with
`rgb`, camera, and optional depth, alpha, Gaussian IDs, and contribution
weights. That interface belongs to the perception pipeline and must remain
separate from the viewer RGB output. A future implementation that cannot
provide a particular field must report it as unavailable rather than fabricate
or infer it from a screenshot.

The viewer is not a clone of SuperSplat, is not a general-purpose Gaussian
editor, and is not a separate paper method. It does not edit, delete, duplicate,
recolor, group, segment, or optimize Gaussians. It also does not bypass the
research question by using `labels.json`, object boxes, or an oracle membership
assignment as a hidden visualization input. If an engineering demo later shows
an annotation or oracle group, the UI and saved metadata must label it as
user-provided or oracle-assisted.

### 2.3 Explicit non-goals

The initial viewer does not attempt to:

- edit or export Gaussian attributes, create transactions, or provide undo;
- reconstruct a missing scene, repair a corrupt source, or convert every PLY
  variant;
- provide semantic search, object selection, segmentation, tracking, or scene
  graphs;
- produce research `RenderObservation` fields, lift masks to Gaussians, or
  estimate visibility/contribution evidence; or
- replace the local Scene Agent server, its I/O tests, or the later research
  renderer with a second implementation.

## 3. Functional requirements

### 3.1 Source opening and authenticity

1. Open the configured compressed PLY by a stable logical scene identifier,
   initially `interiorgs_0231_840445`, rather than exposing arbitrary filesystem
   paths to the browser.
2. Inspect the source schema and fingerprint it before serving. The manifest
   must report the SHA-256 digest, byte size, format, Gaussian count, chunk
   count when available, and coordinate convention.
3. Render the compressed source with the pinned PlayCanvas dependency already
   declared by the repository (`playcanvas` version `2.3.3`) and the official
   Gaussian-splat loading/rendering path. Do not reimplement splat projection,
   sorting, alpha compositing, or rasterization.
4. Preserve the compressed source byte-for-byte. The viewer may read it and
   compute a digest, but never writes to it, rewrites it, or uses it as an
   output target.
5. Refuse unsupported, truncated, malformed, non-finite, or changed input with
   a visible error and no partial capture. There must be no silent fallback to
   a point cloud, an empty canvas, a mock mesh, or a synthetic scene.
6. Show the source logical ID, format, Gaussian count, coordinate system, and
   digest prefix in the UI so a human can tell which scene is on screen.

### 3.2 Interactive RGB view

The canvas must support the following operations against the scene as loaded:

- orbit around a recorded target using a world-Z-up trackball/orbit model;
- pan in the camera's screen plane;
- zoom by changing orbit distance or the approved projection parameter;
- reset to a deterministic default view;
- activate the deterministic bird's-eye view;
- show or hide a non-semantic XYZ axis/ground-grid aid, if the aid is useful;
- display loading, ready, and failure states without claiming readiness early;
- create a reference capture after resetting to the deterministic bird's-eye
  camera and fixed reference framebuffer; and
- optionally download the current interactive view and camera as a clearly
  labeled non-reference export.

The axis/grid aid is a coordinate aid only. It must not be interpreted as
occupancy, a room boundary, a semantic label, or a predicted object extent.
Controls must be keyboard reachable, visibly disabled while loading, and must
not expose edit buttons or controls that suggest unsupported behavior.

Interactive rendering is allowed to adapt resolution or frame rate when the
GPU is constrained, but it must never silently change the source, coordinate
system, Gaussian count, or renderer path.

### 3.3 UI behavior and state transitions

The UI must make its state explicit: `idle` (no scene selected), `loading`
(manifest or stream in progress), `ready` (authentic RGB renderer settled),
`capturing` (camera and framebuffer frozen), and `error` (the reason and a
safe recovery action are shown). A stale frame must not be labeled `ready` for
a different source digest. The source panel remains visible in every state,
and the reference-capture and non-reference-export actions are disabled until
the renderer reports a settled frame.

The reset and bird's-eye controls must be separate and clearly named. The UI
must also distinguish `Save bird's-eye reference capture` from `Export current
view (non-reference)`. The reference action resets and settles the documented
bird's-eye camera before capture; it never serializes an arbitrary interactive
camera under the reference contract. A camera change updates an on-screen
camera summary but does not write a reference file. Errors identify whether
the manifest, stream, renderer, camera bounds, or output write failed; they do
not reveal server stack traces or arbitrary paths. The UI may show load and
frame timing diagnostics, but labels them as measurements rather than quality
or perception scores.

### 3.4 Reference capture contract

A reference capture is a pair of files below the ignored viewer output
namespace:

```text
outputs/viewer/<capture_id>/screenshot.png
outputs/viewer/<capture_id>/camera.json
```

`capture_id` is a short server-generated or strictly validated identifier. It
must not be interpreted as a path. In the initial schema, every reference
capture is the deterministic `zup_aabb_v1` bird's-eye view: the camera is
orthographic, `capture.view_kind` is exactly `birdseye`, and
`capture.camera_algorithm` is exactly `zup_aabb_v1`. The PNG is the settled
bird's-eye canvas at the fixed reference framebuffer size. The JSON is
canonical UTF-8 JSON with stable key ordering and at least these fields:

```json
{
  "schema_version": 1,
  "source": {
    "scene_id": "interiorgs_0231_840445",
    "sha256": "...",
    "size_bytes": 0,
    "gaussian_count": 524508,
    "format": "playcanvas_compressed_ply"
  },
  "coordinate_system": {
    "world_up": "+Z",
    "floor_axes": ["+X", "+Y"],
    "units": "scene_units"
  },
  "camera": {
    "projection": "orthographic",
    "position": [0.0, 0.0, 0.0],
    "target": [0.0, 0.0, 0.0],
    "view_up": [0.0, 1.0, 0.0],
    "near": 0.0,
    "far": 0.0,
    "orthographic_height": 0.0,
    "viewport_px": [1280, 720],
    "pixel_ratio": 1
  },
  "render_config": {
    "renderer": "playcanvas-official-gaussian",
    "playcanvas_version": "2.3.3",
    "background_rgba": [0, 0, 0, 1]
  },
  "capture": {
    "view_kind": "birdseye",
    "camera_algorithm": "zup_aabb_v1"
  }
}
```

The example values are placeholders for the schema shape, not an acceptable
capture. A real file must contain finite values and the measured source
metadata. A reference writer must reject a perspective projection, another
`view_kind`, another camera algorithm, or camera values that do not match the
deterministic bird's-eye result for the validated source bounds and fixed
framebuffer. It may add browser, operating-system, GPU, color-space, exposure,
and renderer configuration fields in a `diagnostics` object. It must not
include an absolute source path or untrusted user text when a logical ID is
sufficient. Capture time may be stored as a diagnostic field, but it must not
participate in the deterministic camera digest.

For reproducibility, the deterministic metadata subset is the canonical JSON
projection containing `schema_version`, `source`, `coordinate_system`, the
complete `camera` object, the stable `render_config` fields, and
`capture.view_kind`/`capture.camera_algorithm`. The projection excludes
`capture_id`, wall-clock times, browser/OS/GPU identifiers, load/frame timing,
memory readings, request/session IDs, and other `diagnostics`. The writer must
store a `deterministic_camera_digest` (SHA-256 of the canonical UTF-8
projection, with the digest field itself excluded) and tests must compare this
subset or digest exactly. Full JSON files may differ in diagnostic fields;
PNGs are compared separately using the documented visual tolerance.

The fixed reference framebuffer is 1280 x 720 at pixel ratio 1 unless a later
version changes the schema and acceptance fixtures. CSS scaling must not alter
the recorded framebuffer dimensions. An interactive current-view image or a
capture at another size may be downloaded only as a clearly labeled
non-reference export. Such an export is outside this schema, must not use the
reference route or `outputs/viewer/<capture_id>/` reference namespace, and
must not be claimed as reproducible under this contract.

## 4. Architecture and integration contract

The viewer consists of four small responsibilities:

```text
browser viewer UI
    -> loopback viewer API
        -> allowlisted source manifest/fingerprint service
        -> bounded compressed-source stream
    -> PlayCanvas 2.3.3 official Gaussian loader and renderer
    -> deterministic bird's-eye reference capture
        -> validated outputs/viewer artifacts
```

### 4.1 Browser layer

The browser owns the canvas, controls, camera state, loading progress, and PNG
capture. It must load JavaScript and the PlayCanvas package from the local
project bundle. Remote CDNs, remote fonts, remote images, and network model
services are not part of the initial viewer. The browser must only request
viewer routes and same-origin static assets.

### 4.2 Local service layer

The existing local server pattern in
`src/scene_agent/webapp.py` (including its static-serving boundary near
`_serve_static`) is the integration point. A future implementation may add
viewer-specific routes, but it must not weaken the current loopback and path
validation behavior. Proposed route semantics are:

- `GET /api/viewer/manifest?scene_id=<id>` returns bounded JSON metadata and a
  short-lived stream token or an opaque stream URL for that allowlisted scene;
- `GET /api/viewer/source/<id>` streams the exact compressed bytes for the
  allowlisted scene; and
- `POST /api/viewer/captures` accepts only a validated deterministic
  bird's-eye reference PNG and camera JSON, then stores the pair under
  `outputs/viewer/<capture_id>/` through the validated server-side reference
  writer.

The reference capture route accepts only `Content-Type: application/json;
charset=utf-8`, requires a declared body length no larger than 24 MiB, and
accepts a bounded base64-encoded PNG plus camera metadata (the decoded PNG is
at most 16 MiB). It rejects missing, different, or unbounded/chunked content
types, validates the reference camera schema and values against the trusted
bounds, and validates the PNG before atomically committing the JSON/PNG pair.
It rejects arbitrary current-view or perspective payloads. A browser-only
download is a non-reference export: it may be offered for convenience, but it
cannot satisfy the required
`outputs/viewer/<capture_id>/` location, atomic pair, ignored-output policy,
or server-side overwrite protection.

These names are a contract proposal, not an instruction to edit the existing
server in this documentation task. The implementation may choose equivalent
same-origin names if it preserves the properties below.

The manifest operation may inspect a file and produce small JSON, but the
source route must stream from a file descriptor in bounded chunks (for example
256 KiB to 1 MiB). It must set an accurate content length and digest metadata,
and it must never read the approximately 32 MB source into a Python response
buffer. HTTP range requests are optional; if supported, ranges must be bounded,
validated, and covered by tests. The source response must not be transparently
recompressed or altered because byte identity and digest verification matter.

### 4.3 Renderer layer

The renderer adapter passes the compressed stream or same-origin URL to the
official pinned PlayCanvas Gaussian asset path. It owns no semantic state and
must not attach categories, object IDs, segmentation masks, or scene-graph
membership to the renderable. If the official loader cannot consume the exact
source, the adapter must report that incompatibility and stop; it must not
quietly invent a different representation.

### 4.4 Capture/output layer

The reference capture layer verifies that the loaded source digest still
matches the manifest, resets and settles the deterministic bird's-eye camera,
snapshots the camera and render configuration, and writes a PNG plus JSON
atomically under `outputs/viewer/`. It rejects any other view kind or
projection. Output names are validated as short relative names. Partial files
are removed or quarantined as failed artifacts; the source is never a
destination. The existing repository ignore rule for `outputs/` is part of
this policy, but tests must still check that no generated artifact is staged
or committed. An interactive export remains browser-local and outside this
reference writer and namespace.

### 4.5 Separation from research perception

The viewer may share a camera serialization format with future perception code,
but it must not share a semantic output object. A later renderer-observation
adapter can consume the same immutable source and camera metadata and write to
a separate namespace such as `outputs/render_observations/`. RGB screenshot
files are not a substitute for Gaussian IDs, alpha/depth, or contribution
weights.

## 5. Inputs and outputs

### 5.1 Inputs

Required inputs:

- one allowlisted compressed PLY source;
- a verified source manifest with count, digest, schema, and Z-up metadata;
- a browser viewport and the fixed reference capture configuration; and
- user camera gestures or the deterministic bird's-eye command.

Optional metadata inputs:

- occupancy bounds, if they are explicitly designated as geometric framing
  metadata; and
- a user-supplied camera target, if it is recorded in the capture metadata.

Occupancy and structure files may help frame an engineering view, but they are
not segmentation or Gaussian-membership input. `labels.json` and any
ground-truth object assignment are outside the initial viewer input contract.

### 5.2 Outputs

The viewer produces:

- an on-screen RGB framebuffer;
- a deterministic bird's-eye reference PNG and canonical camera metadata tied
  to the source digest; optionally, a clearly labeled non-reference
  interactive export;
- a small manifest/status response; and
- diagnostics such as load time, frame timing, and measured memory where the
  browser/runtime can report them.

It does not produce an edited PLY, canonical decoded PLY, segmentation mask,
object membership array, scene graph, edit transaction, or research
`RenderObservation` during this milestone.

## 6. Coordinate and camera conventions

### 6.1 World convention

The known InteriorGS scene is Z-up. World +Z is height, and the floor plane is
the XY plane. The viewer must carry an explicit coordinate-system value in the
manifest and capture metadata. A generic input with an unknown or conflicting
up axis must not silently be treated as Z-up; it must either be rejected for
the bird's-eye operation or be opened in an explicitly labeled manual view.

For the top-down camera, world +X is screen-right and world +Y is screen-up;
the camera looks along -Z. The axis aid must show this mapping. Scene units are
preserved as `scene_units` unless trusted metadata establishes a metric scale.
The viewer must never claim that an arbitrary scene unit is a meter.

### 6.2 Interactive orbit

The orbit target is a recorded world-space point. Yaw rotates around +Z,
elevation is measured from the XY plane, and distance is a positive scalar in
scene units. Elevation is clamped away from exactly +/-90 degrees for ordinary
orbit views to avoid a singular trackball; the bird's-eye view uses a fixed
top-down orientation instead. Pan translates target and camera together in
the camera screen plane. Every resulting camera state is finite and is
serialized as position, target, view-up vector, projection, clipping planes,
and viewport.

### 6.3 Reproducible Z-up bird's-eye algorithm

The first implementation must use the following deterministic procedure,
identified by `zup_aabb_v1` in metadata:

1. Obtain a trusted world-space axis-aligned bounding box (AABB) for the
   renderable, preferably from the validated manifest or the approved
   occupancy metadata. Do not use object labels to compute it. Let
   `b_min = (x_min, y_min, z_min)` and `b_max = (x_max, y_max, z_max)`.
2. Before any subtraction or extent calculation, require all six bound values
   to be finite and require `x_min <= x_max`, `y_min <= y_max`, and
   `z_min <= z_max`. Reject a non-finite or inverted AABB as invalid framing
   metadata; never clamp a negative extent to zero. Then compute
   `e_x = x_max - x_min`, `e_y = y_max - y_min`, `e_z = z_max - z_min`, and
   center `c = b_min + (b_max - b_min) / 2` using finite, overflow-safe
   arithmetic. Reject if a derived extent or center is non-finite. If all
   three extents are zero, reject the fully degenerate AABB with a framing
   error because it cannot prove authentic framing. A valid AABB with only one
   or two zero extents remains eligible for the deterministic policy below.
3. For a reference viewport of width `w` and height `h`, set
   `aspect = w / h` and fixed framing margin `m = 1.10`. Set the orthographic
   vertical span to:

   ```text
   span_y = m * max(e_y, e_x / aspect, epsilon)
   ```

   where `span_y` is the full vertical span and `epsilon` is the fixed positive
   scene-unit fallback `1e-6`. Thus a valid partially degenerate AABB with
   `e_x = e_y = 0` and `e_z > 0` uses `m * epsilon` for `span_y`; a zero extent
   on only one floor axis is framed by the other floor-axis extent. There is
   no fully degenerate fallback because that case is rejected in step 2.
   PlayCanvas's orthographic camera property is a half-height, so require
   `camera.orthoHeight = span_y / 2`. The capture field
   `camera.orthographic_height` stores this PlayCanvas half-height, not the
   full `span_y`; an implementation may additionally record `span_y` as
   `orthographic_span_y` for auditability. This fits the XY AABB with the same
   margin for every reference viewport aspect.
4. Set an above-scene clearance

   ```text
   d = max(e_x, e_y, e_z, 1.0)
   z_camera = z_max + 2 * d
   ```

   and camera position `p = (c_x, c_y, z_camera)`. Set target `t = c`, camera
   view-up vector `v = (0, 1, 0)`, and the fixed top-down orientation looking
   along -Z. `v` is a camera view-up vector, not the world-up vector; world up
   remains +Z. Orthographic projection means changing the clearance does not
   change XY framing, while the generous clearance leaves room for the
   complete Z extent between the clipping planes.
5. Set clipping distances from the actual AABB rather than arbitrary defaults:

   ```text
   near = max(1e-4, z_camera - z_max - d / 2)
   far  = z_camera - z_min + d / 2
   ```

   If the derived values are non-finite or `far <= near`, reject the camera and
   show a framing error. Record the final finite values.
6. Reset projection, viewport, background, and renderer settings to the fixed
   capture configuration before a reproducibility capture. Render one settled
   frame after asset loading; do not capture the loading spinner or a partially
   uploaded asset.

This algorithm is a framing heuristic, not a visibility or occupancy method.
It deliberately does not use semantic labels, masks, Gaussian contribution
weights, or learned predictions. If the source bounds are unavailable, the
viewer may offer manual orbit controls but must not pretend that its fallback
camera is the reproducible bird's-eye result.

## 7. Safety and security boundary

All viewer services are local-only. The implementation must:

- bind only to `127.0.0.1`, `localhost`, or `::1`, and reject wildcard,
  LAN-facing, and public addresses;
- validate the HTTP Host header and require loopback clients in the same
  manner as the existing local server;
- serve only same-origin viewer assets and disable permissive CORS;
- require every mutating capture request to carry an `Origin` header that
  exactly matches the active viewer origin (scheme, host, and port), rejecting
  an absent, `null`, or mismatched origin; do not use `Referer` as a fallback;
- issue an unguessable per-session CSRF token through the same-origin viewer
  handshake and require it in a dedicated header (for example,
  `X-Viewer-CSRF`) on every capture write. Bind the token to the session and
  compare it in constant time; reject absent, expired, malformed, or
  mismatched tokens, and never put the token in a URL or log it;
- accept the reference capture route only as bounded
  `application/json; charset=utf-8` with a declared `Content-Length` no larger
  than 24 MiB. Reject missing/different content types and unbounded/chunked
  bodies before parsing;
- keep a restrictive content-security policy: local scripts/styles only,
  `connect-src 'self'`, no objects, no inline or remote code, and no remote
  images or fonts unless a later review explicitly permits them;
- map a logical scene ID to an allowlisted configured file, reject NUL bytes,
  absolute paths, `..`, path separators in IDs, symlink escapes, directories,
  and non-regular files;
- avoid returning absolute server paths or arbitrary directory listings to the
  browser;
- expose only the manifest, exact source stream, status, and validated capture
  operations; never expose an arbitrary file-read route;
- bound request bodies, PNG sizes, stream ranges, capture IDs, and concurrent
  expensive operations;
- verify source size, schema, and digest before and after a long stream when
  practical, aborting a capture if the source changes; and
- avoid logging source contents, credentials, complete paths, or arbitrary
  request bodies.

The compressed PLY is already a binary payload. Set an explicit content type,
`Content-Length`, `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`,
and a digest header or manifest field. Do not enable a file endpoint that can
be repurposed to read `labels.json`, SSH material, environment files, or any
file outside the configured scene allowlist.

Capture writes are the only viewer mutation. They must resolve beneath
`outputs/viewer/`, use safe names, and write atomically. A malformed capture
request must not overwrite an existing capture or source. The viewer has no
user-authentication boundary, so loopback confinement, exact Origin checking,
and the unguessable CSRF/session token are all required; accidental remote
binding or bypass of any one of these checks is a security failure, not a
deployment option.

## 8. Performance and memory budget

The budget is a target to measure on a documented reference machine, not a
result to assume. Record cold and warm timings, browser process RSS where
available, renderer/GPU memory where available, viewport size, browser,
PlayCanvas version, and GPU. Keep the compressed source path as the baseline.

Initial targets for the 524,508-Gaussian scene are:

- source transfer and manifest path add no more than 32 MiB to local service
  RSS above its idle baseline; the service must use bounded streaming chunks,
  not a 32 MB response buffer;
- browser/renderer working-set increase should remain at or below 512 MiB
  above the browser baseline, and GPU allocation should target at most 512 MiB
  when the browser exposes a measurement; and
- first usable frame should arrive within 10 seconds cold and 3 seconds warm
  on the documented reference machine, with at least 20 FPS at the 1280 x 720
  interactive viewport after loading. These are engineering goals and must be
  reported as pass, fail, or unmeasured.

The root project's decoder target is 512 MiB with a 1 GiB hard limit, but the
viewer should not invoke that decoder for the initial render. If an official
renderer path requires a canonical representation, the implementation must
measure its memory and stop before exceeding the approved budget; it must not
silently duplicate the compressed source and approximately 124 MB decoded
scene several times. A low-memory fallback may lower framebuffer resolution or
disable optional axes/grid aids, but may not replace authentic splat rendering
with a mock.

## 9. Testing and visual QA

Tests must cover the real contract as well as small fixtures. Synthetic files
are appropriate for parser and security tests, but never sufficient for the
authentic-rendering acceptance gate.

### 9.1 Deterministic/unit tests

- Validate manifest fields and the known count/digest/format for a test source.
- Verify that a stream's bytes and digest exactly match the source, including
  chunk boundaries and any supported range responses.
- Exercise rejection of path traversal, absolute paths, NULs, symlinks,
  directories, wrong extensions, unknown scene IDs, and source mutation.
- Check the bird's-eye math for normal extents, non-16:9 aspect ratios,
  negative coordinates, and each valid partially degenerate case: one zero
  extent, two zero extents, and `e_x = e_y = 0` with `e_z > 0`, which must use
  the fixed epsilon span. Reject every inverted-axis permutation and every
  non-finite bound before extent calculation, and reject a fully degenerate
  AABB without producing a reference camera or capture. Test unknown
  coordinate systems separately.
- Assert finite camera values, positive clipping range, +Z world-up metadata,
  and the expected +X/+Y screen orientation.
- Validate reference capture JSON schema, stable ordering, safe capture names,
  source digest linkage, fixed viewport, exact `birdseye` view kind,
  `zup_aabb_v1` algorithm, orthographic projection, and refusal to overwrite
  source or an existing capture. Reject perspective, arbitrary current-view,
  and otherwise non-reference payloads at the reference route.
- Exercise loopback bind/Host/client checks, request-size limits, and the
  absence of permissive CORS. For capture writes, reject absent, `null`, and
  mismatched `Origin` values; absent, expired, and mismatched CSRF/session
  tokens; wrong or missing content types; and missing, oversized, or chunked
  body lengths. A valid same-origin token request must write one atomic pair
  and must not overwrite an existing pair.

### 9.2 Browser/integration tests

Using the pinned local PlayCanvas package and a browser automation harness:

- load the real compressed PLY through the local stream route and assert the
  official Gaussian asset reports ready;
- assert that the canvas contains non-background rendered splats and that the
  reported Gaussian count is 524,508 for the known source;
- orbit, pan, zoom, reset, keyboard-access controls, and return to bird's-eye;
- after an arbitrary interactive camera change, invoke `Save bird's-eye
  reference capture`, assert that it resets and settles `zup_aabb_v1`, and
  verify the fixed-size PNG and JSON are both present and linked to the same
  source digest;
- invoke `Export current view (non-reference)` and assert that it is clearly
  labeled, does not call the reference route, and creates no reference output
  pair;
- reload from the same manifest and compare the deterministic camera metadata
  subset or `deterministic_camera_digest` exactly; compare screenshots
  separately with a documented pixel/perceptual tolerance because GPU,
  browser, and color-management differences can affect pixels; and
- confirm the browser makes only same-origin requests and that the viewer UI
  contains no segmentation, object-group, edit, or contribution claims.

### 9.3 Manual visual QA checklist

On the reference scene, a reviewer must check that:

1. The scene is recognizably rendered as Gaussian splats, not a placeholder
   point cloud or empty canvas.
2. The initial and bird's-eye views are upright under Z-up; floor features lie
   in XY and height increases toward +Z.
3. The validated AABB is framed with visible margin and is not clipped by
   near/far planes. The reference result is stable after reload.
4. Orbit and pan feel continuous, reset returns to the same camera, and no
   control mutates the PLY.
5. Loading, corrupt-source, unsupported-renderer, and insufficient-memory
   errors are honest, actionable, and do not leave misleading screenshots.
6. The bird's-eye reference screenshot and metadata can be traced to the
   source digest and capture configuration without opening arbitrary files;
   an interactive export is visibly labeled non-reference.

Visual QA findings must name the scene, browser/GPU, viewport, renderer
version, and failure mode. A rendered screenshot alone is not a test report.

## 10. Milestones

### M0 — Contract and fixture review

Freeze the source logical ID, manifest fields, output namespace, coordinate
conventions, camera algorithm version, and acceptance fixtures. Confirm that
the package lock pins PlayCanvas 2.3.3. No scene or code changes are part of
this documentation milestone.

### M1 — Authentic source manifest and bounded stream

Add only the allowlisted manifest and compressed-source stream integration.
Prove byte/digest identity, count reporting, loopback confinement, traversal
rejection, bounded response buffering, and source immutability.

### M2 — Thin interactive renderer

Connect the browser canvas to the official pinned PlayCanvas Gaussian path.
Prove that the real 524,508-Gaussian source renders interactively and expose
only RGB state and ordinary camera controls.

### M3 — Bird's-eye and capture reproducibility

Implement `zup_aabb_v1`, fixed capture configuration, camera JSON validation,
PNG capture, and the output namespace. Prove exact camera metadata repeatability
and visual tolerance on reload.

### M4 — Hardening and handoff

Complete security tests, performance measurements, visual QA, failure-state
behavior, and a short runbook. Freeze the viewer contract before any future
perception adapter uses its camera metadata.

### M5 — Explicitly separate later perception work

If research work needs depth, alpha, Gaussian IDs, or contribution weights,
design and test a separate `RenderObservation` adapter. Do not enlarge the
viewer claim silently. The viewer remains complete at M4 even if M5 is not
implemented.

## 11. Acceptance gates

The viewer milestone is complete only when all applicable gates are evidenced:

- **Authenticity:** the real compressed source, with 524,508 Gaussians, is
  rendered through the pinned official PlayCanvas Gaussian path; no mock,
  oracle, canonical-only substitute, or silent fallback is involved.
- **Immutability:** source SHA-256, size, mtime where available, and a
  byte-level read check remain unchanged through load, interaction, and
  capture. No source or dataset file is an output.
- **Coordinate correctness:** the manifest records Z-up, the axis aid and
  bird's-eye view are upright, inverted and non-finite bounds are rejected
  before extent calculation, fully degenerate bounds are rejected, and valid
  partially degenerate cases follow the documented epsilon policy.
- **Reproducibility:** every reference capture is the fixed orthographic
  `zup_aabb_v1` bird's-eye view. The same source digest, renderer version,
  camera algorithm, render settings, viewport, and bounds produce identical
  deterministic camera metadata (or digest); volatile diagnostics may vary,
  and PNGs match within the documented visual tolerance.
- **Output hygiene:** reference captures are below ignored `outputs/viewer/`;
  interactive current-view images are clearly non-reference and never enter
  the reference route or namespace; metadata does not disclose arbitrary
  absolute paths; no generated large artifact is staged or committed.
- **Security:** only loopback clients can use the service, source IDs are
  allowlisted, traversal/symlink/read-probe tests pass, and the source route
  streams without buffering the full PLY in the Python server. Mutating
  capture requests also require an exact same-origin `Origin` and a valid
  unguessable CSRF/session token with the bounded JSON content type.
- **Performance:** cold/warm latency, frame rate, server RSS, browser RSS, and
  GPU memory are measured or explicitly marked unmeasured against the targets.
- **Research boundary:** UI, API, and metadata explicitly state RGB-only
  visualization; no scene understanding, segmentation, grouping, graph, or
  contribution-aware lifting is claimed.
- **Verification:** automated tests and the manual visual checklist pass on
  the real scene, and the exact changed files and remaining risks are reported.

## 12. Failure cases and required behavior

- **Missing source or unknown scene ID:** return a bounded not-found error and
  show no stale canvas as if it were the requested scene.
- **Malformed/truncated/schema-incompatible PLY:** reject before rendering;
  do not decode, repair, or substitute a point cloud.
- **Source changes during load/stream:** detect a digest/size mismatch,
  invalidate the manifest, and require a fresh open. Never save a capture as
  though it represented the old source.
- **Unknown or conflicting up-axis:** allow only explicitly labeled manual
  inspection; disable the reproducible Z-up command.
- **Bounds absent:** disable reference capture, report a framing error, and
  offer a manual camera only if the renderer can safely initialize one.
- **Bounds non-finite or inverted:** reject them before extent calculation,
  disable reference capture, and report invalid framing metadata; never clamp
  a negative extent to zero.
- **Bounds fully degenerate:** reject the reference camera and capture with a
  framing error. Do not substitute a `1.0` scene extent. Valid partially
  degenerate bounds use the fixed epsilon span and `1.0` minimum clearance
  documented by `zup_aabb_v1`.
- **PlayCanvas loader or WebGL failure:** show the exact supported-path
  limitation, record the browser/runtime diagnostic, and do not claim an
  authentic render.
- **Insufficient CPU/GPU memory or slow frame rate:** report measured limits,
  optionally reduce viewport/diagnostic aids, and stop safely if the budget is
  exceeded. Never invoke an unapproved high-memory decode silently.
- **Capture failure, non-reference payload, or oversized request:** reject an
  arbitrary current-view/perspective payload at the reference route, leave no
  misleading partial pair, preserve existing captures, and explain whether
  camera validation, metadata, PNG, or the server write failed.
- **Cross-origin or unprotected capture request:** reject absent, `null`, or
  mismatched `Origin` values and absent/mismatched/expired CSRF/session tokens
  before parsing or writing. Reject non-JSON or unbounded bodies as well.
- **Loopback/security violation:** reject the request and keep the service
  local. A convenience mode that binds to all interfaces is not permitted.

## 13. Reproducibility record

Every benchmark or visual report must record:

- source logical ID, SHA-256, size, schema, Gaussian count, and coordinate
  convention;
- camera algorithm version, AABB values, margin, clipping values, projection,
  camera pose, target, view-up vector, and viewport/pixel ratio;
- PlayCanvas package version, browser version, operating system, GPU/WebGL
  backend, render settings, and any resolution adaptation;
- cold/warm load definition, elapsed time, steady-state frame rate, peak RSS,
  and GPU-memory measurement method or `unmeasured`; and
- output paths relative to `outputs/viewer/`, capture JSON, screenshot
  comparison tolerance, and a list of known visual differences.

Use fixed numeric formatting and stable JSON ordering. Do not use random
camera jitter. Any future stochastic diagnostic must record its seed and must
not affect the deterministic bird's-eye path.

## 14. Handoff to later perception work

The viewer hands off three stable facts: an immutable source digest, a camera
serialization with an explicit Z-up convention, and an RGB screenshot for
human inspection. It does not hand off object membership or semantic labels.

When the perception pipeline is ready, its renderer-observation adapter should
consume the same source and camera metadata and emit a separate observation
record such as:

```text
RenderObservation {
  view_id,
  rgb,
  camera,
  alpha | none,
  depth | none,
  gaussian_ids | none,
  contribution_weights | none
}
```

That adapter must document rasterizer semantics, projected Gaussian footprints,
occlusion/alpha compositing, missing visibility, and contribution weighting
before mask lifting is evaluated. The viewer's screenshot can be a qualitative
aid or a user-annotation surface, but it cannot be treated as evidence
that the multiview mask-to-Gaussian method works. Research evaluation must
label ground truth, heuristics, predictions, and user annotations separately.

The handoff is successful when a later perception worker can reproduce the
same camera from metadata, verify the source digest, and request richer fields
without changing the viewer's RGB-only contract or exposing arbitrary files.
