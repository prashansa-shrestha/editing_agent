"use strict";

import {
  CAMERA_DEFAULTS,
  cameraPose,
  fitCameraState,
  orbitCameraState,
  panCameraState,
  resetCameraState,
  validateAabb,
  zoomCameraState,
} from "/static/viewer-camera.js";
import {
  StaleViewerLoadError,
  ViewerLoadLifecycle,
} from "/static/viewer-lifecycle.js";

const SCENE_ID = "interiorgs_0231_840445";
const PLAYCANVAS_VERSION = "2.3.3";
const PLAYCANVAS_MODULE_URL = `/vendor/playcanvas-${PLAYCANVAS_VERSION}.mjs`;
const MANIFEST_URL = `/api/viewer/manifest?scene_id=${encodeURIComponent(SCENE_ID)}`;
const SOURCE_URL = `/api/viewer/source/${encodeURIComponent(SCENE_ID)}`;
const LOAD_TIMEOUT_MS = 30000;

const viewer = {
  app: null,
  asset: null,
  cameraEntity: null,
  sceneEntity: null,
  cameraState: null,
  manifest: null,
  resizeObserver: null,
  pointer: null,
  fitToScene: true,
  resizeInFlight: false,
  resizeSequence: 0,
  status: "idle",
};

const elements = {};
const lifecycle = new ViewerLoadLifecycle();

class ViewerStageError extends Error {
  constructor(stage, message) {
    super(message);
    this.name = "ViewerStageError";
    this.stage = stage;
  }
}

function findElements() {
  [
    "viewer-panel",
    "viewer-canvas-wrap",
    "viewer-canvas",
    "viewer-overlay",
    "viewer-overlay-title",
    "viewer-overlay-message",
    "viewer-state-badge",
    "viewer-load-button",
    "viewer-reset-button",
    "viewer-fit-button",
    "viewer-error",
    "viewer-error-heading",
    "viewer-error-message",
    "viewer-source-id",
    "viewer-source-format",
    "viewer-source-count",
    "viewer-source-up",
    "viewer-source-digest",
    "viewer-camera-summary",
  ].forEach((id) => {
    elements[id.replaceAll("-", "_")] = document.getElementById(id);
  });
}

function assertSameOrigin(url) {
  const resolved = new URL(url, window.location.href);
  if (resolved.origin !== window.location.origin) {
    throw new ViewerStageError("request", "The viewer refused a non-local asset URL.");
  }
  return `${resolved.pathname}${resolved.search}`;
}

function setViewerState(status, title, message) {
  viewer.status = status;
  elements.viewer_panel.dataset.viewerState = status;
  elements.viewer_state_badge.textContent = status;
  elements.viewer_state_badge.className = `status-badge viewer-state viewer-state-${status}`;
  elements.viewer_overlay_title.textContent = title;
  elements.viewer_overlay_message.textContent = message;
  const ready = status === "ready";
  const loading = status === "loading";
  elements.viewer_canvas.classList.toggle("is-render-visible", ready);
  elements.viewer_canvas.setAttribute("aria-hidden", String(!ready));
  elements.viewer_canvas.tabIndex = ready ? 0 : -1;
  elements.viewer_overlay.hidden = ready;
  elements.viewer_load_button.disabled = loading;
  elements.viewer_load_button.textContent = loading ? "Loading scene…" : ready ? "Reload scene" : "Load RGB scene";
  elements.viewer_reset_button.disabled = !ready;
  elements.viewer_fit_button.disabled = !ready;
}

function hideRenderedCanvas() {
  elements.viewer_canvas.classList.remove("is-render-visible");
  elements.viewer_canvas.setAttribute("aria-hidden", "true");
  elements.viewer_canvas.tabIndex = -1;
}

function showViewerError(error) {
  const stage = error instanceof ViewerStageError ? error.stage : "renderer";
  const message = error instanceof Error ? error.message : "The viewer failed without a useful diagnostic.";
  elements.viewer_error_heading.textContent = `${stage[0].toUpperCase()}${stage.slice(1)} stage failed`;
  elements.viewer_error_message.textContent = `${message} Retry the local scene load; the source was not modified.`;
  elements.viewer_error.hidden = false;
  setViewerState("error", "Scene unavailable", `The ${stage} stage did not complete. See the actionable error below.`);
}

function clearViewerError() {
  elements.viewer_error.hidden = true;
}

function updateManifestPanel(manifest) {
  elements.viewer_source_id.textContent = manifest.scene_id;
  elements.viewer_source_format.textContent = manifest.format;
  elements.viewer_source_count.textContent = new Intl.NumberFormat("en-US").format(manifest.gaussian_count);
  elements.viewer_source_up.textContent = manifest.coordinate_system.world_up;
  elements.viewer_source_digest.textContent = `${manifest.sha256.slice(0, 16)}…`;
}

function validateManifest(payload) {
  if (!payload || payload.ok !== true || payload.scene_id !== SCENE_ID) {
    throw new ViewerStageError("manifest", "The server returned the wrong logical scene manifest.");
  }
  if (
    payload.format !== "playcanvas_compressed_ply"
    || !Number.isSafeInteger(payload.gaussian_count)
    || payload.gaussian_count <= 0
    || typeof payload.sha256 !== "string"
    || !/^[a-f0-9]{64}$/.test(payload.sha256)
    || payload.coordinate_system?.world_up !== "+Z"
  ) {
    throw new ViewerStageError("manifest", "The manifest is missing trusted compressed-scene or +Z metadata.");
  }
  try {
    validateAabb(payload.scene_aabb);
  } catch (error) {
    throw new ViewerStageError("camera bounds", error.message);
  }
  return payload;
}

async function fetchManifest() {
  let response;
  try {
    response = await fetch(assertSameOrigin(MANIFEST_URL), {
      headers: { Accept: "application/json" },
      cache: "no-store",
      credentials: "same-origin",
    });
  } catch (_error) {
    throw new ViewerStageError("manifest", "The loopback manifest endpoint could not be reached.");
  }
  let payload;
  try {
    payload = await response.json();
  } catch (_error) {
    throw new ViewerStageError("manifest", `The manifest response was unreadable (HTTP ${response.status}).`);
  }
  if (!response.ok) {
    throw new ViewerStageError("manifest", `The server rejected the scene manifest (HTTP ${response.status}).`);
  }
  return validateManifest(payload);
}

function withTimeout(executor, stage, message) {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new ViewerStageError(stage, message)), LOAD_TIMEOUT_MS);
    executor(
      (value) => {
        window.clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        window.clearTimeout(timer);
        reject(error);
      },
    );
  });
}

function loadAsset(app, asset) {
  return withTimeout((resolve, reject) => {
    asset.once("load", resolve);
    asset.once("error", () => reject(new ViewerStageError("source stream", "PlayCanvas could not parse the exact compressed PLY stream.")));
    app.assets.add(asset);
    app.assets.load(asset);
  }, "source stream", "The exact compressed PLY did not finish loading in time.");
}

function waitForSorter(sorter) {
  if (!sorter) {
    return Promise.reject(new ViewerStageError("renderer", "The official GSplat instance did not create its depth sorter."));
  }
  return withTimeout((resolve, reject) => {
    sorter.once("updated", (count) => {
      if (!Number.isInteger(count) || count <= 0) {
        reject(new ViewerStageError("renderer", "The official GSplat sorter reported no visible splats."));
      } else {
        resolve(count);
      }
    });
  }, "renderer", "The official GSplat sorter did not settle in time.");
}

function waitForRenderedFrames(app, frameCount = 2) {
  return withTimeout((resolve) => {
    let remaining = frameCount;
    const onFrame = () => {
      remaining -= 1;
      if (remaining <= 0) {
        resolve();
      } else {
        app.once("postrender", onFrame);
      }
    };
    app.once("postrender", onFrame);
  }, "renderer", "PlayCanvas did not produce a settled frame in time.");
}

function measureViewerViewport() {
  const bounds = elements.viewer_canvas_wrap.getBoundingClientRect();
  const width = Math.floor(bounds.width);
  const height = Math.floor(bounds.height);
  const aspect = width / height;
  if (
    !Number.isFinite(width)
    || !Number.isFinite(height)
    || !Number.isFinite(aspect)
    || width <= 0
    || height <= 0
    || aspect <= 0
  ) {
    throw new ViewerStageError("renderer resize", "The viewer canvas has no positive finite layout size.");
  }
  return { width, height, aspect };
}

function resizeRenderer({ refit = false } = {}) {
  if (!viewer.app) {
    throw new ViewerStageError("renderer resize", "The renderer is unavailable for resize.");
  }
  const viewport = measureViewerViewport();
  const { width, height, aspect } = viewport;
  viewer.app.resizeCanvas(width, height);
  if (elements.viewer_canvas.width <= 0 || elements.viewer_canvas.height <= 0) {
    throw new ViewerStageError("renderer resize", "PlayCanvas produced a zero-sized framebuffer.");
  }
  if (refit && viewer.fitToScene && viewer.cameraState && viewer.manifest) {
    viewer.cameraState = fitCameraState(viewer.cameraState, viewer.manifest.scene_aabb, aspect);
  }
  applyCameraState();
  return viewport;
}

function updateCameraSummary(pose) {
  const coordinates = pose.position.map((value) => value.toFixed(2)).join(", ");
  elements.viewer_camera_summary.textContent = `Perspective · position [${coordinates}] · target [${pose.target.map((value) => value.toFixed(2)).join(", ")}] · +Z up`;
}

function applyCameraState() {
  if (!viewer.cameraEntity || !viewer.cameraState) {
    return;
  }
  const pose = cameraPose(viewer.cameraState);
  viewer.cameraEntity.setPosition(...pose.position);
  viewer.cameraEntity.lookAt(
    new viewer.pc.Vec3(...pose.target),
    new viewer.pc.Vec3(...pose.viewUp),
  );
  viewer.cameraEntity.camera.nearClip = pose.near;
  viewer.cameraEntity.camera.farClip = pose.far;
  updateCameraSummary(pose);
}

function destroyRenderer() {
  hideRenderedCanvas();
  viewer.resizeInFlight = false;
  viewer.resizeSequence += 1;
  if (viewer.resizeObserver) {
    viewer.resizeObserver.disconnect();
    viewer.resizeObserver = null;
  }
  if (viewer.app) {
    viewer.app.destroy();
  }
  viewer.app = null;
  viewer.asset = null;
  viewer.cameraEntity = null;
  viewer.sceneEntity = null;
  viewer.cameraState = null;
}

async function createRenderer(manifest, generation) {
  let pc;
  try {
    pc = await import(assertSameOrigin(PLAYCANVAS_MODULE_URL));
  } catch (_error) {
    throw new ViewerStageError("renderer module", `The verified local PlayCanvas ${PLAYCANVAS_VERSION} module could not be loaded.`);
  }
  lifecycle.requireCurrent(generation);
  if (pc.version !== PLAYCANVAS_VERSION || typeof pc.GSplatResource !== "function") {
    throw new ViewerStageError("renderer module", `The browser did not receive official PlayCanvas ${PLAYCANVAS_VERSION} GSplat support.`);
  }
  viewer.pc = pc;

  let app;
  try {
    app = new pc.Application(elements.viewer_canvas, {
      graphicsDeviceOptions: {
        alpha: false,
        antialias: false,
        powerPreference: "high-performance",
      },
    });
  } catch (_error) {
    throw new ViewerStageError("WebGL renderer", "A compatible local WebGL graphics context is unavailable.");
  }
  viewer.app = app;
  app.graphicsDevice.maxPixelRatio = Math.min(window.devicePixelRatio || 1, 1.5);
  app.setCanvasFillMode(pc.FILLMODE_NONE);
  app.setCanvasResolution(pc.RESOLUTION_AUTO);
  app.start();
  lifecycle.requireCurrent(generation);

  const camera = new pc.Entity("viewer-camera");
  camera.addComponent("camera", {
    clearColor: new pc.Color(0.025, 0.04, 0.055, 1),
    fov: CAMERA_DEFAULTS.fovDegrees,
    projection: pc.PROJECTION_PERSPECTIVE,
  });
  app.root.addChild(camera);
  viewer.cameraEntity = camera;
  const initialViewport = resizeRenderer();
  viewer.fitToScene = true;
  viewer.cameraState = resetCameraState(manifest.scene_aabb, initialViewport.aspect);
  applyCameraState();

  const asset = new pc.Asset(
    manifest.scene_id,
    "gsplat",
    {
      url: assertSameOrigin(SOURCE_URL),
      filename: `${manifest.scene_id}.ply`,
      size: manifest.size_bytes,
    },
    { decompress: false },
  );
  viewer.asset = asset;
  await loadAsset(app, asset);
  lifecycle.requireCurrent(generation);
  if (!(asset.resource instanceof pc.GSplatResource)) {
    throw new ViewerStageError("renderer", "PlayCanvas did not return an official GSplat resource.");
  }
  if (asset.resource.splatData?.numSplats !== manifest.gaussian_count) {
    throw new ViewerStageError("renderer", "The official GSplat resource count does not match the trusted manifest.");
  }

  const entity = asset.resource.instantiate();
  entity.name = manifest.scene_id;
  entity.setLocalEulerAngles(0, 0, 0);
  const sorterSettled = waitForSorter(entity.gsplat?.instance?.sorter);
  app.root.addChild(entity);
  viewer.sceneEntity = entity;
  applyCameraState();
  await sorterSettled;
  lifecycle.requireCurrent(generation);
  resizeRenderer({ refit: true });
  await waitForRenderedFrames(app, 2);
  lifecycle.requireCurrent(generation);
  measureViewerViewport();
  if (elements.viewer_canvas.width <= 0 || elements.viewer_canvas.height <= 0) {
    throw new ViewerStageError("renderer", "The settled PlayCanvas framebuffer is not positive-sized.");
  }

  viewer.resizeObserver = new ResizeObserver(handleObservedResize);
  viewer.resizeObserver.observe(elements.viewer_canvas_wrap);
}

async function handleObservedResize() {
  if (!viewer.app) {
    return;
  }
  const resizeSequence = ++viewer.resizeSequence;
  const generation = lifecycle.generation;
  const shouldSettleReady = viewer.status === "ready" || viewer.resizeInFlight;
  if (viewer.status === "ready") {
    viewer.resizeInFlight = true;
    setViewerState("loading", "Resizing RGB scene", "PlayCanvas is settling the resized framebuffer…");
  }
  try {
    resizeRenderer({ refit: true });
    if (shouldSettleReady) {
      await waitForRenderedFrames(viewer.app, 2);
      if (
        resizeSequence !== viewer.resizeSequence
        || !lifecycle.isCurrent(generation)
        || !viewer.app
      ) {
        return;
      }
      measureViewerViewport();
      viewer.resizeInFlight = false;
      setViewerState("ready", "RGB scene ready", "The resized official GSplat frame has settled.");
    }
  } catch (error) {
    if (resizeSequence !== viewer.resizeSequence || !lifecycle.isCurrent(generation)) {
      return;
    }
    viewer.resizeInFlight = false;
    destroyRenderer();
    showViewerError(error);
  }
}

async function loadScene() {
  if (viewer.status === "loading") {
    return;
  }
  clearViewerError();
  const generation = lifecycle.begin();
  setViewerState("loading", "Loading authentic RGB scene", "Validating the manifest before PlayCanvas streams the exact compressed source…");
  destroyRenderer();
  try {
    const manifest = await fetchManifest();
    lifecycle.requireCurrent(generation);
    viewer.manifest = manifest;
    updateManifestPanel(manifest);
    elements.viewer_overlay_message.textContent = `Manifest verified. Official PlayCanvas ${PLAYCANVAS_VERSION} is loading ${new Intl.NumberFormat("en-US").format(manifest.gaussian_count)} compressed splats…`;
    await createRenderer(manifest, generation);
    lifecycle.requireCurrent(generation);
    lifecycle.ready(generation);
    setViewerState("ready", "RGB scene ready", "The official GSplat resource has settled.");
    elements.viewer_canvas.focus({ preventScroll: true });
  } catch (error) {
    if (error instanceof StaleViewerLoadError || !lifecycle.isCurrent(generation)) {
      return;
    }
    destroyRenderer();
    lifecycle.fail(generation);
    showViewerError(error);
  }
}

function resetView() {
  if (viewer.status !== "ready") {
    return;
  }
  viewer.fitToScene = true;
  viewer.cameraState = resetCameraState(
    viewer.manifest.scene_aabb,
    measureViewerViewport().aspect,
  );
  applyCameraState();
}

function fitView() {
  if (viewer.status !== "ready") {
    return;
  }
  viewer.fitToScene = true;
  viewer.cameraState = fitCameraState(
    viewer.cameraState,
    viewer.manifest.scene_aabb,
    measureViewerViewport().aspect,
  );
  applyCameraState();
}

function orbit(deltaX, deltaY) {
  viewer.cameraState = orbitCameraState(viewer.cameraState, -deltaX * 0.28, -deltaY * 0.28);
  applyCameraState();
}

function pan(deltaX, deltaY) {
  viewer.fitToScene = false;
  viewer.cameraState = panCameraState(
    viewer.cameraState,
    deltaX,
    deltaY,
    Math.max(1, elements.viewer_canvas.clientHeight),
  );
  applyCameraState();
}

function bindControls() {
  elements.viewer_load_button.addEventListener("click", loadScene);
  elements.viewer_reset_button.addEventListener("click", resetView);
  elements.viewer_fit_button.addEventListener("click", fitView);
  elements.viewer_canvas.addEventListener("contextmenu", (event) => event.preventDefault());
  elements.viewer_canvas.addEventListener("pointerdown", (event) => {
    if (viewer.status !== "ready") {
      return;
    }
    const mode = event.button === 1 || event.button === 2 || event.shiftKey ? "pan" : "orbit";
    viewer.pointer = { id: event.pointerId, x: event.clientX, y: event.clientY, mode };
    elements.viewer_canvas.setPointerCapture(event.pointerId);
  });
  elements.viewer_canvas.addEventListener("pointermove", (event) => {
    if (!viewer.pointer || viewer.pointer.id !== event.pointerId || viewer.status !== "ready") {
      return;
    }
    const deltaX = event.clientX - viewer.pointer.x;
    const deltaY = event.clientY - viewer.pointer.y;
    viewer.pointer.x = event.clientX;
    viewer.pointer.y = event.clientY;
    if (viewer.pointer.mode === "pan") {
      pan(deltaX, deltaY);
    } else {
      orbit(deltaX, deltaY);
    }
  });
  const releasePointer = (event) => {
    if (viewer.pointer?.id === event.pointerId) {
      viewer.pointer = null;
    }
  };
  elements.viewer_canvas.addEventListener("pointerup", releasePointer);
  elements.viewer_canvas.addEventListener("pointercancel", releasePointer);
  elements.viewer_canvas.addEventListener("wheel", (event) => {
    if (viewer.status !== "ready") {
      return;
    }
    event.preventDefault();
    viewer.fitToScene = false;
    viewer.cameraState = zoomCameraState(viewer.cameraState, event.deltaY);
    applyCameraState();
  }, { passive: false });
  elements.viewer_canvas.addEventListener("keydown", (event) => {
    if (viewer.status !== "ready") {
      return;
    }
    const key = event.key.toLowerCase();
    const panStep = 18;
    const orbitStep = 4;
    if (key === "0") {
      resetView();
    } else if (key === "f") {
      fitView();
    } else if (key === "+" || key === "=") {
      viewer.fitToScene = false;
      viewer.cameraState = zoomCameraState(viewer.cameraState, -120);
      applyCameraState();
    } else if (key === "-" || key === "_") {
      viewer.fitToScene = false;
      viewer.cameraState = zoomCameraState(viewer.cameraState, 120);
      applyCameraState();
    } else if (["arrowleft", "arrowright", "arrowup", "arrowdown"].includes(key)) {
      if (event.shiftKey) {
        pan(key === "arrowleft" ? -panStep : key === "arrowright" ? panStep : 0, key === "arrowup" ? -panStep : key === "arrowdown" ? panStep : 0);
      } else {
        orbit(key === "arrowleft" ? orbitStep : key === "arrowright" ? -orbitStep : 0, key === "arrowup" ? orbitStep : key === "arrowdown" ? -orbitStep : 0);
      }
    } else {
      return;
    }
    event.preventDefault();
  });
}

function initialiseViewer() {
  findElements();
  bindControls();
  setViewerState("idle", "Scene not loaded", "Load the allowlisted scene to begin an official PlayCanvas RGB render.");
  window.addEventListener("beforeunload", () => {
    lifecycle.cancel();
    destroyRenderer();
  }, { once: true });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initialiseViewer, { once: true });
} else {
  initialiseViewer();
}
