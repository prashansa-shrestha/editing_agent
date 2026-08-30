# Gaussian Scene I/O Workbench

Gaussian Scene I/O Workbench is a local-first checkpoint for Milestone 1A of
the training-free 3D Gaussian Splatting scene agent. It safely inspects the
supplied compressed PLY, decodes it into a canonical CPU-editable PLY, and
validates a no-op canonical round trip. Generated files belong under the
ignored `outputs/milestone1/` directory; the original scene is never edited in
place.

This milestone intentionally stops at dependable scene I/O. It does **not**
provide rendering or a 3D preview, object grouping, natural-language planning,
scene editing, or a language-agent interface yet.

## Requirements

- Python 3.11 or newer
- Node.js 18 or newer and npm
- A readable compressed Gaussian-splat PLY on the machine running the server

The browser is only a local control surface. The server reads the scene path
you provide, so the PLY does not get uploaded to a browser or a third party.

## Install

Run these commands from the repository root:

```bash
npm ci
python3 -m pip install -e '.[test]'
```

`npm ci` installs the exact Node dependency versions recorded in
`package-lock.json`; the Milestone 1A decoder uses the pinned PlayCanvas
package. `python3 -m pip install -e '.[test]'` installs this Python package in
editable mode, along with the pinned test tools, so local source changes are
picked up without rebuilding a wheel.

If your machine uses a virtual environment, activate it before the Python
command. The commands install project-local dependencies; they do not modify
the original scene or dataset.

## Launch the local workbench

```bash
python3 -m scene_agent
```

This starts the local HTTP server and serves the workbench. Open
<http://127.0.0.1:8765> in a browser on the same machine. Keep the terminal
running while you use the UI.

In VS Code, use the **Ports** view to forward port `8765` if the server runs
inside a remote development environment. In Google Colab, use the notebook’s
port-forwarding or proxy mechanism for port `8765`, then open the forwarded
local URL. The port-forwarding layer only makes the local server reachable in
your browser; it does not turn the scene into an upload or deploy the app.

## Use the UI

1. Confirm or replace the compressed scene path. The supplied default is
   `/data/Desktop/Prashansa/major/scenebench/data/InteriorGS/0231_840445/3dgs_compressed.ply`.
2. Select **Inspect source** to validate the compressed header and show the
   source fingerprint, schema, Gaussian count, format, and file size.
3. Select **Decode scene** to create a fresh canonical PLY. An output name is
   optional; leaving it blank lets the server derive a deterministic name.
   Existing artifacts are never overwritten.
   Runtime, peak RAM, and the decoded output path are shown when the request
   completes.
4. Select **Run round trip** to reload the last decoded path, write a no-op
   copy, and validate the canonical schema and row order. The UI keeps the
   successful decoded path in the round-trip field for this step.
5. Use the **Milestone outputs** panel to refresh and review artifacts already
   published under the server’s output directory.

The workbench disables operation buttons while a request is running and shows
actionable errors in the **Live activity** panel. A failed request does not
make an output claim. The server refuses unsafe or conflicting output targets;
do not bypass that boundary by modifying files manually.

## Safety and scope

The compressed source is an immutable input. Decode and round-trip artifacts
are written separately below `outputs/milestone1/`, which is ignored by Git.
Do not place a dataset, credentials, model weights, or other large generated
files in the repository. A canonical decode is a float32 editable
representation after quantization unpacking, not a byte-for-byte reconstruction
of unknown pre-compression data.

Milestone 1A is CPU-only and does not require CUDA, WebGL, WebGPU, a renderer,
or a model API. The supplied real scene contains 524,508 Gaussians; resource
reports from the server should be recorded with experiments rather than
treated as a guarantee for every larger scene.

## Run tests

```bash
python3 -m pytest
```

This runs the repository’s deterministic inspection, canonical I/O, decoder,
and real-scene safety checks. It does not modify the original PLY. Generated
test outputs remain in the ignored milestone output area.

## Stop the server

Return to the terminal running `python3 -m scene_agent` and press
**Ctrl+C**. This sends an interrupt to the local process and stops the HTTP
server; it does not delete the source scene or generated outputs.
