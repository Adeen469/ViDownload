const urlInput = document.getElementById("url-input");
const fetchBtn = document.getElementById("fetch-btn");
const errorMsg = document.getElementById("error-msg");
const loadingMsg = document.getElementById("loading-msg");
const result = document.getElementById("result");
const thumb = document.getElementById("thumb");
const titleEl = document.getElementById("title");
const uploaderEl = document.getElementById("uploader");
const durationEl = document.getElementById("duration");
const qualitySelect = document.getElementById("quality-select");
const downloadBtn = document.getElementById("download-btn");
const downloadStatus = document.getElementById("download-status");
const downloadProgress = document.getElementById("download-progress");
const progressBar = document.getElementById("progress-bar");
const progressLabel = document.getElementById("progress-label");

let currentUrl = "";

function showError(msg) {
  errorMsg.textContent = msg;
  errorMsg.classList.remove("hidden");
}

function clearError() {
  errorMsg.classList.add("hidden");
}

function formatDuration(seconds) {
  if (!seconds) return "";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `Duration: ${m}:${s.toString().padStart(2, "0")}`;
}

async function fetchInfo() {
  const url = urlInput.value.trim();
  clearError();
  result.classList.add("hidden");

  if (!url) {
    showError("Paste a link first.");
    return;
  }

  currentUrl = url;
  loadingMsg.classList.remove("hidden");
  fetchBtn.disabled = true;

  try {
    const res = await fetch("/api/info", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await res.json();

    if (!res.ok) {
      showError(data.error || "Something went wrong.");
      return;
    }

    thumb.src = data.thumbnail || "";
    titleEl.textContent = data.title || "Untitled";
    uploaderEl.textContent = data.uploader ? `By ${data.uploader}` : "";
    durationEl.textContent = formatDuration(data.duration);

    qualitySelect.innerHTML = "";
    if (!data.formats || data.formats.length === 0) {
      showError("No downloadable formats were found for this link.");
      return;
    }
    data.formats.forEach((f) => {
      const opt = document.createElement("option");
      opt.value = f.format_id;
      opt.textContent = `${f.label} | Size: ${f.filesize}`;
      qualitySelect.appendChild(opt);
    });

    result.classList.remove("hidden");
  } catch (err) {
    showError("Couldn't reach the server.");
  } finally {
    loadingMsg.classList.add("hidden");
    fetchBtn.disabled = false;
  }
}

async function downloadSelected() {
  const formatId = qualitySelect.value;
  if (!formatId || !currentUrl) return;

  downloadStatus.classList.remove("hidden");
  downloadProgress.classList.remove("hidden");
  progressBar.removeAttribute("value");
  progressLabel.textContent = "Preparing video…";
  downloadBtn.disabled = true;
  clearError();

  try {
    const startRes = await fetch("/api/download/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: currentUrl, format_id: formatId }),
    });
    const startData = await startRes.json();
    if (!startRes.ok) {
      showError(startData.error || "Download failed.");
      return;
    }

    const jobId = startData.job_id;
    let statusData;
    do {
      await new Promise((resolve) => window.setTimeout(resolve, 500));
      const statusRes = await fetch(`/api/download/status/${jobId}`);
      statusData = await statusRes.json();
      if (!statusRes.ok || statusData.status === "error") {
        showError(statusData.error || "Download failed.");
        return;
      }
      progressBar.value = statusData.progress;
      progressLabel.textContent = `Preparing ${statusData.progress}%`;
    } while (statusData.status !== "ready");

    const res = await fetch(`/api/download/file/${jobId}`);

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      showError(data.error || "Download failed.");
      return;
    }

    const reader = res.body.getReader();
    const chunks = [];
    const total = Number(res.headers.get("Content-Length")) || 0;
    let received = 0;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      received += value.length;
      if (total) {
        const percent = Math.min(100, Math.round((received / total) * 100));
        progressBar.value = percent;
        progressLabel.textContent = `Saving ${percent}%`;
      } else {
        progressLabel.textContent = `${Math.round(received / 1024 / 1024)} MB received`;
      }
    }

    progressBar.value = 100;
    progressLabel.textContent = "100%";
    const blob = new Blob(chunks, { type: res.headers.get("Content-Type") || "video/mp4" });
    const disposition = res.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="?([^";]+)"?/);
    const filename = match ? match[1] : "video";
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    link.click();
    URL.revokeObjectURL(link.href);
  } catch (err) {
    showError("Download failed.");
  } finally {
    downloadStatus.classList.add("hidden");
    downloadProgress.classList.add("hidden");
    downloadBtn.disabled = false;
  }
}

fetchBtn.addEventListener("click", fetchInfo);
urlInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") fetchInfo();
});
downloadBtn.addEventListener("click", downloadSelected);
