const jobsBody = document.getElementById("jobs-body");
const toast = document.getElementById("toast");
const dialog = document.getElementById("detail-dialog");
const dialogContent = document.getElementById("dialog-content");
let jobs = [];
let pollTimer;

const criterionNames = {
  problem_relevance: "Problem relevance & product value",
  functionality_reliability: "Functionality & reliability",
  firmgen_usage: "Effective use of FirmGen",
  technical_quality: "Technical quality & engineering",
  innovation_product_thinking: "Innovation & product thinking",
  scalability_robustness: "Scalability & robustness",
  demonstration_documentation: "Demonstration & documentation"
};
const criterionMax = {
  problem_relevance: 20, functionality_reliability: 25, firmgen_usage: 20,
  technical_quality: 15, innovation_product_thinking: 10,
  scalability_robustness: 5, demonstration_documentation: 5
};

function escapeHtml(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

async function api(url, options) {
  const response = await fetch(url, options);
  let body = {};
  try { body = await response.json(); } catch (_) {}
  if (!response.ok) throw new Error(body.detail || "Request failed.");
  return body;
}

function notify(message, isError) {
  toast.textContent = message;
  toast.classList.toggle("error", Boolean(isError));
  toast.classList.add("show");
  window.setTimeout(function () { toast.classList.remove("show"); }, 3600);
}

function statusLabel(job) {
  const labels = { queued: "Queued", processing: "Evaluating", completed: "Complete", failed: "Failed" };
  return '<span class="status ' + escapeHtml(job.status) + '">' +
    escapeHtml(labels[job.status] || job.status) + "</span>";
}

function renderStats() {
  const active = jobs.filter(function (job) { return job.status === "queued" || job.status === "processing"; }).length;
  const completed = jobs.filter(function (job) { return job.status === "completed"; });
  const flagged = jobs.filter(function (job) { return job.manual_review_recommended; }).length;
  const average = completed.length
    ? completed.reduce(function (sum, job) { return sum + Number(job.total_score || 0); }, 0) / completed.length
    : null;
  document.getElementById("stat-total").textContent = jobs.length;
  document.getElementById("stat-active").textContent = active;
  document.getElementById("stat-completed").textContent = completed.length;
  document.getElementById("stat-review").textContent = flagged;
  document.getElementById("stat-total-note").textContent = jobs.length ? "Across all uploaded batches" : "Ready for the first batch";
  document.getElementById("stat-average").textContent = average == null ? "Average score -" : "Average score " + average.toFixed(1);
}

function renderJobs() {
  renderStats();
  if (!jobs.length) {
    jobsBody.innerHTML = '<tr class="empty-row"><td colspan="7">Upload a response sheet or add a repository to begin.</td></tr>';
    return;
  }
  jobsBody.innerHTML = jobs.map(function (job) {
    const title = job.project_title || job.team_id || "Pending repository";
    const score = job.total_score == null ? "-" : Number(job.total_score).toFixed(1);
    const confidence = job.overall_confidence == null ? "-" : Math.round(job.overall_confidence * 100) + "%";
    const evidence = job.manual_review_recommended
      ? '<span class="review-flag">Flagged</span>'
      : '<span class="review-clear">Clear</span>';
    const action = job.status === "completed"
      ? '<button class="button secondary compact view-job" data-job="' + job.id + '">View</button> ' +
        '<button class="button secondary compact retry-job" data-job="' + job.id + '">Re-run</button>'
      : job.status === "failed"
        ? '<button class="button secondary compact view-job" data-job="' + job.id + '">Details</button> ' +
          '<button class="button secondary compact retry-job" data-job="' + job.id + '">Retry</button>'
        : '<span class="confidence">' + escapeHtml(job.progress || "Waiting") + "</span>";
    return "<tr>" +
      '<td class="submission-cell"><strong>' + escapeHtml(title) + '</strong><a href="' +
      escapeHtml(job.repo_url) + '" target="_blank" rel="noreferrer">' + escapeHtml(job.repo_url) + "</a></td>" +
      "<td>" + statusLabel(job) + "</td>" +
      '<td><span class="confidence">' + escapeHtml(job.commit_sha ? job.commit_sha.slice(0, 8) : "-") + "</span></td>" +
      '<td><span class="score">' + score + "<small> / 100</small></span></td>" +
      '<td><span class="confidence">' + confidence + "</span></td>" +
      "<td>" + evidence + "</td><td>" + action + "</td></tr>";
  }).join("");
}

async function refresh() {
  try {
    const data = await api("/api/jobs");
    jobs = data.jobs || [];
    renderJobs();
    document.getElementById("last-updated").textContent = "Updated " + new Date().toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
  } catch (error) {
    if (error.message.includes("Authentication")) window.location.href = "/login";
  }
  window.clearTimeout(pollTimer);
  const active = jobs.some(function (job) { return job.status === "queued" || job.status === "processing"; });
  pollTimer = window.setTimeout(refresh, active ? 2200 : 6000);
}

async function submitRepo(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button");
  button.disabled = true;
  try {
    const body = {
      team_id: document.getElementById("team-id").value,
      repo_url: document.getElementById("repo-url").value,
      force: false
    };
    const data = await api("/api/submit", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)
    });
    notify(data.created ? "Repository queued for evaluation." : "This repository is already in the ledger.");
    form.reset();
    await refresh();
  } catch (error) {
    notify(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function uploadSheet(file) {
  if (!file) return;
  const progress = document.getElementById("upload-progress");
  progress.hidden = false;
  try {
    const form = new FormData();
    form.append("file", file);
    const data = await api("/api/upload", {method: "POST", body: form});
    let message = data.created + " submission" + (data.created === 1 ? "" : "s") + " queued";
    if (data.duplicates) message += "; " + data.duplicates + " duplicate" + (data.duplicates === 1 ? "" : "s") + " skipped";
    if (data.invalid && data.invalid.length) message += "; " + data.invalid.length + " invalid row(s)";
    notify(message + ".");
    await refresh();
  } catch (error) {
    notify(error.message, true);
  } finally {
    progress.hidden = true;
    document.getElementById("sheet-file").value = "";
  }
}

function listHtml(title, values) {
  const items = (values || []).map(function (entry) { return "<li>" + escapeHtml(entry) + "</li>"; }).join("");
  return items ? "<small>" + escapeHtml(title) + "</small><ul>" + items + "</ul>" : "";
}

function criterionHtml(item) {
  return '<article class="criterion-card"><div class="criterion-top"><strong>' +
    escapeHtml(criterionNames[item.criterion_id] || item.criterion_id) +
    '</strong><span class="criterion-score">' + Number(item.score).toFixed(1) + " / " +
    criterionMax[item.criterion_id] + '</span></div><p>' + escapeHtml(item.rationale) + "</p>" +
    listHtml("Evidence", item.evidence) + listHtml("Missing evidence", item.missing_evidence) + "</article>";
}

function showDetails(job) {
  if (job.status === "failed") {
    dialogContent.innerHTML = '<div class="detail-inner"><p class="eyebrow">Evaluation failed</p><h2>' +
      escapeHtml(job.project_title || job.team_id || "Submission") + '</h2><div class="form-error"><strong>What happened</strong><p>' +
      escapeHtml(job.error || "No error details were recorded.") + "</p></div></div>";
    dialog.showModal();
    return;
  }
  const result = job.result || {};
  const firmgen = result.firmgen_evidence || {};
  const eligibility = result.firmware_eligible === false
    ? '<div class="form-error"><strong>Firmware prerequisite not met</strong><p>This repository received 0/100 before rubric scoring.</p>' + listHtml("Checked evidence", result.firmware_evidence) + "</div>"
    : "";
  dialogContent.innerHTML = '<div class="detail-inner"><p class="eyebrow">Final evaluation</p><h2>' +
    escapeHtml(result.project_title || job.project_title) + '</h2><p class="detail-summary">' +
    escapeHtml(result.project_summary || "") + '</p><div class="detail-score">' +
    Number(result.total_score || 0).toFixed(1) + '<small>/ 100</small></div>' + eligibility +
    (result.observed_demo ? '<p class="detail-summary"><strong>Observed demo:</strong> ' + escapeHtml(result.observed_demo) + "</p>" : "") +
    (firmgen.rationale ? '<p class="detail-summary"><strong>FirmGen evidence:</strong> ' + escapeHtml(firmgen.rationale) + "</p>" + listHtml("FirmGen evidence sources", firmgen.evidence_sources) : "") +
    listHtml("Scoring adjustments", result.caps_applied) +
    (result.criteria || []).map(criterionHtml).join("") + "</div>";
  dialog.showModal();
}

jobsBody.addEventListener("click", async function (event) {
  const view = event.target.closest(".view-job");
  const retry = event.target.closest(".retry-job");
  if (view) {
    const job = jobs.find(function (entry) { return entry.id === view.dataset.job; });
    if (job) showDetails(job);
  }
  if (retry) {
    retry.disabled = true;
    try {
      await api("/api/jobs/" + retry.dataset.job + "/retry", {method: "POST"});
      notify("A fresh evaluation has been queued.");
      await refresh();
    } catch (error) {
      notify(error.message, true);
      retry.disabled = false;
    }
  }
});

const dropZone = document.getElementById("drop-zone");
["dragenter", "dragover"].forEach(function (name) {
  dropZone.addEventListener(name, function (event) { event.preventDefault(); dropZone.classList.add("dragging"); });
});
["dragleave", "drop"].forEach(function (name) {
  dropZone.addEventListener(name, function (event) { event.preventDefault(); dropZone.classList.remove("dragging"); });
});
dropZone.addEventListener("drop", function (event) { uploadSheet(event.dataTransfer.files[0]); });
document.getElementById("sheet-file").addEventListener("change", function (event) { uploadSheet(event.target.files[0]); });
document.getElementById("repo-form").addEventListener("submit", submitRepo);
document.getElementById("refresh-button").addEventListener("click", refresh);
document.getElementById("dialog-close").addEventListener("click", function () { dialog.close(); });
dialog.addEventListener("click", function (event) { if (event.target === dialog) dialog.close(); });
refresh();
