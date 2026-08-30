(() => {
  "use strict";

  const DEFAULT_SOURCE_PATH = "/data/Desktop/Prashansa/major/scenebench/data/InteriorGS/0231_840445/3dgs_compressed.ply";
  const API = Object.freeze({
    status: "/api/status",
    outputs: "/api/outputs",
    inspect: "/api/inspect",
    decode: "/api/decode",
    roundtrip: "/api/roundtrip",
  });

  const elements = {};
  const state = {
    busy: false,
    canonicalPath: "",
    decodedSourcePath: "",
  };

  class RequestError extends Error {
    constructor(message, type = "request_error", status = 0) {
      super(message);
      this.name = "RequestError";
      this.type = type;
      this.status = status;
    }
  }

  function findElements() {
    const ids = [
      "server-indicator-dot",
      "server-status",
      "source-form",
      "source-path",
      "inspect-button",
      "source-summary",
      "source-report-badge",
      "source-fingerprint",
      "source-format",
      "source-count",
      "source-size",
      "source-schema",
      "decode-form",
      "decode-output-name",
      "decode-button",
      "decode-result",
      "decode-report-badge",
      "decode-runtime",
      "decode-memory",
      "decode-output-path",
      "decode-detail",
      "roundtrip-form",
      "canonical-path",
      "roundtrip-button",
      "roundtrip-result",
      "roundtrip-report-badge",
      "roundtrip-runtime",
      "roundtrip-output-path",
      "roundtrip-detail",
      "progress-region",
      "progress-icon",
      "progress-message",
      "error-panel",
      "error-heading",
      "error-message",
      "refresh-outputs-button",
      "outputs-list",
    ];

    ids.forEach((id) => {
      elements[id.replaceAll("-", "_")] = document.getElementById(id);
    });
  }

  function pick(root, ...paths) {
    for (const path of paths) {
      const parts = Array.isArray(path) ? path : String(path).split(".");
      let current = root;
      let found = true;
      for (const part of parts) {
        if (current === null || current === undefined || typeof current !== "object" || !(part in current)) {
          found = false;
          break;
        }
        current = current[part];
      }
      if (found && current !== null && current !== undefined && current !== "") {
        return current;
      }
    }
    return undefined;
  }

  function unwrap(payload, ...keys) {
    for (const key of keys) {
      const value = pick(payload, key);
      if (value !== undefined && value !== null && typeof value === "object") {
        return value;
      }
    }
    return payload;
  }

  function displayValue(value, fallback = "Not reported") {
    if (value === null || value === undefined || value === "") {
      return fallback;
    }
    if (typeof value === "object") {
      try {
        return JSON.stringify(value);
      } catch (_error) {
        return fallback;
      }
    }
    return String(value);
  }

  function setText(element, value, fallback) {
    if (element) {
      element.textContent = displayValue(value, fallback);
    }
  }

  function finiteNumber(value) {
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
    if (typeof value === "string" && value.trim() !== "") {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : undefined;
    }
    return undefined;
  }

  function formatInteger(value) {
    const number = finiteNumber(value);
    return number === undefined ? displayValue(value) : new Intl.NumberFormat().format(Math.round(number));
  }

  function formatBytes(value, explicitUnit) {
    let bytes = finiteNumber(value);
    if (bytes === undefined) {
      return displayValue(value);
    }
    if (explicitUnit && /^(kb|kib)$/i.test(explicitUnit)) {
      bytes *= 1024;
    } else if (explicitUnit && /^(mb|mib)$/i.test(explicitUnit)) {
      bytes *= 1024 * 1024;
    } else if (explicitUnit && /^(gb|gib)$/i.test(explicitUnit)) {
      bytes *= 1024 * 1024 * 1024;
    }
    if (bytes < 1024) {
      return `${Math.round(bytes)} B`;
    }
    const units = ["KB", "MB", "GB", "TB"];
    let index = -1;
    let scaled = bytes;
    while (scaled >= 1024 && index < units.length - 1) {
      scaled /= 1024;
      index += 1;
    }
    return `${scaled.toFixed(scaled >= 10 ? 1 : 2)} ${units[index]}`;
  }

  function formatDuration(value, explicitUnit) {
    const number = finiteNumber(value);
    if (number === undefined) {
      return displayValue(value);
    }
    let milliseconds = number;
    if (explicitUnit && /^s(ec(ond)?s?)?$/i.test(explicitUnit)) {
      milliseconds = number * 1000;
    } else if (!explicitUnit && number < 10) {
      milliseconds = number * 1000;
    }
    if (milliseconds < 1000) {
      return `${milliseconds.toFixed(milliseconds >= 100 ? 0 : 1)} ms`;
    }
    return `${(milliseconds / 1000).toFixed(milliseconds >= 10000 ? 1 : 2)} s`;
  }

  function formatFingerprint(value) {
    if (value && typeof value === "object") {
      const algorithm = pick(value, "algorithm", "method", "name");
      const digest = pick(value, "digest", "value", "sha256", "hex");
      if (algorithm && digest) {
        return `${String(algorithm)}: ${String(digest)}`;
      }
      if (digest) {
        return String(digest);
      }
    }
    return displayValue(value);
  }

  function schemaSummary(report) {
    const schema = pick(report, "schema", "canonical_schema", "properties", "property_names");
    if (schema === undefined) {
      const element = pick(report, "element", "element_name");
      const propertyCount = pick(report, "property_count", "schema.property_count");
      if (element || propertyCount) {
        const parts = [];
        if (element) {
          parts.push(String(element));
        }
        if (propertyCount) {
          parts.push(`${formatInteger(propertyCount)} properties`);
        }
        return parts.join(" · ");
      }
      if (pick(report, "chunk_count") !== undefined || pick(report, "sh_count") !== undefined) {
        return "chunk bounds (18 float32) · vertex (4 packed uint32 fields) · sh (45 uchar fields)";
      }
      return "Not reported";
    }
    if (typeof schema === "string") {
      return schema;
    }
    if (Array.isArray(schema)) {
      const names = schema.map((item) => {
        if (item && typeof item === "object") {
          const name = pick(item, "name", "property", "field");
          const type = pick(item, "type", "dtype");
          return type ? `${displayValue(name, "property")}: ${String(type)}` : displayValue(name, "property");
        }
        return String(item);
      });
      return names.length > 14 ? `${names.slice(0, 14).join(", ")} … (${names.length} total)` : names.join(", ");
    }
    if (typeof schema === "object") {
      const properties = pick(schema, "properties", "fields", "names");
      if (Array.isArray(properties)) {
        return schemaSummary({ properties });
      }
      const format = pick(schema, "format", "encoding", "description");
      const count = pick(schema, "property_count", "count");
      if (format || count) {
        return [format, count ? `${formatInteger(count)} properties` : undefined].filter(Boolean).join(" · ");
      }
      try {
        return JSON.stringify(schema);
      } catch (_error) {
        return "Reported by server";
      }
    }
    return displayValue(schema);
  }

  function metricValue(report, keys) {
    for (const key of keys) {
      const value = pick(report, key);
      if (value !== undefined) {
        return { value, unit: undefined };
      }
    }
    return { value: undefined, unit: undefined };
  }

  function bytesMetric(report, bases) {
    for (const base of bases) {
      const bytes = pick(report, `${base}_bytes`);
      if (bytes !== undefined) {
        return formatBytes(bytes);
      }
      const megabytes = pick(report, `${base}_mb`, `${base}_MiB`);
      if (megabytes !== undefined) {
        return formatBytes(megabytes, "mb");
      }
      const direct = pick(report, base);
      if (direct !== undefined && typeof direct !== "object") {
        return formatBytes(direct);
      }
    }
    const nestedRuntime = pick(report, "runtime");
    if (nestedRuntime && nestedRuntime !== report) {
      return bytesMetric(nestedRuntime, bases);
    }
    return "Not reported";
  }

  function durationMetric(report) {
    const milliseconds = pick(report, "runtime_ms", "duration_ms", "elapsed_ms", "wall_time_ms", "runtime_milliseconds");
    if (milliseconds !== undefined) {
      return formatDuration(milliseconds, "ms");
    }
    const seconds = pick(report, "runtime_seconds", "duration_seconds", "elapsed_seconds", "wall_time_seconds");
    if (seconds !== undefined) {
      return formatDuration(seconds, "s");
    }
    const nestedRuntime = pick(report, "runtime");
    if (nestedRuntime && nestedRuntime !== report) {
      return durationMetric(nestedRuntime);
    }
    const direct = pick(report, "runtime", "duration", "elapsed", "wall_time");
    return direct === undefined ? "Not reported" : formatDuration(direct);
  }

  function outputPath(payload, report) {
    const path = pick(
      payload,
      "output_path",
      "decoded_path",
      "canonical_path",
      "output.path",
      "output.file_path",
      "output.name",
      "artifact.path",
      "artifact.name",
    ) ?? pick(
      report,
      "output_path",
      "decoded_path",
      "canonical_path",
      "output.path",
      "output.file_path",
      "output.name",
      "artifact.path",
      "artifact.name",
    );
    if (typeof path !== "string" || !path) {
      return "";
    }
    if (path.startsWith("/") || path.startsWith("outputs/")) {
      return path;
    }
    return `outputs/milestone1/${path}`;
  }

  function sourcePath() {
    return elements.source_path.value.trim();
  }

  function optionalOutputName() {
    return elements.decode_output_name.value.trim();
  }

  function setServerStatus(label, status) {
    setText(elements.server_status, label);
    elements.server_indicator_dot.parentElement.classList.remove("is-ready", "is-offline");
    if (status === "ready") {
      elements.server_indicator_dot.parentElement.classList.add("is-ready");
    } else if (status === "offline") {
      elements.server_indicator_dot.parentElement.classList.add("is-offline");
    }
  }

  function setProgress(message, status = "idle") {
    setText(elements.progress_message, message);
    elements.progress_region.classList.remove("is-busy", "is-success", "is-error");
    elements.progress_icon.textContent = status === "success" ? "✓" : status === "error" ? "!" : "✦";
    if (status === "busy") {
      elements.progress_region.classList.add("is-busy");
    } else if (status === "success") {
      elements.progress_region.classList.add("is-success");
    } else if (status === "error") {
      elements.progress_region.classList.add("is-error");
    }
  }

  function clearError() {
    elements.error_panel.hidden = true;
  }

  function showError(context, error) {
    const errorType = error && error.type ? String(error.type) : "request_error";
    let message = error && error.message ? String(error.message) : "The local server did not return a useful error message.";
    if (errorType === "network_error") {
      message = "The local server could not be reached. Start it with `python3 -m scene_agent`, then retry.";
    } else if (errorType === "validation_error" || errorType === "invalid_path") {
      message = `${message} Check that the path points to a supported compressed PLY and that it is readable by the server.`;
    }
    setText(elements.error_heading, `${context} could not finish`);
    setText(elements.error_message, message);
    elements.error_panel.hidden = false;
    setProgress(message, "error");
  }

  function setBusy(busy) {
    state.busy = busy;
    elements.inspect_button.disabled = busy;
    elements.decode_button.disabled = busy;
    elements.roundtrip_button.disabled = busy || !state.canonicalPath;
    elements.refresh_outputs_button.disabled = busy;
    document.body.toggleAttribute("data-request-in-flight", busy);
  }

  async function requestJson(url, options = {}) {
    let response;
    try {
      response = await fetch(url, {
        ...options,
        headers: {
          Accept: "application/json",
          ...(options.body ? { "Content-Type": "application/json" } : {}),
          ...(options.headers || {}),
        },
      });
    } catch (_error) {
      throw new RequestError("The local server could not be reached.", "network_error");
    }

    let payload;
    try {
      payload = await response.json();
    } catch (_error) {
      throw new RequestError(`The server returned an unreadable response (HTTP ${response.status}).`, "invalid_response", response.status);
    }

    if (!response.ok || payload?.ok === false) {
      const error = payload?.error;
      const type = typeof error === "object" && error !== null ? pick(error, "type", "code") : undefined;
      const message = typeof error === "string" ? error : pick(error, "message", "detail") ?? pick(payload, "message", "detail");
      throw new RequestError(displayValue(message, `The server rejected the request (HTTP ${response.status}).`), displayValue(type, "request_error"), response.status);
    }
    return payload;
  }

  async function loadStatus() {
    try {
      const payload = await requestJson(API.status);
      const report = unwrap(payload, "status", "service", "result");
      const version = pick(report, "version", "api_version");
      setServerStatus(version ? `Local server ready · ${String(version)}` : "Local server ready", "ready");
    } catch (_error) {
      setServerStatus("Local server offline", "offline");
    }
  }

  function outputRecords(payload) {
    const list = pick(payload, "outputs", "items", "artifacts", "files", "results");
    if (Array.isArray(list)) {
      return list;
    }
    if (list && typeof list === "object") {
      return Object.entries(list).map(([name, value]) => (typeof value === "object" ? { name, ...value } : { name, value }));
    }
    return [];
  }

  function createOutputItem(record) {
    const item = document.createElement("li");
    item.className = "output-item";

    const name = document.createElement("span");
    name.className = "output-item-name";
    const label = typeof record === "string" ? record : pick(record, "name", "filename", "path", "output_path", "relative_path");
    name.textContent = displayValue(label, "Unnamed artifact");
    item.appendChild(name);

    const metadata = document.createElement("span");
    metadata.className = "output-item-meta";
    const size = typeof record === "object" && record !== null ? pick(record, "size_bytes", "bytes") : undefined;
    const modified = typeof record === "object" && record !== null ? pick(record, "modified", "modified_at", "created_at") : undefined;
    const details = [];
    if (size !== undefined) {
      details.push(formatBytes(size));
    }
    if (modified !== undefined) {
      details.push(displayValue(modified));
    }
    metadata.textContent = details.length ? details.join(" · ") : "Milestone output";
    item.appendChild(metadata);
    return item;
  }

  async function loadOutputs(options = {}) {
    try {
      const payload = await requestJson(API.outputs);
      const records = outputRecords(payload);
      elements.outputs_list.replaceChildren();
      if (!records.length) {
        const empty = document.createElement("li");
        empty.className = "empty-output";
        empty.textContent = "No published milestone outputs yet.";
        elements.outputs_list.appendChild(empty);
      } else {
        records.forEach((record) => elements.outputs_list.appendChild(createOutputItem(record)));
      }
      return true;
    } catch (error) {
      const empty = document.createElement("li");
      empty.className = "empty-output";
      empty.textContent = "Outputs could not be loaded. Check the local server and refresh.";
      elements.outputs_list.replaceChildren(empty);
      if (!options.quiet) {
        showError("Loading outputs", error);
      }
      return false;
    }
  }

  function renderInspection(payload) {
    const report = unwrap(payload, "inspection", "report", "result");
    const fingerprint = pick(report, "fingerprint", "source_fingerprint", "sha256", "digest", "source.digest", "source.sha256") ?? pick(payload, "fingerprint", "source_fingerprint", "sha256", "digest", "source.sha256");
    const format = pick(report, "format", "source_format", "file_format", "encoding") ?? pick(payload, "format", "source_format", "file_format", "encoding");
    const count = pick(report, "gaussian_count", "vertex_count", "count", "scene.gaussian_count") ?? pick(payload, "gaussian_count", "vertex_count", "count");
    const size = pick(report, "size_bytes", "file_size_bytes", "bytes", "source.size_bytes") ?? pick(payload, "size_bytes", "file_size_bytes", "bytes", "source.size_bytes");

    setText(elements.source_fingerprint, formatFingerprint(fingerprint));
    setText(elements.source_format, format);
    setText(elements.source_count, count === undefined ? undefined : formatInteger(count));
    setText(elements.source_size, size === undefined ? undefined : formatBytes(size));
    setText(elements.source_schema, schemaSummary(report));
    elements.source_summary.hidden = false;
    elements.source_report_badge.className = "status-badge status-badge-neutral";
    elements.source_report_badge.textContent = "Inspected";
  }

  function renderDecode(payload) {
    const report = unwrap(payload, "report", "decode_report", "result");
    const path = outputPath(payload, report);
    setText(elements.decode_runtime, durationMetric(report));
    setText(elements.decode_memory, bytesMetric(report, ["peak_rss", "peak_ram", "peak_memory", "memory"]));
    setText(elements.decode_output_path, path);
    elements.decode_result.hidden = false;
    elements.decode_report_badge.className = "status-badge status-badge-success";
    elements.decode_report_badge.textContent = "Complete";

    if (path) {
      state.canonicalPath = path;
      state.decodedSourcePath = sourcePath();
      elements.canonical_path.value = path;
      elements.roundtrip_button.disabled = state.busy;
      setText(elements.decode_detail, "The decoded path is pinned below and ready for a no-op round trip.");
    } else {
      setText(elements.decode_detail, "The server completed the decode but did not return an output path, so round trip is unavailable.");
    }
  }

  function renderRoundtrip(payload) {
    const report = unwrap(payload, "report", "roundtrip_report", "result");
    const path = outputPath(payload, report) || state.canonicalPath;
    const comparison = pick(payload, "comparison") ?? pick(report, "comparison");
    const equal = pick(comparison, "equal");
    setText(elements.roundtrip_runtime, durationMetric(report));
    setText(elements.roundtrip_output_path, path);
    elements.roundtrip_result.hidden = false;
    if (equal === false) {
      elements.roundtrip_report_badge.className = "status-badge status-badge-error";
      elements.roundtrip_report_badge.textContent = "Differences found";
      setText(elements.roundtrip_detail, "The server reported a difference after writing. Review the output before using it; no source file was edited.");
    } else {
      elements.roundtrip_report_badge.className = "status-badge status-badge-success";
      elements.roundtrip_report_badge.textContent = "Verified";
      setText(elements.roundtrip_detail, "No scene parameters were edited. The canonical schema and row order were checked after writing.");
    }
    return equal !== false;
  }

  async function handleInspect(event) {
    event.preventDefault();
    if (state.busy) {
      return;
    }
    const path = sourcePath();
    if (!path) {
      showError("Inspecting source", new RequestError("Enter a local compressed PLY path before inspecting.", "validation_error"));
      elements.source_path.focus();
      return;
    }
    clearError();
    setBusy(true);
    setProgress("Inspecting the compressed source schema…", "busy");
    try {
      const payload = await requestJson(API.inspect, {
        method: "POST",
        body: JSON.stringify({ source_path: path }),
      });
      renderInspection(payload);
      setProgress("Source inspected. No source bytes were changed.", "success");
      setServerStatus("Local server ready", "ready");
    } catch (error) {
      showError("Inspecting source", error);
    } finally {
      setBusy(false);
    }
  }

  async function handleDecode(event) {
    event.preventDefault();
    if (state.busy) {
      return;
    }
    const path = sourcePath();
    if (!path) {
      showError("Decoding scene", new RequestError("Enter a local compressed PLY path before decoding.", "validation_error"));
      elements.source_path.focus();
      return;
    }
    clearError();
    setBusy(true);
    setProgress("Decoding on the CPU and validating the new output…", "busy");
    try {
      const body = { source_path: path };
      const outputName = optionalOutputName();
      if (outputName) {
        body.output_name = outputName;
      }
      const payload = await requestJson(API.decode, {
        method: "POST",
        body: JSON.stringify(body),
      });
      renderDecode(payload);
      setProgress(state.canonicalPath ? "Decode complete. The canonical path is ready for round trip." : "Decode complete, but the response omitted an output path.", "success");
      setServerStatus("Local server ready", "ready");
      void loadOutputs({ quiet: true });
    } catch (error) {
      showError("Decoding scene", error);
    } finally {
      setBusy(false);
    }
  }

  async function handleRoundtrip(event) {
    event.preventDefault();
    if (state.busy) {
      return;
    }
    const path = state.canonicalPath || elements.canonical_path.value.trim();
    if (!path) {
      showError("Running round trip", new RequestError("Decode a scene successfully before running a round trip.", "validation_error"));
      return;
    }
    clearError();
    setBusy(true);
    setProgress("Reloading, writing, and comparing the canonical scene…", "busy");
    try {
      const body = { canonical_path: path };
      const payload = await requestJson(API.roundtrip, {
        method: "POST",
        body: JSON.stringify(body),
      });
      const matches = renderRoundtrip(payload);
      setProgress(matches ? "Round trip verified. No scene parameters were edited." : "Round trip completed with reported differences; review the validation result.", matches ? "success" : "error");
      setServerStatus("Local server ready", "ready");
      void loadOutputs({ quiet: true });
    } catch (error) {
      showError("Running round trip", error);
    } finally {
      setBusy(false);
    }
  }

  async function handleRefreshOutputs() {
    if (state.busy) {
      return;
    }
    clearError();
    setBusy(true);
    setProgress("Refreshing published milestone outputs…", "busy");
    try {
      const loaded = await loadOutputs();
      if (loaded) {
        setProgress("Output list refreshed.", "success");
      }
    } catch (_error) {
      // loadOutputs renders a useful error state itself.
    } finally {
      setBusy(false);
    }
  }

  function bindEvents() {
    elements.source_form.addEventListener("submit", handleInspect);
    elements.decode_form.addEventListener("submit", handleDecode);
    elements.roundtrip_form.addEventListener("submit", handleRoundtrip);
    elements.refresh_outputs_button.addEventListener("click", handleRefreshOutputs);
  }

  async function initialise() {
    findElements();
    bindEvents();
    elements.source_path.value = elements.source_path.value || DEFAULT_SOURCE_PATH;
    setServerStatus("Connecting to local server…", "unknown");
    setProgress("Checking the local server and published outputs…", "busy");
    await Promise.all([loadStatus(), loadOutputs({ quiet: true })]);
    setProgress("Ready when you are.", "idle");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialise, { once: true });
  } else {
    void initialise();
  }
})();
