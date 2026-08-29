# Milestone 1A compressed-PLY format contract

This repository freezes Milestone 1A as a CPU-only canonicalization and
validation boundary. It reads a compressed Gaussian-splat PLY, writes a
canonical editable binary PLY partial target, and the Python loader then
performs a canonical load, no-op write, and reload validation before any later
finalization. Editing, rendering, and grouping remain outside this milestone.

## Pinned implementation evidence

The decoder uses the CPU data model and decompression routine from exactly
`playcanvas@2.3.3`. The npm package resolves to PlayCanvas engine commit
[`eb1a80f2f40eedd0e978452b4e07a55e5f594902`](https://github.com/playcanvas/engine/commit/eb1a80f2f40eedd0e978452b4e07a55e5f594902).
The relevant upstream sources are
[`ply.js`](https://github.com/playcanvas/engine/blob/eb1a80f2f40eedd0e978452b4e07a55e5f594902/src/framework/parsers/ply.js)
and
[`gsplat-compressed-data.js`](https://github.com/playcanvas/engine/blob/eb1a80f2f40eedd0e978452b4e07a55e5f594902/src/scene/gsplat/gsplat-compressed-data.js).
The exact npm release is recorded without a range in `package.json` and
`package-lock.json`.

The wrapper deliberately validates more strictly than PlayCanvas's browser
loader: only the frozen compressed schema below is accepted, unknown fields
fail, payload length must match exactly, and trailing bytes are rejected.

PlayCanvas is distributed under the [MIT license](https://github.com/playcanvas/engine/blob/eb1a80f2f40eedd0e978452b4e07a55e5f594902/LICENSE).

## Milestone 1A validation gate

The Node decoder validates the compressed header, element counts, payload byte
length, finite chunk values, and finite decoded values before creating the
partial output. The Python side must then load that canonical PLY, validate the
exact 59-property float32 schema and count, write it without edits, reload the
written file, and compare count, property order/types, finite values, and
row-order IDs. Only a successful no-op write/reload is eligible for later
finalization. A decoder output is never presented as an edited or rendered
scene.

Unknown-property policy is deliberately asymmetric. Unknown elements,
properties, types, or list/variable-length properties in the compressed source
are hard errors; they are not guessed at or silently dropped. In the later
canonical Python representation, approved unknown scalar properties may be
carried through a round trip, but list-valued properties are rejected rather
than silently altered. The frozen 59 canonical properties themselves are
always emitted in the exact order below.

## Accepted source schema

The source must be a binary little-endian PLY with exactly these elements and
properties, in this order:

```text
element chunk <ceil(vertex_count / 256)>
property float min_x
property float min_y
property float min_z
property float max_x
property float max_y
property float max_z
property float min_scale_x
property float min_scale_y
property float min_scale_z
property float max_scale_x
property float max_scale_y
property float max_scale_z
property float min_r
property float min_g
property float min_b
property float max_r
property float max_g
property float max_b

element vertex <vertex_count>
property uint packed_position
property uint packed_rotation
property uint packed_scale
property uint packed_color

element sh <vertex_count>
property uchar f_rest_0
...
property uchar f_rest_44
```

`vertex_count` must be positive. The payload is, in order, 18 little-endian
float32 chunk values per chunk, four little-endian uint32 values per vertex,
then 45 uint8 SH values per vertex. The decoder requires the payload to end
exactly after the last SH byte.

## Canonical decoded schema

The partial output is binary little-endian PLY with one `vertex` element. Every
property is a float32 and every row keeps the compressed source row index:

```text
x, y, z,
f_dc_0, f_dc_1, f_dc_2,
f_rest_0 ... f_rest_44,
opacity,
scale_0, scale_1, scale_2,
rot_0, rot_1, rot_2, rot_3
```

The row index is the stable canonical Gaussian ID for this milestone. No
Morton sort or other reordering is performed. The original pre-compression
order is unrecoverable; the canonical ID means “row `i` in the compressed
source and decoded output,” not a recovered pre-compression identity.

The current InteriorGS scene convention is Z-up: X/Y span the floor plane and
Z is height. This is recorded metadata/assumption, not inferred from labels.
Canonical rotations use `rot_0 = w`, `rot_1 = x`, `rot_2 = y`, and
`rot_3 = z`; this is the ordering produced by the pinned PlayCanvas iterator.
The canonical `scale_0..2` values are natural-log scales (the renderer applies
`exp` when it needs linear scale). DC spherical-harmonic values use
`f_dc = (linear_color - 0.5) / 0.28209479177387814`, matching the pinned
PlayCanvas `SH_C0` convention.

PlayCanvas's pinned routine unpacks position and scale with 11/10/11-bit
normalized fields and linearly interpolates them with each row's chunk bounds.
It unpacks rotations using the pinned largest-component convention and maps
the three 15-coefficient SH blocks from bytes with `byte * (8 / 255) - 4`.
Those operations are intentionally delegated to the pinned
`GSplatCompressedData.decompress()` implementation.

Alpha is an 8-bit normalized value from `packed_color`. Its endpoints are
explicitly canonicalized to finite opacity logits: alpha `0` becomes `-40`
and alpha `1` becomes `+40`, matching PlayCanvas 2.3.3. Intermediate alpha
uses the ordinary logit. All output values are checked for finiteness.

Canonicalization is not a byte-exact inverse of the compressed source. It is a
float32 editable representation after quantization unpacking, and therefore
must not be described as restoring the original uncompressed bytes.

## Decoder descriptor and data-safety contract

```text
node scripts/decode_compressed_ply.mjs <source> <output-fd>
```

The Node decoder does not accept or open an output pathname. `output-fd` must
be an inherited descriptor for a new, empty, mode-0600 regular file. The
Python orchestrator creates that unique partial with `O_EXCL` and
`O_NOFOLLOW`, relative to retained directory descriptors rooted at
`outputs/milestone1/`, then passes only the already-open descriptor to Node.
Node writes and syncs that descriptor but never links, unlinks, or renames an
output. Direct CLI callers must provide the descriptor explicitly; ordinary
users should call the Python `decode_compressed_ply` API.

The supplied dataset, including
`/data/Desktop/Prashansa/major/scenebench/data/InteriorGS/0231_840445/3dgs_compressed.ply`,
is immutable and is never opened for writing. All generated artifacts belong
in the ignored `outputs/milestone1/` directory. Python validates the same
retained partial inode, including schema, count, payload length, and
finiteness, before publishing exactly that inode with no-replace
`linkat(AT_EMPTY_PATH)`. Cleanup compares device/inode identity and therefore
never unlinks a partial or final name replaced by another process.
Parent-directory swaps are detected, while all write, publication, and cleanup
authority remains anchored to the originally opened directory. Existing final
files are refused. These primitives currently require Linux `openat`/`linkat`
semantics, which matches the supported Google Colab runtime.

The Node path uses no GraphicsDevice, WebGL, WebGPU, CUDA, or other GPU
resource: its VRAM requirement is zero. It uses CPU typed arrays and is sized
for the supplied 524,508-row scene under a 1 GiB RSS budget. The compressed
source is about 32 MB; the canonical 59-float32 body is about 124 MB, with
additional bounded working arrays. A measured real-scene decode peaked at
about 234 MB RSS. These are engineering limits to record with experiments,
not a claim that every larger scene fits.

Malformed headers, wrong property types/order, unknown elements/properties,
count mismatches, non-finite chunk values, impossible payload lengths, and
non-finite decoded values are hard errors with a diagnostic on stderr.
