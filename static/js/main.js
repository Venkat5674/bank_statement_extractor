/**
 * main.js – BankLens frontend logic
 *
 * Flow:
 *  1. Drag-and-drop / click-to-browse file selection
 *  2. POST /api/upload → receive task_id
 *  3. Poll GET /api/status/<task_id> every 1.5s
 *  4. On done → render stats + table; enable JSON/CSV export
 *  5. Search, sort, filter the transaction table in memory
 */

"use strict";

// ─── State ────────────────────────────────────────────────────────────────────
let selectedFile  = null;
let currentTaskId = null;
let allTxns       = [];          // raw result from API
let filtered      = [];          // after search/filter
let sortState     = { col: "date", dir: "asc" };
let pollTimer     = null;

// ─── DOM refs ─────────────────────────────────────────────────────────────────
const dropZone       = document.getElementById("dropZone");
const fileInput      = document.getElementById("fileInput");
const fileInfo       = document.getElementById("fileInfo");
const fileName       = document.getElementById("fileName");
const fileSize       = document.getElementById("fileSize");
const clearFile      = document.getElementById("clearFile");
const extractBtn     = document.getElementById("extractBtn");

const progressSec    = document.getElementById("progress-section");
const resultsSec     = document.getElementById("results-section");

// Progress
const progressBar    = document.getElementById("progressBar");
const progressLabel  = document.getElementById("progressLabel");
const stepFilename   = document.getElementById("stepFilename");

// Stats
const statTotal      = document.getElementById("statTotal");
const statDebit      = document.getElementById("statDebit");
const statCredit     = document.getElementById("statCredit");
const statBalance    = document.getElementById("statBalance");

// Table
const searchInput    = document.getElementById("searchInput");
const typeFilter     = document.getElementById("typeFilter");
const txBody         = document.getElementById("txBody");
const tableEmpty     = document.getElementById("tableEmpty");
const rowCount       = document.getElementById("rowCount");

// Exports
const exportJson     = document.getElementById("exportJson");
const exportCsv      = document.getElementById("exportCsv");
const newUpload      = document.getElementById("newUpload");

// JSON viewer
const jsonToggle     = document.getElementById("jsonToggle");
const jsonBody       = document.getElementById("jsonBody");
const jsonChevron    = document.getElementById("jsonChevron");

// Toast
const errorToast     = document.getElementById("errorToast");
const toastMsg       = document.getElementById("toastMsg");
const toastClose     = document.getElementById("toastClose");

// ─── Helpers ──────────────────────────────────────────────────────────────────

function fmt(n) {
  if (n === null || n === undefined) return "—";
  return Number(n).toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function fmtFileSize(bytes) {
  if (bytes < 1024)         return bytes + " B";
  if (bytes < 1024 * 1024)  return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / 1024 / 1024).toFixed(1) + " MB";
}

function showToast(msg) {
  toastMsg.textContent = msg;
  errorToast.classList.add("show");
  setTimeout(() => errorToast.classList.remove("show"), 6000);
}

toastClose.addEventListener("click", () => errorToast.classList.remove("show"));

// ─── File selection ───────────────────────────────────────────────────────────

function setFile(file) {
  selectedFile = file;
  fileName.textContent = file.name;
  fileSize.textContent = fmtFileSize(file.size);
  fileInfo.hidden      = false;
  extractBtn.disabled  = false;
  // Re-init icons inside newly shown element
  lucide.createIcons();
}

function clearSelection() {
  selectedFile         = null;
  fileInput.value      = "";
  fileInfo.hidden      = true;
  extractBtn.disabled  = true;
}

// Drag-and-drop
dropZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropZone.classList.add("drag-over");
});
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropZone.classList.remove("drag-over");
  const f = e.dataTransfer.files[0];
  if (f) setFile(f);
});

// Click to browse
dropZone.addEventListener("click", (e) => {
  if (e.target.closest("#browseBtn") || e.target.closest("label")) return;
  fileInput.click();
});
dropZone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") fileInput.click();
});
fileInput.addEventListener("change", () => {
  if (fileInput.files[0]) setFile(fileInput.files[0]);
});
clearFile.addEventListener("click", (e) => { e.stopPropagation(); clearSelection(); });

// ─── Upload & polling ─────────────────────────────────────────────────────────

extractBtn.addEventListener("click", startExtraction);

async function startExtraction() {
  if (!selectedFile) return;

  extractBtn.disabled = true;
  resultsSec.hidden   = true;
  progressSec.hidden  = false;
  progressSec.scrollIntoView({ behavior: "smooth", block: "start" });

  stepFilename.textContent = selectedFile.name;
  setStep("upload", "active");
  setProgress(5, "Uploading…");

  const form = new FormData();
  form.append("file", selectedFile);

  try {
    const res  = await fetch("/api/upload", { method: "POST", body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Upload failed");

    currentTaskId = data.task_id;
    setStep("upload", "done");
    setStep("ocr", "active");
    setProgress(20, "Running OCR engines…");
    pollTimer = setInterval(poll, 1500);

  } catch (err) {
    setStep("upload", "error");
    setProgress(0, "Upload failed.");
    showToast("Upload error: " + err.message);
    extractBtn.disabled = false;
  }
}

async function poll() {
  try {
    const res  = await fetch(`/api/status/${currentTaskId}`);
    const data = await res.json();

    if (data.status === "ocr" || data.status === "queued") {
      setStep("ocr", "active");
      setProgress(data.progress ?? 30, data.step);

    } else if (data.status === "parsing") {
      setStep("ocr", "done");
      setStep("parse", "active");
      setProgress(data.progress ?? 70, data.step);

    } else if (data.status === "done") {
      clearInterval(pollTimer);
      setStep("ocr",   "done");
      setStep("parse", "done");
      setProgress(100, "Complete ✓");
      setTimeout(() => renderResults(data), 400);

    } else if (data.status === "error") {
      clearInterval(pollTimer);
      setStep("parse", "error");
      setProgress(0, "Processing failed.");
      showToast("Processing error: " + (data.error || "Unknown"));
      extractBtn.disabled = false;
    }
  } catch (err) {
    // Network blip — keep polling
  }
}

// ─── Progress helpers ─────────────────────────────────────────────────────────

function setProgress(pct, label) {
  progressBar.style.width = pct + "%";
  progressLabel.textContent = label;
}

const STEP_IDS = { upload: "step-upload", ocr: "step-ocr", parse: "step-parse" };
const BADGE_IDS = { upload: "badge-upload", ocr: "badge-ocr", parse: "badge-parse" };
const BADGE_LABELS = { pending: "waiting", active: "running…", done: "done", error: "failed" };

function setStep(key, state) {
  const el    = document.getElementById(STEP_IDS[key]);
  const badge = document.getElementById(BADGE_IDS[key]);
  if (!el || !badge) return;
  el.className    = `step ${state}`;
  badge.className = `step-badge ${state}`;
  badge.textContent = BADGE_LABELS[state] ?? state;
  lucide.createIcons();
}

// ─── Results rendering ────────────────────────────────────────────────────────

function renderResults(data) {
  allTxns  = data.result || [];
  filtered = [...allTxns];

  // Stats
  const totalDebit  = allTxns.reduce((s, t) => s + (t.debit  ?? 0), 0);
  const totalCredit = allTxns.reduce((s, t) => s + (t.credit ?? 0), 0);
  const lastBalance = allTxns.length
    ? allTxns[allTxns.length - 1].balance
    : null;

  statTotal.textContent   = allTxns.length;
  statDebit.textContent   = fmt(totalDebit);
  statCredit.textContent  = fmt(totalCredit);
  statBalance.textContent = lastBalance !== null ? fmt(lastBalance) : "—";

  // JSON viewer
  jsonBody.textContent = JSON.stringify(allTxns, null, 2);

  progressSec.hidden = true;
  resultsSec.hidden  = false;
  resultsSec.scrollIntoView({ behavior: "smooth", block: "start" });
  lucide.createIcons();

  renderTable();
}

// ─── Table ────────────────────────────────────────────────────────────────────

function renderTable() {
  txBody.innerHTML = "";

  if (filtered.length === 0) {
    tableEmpty.hidden = false;
    rowCount.textContent = 0;
    lucide.createIcons();
    return;
  }

  tableEmpty.hidden = true;
  rowCount.textContent = filtered.length;

  filtered.forEach((tx) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="td-date">${tx.date ?? "—"}</td>
      <td class="td-desc" title="${escHtml(tx.description ?? "")}">${escHtml(tx.description ?? "")}</td>
      <td class="num-col ${tx.debit  != null ? "td-debit"  : "td-null"}">${tx.debit  != null ? fmt(tx.debit)  : "—"}</td>
      <td class="num-col ${tx.credit != null ? "td-credit" : "td-null"}">${tx.credit != null ? fmt(tx.credit) : "—"}</td>
      <td class="num-col td-balance">${tx.balance != null ? fmt(tx.balance) : "—"}</td>
    `;
    txBody.appendChild(tr);
  });
}

function escHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ─── Search & filter ──────────────────────────────────────────────────────────

function applyFilters() {
  const q    = searchInput.value.trim().toLowerCase();
  const type = typeFilter.value;

  filtered = allTxns.filter((tx) => {
    const matchSearch = !q || (tx.description ?? "").toLowerCase().includes(q)
                           || (tx.date ?? "").includes(q);
    const matchType =
      type === "all"    ? true :
      type === "debit"  ? tx.debit  != null :
      type === "credit" ? tx.credit != null : true;
    return matchSearch && matchType;
  });

  applySortInPlace();
  renderTable();
}

searchInput.addEventListener("input",  applyFilters);
typeFilter.addEventListener("change",  applyFilters);

// ─── Sorting ──────────────────────────────────────────────────────────────────

document.querySelectorAll(".tx-table th.sortable").forEach((th) => {
  th.addEventListener("click", () => {
    const col = th.dataset.col;
    if (sortState.col === col) {
      sortState.dir = sortState.dir === "asc" ? "desc" : "asc";
    } else {
      sortState = { col, dir: "asc" };
    }
    // Update aria-sort
    document.querySelectorAll(".tx-table th").forEach((h) => h.removeAttribute("aria-sort"));
    th.setAttribute("aria-sort", sortState.dir === "asc" ? "ascending" : "descending");

    applySortInPlace();
    renderTable();
  });
});

function applySortInPlace() {
  const { col, dir } = sortState;
  filtered.sort((a, b) => {
    let av = a[col], bv = b[col];
    if (av == null) av = dir === "asc" ? Infinity : -Infinity;
    if (bv == null) bv = dir === "asc" ? Infinity : -Infinity;
    if (typeof av === "string") return dir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
    return dir === "asc" ? av - bv : bv - av;
  });
}

// ─── Exports ──────────────────────────────────────────────────────────────────

exportJson.addEventListener("click", () => {
  if (!currentTaskId) return;
  window.open(`/api/download/${currentTaskId}/json`, "_blank");
});

exportCsv.addEventListener("click", () => {
  if (!currentTaskId) return;
  window.open(`/api/download/${currentTaskId}/csv`, "_blank");
});

// ─── New upload ───────────────────────────────────────────────────────────────

newUpload.addEventListener("click", () => {
  clearInterval(pollTimer);
  currentTaskId  = null;
  allTxns        = [];
  filtered       = [];
  sortState      = { col: "date", dir: "asc" };

  clearSelection();
  resultsSec.hidden  = true;
  progressSec.hidden = true;
  searchInput.value  = "";
  typeFilter.value   = "all";
  progressBar.style.width = "0%";
  progressLabel.textContent = "Queued…";
  extractBtn.disabled = true;

  ["upload", "ocr", "parse"].forEach((k) => setStep(k, "pending"));
  document.getElementById("upload-section").scrollIntoView({ behavior: "smooth" });
});

// ─── JSON viewer toggle ───────────────────────────────────────────────────────

jsonToggle.addEventListener("click", () => {
  const open = !jsonBody.hidden;
  jsonBody.hidden = open;
  jsonToggle.classList.toggle("open", !open);
});
jsonToggle.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") jsonToggle.click();
});
