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
      opt.textContent = `${f.label} — ${f.filesize}`;
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
  downloadBtn.disabled = true;
  clearError();

  try {
    const res = await fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: currentUrl, format_id: formatId }),
    });

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      showError(data.error || "Download failed.");
      return;
    }

    const blob = await res.blob();
    const disposition = res.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="?([^"]+)"?/);
    const filename = match ? match[1] : "video";

    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(link.href);
  } catch (err) {
    showError("Download failed.");
  } finally {
    downloadStatus.classList.add("hidden");
    downloadBtn.disabled = false;
  }
}

fetchBtn.addEventListener("click", fetchInfo);
urlInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") fetchInfo();
});
downloadBtn.addEventListener("click", downloadSelected);