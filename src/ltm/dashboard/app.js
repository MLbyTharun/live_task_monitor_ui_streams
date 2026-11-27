/* Live Task Monitor dashboard — no build step, no dependencies. */
"use strict";

const $ = (selector) => document.querySelector(selector);
const senders = new Map();
const seenEvents = new Set();
let appState = null;
let eventSource = null;
let pollTimer = null;
let projectList = [];
let cameraConfigs = [];
let editingCameraId = null;

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ESCAPES[char]);

function timeOf(value) {
  const date = value ? new Date(value) : new Date();
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body && body.detail) detail = body.detail;
    } catch (_) { /* not JSON */ }
    throw new Error(detail);
  }
  return response.json();
}

function toast(message, isError = false) {
  const node = document.createElement("div");
  node.className = `toast${isError ? " error" : ""}`;
  node.textContent = message;
  $("#toasts").appendChild(node);
  setTimeout(() => node.remove(), 5200);
}

function setConnected(online) {
  $("#conn-dot").classList.toggle("online", Boolean(online));
}

/* ------------------------------------------------------------------ state */
async function refreshState() {
  try {
    appState = await api("/api/state");
    setConnected(true);
    render();
  } catch (error) {
    setConnected(false);
    console.error("state refresh failed", error);
  }
}

function render() {
  if (!appState) return;
  const state = appState;

  $("#model-badge").textContent = `${state.model.provider} · ${state.model.name}`;
  $("#source-badge").textContent = `tasks: ${state.task_source}`;
  $("#progress-text").textContent = `${state.progress.completed}/${state.progress.total} done`;

  renderProject(state);
  renderTask(state);
  renderVotes(state);
  renderStreams(state.streams);
  renderAnalysis(state);
  renderConfigErrors(state.config_errors);
  renderButtons(state);
}

/* ---------------------------------------------------------------- project */
function renderProject(state) {
  const project = state.project;
  const label = project ? project.name || project.id : "no project";
  $("#project-badge").textContent = label;
  $("#project-status").textContent = project
    ? `active: ${project.name || project.id} · ${project.id}`
    : "select a project to load tasks";
}

async function loadProjects() {
  const select = $("#project-select");
  const current = appState && appState.project ? appState.project.id : "";
  try {
    const data = await api("/api/projects");
    projectList = data.projects || [];

    if (current && !projectList.some((project) => project.id === current) && appState.project) {
      projectList.unshift({ id: current, name: appState.project.name || current });
    }

    select.innerHTML = "";
    if (!projectList.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "No projects available — paste an ID";
      select.appendChild(option);
    } else {
      for (const project of projectList) {
        const option = document.createElement("option");
        option.value = project.id;
        option.textContent = `${project.name || project.id} (${project.id})`;
        select.appendChild(option);
      }
      if (current) select.value = current;
    }

    const errorBox = $("#project-error");
    if (data.error) {
      errorBox.classList.remove("hidden");
      errorBox.innerHTML = `<strong>Project list unavailable</strong><br>${esc(data.error)}<br>
        You can still paste a project ID above.`;
    } else {
      errorBox.classList.add("hidden");
      errorBox.innerHTML = "";
    }
  } catch (error) {
    select.innerHTML = `<option value="">Projects unavailable — paste an ID</option>`;
    const errorBox = $("#project-error");
    errorBox.classList.remove("hidden");
    errorBox.innerHTML = `<strong>Project list unavailable</strong><br>${esc(error.message)}`;
  }
}

async function loadSelectedProject() {
  const manual = $("#project-manual").value.trim();
  const selected = $("#project-select").value;
  const projectId = manual || selected;
  if (!projectId) {
    toast("Choose a project or paste a project ID.", true);
    return;
  }

  const known = projectList.find((project) => project.id === projectId);
  const name = known ? known.name : projectId;
  const button = $("#btn-project-load");
  button.disabled = true;
  button.textContent = "Loading…";
  try {
    await api("/api/project", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: projectId, name }),
    });
    $("#project-manual").value = "";
    await refreshState();
    await loadCameras();
    toast(`Project selected: ${name}`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "Load project";
  }
}

/* ----------------------------------------------------------------- cameras */
async function loadCameras() {
  try {
    const data = await api("/api/cameras");
    cameraConfigs = data.cameras || [];
  } catch (error) {
    cameraConfigs = [];
    console.error("camera config load failed", error);
  }
}

function cameraById(cameraId) {
  return cameraConfigs.find((camera) => camera.id === cameraId) || null;
}

function openCameraDialog(camera) {
  editingCameraId = camera ? camera.id : null;
  $("#camera-dialog-title").textContent = camera ? "Edit camera" : "Add camera";
  $("#camera-name").value = camera ? camera.name || "" : "";
  $("#camera-id").value = camera ? camera.id : "";
  $("#camera-id").disabled = Boolean(camera);
  $("#camera-type").value = camera ? camera.type : "video";
  $("#camera-url").value = camera ? camera.url || "" : "";
  $("#camera-fps").value = camera ? camera.fps_limit || 2 : 2;
  $("#camera-enabled").checked = camera ? Boolean(camera.enabled) : true;
  $("#btn-camera-delete").classList.toggle("hidden", !camera);
  $("#camera-test-result").classList.add("hidden");
  toggleCameraUrl();
  $("#camera-dialog").showModal();
}

function closeCameraDialog() {
  $("#camera-dialog").close();
  editingCameraId = null;
}

function toggleCameraUrl() {
  const isVideo = $("#camera-type").value === "video";
  $("#camera-url-field").classList.toggle("hidden", !isVideo);
}

function cameraPayload() {
  const type = $("#camera-type").value;
  const name = $("#camera-name").value.trim();
  const rawId = $("#camera-id").value.trim() || name.toLowerCase().replace(/[^a-z0-9_.-]+/g, "-");
  return {
    id: rawId || undefined,
    name,
    type,
    url: type === "video" ? $("#camera-url").value.trim() : "",
    enabled: $("#camera-enabled").checked,
    fps_limit: Number($("#camera-fps").value) || 2,
  };
}

async function saveCamera(event) {
  event.preventDefault();
  const payload = cameraPayload();
  if (!payload.name) {
    toast("Give the camera a name.", true);
    return;
  }
  if (payload.type === "video" && payload.enabled && !payload.url) {
    toast("An enabled video camera needs a stream URL.", true);
    return;
  }

  const button = $("#btn-camera-save");
  button.disabled = true;
  button.textContent = "Saving…";
  try {
    await api("/api/cameras", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    closeCameraDialog();
    await refreshState();
    await loadCameras();
    toast(`Camera saved: ${payload.name}`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "Save camera";
  }
}

async function testCamera() {
  const payload = cameraPayload();
  if (payload.type === "video" && !payload.url) {
    toast("Enter a stream URL first.", true);
    return;
  }
  const box = $("#camera-test-result");
  box.className = "test-result";
  box.textContent = "Testing…";
  try {
    const result = await api("/api/cameras/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (result.ok) {
      box.className = "test-result ok";
      box.textContent = result.type === "push"
        ? `Push stream is connected (${result.frames || 0} frames received).`
        : `Connected — ${result.width}×${result.height} frame received.`;
    } else {
      box.className = "test-result error";
      box.textContent = result.error || "No frame received.";
    }
  } catch (error) {
    box.className = "test-result error";
    box.textContent = error.message;
  }
}

async function deleteCamera(cameraId) {
  const camera = cameraById(cameraId);
  if (!camera) return;
  if (!confirm(`Delete camera "${camera.name || camera.id}"?`)) return;
  try {
    await api(`/api/cameras/${encodeURIComponent(cameraId)}`, { method: "DELETE" });
    await refreshState();
    await loadCameras();
    toast(`Camera deleted: ${camera.name || camera.id}`);
  } catch (error) {
    toast(error.message, true);
  }
}

function renderTask(state) {
  const task = state.current_task;
  const container = $("#current-task");

  if (!task) {
    const allDone = state.progress.total > 0 && state.progress.remaining === 0;
    container.innerHTML = `<div class="empty">${
      allDone
        ? "🎉 All tasks are completed.<br>Reset progress or refresh tasks to run again."
        : "No active task. Check the task source in <code>config/config.yaml</code>."
    }</div>`;
  } else {
    container.innerHTML = `
      <div class="task-title">${esc(task.title)}</div>
      ${task.description ? `<p class="task-desc">${esc(task.description)}</p>` : ""}
      ${
        task.instructions
          ? `<details class="instructions"><summary>Work instructions</summary><p>${esc(task.instructions)}</p></details>`
          : ""
      }
      <div class="chips">
        ${task.action_type ? `<span class="chip">${esc(task.action_type)}</span>` : ""}
        ${task.expected_time_sec ? `<span class="chip">~${Math.round(task.expected_time_sec)}s</span>` : ""}
        ${task.success_message ? `<span class="chip ok">${esc(task.success_message)}</span>` : ""}
      </div>`;
  }

  $("#next-task").textContent = state.next_task ? state.next_task.title : "—";
  const pct = state.progress.total ? (state.progress.completed / state.progress.total) * 100 : 0;
  $("#progress-fill").style.width = `${pct}%`;
}

function renderVotes(state) {
  const total = Math.max(1, state.votes_required || 1);
  const filled = Math.min(state.votes || 0, total);
  $("#vote-dots").innerHTML = Array.from({ length: total }, (_, index) =>
    `<span class="vote-dot${index < filled ? " filled" : ""}"></span>`
  ).join("");
  $("#vote-text").textContent =
    `${filled}/${total} · min confidence ${Math.round((state.min_confidence || 0) * 100)}%`;
}

function renderButtons(state) {
  const hasTask = Boolean(state.current_task);
  $("#btn-analyze").disabled = !hasTask;
  $("#btn-complete").disabled = !hasTask;
}

function renderConfigErrors(errors) {
  const box = $("#config-errors");
  if (!errors || !errors.length) {
    box.classList.add("hidden");
    box.innerHTML = "";
    return;
  }
  box.classList.remove("hidden");
  box.innerHTML = `<strong>Configuration needs attention</strong><ul>${errors
    .map((error) => `<li>${esc(error)}</li>`)
    .join("")}</ul>`;
}

/* ---------------------------------------------------------------- streams */
function renderStreams(streams) {
  const container = $("#streams");
  const existing = new Map(
    Array.from(container.children).map((node) => [node.dataset.id, node])
  );

  $("#stream-count").textContent =
    `${streams.filter((stream) => stream.connected).length}/${streams.length} online`;

  for (const stream of streams) {
    let tile = existing.get(stream.id);
    if (!tile) {
      tile = document.createElement("article");
      tile.className = "stream";
      tile.dataset.id = stream.id;
      tile.innerHTML = `
        <div class="stream-media">
          <img alt="${esc(stream.name)}" />
          <div class="offline">No signal</div>
        </div>
        <div class="stream-info">
          <div><strong class="stream-name"></strong><span class="stream-type"></span></div>
          <div class="stream-status"><span class="led"></span><span class="status-text"></span></div>
          <div class="stream-controls">
            <button class="ghost small edit-camera" type="button">Edit</button>
            <button class="ghost small danger delete-camera" type="button">Delete</button>
          </div>
        </div>
        <button class="ghost small camera-btn hidden" type="button">Use my camera</button>`;
      tile.querySelector("img").addEventListener("error", (event) => {
        event.target.style.display = "none";
      });
      container.appendChild(tile);
    }

    const image = tile.querySelector("img");
    const offline = tile.querySelector(".offline");
    const enabled = stream.enabled !== false;
    tile.querySelector(".stream-name").textContent = stream.name;
    tile.querySelector(".stream-type").textContent = stream.type;
    tile.querySelector(".status-text").textContent = !enabled
      ? "disabled"
      : stream.connected
        ? `${stream.fps.toFixed(1)} fps · ${stream.frames} frames`
        : stream.error || "offline";
    offline.textContent = enabled ? "No signal" : "Disabled";

    tile.classList.toggle("offline", !stream.connected || !enabled);
    tile.classList.toggle("disabled", !enabled);
    if (stream.connected && enabled) {
      offline.style.display = "none";
      image.style.display = "block";
      const last = Number(tile.dataset.snapAt || 0);
      if (Date.now() - last > 1500) {
        image.src = `/api/streams/${encodeURIComponent(stream.id)}/snapshot?t=${Date.now()}`;
        tile.dataset.snapAt = String(Date.now());
      }
    } else {
      image.style.display = "none";
      offline.style.display = "flex";
      if (!enabled && senders.has(stream.id)) stopCamera(stream.id);
    }

    const cameraButton = tile.querySelector(".camera-btn");
    if (stream.type === "push" && enabled) {
      cameraButton.classList.remove("hidden");
      const sending = senders.has(stream.id);
      cameraButton.textContent = sending ? "Stop camera" : "Use my camera";
      cameraButton.classList.toggle("active", sending);
    } else {
      cameraButton.classList.add("hidden");
    }

    existing.delete(stream.id);
  }

  for (const leftover of existing.values()) leftover.remove();
}

/* --------------------------------------------------------------- analysis */
function renderAnalysis(state) {
  const record = state.last_analysis;
  const container = $("#last-analysis");
  $("#analysis-time").textContent = record ? timeOf(record.timestamp) : "";

  if (!record) {
    container.innerHTML = `<div class="empty">No analysis yet. It runs automatically every ${state.analysis_interval_seconds}s.</div>`;
    $("#history").innerHTML = "";
    return;
  }

  if (record.error) {
    container.innerHTML = `<div class="analysis-error">${esc(record.error)}</div>`;
  } else if (record.verdict) {
    const verdict = record.verdict;
    const yes = verdict.task_completed;
    container.innerHTML = `
      <div class="verdict-head">
        <span class="verdict ${yes ? "yes" : "no"}">${yes ? "Completed" : "Not yet"}</span>
        <span class="confidence">${Math.round((verdict.confidence || 0) * 100)}% confidence</span>
      </div>
      <div class="conf-bar"><div class="conf-fill ${yes ? "yes" : ""}" style="width:${Math.round((verdict.confidence || 0) * 100)}%"></div></div>
      <p class="reasoning">${esc(verdict.reasoning || "No reasoning returned.")}</p>
      ${
        verdict.evidence && verdict.evidence.length
          ? `<ul class="evidence">${verdict.evidence.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>`
          : ""
      }
      <div class="analysis-meta">
        <span>model ${esc(verdict.model || state.model.name)}</span>
        ${verdict.latency_ms ? `<span>${verdict.latency_ms} ms</span>` : ""}
        <span>votes ${record.votes}/${record.votes_required}</span>
        <span>${record.stream_ids.length} frame(s)</span>
      </div>
      ${verdict.parse_error ? `<div class="warn-note">The model reply was not valid JSON; treated as not completed.</div>` : ""}`;
  }

  const history = (state.history || []).slice(0, 7);
  $("#history").innerHTML = history.length
    ? history
        .map((item) => {
          const verdict = item.verdict;
          const label = item.error
            ? `<span class="no">error</span>`
            : verdict
              ? `<span class="${verdict.task_completed ? "yes" : "no"}">${verdict.task_completed ? "yes" : "no"} · ${Math.round((verdict.confidence || 0) * 100)}%</span>`
              : `<span class="no">—</span>`;
          return `<div class="history-row"><span>${timeOf(item.timestamp)}</span><strong>${esc(item.task_title)}</strong>${label}</div>`;
        })
        .join("")
    : "";
}

/* ----------------------------------------------------------------- events */
function eventText(event) {
  const data = event.data || {};
  switch (event.type) {
    case "analysis": {
      const verdict = data.verdict;
      if (!verdict) return "Analysis without a verdict";
      const label = verdict.task_completed ? "completed" : "not completed";
      return `Analysis · ${label} (${Math.round((verdict.confidence || 0) * 100)}%) — ${esc(
        (verdict.reasoning || "").slice(0, 120)
      )}`;
    }
    case "task_completed":
      return `✅ <strong>Task completed:</strong> ${esc(data.task && data.task.title)}${
        data.next_task ? ` → next: <strong>${esc(data.next_task.title)}</strong>` : " → no tasks remaining"
      }`;
    case "task_activated":
      return `▶ Now active: <strong>${esc(data.task && data.task.title)}</strong>`;
    case "project_changed":
      return `Project switched to <strong>${esc(data.project && (data.project.name || data.project.id))}</strong> (${data.cameras || 0} cameras)`;
    case "cameras_changed":
      return data.action === "deleted"
        ? `Camera removed: ${esc(data.camera && data.camera.id)}`
        : `Camera saved: <strong>${esc(data.camera && (data.camera.name || data.camera.id))}</strong>`;
    case "all_tasks_completed":
      return `🎉 All tasks completed (${data.total || 0})`;
    case "analysis_error":
      return `⚠ ${esc(data.error)}`;
    case "task_source_error":
      return `⚠ Task API: ${esc(data.error)}`;
    case "engine_error":
      return `⚠ Engine: ${esc(data.error)}`;
    case "tasks_refreshed":
      return `↻ ${data.count} task(s) from ${esc(data.source)}`;
    case "progress_reset":
      return "Progress reset";
    case "engine_started":
      return `Engine started · model ${esc(data.model)}`;
    case "engine_stopped":
      return "Engine stopped";
    case "analysis_skipped":
      return `Skipped: ${esc(data.reason)}`;
    default:
      return esc(event.type);
  }
}

function addEvent(event) {
  if (!event || seenEvents.has(event.seq)) return;
  seenEvents.add(event.seq);

  const item = document.createElement("li");
  item.className = `event ${event.type}`;
  item.innerHTML = `<span class="event-time">${timeOf(event.ts)}</span><span class="event-body">${eventText(event)}</span>`;
  const feed = $("#events");
  feed.prepend(item);
  while (feed.children.length > 100) feed.lastChild.remove();

  if (event.type === "task_completed") {
    const data = event.data || {};
    toast(
      data.next_task
        ? `Task completed: ${data.task.title} → next: ${data.next_task.title}`
        : `Task completed: ${data.task.title} — all tasks done`
    );
  }

  if (
    ["task_completed", "task_activated", "all_tasks_completed", "tasks_refreshed", "progress_reset", "task_source_error", "project_changed", "cameras_changed"].includes(
      event.type
    )
  ) {
    refreshState();
  }

  if (event.type === "project_changed") {
    loadCameras();
  }
}

function connectEvents() {
  if (eventSource) eventSource.close();
  eventSource = new EventSource("/api/events");
  eventSource.onopen = () => setConnected(true);
  eventSource.onmessage = (message) => {
    try {
      addEvent(JSON.parse(message.data));
    } catch (error) {
      console.error("bad event", error);
    }
  };
  eventSource.onerror = () => setConnected(false);
}

/* ----------------------------------------------------------------- camera */
async function toggleCamera(streamId, button) {
  if (senders.has(streamId)) {
    stopCamera(streamId);
    return;
  }
  try {
    const media = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: false,
    });
    const video = document.createElement("video");
    video.srcObject = media;
    video.muted = true;
    video.playsInline = true;
    await video.play();

    const canvas = document.createElement("canvas");
    let busy = false;
    const timer = setInterval(async () => {
      if (busy || !video.videoWidth || !video.videoHeight) return;
      busy = true;
      try {
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        canvas.getContext("2d").drawImage(video, 0, 0);
        const blob = await new Promise((resolve) =>
          canvas.toBlob(resolve, "image/jpeg", 0.8)
        );
        if (!blob) return;
        await fetch(`/api/ingest/${encodeURIComponent(streamId)}`, {
          method: "POST",
          headers: { "Content-Type": "image/jpeg" },
          body: blob,
        });
      } catch (_) {
        /* transient network errors are fine; next frame retries */
      } finally {
        busy = false;
      }
    }, 500);

    senders.set(streamId, { media, video, timer });
    button.textContent = "Stop camera";
    button.classList.add("active");
    toast("Camera connected — frames are being sent to the monitor.");
  } catch (error) {
    toast(`Camera unavailable: ${error.message}`, true);
  }
}

function stopCamera(streamId) {
  const sender = senders.get(streamId);
  if (!sender) return;
  clearInterval(sender.timer);
  sender.media.getTracks().forEach((track) => track.stop());
  sender.video.srcObject = null;
  senders.delete(streamId);
  const button = document.querySelector(
    `.stream[data-id="${CSS.escape(streamId)}"] .camera-btn`
  );
  if (button) {
    button.textContent = "Use my camera";
    button.classList.remove("active");
  }
}

/* ------------------------------------------------------------------ wiring */
$("#btn-analyze").addEventListener("click", async () => {
  const button = $("#btn-analyze");
  button.disabled = true;
  button.textContent = "Analysing…";
  try {
    await api("/api/analyze", { method: "POST" });
    await refreshState();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.textContent = "Analyze now";
    button.disabled = !(appState && appState.current_task);
  }
});

$("#btn-refresh").addEventListener("click", async () => {
  try {
    await api("/api/tasks/refresh", { method: "POST" });
    await refreshState();
    toast("Task list refreshed.");
  } catch (error) {
    toast(error.message, true);
  }
});

$("#btn-complete").addEventListener("click", async () => {
  if (!appState || !appState.current_task) return;
  if (!confirm(`Mark "${appState.current_task.title}" as completed?`)) return;
  try {
    await api(`/api/tasks/${encodeURIComponent(appState.current_task.id)}/complete`, {
      method: "POST",
    });
    await refreshState();
  } catch (error) {
    toast(error.message, true);
  }
});

$("#btn-reset").addEventListener("click", async () => {
  if (!confirm("Clear all completion progress? Every task will become pending again.")) return;
  try {
    await api("/api/state/reset", { method: "POST" });
    await refreshState();
    toast("Progress reset.");
  } catch (error) {
    toast(error.message, true);
  }
});

$("#streams").addEventListener("click", (event) => {
  const tile = event.target.closest(".stream");
  if (!tile) return;
  const streamId = tile.dataset.id;

  if (event.target.closest(".camera-btn")) {
    toggleCamera(streamId, event.target.closest(".camera-btn"));
    return;
  }
  if (event.target.closest(".edit-camera")) {
    openCameraDialog(cameraById(streamId));
    return;
  }
  if (event.target.closest(".delete-camera")) {
    deleteCamera(streamId);
  }
});

$("#btn-project-load").addEventListener("click", loadSelectedProject);
$("#btn-project-reload").addEventListener("click", loadProjects);
$("#btn-add-camera").addEventListener("click", () => openCameraDialog(null));
$("#camera-form").addEventListener("submit", saveCamera);
$("#camera-type").addEventListener("change", toggleCameraUrl);
$("#btn-camera-test").addEventListener("click", testCamera);
$("#btn-camera-cancel").addEventListener("click", closeCameraDialog);
$("#btn-camera-close").addEventListener("click", closeCameraDialog);
$("#btn-camera-delete").addEventListener("click", async () => {
  if (!editingCameraId) return;
  const cameraId = editingCameraId;
  closeCameraDialog();
  await deleteCamera(cameraId);
});

window.addEventListener("beforeunload", () => {
  for (const streamId of Array.from(senders.keys())) stopCamera(streamId);
});

/* -------------------------------------------------------------------- boot */
(async function boot() {
  await refreshState();
  await loadProjects();
  await loadCameras();
  connectEvents();
  pollTimer = setInterval(refreshState, 2000);
})();
