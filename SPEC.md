# 3D Scene Agent: Development and Research Specification

Status: authoritative starting specification  
Project owner: Prashansa  
Target: full research submission in approximately six weeks  
Primary constraint: no training or fine-tuning of a model; local-first and resource-constrained execution

## 1. Purpose

Build a training-free system that accepts an existing 3D Gaussian Splatting scene and a natural-language edit instruction, identifies the intended object, performs a deterministic object-level edit, verifies the result, and preserves an exact undo record.

The system should eventually support instructions such as:

> Move the chair 50 cm to the right of the table.

The language model is a planner and reference resolver. It must not directly generate Gaussian parameters, arbitrary Python, or unvalidated file mutations.

## 2. Research question

Can an arbitrary pretrained Gaussian-splat scene be converted into an editable object-centric representation and manipulated through natural language, entirely without per-scene semantic-field training, on resource-constrained hardware?

## 3. Proposed research contribution

The broad idea “an LLM calls tools to edit a Gaussian scene” is not novel by itself. Prior work includes 3D-GPT, Chat-Edit-3D, Gaussian Grouping, GaussianEditor, 3DitScene, ObjectGS, AG²aussian, and 3DSceneEditor.

The defensible contribution should be the combination of:

1. Training-free object discovery in an existing Gaussian scene.
2. Visibility- and contribution-aware lifting of 2D masks to Gaussian membership scores.
3. Compact object-centric scene graphs instead of learned language features on every Gaussian.
4. Deterministic, typed, reversible Gaussian edit transactions.
5. Geometric and visual verification of edits.
6. Local-first execution under explicit RAM, VRAM, and latency budgets.

Candidate paper framing:

> Training-Free Object-Centric Gaussian Scene Editing on Resource-Constrained Devices

Candidate central claim:

> Visibility-weighted multiview consensus can recover editable object-level Gaussian groups without semantic-field optimization, enabling local, reversible, and geometrically verified language-driven editing.

This claim remains a hypothesis until experiments validate it.

## 4. Definitions

### Training-free

For this project, training-free means:

- No new foundation model is trained.
- No fine-tuning, LoRA, or adaptation is performed.
- No semantic feature field is optimized for each scene.
- No Gaussian parameters are optimized through gradient descent for ordinary structural edits.
- Inference with pretrained local models is allowed.
- Deterministic preprocessing, rendering, segmentation, graph algorithms, and geometry calculations are allowed.

### On-device / local-first

- Core scene loading, grouping, editing, verification, and storage must work locally.
- The initial implementation must not require an A100-class GPU.
- Optional APIs may be evaluated as baselines, but the primary system must remain functional without them.
- Models must be loadable sequentially when simultaneous loading exceeds memory.

### Scene understanding

Initial scene understanding is deliberately narrow:

- Object category and instance identity
- Gaussian membership or membership confidence
- Object centroid and bounding box
- Object transform and pivot
- Basic support, containment, adjacency, and relative-position relations

Detailed affordance prediction, physics, materials, and narrative description are not required for the initial method.

## 5. Exact inputs and outputs

### Final system inputs

1. Existing Gaussian scene, initially a PLY file.
2. Camera information or generated render cameras.
3. Natural-language instruction.
4. Optional local pretrained 2D segmentation/VLM models.

### Final system outputs

1. Edited Gaussian scene.
2. Object-centric scene graph.
3. Structured transaction record.
4. Before-and-after renders.
5. Validation report.
6. Exact undo operation.

Example structured command:

```json
{
  "action": "translate",
  "target": {
    "object_id": "chair_1"
  },
  "offset_m": [0.5, 0.0, 0.0],
  "constraints": {
    "remain_inside_scene": true,
    "avoid_collisions": true,
    "preserve_support": true
  }
}
```

## 6. Initial assumptions

- The Gaussian scene already exists.
- The initial scenes are static.
- Initial edits are rigid transformations or attribute changes.
- The scene may not contain object identities.
- Lighting may be baked into spherical harmonics.
- The first implementation may use a manually or oracle-provided object group.
- Automatic object grouping is added only after the editing engine is verified.
- Input formats and coordinate systems must be detected; never assume Y-up.
- Unknown Gaussian properties must be preserved during file round trips.

## 7. Explicit non-goals for the initial submission pipeline

- Training a text-to-3D model
- Generating an entire room from text
- Training an LLM
- Diffusion-based scene refinement
- Photorealistic relighting after moving an object
- Non-rigid deformation
- Dynamic/4D scenes
- Full physical simulation
- Supporting every Gaussian file format immediately
- Building a polished chat frontend before the method works
- Reproducing every related repository

## 8. Real dataset currently available

Scene directory:

```text
/data/Desktop/Prashansa/major/scenebench/data/InteriorGS/0231_840445
```

Contents discovered during read-only inspection:

```text
3dgs_compressed.ply  ~32.1 MB
labels.json          455 semantic annotations
structure.json       8 rooms, 42 walls, 14 openings
occupancy.json       occupancy bounds and scale
occupancy.png        192 x 196 grayscale occupancy image
```

Gaussian scene characteristics:

- Binary little-endian compressed PLY
- Generated by `splat-transform 0.1.3`
- 524,508 Gaussians
- 2,049 quantization chunks
- Packed fields: `packed_position`, `packed_rotation`, `packed_scale`, `packed_color`
- 45 quantized higher-order spherical-harmonic fields
- The data appears Z-up: X/Y form the floor plane and Z is height.

Important usage rule:

- `labels.json` contains categories, instance IDs, and mostly 3D bounding boxes, but not Gaussian indices.
- These labels may be used as oracle information, initial engineering scaffolding, and evaluation ground truth.
- They must not be used as hidden semantic input to the final claimed training-free perception method.

Original images and camera calibration are not present in this scene directory. Cameras must initially be generated using scene and occupancy bounds.

## 9. File-format strategy

Do not edit packed integers in `3dgs_compressed.ply` directly.

Use the official PlayCanvas `splat-transform` library/CLI or a verified compatible decoder to obtain an editable representation. Preserve the original file permanently.

Expected workflow:

```text
original compressed PLY (immutable)
        -> decoder
uncompressed/editable working representation
        -> edit executor
edited working representation
        -> optional recompression
exported edited scene
```

The first round-trip test must prove:

- Gaussian count is unchanged.
- Property names and data types are preserved where applicable.
- No NaN or infinite values are introduced.
- Position bounds remain consistent.
- The decoded/re-encoded scene renders equivalently.
- Unknown properties are not silently dropped.

## 10. System architecture

```text
Gaussian scene
    |
    v
Gaussian I/O and immutable source store
    |
    +---------------------------+
    |                           |
    v                           v
Renderer                  Scene/occupancy metadata
    |
    v
Render observations
(RGB, camera, alpha/depth, Gaussian contribution evidence)
    |
    v
2D zero-shot segmenter
    |
    v
Mask-to-Gaussian lifter
    |
    v
Multiview consensus and graph cleanup
    |
    v
Object-centric scene graph
    |
    +----------- natural-language instruction
    |                           |
    |                           v
    |                     Local LLM planner
    |                           |
    +---------------------------+
                |
                v
Typed command validator
                |
                v
Edit preflight verifier
                |
                v
Deterministic Gaussian executor
                |
                v
Transaction log + edited scene
                |
                v
Post-edit geometric and rendered verification
```

## 11. Required module boundaries

Recommended repository structure:

```text
README.md
AGENTS.md
SPEC.md
pyproject.toml or requirements.txt

src/scene_agent/
  scene/
    gaussian_scene.py
    coordinate_system.py
    scene_graph.py
    object_membership.py

  perception/
    renderer.py
    render_observation.py
    segmenter.py
    mask_lifting.py
    multiview_fusion.py
    graph_cleanup.py

  editing/
    command_schema.py
    transforms.py
    executor.py
    validators.py
    transactions.py

  agents/
    backend.py
    planner.py
    verifier.py

  evaluation/
    grouping_metrics.py
    editing_metrics.py
    error_attribution.py

tests/
  fixtures/
  test_gaussian_roundtrip.py
  test_translation_undo.py
  test_coordinate_system.py
  test_mask_lifting.py
  test_multiview_fusion.py
  test_command_validation.py
  test_non_target_preservation.py
```

## 12. Core interfaces

### GaussianScene

Responsibilities:

- Load supported Gaussian formats through a verified decoder.
- Expose positions, rotations, scales, opacity, DC color, and higher-order SH.
- Preserve unknown fields.
- Record coordinate-system metadata.
- Save without mutating the original.

Conceptual interface:

```python
class GaussianScene:
    @classmethod
    def load(cls, path: Path) -> "GaussianScene": ...

    def clone(self) -> "GaussianScene": ...
    def save(self, path: Path) -> None: ...
    def gaussian_count(self) -> int: ...
    def property_digest(self) -> dict[str, str]: ...
```

### RenderObservation

Renderer output must not contain semantic categories.

```python
@dataclass
class RenderObservation:
    view_id: str
    rgb: Array
    camera: Camera
    alpha: Array | None
    depth: Array | None
    gaussian_ids: Array | None
    contribution_weights: Array | None
```

The exact representation of Gaussian-to-pixel contributions depends on the rasterizer and must be documented.

### SegmentObservation

```python
@dataclass
class SegmentObservation:
    view_id: str
    query: str
    mask_probability: Array
    category: str | None
    confidence: float
```

### ObjectMembership

Do not store hundreds of thousands of indices directly in scene-graph JSON.

```python
@dataclass
class ObjectMembership:
    gaussian_indices: Array
    confidence: Array
```

Store memberships in compact sidecar files such as NPZ, referenced by scene-graph nodes.

### EditTransaction

```python
@dataclass
class EditTransaction:
    transaction_id: str
    source_scene_digest: str
    target_object_id: str
    selected_indices_digest: str
    command: dict
    inverse_command: dict
    validation_before: dict
    validation_after: dict | None
```

## 13. Editing mathematics

### Translation

For selected Gaussian center `p` and translation `t`:

```text
p' = p + t
```

Only selected centers change.

### Rotation around object pivot

For pivot `c` and rotation matrix `R`:

```text
p' = R(p - c) + c
q' = q_R * q
```

Quaternion ordering must be detected and documented for each format.

### Uniform scaling around pivot

```text
p' = c + s(p - c)
scale' = scale + log(s)   if stored in log scale
```

If scales are stored in linear space, multiply instead. Never assume the representation.

### Recoloring

Initially modify only the DC spherical-harmonic/color component. Preserve higher-order view-dependent coefficients unless the experiment explicitly studies them.

## 14. Research-critical method: mask-to-Gaussian lifting

The existing QA repository incorrectly treats 2D-mask-to-3D-point conversion as a straightforward upstream operation. For Gaussian splats this is a central difficulty and must be explicit.

Conceptual membership score for Gaussian `g` and object `o`:

```text
S(g, o) = sum over views v of
          visibility(g, v)
          * contribution(g, v)
          * mask_probability(o, projection(g, v))
          * view_reliability(v)
```

The implementation must address:

- Occlusion and alpha compositing
- Weak versus dominant pixel contribution
- Large projected footprints
- Boundary uncertainty
- Missing visibility
- Conflicting labels across views
- Duplicate instances of the same category

Required internal baselines:

1. Single-view projection
2. Naive multiview majority voting
3. Majority voting plus KNN cleanup
4. Proposed visibility/contribution-aware fusion

## 15. Verification

### Preflight checks

- Target exists and is unambiguous.
- Selected membership is non-empty.
- Command values are finite and within configured limits.
- Coordinate system is known.
- Proposed bounding box remains within scene bounds.
- Proposed position does not substantially collide with protected objects.
- Support constraints are preserved when requested.

### Post-edit checks

- Only target Gaussian properties changed.
- Non-target Gaussian digest is unchanged.
- Gaussian count changes only for duplicate/delete operations.
- Output is renderable.
- Target is visible from at least one verification view when expected.
- Undo restores the original within defined numeric tolerance.

## 16. Evaluation

### Grouping metrics

- Gaussian-level precision, recall, and IoU when membership ground truth is available
- Bounding-box agreement
- Cross-view consistency
- Instance over-merging
- Instance fragmentation
- Boundary contamination

### Editing metrics

- Correct target selection rate
- Command execution success
- Non-target collateral-change rate
- Constraint violation rate
- Exact or tolerance-based undo fidelity
- Edit latency
- Peak RAM
- Peak VRAM

### Agent metrics

- Valid structured-command rate
- Reference-resolution accuracy
- Repair/rejection accuracy
- Unsupported-command hallucination rate
- Small local model versus optional API model

### Ablations

- Without visibility weighting
- Without contribution weighting
- Without graph cleanup
- Different numbers of views
- Orbit views versus occupancy-aware interior views
- Binary versus confidence-weighted membership
- With versus without edit verification

## 17. Milestones and acceptance gates

### Milestone 1: Lossless Gaussian I/O

Goal: safely decode, load, inspect, and save the supplied compressed scene.

Acceptance:

- Original remains untouched.
- 524,508 Gaussians recovered.
- Round-trip property tests pass.
- Scene renders equivalently.
- Z-up convention recorded.

### Milestone 2: Known-group translation and undo

Goal: use one oracle/manual bounding box to select a chair-like object, translate it, and undo.

Acceptance:

- Only selected centers change.
- Non-target properties are unchanged.
- Edited output renders.
- Undo restores the original within tolerance.

This is engineering validation, not the claimed automatic grouping method.

### Milestone 3: Complete deterministic edit engine

Add rotation, scale, recolor, hide, duplicate, transaction persistence, and validators.

### Milestone 4: Real renderer observations

Generate interior views and expose the contribution information required by mask lifting.

### Milestone 5: Single-view mask lifting baseline

Segment an object in one rendered view and produce Gaussian membership scores.

### Milestone 6: Multiview training-free grouping

Implement the proposed fusion algorithm and compare it against internal baselines.

### Milestone 7: Local language planner

Convert natural language to validated edit commands. Do not permit arbitrary code generation.

### Milestone 8: Full evaluation and paper artifacts

Run grouping, editing, compute, agent, and ablation experiments. Freeze method before final writing.

## 18. Six-week execution emphasis

Week 1:

- Milestones 1-2
- Freeze task, assumptions, and coordinate conventions
- Produce first before/after/undo figure

Week 2:

- Milestones 3-4
- Renderer contribution audit
- Initial camera-generation strategy

Week 3:

- Milestone 5
- Automated evaluation pipeline
- Ground-truth interpretation policy

Week 4:

- Milestone 6
- Main baselines and preliminary ablations
- Freeze core method

Week 5:

- Milestone 7 if it does not threaten experiments
- Main experiments, compute profiling, qualitative figures
- Draft introduction, method, and setup

Week 6:

- Final ablations
- Paper, limitations, supplement, reproducibility package
- Co-author review and submission preparation

If schedule slips, the local LLM interface is lower priority than the grouping method, executor correctness, and evaluation.

## 19. Codex working rules

Codex must:

1. Read repository instructions before actions.
2. Inspect before modifying.
3. Explain the planned change and why before editing.
4. Make small, reviewable changes.
5. Never overwrite original scene data.
6. Use temporary/output directories for generated data.
7. Preserve existing user changes.
8. Run real tests, not only import checks.
9. Explain research-critical code, equations, tensor shapes, and failure cases.
10. Report every modified file.
11. Distinguish mocked, oracle-assisted, and genuinely perceived results.
12. Never use ground-truth labels as hidden input to the evaluated perception method.

## 20. Human comprehension gate

Before accepting research-critical code, the researcher should be able to answer:

1. What are the function inputs and outputs?
2. What are all important tensor/array shapes?
3. What coordinate system is used?
4. Which equation or algorithm is implemented?
5. What assumptions does it make?
6. What test demonstrates correctness?
7. What failure cases remain?
8. Which paper claim depends on it?

Codex may automate implementation, but final scientific responsibility remains with the researchers.

## 21. Suggested multi-agent workflow

Maximum useful parallel roles at any one time:

- Orchestrator: maintains this specification, scope, and integration decisions.
- Repository/data worker: format inspection, implementation, and tests.
- Method worker: algorithm derivation, baselines, and metrics.
- Reviewer: checks correctness, novelty overlap, claims, and test coverage.

Use parallel agents only for independent work. Do not have multiple workers edit the same core files simultaneously.

## 22. Reuse policy for the existing QA repository

Repository inspected:

```text
/data/Downloads/3DSceneAgent/project
```

Potentially reusable concepts:

- Scene-graph schema ideas
- Geometry tools
- Backend-neutral tool loop
- Oracle-versus-perceived error attribution
- Verification discipline

Do not inherit its roadmap directly. It is designed for 3D question answering, Anthropic API validation, QRA tools, USD-first rendering, and mocked perception.

Specific limitations discovered:

- Renderer and segmenter responsibilities are conflated.
- 2D-mask-to-Gaussian attribution is bypassed.
- `simulate_move` predicts consequences but does not edit geometry.
- Scene nodes lack resolvable Gaussian membership.
- Assembly logic is mock- and USD-specific.
- Coordinate calculations assume Y-up, while the current InteriorGS scene is Z-up.

## 23. First Codex task in a fresh repository

Use this exact task after creating/opening the empty repository:

> Read `SPEC.md` and any `AGENTS.md` completely. Do not modify files yet. Inspect the available Python/Node environment and the scene at `/data/Desktop/Prashansa/major/scenebench/data/InteriorGS/0231_840445`. Determine the safest low-memory method to decode `3dgs_compressed.ply` using the official PlayCanvas `splat-transform` library or another verified compatible decoder. Report the planned repository files, dependencies, expected working-file size, memory risks, coordinate convention, and exact Milestone-1 tests. Preserve the original scene. Wait for approval before implementation.

After approval, implement Milestone 1 only.

## 24. Stop conditions

Stop and request direction if:

- Decoding would overwrite or destructively modify source data.
- The format decoder drops properties needed for rendering.
- The coordinate convention cannot be established.
- Required writes fall outside the authorized repository/output paths.
- A proposed dependency requires materially more RAM/VRAM than the target hardware.
- A design decision would change the research claim or evaluation protocol.

