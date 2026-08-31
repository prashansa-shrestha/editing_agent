"""Static and executable contracts for the M2 browser viewer."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = REPOSITORY_ROOT / "src" / "scene_agent" / "web"
HTML = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
VIEWER_JS = (WEB_ROOT / "viewer.js").read_text(encoding="utf-8")
STYLES = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")
CAMERA_MODULE = WEB_ROOT / "viewer-camera.js"
LIFECYCLE_MODULE = WEB_ROOT / "viewer-lifecycle.js"


def _run_camera_contract(source: str) -> object:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable for executable browser camera math checks")
    completed = subprocess.run(
        [node, "--input-type=module", "-e", source],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_camera_math_is_finite_z_up_and_deterministic():
    result = _run_camera_contract(
        """
import {
  CAMERA_DEFAULTS, cameraPose, fitCameraState, orbitCameraState,
  panCameraState, resetCameraState, zoomCameraState
} from './src/scene_agent/web/viewer-camera.js';
const aabb = {min: [-6, -8, -5], max: [7, 9, 6]};
const aspect = 16 / 9;
const resetA = resetCameraState(aabb, aspect);
const resetB = resetCameraState(aabb, aspect);
const pose = cameraPose(resetA);
const orbited = orbitCameraState(resetA, 17, 1000);
const panned = panCameraState(orbited, 24, -11, 720);
const pannedPose = cameraPose(panned);
const zoomed = zoomCameraState(panned, -240);
const fitted = fitCameraState(zoomed, aabb, aspect);
console.log(JSON.stringify({
  defaults: CAMERA_DEFAULTS,
  resetA, resetB, pose, orbited, panned, pannedPose, zoomed, fitted
}));
"""
    )

    assert result["resetA"] == result["resetB"]
    assert result["pose"]["viewUp"] == [0, 0, 1]
    assert result["pose"]["near"] > 0
    assert result["pose"]["far"] > result["pose"]["near"]
    assert result["orbited"]["elevationDegrees"] == 85
    assert result["orbited"]["yawDegrees"] == result["resetA"]["yawDegrees"] + 17
    assert result["panned"]["target"] != result["orbited"]["target"]
    translation = [
        result["panned"]["target"][index] - result["orbited"]["target"][index]
        for index in range(3)
    ]
    position_translation = [
        result["pannedPose"]["position"][index] - result["pose"]["position"][index]
        for index in range(3)
    ]
    # The pose comparison includes the deliberate orbit, so only finiteness is asserted here;
    # pan itself preserves yaw, elevation, and distance in the state below.
    assert all(abs(value) < 1e9 for value in translation + position_translation)
    assert result["panned"]["yawDegrees"] == result["orbited"]["yawDegrees"]
    assert result["panned"]["elevationDegrees"] == result["orbited"]["elevationDegrees"]
    assert result["panned"]["distance"] == result["orbited"]["distance"]
    assert 0 < result["zoomed"]["distance"] < result["panned"]["distance"]
    assert result["fitted"]["target"] == result["resetA"]["target"]
    assert result["fitted"]["distance"] == result["resetA"]["distance"]
    assert result["fitted"]["yawDegrees"] == result["zoomed"]["yawDegrees"]


@pytest.mark.parametrize(
    "aabb_source",
    [
        "{min: [0, 0, 0], max: [0, 0, 0]}",
        "{min: [2, 0, 0], max: [1, 1, 1]}",
        "{min: [0, 0, 0], max: [1, Number.NaN, 1]}",
        "{min: [0, 0, 0], max: [1, 1, Number.POSITIVE_INFINITY]}",
    ],
)
def test_camera_math_rejects_unsafe_bounds(aabb_source: str):
    result = _run_camera_contract(
        f"""
import {{ resetCameraState }} from './src/scene_agent/web/viewer-camera.js';
let rejected = false;
try {{ resetCameraState({aabb_source}, 1); }} catch (_error) {{ rejected = true; }}
console.log(JSON.stringify({{rejected}}));
"""
    )
    assert result == {"rejected": True}


def test_portrait_and_landscape_fit_use_the_limiting_field_of_view():
    result = _run_camera_contract(
        """
import { CAMERA_DEFAULTS, resetCameraState } from './src/scene_agent/web/viewer-camera.js';
const aabb = {
  min: [-6.4937675035358176, -8.491147283351491, -5.602591798801276],
  max: [6.81342827358616, 8.76070222006658, 5.533811335966632]
};
const inspect = (aspect) => {
  const state = resetCameraState(aabb, aspect);
  const verticalHalf = CAMERA_DEFAULTS.fovDegrees * Math.PI / 360;
  const horizontalHalf = Math.atan(Math.tan(verticalHalf) * aspect);
  const angularRadius = Math.asin(state.radius / state.distance);
  return {aspect, distance:state.distance, angularRadius, verticalHalf, horizontalHalf};
};
console.log(JSON.stringify({portrait:inspect(254 / 330), landscape:inspect(16 / 9)}));
"""
    )

    portrait = result["portrait"]
    landscape = result["landscape"]
    assert portrait["angularRadius"] < portrait["horizontalHalf"]
    assert portrait["angularRadius"] < portrait["verticalHalf"]
    assert landscape["angularRadius"] < landscape["horizontalHalf"]
    assert landscape["angularRadius"] < landscape["verticalHalf"]
    assert portrait["distance"] > landscape["distance"]


@pytest.mark.parametrize("aspect_source", ["0", "-1", "Number.NaN", "Number.POSITIVE_INFINITY"])
def test_camera_math_rejects_invalid_aspect(aspect_source: str):
    result = _run_camera_contract(
        f"""
import {{ resetCameraState }} from './src/scene_agent/web/viewer-camera.js';
let rejected = false;
try {{ resetCameraState({{min:[0,0,0], max:[1,1,1]}}, {aspect_source}); }} catch (_error) {{ rejected = true; }}
console.log(JSON.stringify({{rejected}}));
"""
    )
    assert result == {"rejected": True}


def test_load_lifecycle_rejects_stale_ready_across_reload_and_failure():
    result = _run_camera_contract(
        """
import { StaleViewerLoadError, ViewerLoadLifecycle } from './src/scene_agent/web/viewer-lifecycle.js';
const lifecycle = new ViewerLoadLifecycle();
const first = lifecycle.begin();
lifecycle.ready(first);
const reload = lifecycle.begin();
let staleReadyRejected = false;
try { lifecycle.ready(first); } catch (error) { staleReadyRejected = error instanceof StaleViewerLoadError; }
lifecycle.fail(reload);
const retry = lifecycle.begin();
lifecycle.cancel();
let cancelledRetryRejected = false;
try { lifecycle.ready(retry); } catch (error) { cancelledRetryRejected = error instanceof StaleViewerLoadError; }
console.log(JSON.stringify({staleReadyRejected, cancelledRetryRejected, state:lifecycle.state}));
"""
    )

    assert result == {
        "staleReadyRejected": True,
        "cancelledRetryRejected": True,
        "state": "idle",
    }


def test_frontend_uses_official_playcanvas_asset_and_gsplat_resource_path():
    assert 'await import(assertSameOrigin(PLAYCANVAS_MODULE_URL))' in VIEWER_JS
    assert 'new pc.Asset(' in VIEWER_JS
    assert '"gsplat"' in VIEWER_JS
    assert 'asset.resource instanceof pc.GSplatResource' in VIEWER_JS
    assert 'asset.resource.instantiate()' in VIEWER_JS
    assert '{ decompress: false }' in VIEWER_JS
    assert 'SOURCE_URL = `/api/viewer/source/${encodeURIComponent(SCENE_ID)}`' in VIEWER_JS
    assert "hash: manifest.sha256" not in VIEWER_JS
    assert "point cloud" not in VIEWER_JS.lower()
    assert "cdn" not in VIEWER_JS.lower()
    assert "http://" not in VIEWER_JS and "https://" not in VIEWER_JS


def test_ready_follows_official_sort_and_settled_render_frames():
    create_renderer = VIEWER_JS.split("async function createRenderer", 1)[1].split(
        "async function loadScene", 1
    )[0]
    load_scene = VIEWER_JS.split("async function loadScene", 1)[1].split(
        "function resetView", 1
    )[0]

    assert create_renderer.index("await loadAsset(app, asset)") < create_renderer.index("await sorterSettled")
    assert create_renderer.index("await sorterSettled") < create_renderer.index("resizeRenderer({ refit: true })")
    assert create_renderer.index("resizeRenderer({ refit: true })") < create_renderer.index("await waitForRenderedFrames(app, 2)")
    assert load_scene.index("await createRenderer(manifest, generation)") < load_scene.index(
        'setViewerState("ready"'
    )
    assert load_scene.index("destroyRenderer();", load_scene.index("catch")) < load_scene.index(
        "showViewerError(error)", load_scene.index("catch")
    )
    assert 'lifecycle.ready(generation)' in load_scene
    assert 'error instanceof StaleViewerLoadError' in load_scene
    assert 'elements.viewer_canvas.classList.toggle("is-render-visible", ready)' in VIEWER_JS
    assert 'elements.viewer_canvas.setAttribute("aria-hidden", String(!ready))' in VIEWER_JS
    assert 'elements.viewer_canvas.tabIndex = ready ? 0 : -1' in VIEWER_JS
    assert '#viewer-canvas.is-render-visible' in STYLES
    assert 'transition: opacity' not in STYLES
    canvas_wrap_styles = STYLES.split('.viewer-canvas-wrap {', 1)[1].split('}', 1)[0]
    overlay_styles = STYLES.split('.viewer-overlay {', 1)[1].split('}', 1)[0]
    assert 'background: #000000' in canvas_wrap_styles
    assert 'background: #000000' in overlay_styles
    canvas_focus_styles = STYLES.split('#viewer-canvas:focus-visible {', 1)[1].split('}', 1)[0]
    assert 'inset 0 0 0 2px #ffffff' in canvas_focus_styles
    assert 'inset 0 0 0 4px #000000' in canvas_focus_styles


def test_viewer_ui_preserves_workbench_and_exposes_rgb_only_controls():
    for existing_id in (
        'id="source-form"',
        'id="decode-form"',
        'id="roundtrip-form"',
        'id="outputs-list"',
    ):
        assert existing_id in HTML
    for viewer_id in (
        'id="viewer-canvas"',
        'id="viewer-load-button"',
        'id="viewer-reset-button"',
        'id="viewer-fit-button"',
        'id="viewer-source-id"',
        'id="viewer-source-format"',
        'id="viewer-source-count"',
        'id="viewer-source-up"',
        'id="viewer-source-digest"',
    ):
        assert viewer_id in HTML
    assert 'type="module" src="/static/viewer.js"' in HTML
    assert 'tabindex="-1"' in HTML
    assert 'id="viewer-canvas" hidden' not in HTML
    assert 'aria-hidden="true"' in HTML
    assert "RGB only" in HTML
    assert "no depth, IDs, segmentation, semantics, or edit controls" in HTML
    assert "bird's-eye" not in HTML.lower()
    assert "capture" not in HTML.lower()
    assert "export current view" not in HTML.lower()
    assert "contribution-aware" not in HTML.lower()
    assert "scene graph" not in HTML.lower()


def test_controls_are_state_gated_and_support_pointer_wheel_and_keyboard():
    assert 'elements.viewer_reset_button.disabled = !ready' in VIEWER_JS
    assert 'elements.viewer_fit_button.disabled = !ready' in VIEWER_JS
    assert 'addEventListener("pointerdown"' in VIEWER_JS
    assert 'addEventListener("pointermove"' in VIEWER_JS
    assert 'addEventListener("wheel"' in VIEWER_JS
    assert 'addEventListener("keydown"' in VIEWER_JS
    assert 'event.shiftKey' in VIEWER_JS
    assert 'key === "0"' in VIEWER_JS
    assert 'key === "f"' in VIEWER_JS
    assert 'key === "+"' in VIEWER_JS
    assert '"arrowleft"' in VIEWER_JS
