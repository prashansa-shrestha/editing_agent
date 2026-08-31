"use strict";

export const CAMERA_DEFAULTS = Object.freeze({
  yawDegrees: -45,
  elevationDegrees: 28,
  fovDegrees: 50,
  fitMargin: 1.18,
  minElevationDegrees: -85,
  maxElevationDegrees: 85,
});

const MIN_DISTANCE = 1e-4;

function finiteNumber(value, label) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${label} must be finite`);
  }
  return value;
}

function finiteVector(value, label) {
  if (!Array.isArray(value) || value.length !== 3) {
    throw new Error(`${label} must contain three values`);
  }
  return value.map((item, index) => finiteNumber(item, `${label}[${index}]`));
}

function add(left, right) {
  return left.map((value, index) => value + right[index]);
}

function subtract(left, right) {
  return left.map((value, index) => value - right[index]);
}

function scale(vector, factor) {
  return vector.map((value) => value * factor);
}

function cross(left, right) {
  return [
    left[1] * right[2] - left[2] * right[1],
    left[2] * right[0] - left[0] * right[2],
    left[0] * right[1] - left[1] * right[0],
  ];
}

function normalize(vector, label) {
  const length = Math.hypot(...vector);
  if (!Number.isFinite(length) || length <= 1e-12) {
    throw new Error(`${label} is degenerate`);
  }
  return scale(vector, 1 / length);
}

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

function checkedState(state) {
  const target = finiteVector(state.target, "camera target");
  const yawDegrees = finiteNumber(state.yawDegrees, "camera yaw");
  const elevationDegrees = clamp(
    finiteNumber(state.elevationDegrees, "camera elevation"),
    CAMERA_DEFAULTS.minElevationDegrees,
    CAMERA_DEFAULTS.maxElevationDegrees,
  );
  const distance = Math.max(MIN_DISTANCE, finiteNumber(state.distance, "camera distance"));
  const radius = Math.max(MIN_DISTANCE, finiteNumber(state.radius, "scene radius"));
  return { target, yawDegrees, elevationDegrees, distance, radius };
}

export function validateAabb(value) {
  if (!value || typeof value !== "object") {
    throw new Error("manifest scene_aabb is missing");
  }
  const minimum = finiteVector(value.min, "scene_aabb.min");
  const maximum = finiteVector(value.max, "scene_aabb.max");
  for (let index = 0; index < 3; index += 1) {
    if (minimum[index] > maximum[index]) {
      throw new Error("manifest scene_aabb is inverted");
    }
  }
  const extents = subtract(maximum, minimum);
  if (extents.every((valueAtAxis) => valueAtAxis === 0)) {
    throw new Error("manifest scene_aabb is fully degenerate");
  }
  const center = add(minimum, scale(extents, 0.5));
  const radius = Math.max(MIN_DISTANCE, Math.hypot(...extents) * 0.5);
  if (![...center, ...extents, radius].every(Number.isFinite)) {
    throw new Error("manifest scene_aabb derived values are non-finite");
  }
  return { min: minimum, max: maximum, center, extents, radius };
}

export function fitDistance(radius, aspect, fovDegrees = CAMERA_DEFAULTS.fovDegrees) {
  finiteNumber(radius, "scene radius");
  finiteNumber(aspect, "camera aspect");
  finiteNumber(fovDegrees, "camera field of view");
  if (radius <= 0 || aspect <= 0 || fovDegrees <= 1 || fovDegrees >= 179) {
    throw new Error("scene radius, camera aspect, and field of view must be positive");
  }
  const verticalHalfAngle = (fovDegrees * Math.PI) / 360;
  const horizontalHalfAngle = Math.atan(Math.tan(verticalHalfAngle) * aspect);
  const limitingHalfAngle = Math.min(verticalHalfAngle, horizontalHalfAngle);
  const distance = CAMERA_DEFAULTS.fitMargin * radius / Math.sin(limitingHalfAngle);
  if (!Number.isFinite(distance) || distance <= 0) {
    throw new Error("fitted camera distance is invalid");
  }
  return distance;
}

export function resetCameraState(aabb, aspect) {
  const bounds = validateAabb(aabb);
  return checkedState({
    target: bounds.center,
    yawDegrees: CAMERA_DEFAULTS.yawDegrees,
    elevationDegrees: CAMERA_DEFAULTS.elevationDegrees,
    distance: fitDistance(bounds.radius, aspect),
    radius: bounds.radius,
  });
}

export function fitCameraState(state, aabb, aspect) {
  const current = checkedState(state);
  const bounds = validateAabb(aabb);
  return checkedState({
    ...current,
    target: bounds.center,
    distance: fitDistance(bounds.radius, aspect),
    radius: bounds.radius,
  });
}

export function orbitCameraState(state, deltaYawDegrees, deltaElevationDegrees) {
  const current = checkedState(state);
  return checkedState({
    ...current,
    yawDegrees: current.yawDegrees + finiteNumber(deltaYawDegrees, "yaw delta"),
    elevationDegrees: current.elevationDegrees + finiteNumber(deltaElevationDegrees, "elevation delta"),
  });
}

export function zoomCameraState(state, wheelDelta) {
  const current = checkedState(state);
  const multiplier = Math.exp(clamp(finiteNumber(wheelDelta, "zoom delta") * 0.0015, -2, 2));
  const minimum = Math.max(MIN_DISTANCE, current.radius * 0.05);
  const maximum = Math.max(minimum, current.radius * 100);
  return checkedState({
    ...current,
    distance: clamp(current.distance * multiplier, minimum, maximum),
  });
}

export function cameraPose(state) {
  const current = checkedState(state);
  const yaw = (current.yawDegrees * Math.PI) / 180;
  const elevation = (current.elevationDegrees * Math.PI) / 180;
  const horizontalDistance = current.distance * Math.cos(elevation);
  const offset = [
    horizontalDistance * Math.cos(yaw),
    horizontalDistance * Math.sin(yaw),
    current.distance * Math.sin(elevation),
  ];
  const position = add(current.target, offset);
  const near = Math.max(1e-4, current.distance - current.radius * 1.5);
  const far = Math.max(near + 1e-3, current.distance + current.radius * 1.5);
  if (![...position, near, far].every(Number.isFinite) || far <= near) {
    throw new Error("camera pose is invalid");
  }
  return {
    position,
    target: [...current.target],
    viewUp: [0, 0, 1],
    near,
    far,
  };
}

export function panCameraState(state, deltaX, deltaY, viewportHeight) {
  const current = checkedState(state);
  finiteNumber(deltaX, "horizontal pan delta");
  finiteNumber(deltaY, "vertical pan delta");
  finiteNumber(viewportHeight, "viewport height");
  if (viewportHeight <= 0) {
    throw new Error("viewport height must be positive");
  }
  const pose = cameraPose(current);
  const forward = normalize(subtract(pose.target, pose.position), "camera forward");
  const right = normalize(cross(forward, [0, 0, 1]), "camera right");
  const screenUp = normalize(cross(right, forward), "camera screen up");
  const worldPerPixel = (2 * current.distance * Math.tan((CAMERA_DEFAULTS.fovDegrees * Math.PI) / 360)) / viewportHeight;
  const translation = add(
    scale(right, -deltaX * worldPerPixel),
    scale(screenUp, deltaY * worldPerPixel),
  );
  return checkedState({ ...current, target: add(current.target, translation) });
}
