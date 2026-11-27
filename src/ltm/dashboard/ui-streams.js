/* UI-stream test page: plays videos in UI and pushes screenshots to /api/ingest. */
"use strict";

const $ = (selector) => document.querySelector(selector);
const canvas = $("#capture-canvas");
const ctx = canvas.getContext("2d", { alpha: false });

let screenStream = null;
let screenVideo = null;
let screenTimer = null;
let streamTimer = null;
const busy = new Set();

function ingestHeaders() {
  const token = $("#ingest-token").value.trim();
  return token ? { "X-Ingest-Token": token } : {};
}

function ingestUrl(streamId) {
  const token = $("#ingest-token").value.trim();
  const base = `/api/ingest/${encodeURIComponent(streamId)}`;
  return token ? `${base}?token=${encodeURIComponent(token)}` : base;
}

function dataURLToBlob(dataUrl) {
  const [header, data] = dataUrl.split(",");
  const match = header.match(/:(.*?);/);
  const mime = match ? match[1] : "image/jpeg";
  const binary = atob(data);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index++) {
    bytes[index] = binary.charCodeAt(index);
  }
  return new Blob([bytes], { type: mime });
}

function captureVideo(video, streamId) {
  if (!video || !video.videoWidth || !video.videoHeight) return;
  if (busy.has(streamId)) return;

  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

  // Snapshot synchronously before the next video reuses the shared canvas.
  const blob = dataURLToBlob(canvas.toDataURL("image/jpeg", 0.82));
  busy.add(streamId);
  fetch(ingestUrl(streamId), {
    method: "POST",
    headers: { "Content-Type": "image/jpeg", ...ingestHeaders() },
    body: blob,
  }).catch(() => {
    /* transient network errors are fine; next frame retries */
  }).finally(() => {
    busy.delete(streamId);
  });
}

function startStreamCapture() {
  if (streamTimer) return;
  streamTimer = setInterval(() => {
    if (!$("#capture-streams").checked) return;
    captureVideo($("#cam1"), "cam1");
    captureVideo($("#cam2"), "cam2");
  }, 500);
}

async function startScreenShare() {
  try {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
      throw new Error("getDisplayMedia is not available in this browser/context.");
    }

    screenStream = await navigator.mediaDevices.getDisplayMedia({
      video: true,
      audio: false,
    });

    screenVideo = document.createElement("video");
    screenVideo.srcObject = screenStream;
    screenVideo.muted = true;
    screenVideo.playsInline = true;
    await screenVideo.play();

    $("#screen-preview").srcObject = screenStream;
    $("#screen-card").classList.remove("hidden");
    $("#btn-screen").classList.add("hidden");
    $("#btn-stop-screen").classList.remove("hidden");

    screenTimer = setInterval(() => captureVideo(screenVideo, "ui-screen"), 500);

    const track = screenStream.getVideoTracks()[0];
    if (track) track.addEventListener("ended", stopScreenShare);
  } catch (error) {
    alert(`Screen share failed: ${error.message}`);
  }
}

function stopScreenShare() {
  if (screenTimer) clearInterval(screenTimer);
  screenTimer = null;

  if (screenStream) {
    for (const track of screenStream.getTracks()) track.stop();
  }
  screenStream = null;

  if (screenVideo) {
    screenVideo.srcObject = null;
    screenVideo = null;
  }

  $("#screen-preview").srcObject = null;
  $("#screen-card").classList.add("hidden");
  $("#btn-screen").classList.remove("hidden");
  $("#btn-stop-screen").classList.add("hidden");
}

$("#btn-screen").addEventListener("click", startScreenShare);
$("#btn-stop-screen").addEventListener("click", stopScreenShare);
window.addEventListener("beforeunload", stopScreenShare);

// Try to start video playback after user gesture where browsers require it.
for (const id of ["#cam1", "#cam2"]) {
  const video = $(id);
  video.addEventListener("loadedmetadata", () => video.play().catch(() => {}));
  video.play().catch(() => {});
}

startStreamCapture();
