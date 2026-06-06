const INTAKE_URL = "http://localhost:3927/jobs/capture";

const elements = {
  title: document.getElementById("title"),
  company: document.getElementById("company"),
  location: document.getElementById("location"),
  descriptionCount: document.getElementById("descriptionCount"),
  sourceUrl: document.getElementById("sourceUrl"),
  status: document.getElementById("status"),
  saveButton: document.getElementById("saveButton"),
};

let capturedJob = null;

function setStatus(message, kind = "") {
  elements.status.textContent = message;
  elements.status.className = `status ${kind}`.trim();
}

function setText(element, value, fallback = "Not found") {
  element.textContent = value && String(value).trim() ? value : fallback;
}

function renderCapture(job) {
  setText(elements.title, job.title);
  setText(elements.company, job.company);
  setText(elements.location, job.location);
  setText(elements.sourceUrl, job.source_url);
  elements.descriptionCount.textContent = String((job.description_text || "").length);

  const missing = [];
  for (const field of ["title", "company", "description_text"]) {
    if (!job[field] || !String(job[field]).trim()) {
      missing.push(field);
    }
  }

  if (missing.length > 0) {
    elements.saveButton.disabled = true;
    setStatus(`Capture incomplete. Missing: ${missing.join(", ")}.`, "error");
    return;
  }

  elements.saveButton.disabled = false;
  setStatus("Preview captured. Review the fields, then save to the local intake server.");
}

async function getActiveTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs[0];
}

async function captureCurrentJob() {
  const tab = await getActiveTab();
  if (!tab || !tab.id) {
    throw new Error("No active tab found.");
  }

  if (!tab.url || !/^https:\/\/(www\.)?linkedin\.com\/jobs\//.test(tab.url)) {
    throw new Error("Open a LinkedIn job page before using the clipper.");
  }

  return chrome.tabs.sendMessage(tab.id, { type: "CAPTURE_LINKEDIN_JOB" });
}

async function saveCapturedJob() {
  if (!capturedJob) {
    return;
  }

  elements.saveButton.disabled = true;
  setStatus("Saving to local intake server...");

  try {
    const response = await fetch(INTAKE_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(capturedJob),
    });

    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.error || `Local intake server returned HTTP ${response.status}.`);
    }

    setStatus(`Saved: ${payload.path || payload.id || "captured job"}`, "success");
  } catch (error) {
    elements.saveButton.disabled = false;
    const message =
      error instanceof TypeError
        ? "Local intake server is not reachable at http://localhost:3927. Start it with: python scripts\\job_intake_server.py"
        : error.message;
    setStatus(`Save failed: ${message}`, "error");
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  try {
    capturedJob = await captureCurrentJob();
    if (!capturedJob || capturedJob.error) {
      throw new Error(capturedJob && capturedJob.error ? capturedJob.error : "Capture failed.");
    }
    renderCapture(capturedJob);
  } catch (error) {
    capturedJob = null;
    elements.saveButton.disabled = true;
    setStatus(error.message, "error");
  }
});

elements.saveButton.addEventListener("click", saveCapturedJob);
