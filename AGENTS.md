# Project Instructions

## Communication

- Address the user as Prashansa.
- Explain planned changes before modifying files.
- When suggesting a command, explain in simple language what it does and why it is needed.
- After making changes, list every modified file.
- When debugging, identify the root cause before implementing a fix.

## Project Goal

Build a training-free, local-first agent for editing 3D Gaussian Splatting scenes.

The system should eventually support:

1. Loading and saving Gaussian-splat scenes.
2. Editing known Gaussian groups.
3. Converting text instructions into structured edit commands.
4. Identifying object Gaussians from multiview image evidence.
5. Verifying edits and supporting undo.

Read `SPEC.md` completely before planning or implementing project work.

## Scientific Constraints

- Do not introduce task-specific model training unless Prashansa explicitly changes the research scope.
- Prefer methods that can run on limited GPU memory or CPU.
- Treat dataset labels and ground-truth object assignments as evaluation data, not as normal inference inputs.
- Clearly distinguish ground truth, heuristics, model predictions, and user-provided annotations.
- Never report a mock, placeholder, or oracle-assisted result as a completed method.
- Record assumptions, failure cases, runtime, peak memory, and experimental settings.
- Preserve reproducibility by using fixed seeds and saved configurations where applicable.

## Data Safety

- Never modify an original Gaussian PLY or dataset file in place.
- Write edited scenes and intermediate artifacts to a separate output directory.
- Do not commit large datasets, model weights, generated renders, or credentials.
- Validate PLY schemas and coordinate conventions before transforming data.

## Development Rules

- Prefer minimal, testable changes over large refactors.
- Preserve compatibility with existing code.
- Do not delete existing code or data without explicit permission.
- Do not make unrelated changes.
- Add tests for transformations, serialization, undo, and other deterministic behavior.
- Begin with the smallest representative scene or subset.
- Avoid dependencies or models requiring more GPU memory than the available environment.

## Orchestration Roles

The primary agent is the orchestrator and integration owner. It must:

- Clarify requirements and acceptance criteria.
- Inspect the relevant code and data before delegating.
- Divide work into non-overlapping tasks.
- Integrate results and rerun final checks.
- Maintain communication with Prashansa.
- Make the final decision about whether a task is complete.

The orchestrator may perform small inspections, integration changes, and verification directly. It should not delegate work merely to create more agents.

### Fast Worker

Use a Fast worker for:

- Repository inspection
- File and schema inventories
- Mechanical implementation
- Tests following an established pattern
- Documentation and experiment scripts
- Small changes limited to one module or two tightly coupled files

Fast workers should normally use Luna.

### Deep Worker

Use a Deep worker for:

- Methodology and algorithm design
- Gaussian rendering or visibility reasoning
- Multiview evidence aggregation
- Shared interfaces and architectural decisions
- Root-cause analysis across modules
- Research novelty and experimental design
- Changes that would be expensive to reverse

Deep workers should normally use Sol with high reasoning.

### Reviewer

Use a fresh reviewer after a meaningful implementation round.

The reviewer must receive:

- The task goal
- Constraints
- Acceptance criteria
- Changed files or diff
- Test and experiment evidence

The reviewer must check:

- Correctness
- Scientific validity
- Data leakage
- Destructive data operations
- Test coverage
- Runtime and memory assumptions
- Compatibility with the specification

A reviewer should not silently rewrite the implementation. It should identify concrete issues for the orchestrator to route back to a worker.

## Parallel Work

- Never allow two agents to edit the same file concurrently.
- Prefer at most two parallel workers while reserving capacity for the orchestrator and reviewer.
- Parallelize independent inspection, literature analysis, testing, or isolated modules.
- Keep dependent implementation steps sequential.
- Every delegated task must identify its allowed files and completion criteria.

## Definition of Done

A task is complete only when:

- Its acceptance criteria are satisfied.
- Relevant tests pass.
- The original dataset remains unchanged.
- Output artifacts are clearly separated.
- Assumptions and limitations are documented.
- The orchestrator has reviewed the integrated result.
- A fresh reviewer has checked method-critical changes.

- For implementation tasks, create a local Git checkpoint commit after each meaningful, tested milestone.
- Before committing, inspect `git status` and `git diff`.
- Commit only files changed for the current task.
- Use descriptive commit messages beginning with `checkpoint:`.
- Never push, force-push, amend, rebase, or reset without explicit permission.
- Do not include secrets, generated datasets, model weights, or large artifacts.
- Leave failing or incomplete work uncommitted, and clearly report its status.