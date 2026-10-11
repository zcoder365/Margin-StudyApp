/*
  margin — shared frontend core
  =============================
  firebase auth, the api helper, date/format helpers, and tiny DOM utilities
  shared by app.js and the feature modules (documents, planner, diagnostics,
  insights). feature modules talk to app.js through `nav`.
*/

import { initializeApp } from "https://www.gstatic.com/firebasejs/10.13.0/firebase-app.js";
import { getAuth, GoogleAuthProvider } from "https://www.gstatic.com/firebasejs/10.13.0/firebase-auth.js";

// ---------------------------------------------------------------------------
// firebase config (public web config — not a secret)
// ---------------------------------------------------------------------------
const firebaseConfig = {
  apiKey: "AIzaSyC59bJU9rhAezTFyfOSi138G9xgxXXM8oQ",
  authDomain: "margin-app-4c293.firebaseapp.com",
  projectId: "margin-app-4c293",
  storageBucket: "margin-app-4c293.firebasestorage.app",
  messagingSenderId: "358817583449",
  appId: "1:358817583449:web:7b392f1b84693cd3717c48",
};

const fbApp = initializeApp(firebaseConfig);
export const auth = getAuth(fbApp);
export const provider = new GoogleAuthProvider();

// override with <script>window.MARGIN_API_BASE = "https://…/api"</script> when deployed
export const API_BASE = window.MARGIN_API_BASE || "http://localhost:5000/api";


// ---------------------------------------------------------------------------
// API helper
// ---------------------------------------------------------------------------
export class ApiError extends Error {
  constructor(message, status, details) {
    super(message);
    this.status = status;
    this.details = details || null;
  }
}

async function rawFetch(path, options = {}) {
  const user = auth.currentUser;
  if (!user) throw new ApiError("not signed in", 401);

  const idToken = await user.getIdToken();

  // file uploads go as FormData, everything else as JSON
  const isFormData = options.body instanceof FormData;

  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: {
        Authorization: `Bearer ${idToken}`,
        ...(isFormData ? {} : { "Content-Type": "application/json" }),
        ...(options.headers || {}),
      },
    });
  } catch {
    throw new ApiError("can't reach the margin server — check your connection", 0);
  }

  if (!response.ok) {
    const err = await response.json().catch(() => ({ error: `request failed: ${response.status}` }));
    throw new ApiError(err.error || `request failed: ${response.status}`, response.status, err.details);
  }
  return response;
}

export async function api(path, options = {}) {
  const response = await rawFetch(path, options);
  return response.json();
}

export async function apiBlob(path) {
  const response = await rawFetch(path);
  return response.blob();
}


// ---------------------------------------------------------------------------
// navigation bridge (app.js fills these in)
// ---------------------------------------------------------------------------
export const nav = {
  showView: () => {},
  state: null,           // app.js state object
  openCourse: () => {},
  openAssignmentById: () => {},
  refreshAfterPlanChange: () => {},
};


// ---------------------------------------------------------------------------
// date helpers
// ---------------------------------------------------------------------------
// all due dates in margin are calendar dates. the server stores them as UTC
// midnight, so only the YYYY-MM-DD part is meaningful — parse it as a local date
// (new Date("2026-05-15T00:00:00+00:00") would show May 14 in the Americas).
export function parseDay(isoString) {
  if (!isoString) return null;
  const [y, m, d] = isoString.slice(0, 10).split("-").map(Number);
  if (!y || !m || !d) return null;
  return new Date(y, m - 1, d);
}

export function todayISO() {
  return new Date().toLocaleDateString("en-CA");
}

export function toISODate(date) {
  return date.toLocaleDateString("en-CA");
}

export function addDays(isoString, n) {
  const d = parseDay(isoString);
  d.setDate(d.getDate() + n);
  return toISODate(d);
}

export function formatDate(isoString) {
  const d = parseDay(isoString);
  if (!d) return "";
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export function formatDateLong(isoString) {
  const d = parseDay(isoString);
  if (!d) return "";
  return d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
}

export function daysUntil(isoString) {
  const due = parseDay(isoString);
  if (!due) return null;
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  return Math.round((due - now) / (1000 * 60 * 60 * 24));
}

export function relativeDays(isoString) {
  const n = daysUntil(isoString);
  if (n === null) return "";
  if (n === 0) return "today";
  if (n === 1) return "tomorrow";
  if (n === -1) return "yesterday";
  return n > 0 ? `in ${n} days` : `${-n} days ago`;
}

export function formatMinutes(mins) {
  if (!mins) return "0min";
  if (mins < 60) return `${Math.round(mins)}min`;
  const h = Math.floor(mins / 60);
  const m = Math.round(mins % 60);
  return m > 0 ? `${h}h ${m}min` : `${h}h`;
}

export function formatHours(mins) {
  if (mins == null) return "—";
  const h = mins / 60;
  return h >= 10 ? `${Math.round(h)}h` : `${Math.round(h * 10) / 10}h`;
}


// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------
// el("div.card", { onclick, title }, child, "text", ...) — text is always set via
// textContent, never innerHTML, so document/AI content can't inject markup.
export function el(spec, props = {}, ...children) {
  const [tag, ...classes] = spec.split(".");
  const node = document.createElement(tag || "div");
  if (classes.length) node.className = classes.join(" ");
  if (props && (typeof props !== "object" || props instanceof Node || Array.isArray(props))) {
    children.unshift(props);
    props = {};
  }
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(node.style, v);
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k === "class") node.className += ` ${v}`;
    else if (k in node && typeof v !== "string") node[k] = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

let toastTimer = null;
// action: optional { label, onClick } rendered as a button inside the toast (e.g. Undo)
export function toast(message, type = "info", action = null) {
  let box = document.getElementById("toast");
  if (!box) {
    box = el("div.toast", { id: "toast", role: "status", "aria-live": "polite" });
    document.body.appendChild(box);
  }
  clear(box);
  box.appendChild(el("span", message));
  if (action) {
    box.appendChild(el("button.toast-action", { type: "button", onclick: () => { box.classList.remove("show"); action.onClick(); } }, action.label));
  }
  box.className = `toast show ${type}${action ? " has-action" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.remove("show"), action ? 12000 : 4500);
}

// generic modal shell; returns { overlay, body, footer, close }
export function openModal(title, { wide = false, onClose } = {}) {
  const previous = document.activeElement;
  const body = el("div.modal-body");
  const footer = el("div.modal-footer");
  const close = () => { overlay.remove(); previous?.focus?.(); onClose?.(); };
  const overlay = el("div.modal", { role: "dialog", "aria-modal": "true", "aria-label": title },
    el(`div.modal-content${wide ? ".wide" : ""}`,
      el("div.modal-header", el("h3", title),
        el("button.modal-close", { type: "button", "aria-label": "close", onclick: close }, "✕")),
      body, footer));
  overlay.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } });
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  document.body.appendChild(overlay);
  setTimeout(() => overlay.querySelector(".modal-close")?.focus(), 0);
  return { overlay, body, footer, close };
}

export function nowMinutes() {
  const d = new Date();
  return d.getHours() * 60 + d.getMinutes();
}

export function loading(text = "Loading…") {
  return el("div.loading-row", { role: "status" }, el("div.spinner.small"), el("span", text));
}

export function errorBox(message, onRetry) {
  return el("div.error-box", { role: "alert" },
    el("span", message),
    onRetry ? el("button.btn.btn-ghost.btn-sm", { type: "button", onclick: onRetry }, "Try again") : null,
  );
}

export function emptyState(text, action) {
  return el("div.empty-block", el("p", text), action || null);
}

// labelled status chip; `status` is one of the explicit labels the server returns
export const STATUS_LABELS = {
  on_track: "On track",
  tight: "Tight",
  at_risk: "At risk",
  insufficient_information: "Not enough info",
  done: "Done",
  past_due: "Past due",
  demonstrated: "Demonstrated",
  developing: "Developing",
  needs_practice: "Needs practice",
  insufficient_evidence: "Not enough evidence",
};

export function statusChip(status, label) {
  return el(`span.status-chip.status-${status}`, label || STATUS_LABELS[status] || status);
}

// accessible modal dialog built on the existing .modal styles; resolves true/false
export function confirmDialog({ title, body, confirmLabel = "Confirm", danger = false }) {
  return new Promise((resolve) => {
    const previous = document.activeElement;
    const close = (val) => { overlay.remove(); previous?.focus?.(); resolve(val); };
    const okBtn = el(`button.btn.${danger ? "btn-danger" : "btn-primary"}`, { type: "button", onclick: () => close(true) }, confirmLabel);
    const overlay = el("div.modal", { role: "dialog", "aria-modal": "true", "aria-label": title },
      el("div.modal-content",
        el("div.modal-header", el("h3", title),
          el("button.modal-close", { type: "button", "aria-label": "close", onclick: () => close(false) }, "✕")),
        el("div.modal-body", ...(Array.isArray(body) ? body : [typeof body === "string" ? el("p", body) : body])),
        el("div.modal-footer",
          el("button.btn.btn-ghost", { type: "button", onclick: () => close(false) }, "Cancel"),
          okBtn),
      ),
    );
    overlay.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); close(false); } });
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(false); });
    document.body.appendChild(overlay);
    okBtn.focus();
  });
}

// tab bar with arrow-key navigation (role=tablist)
export function tabs(container, items, onSelect, initial) {
  clear(container);
  container.setAttribute("role", "tablist");
  const buttons = items.map(({ id, label }) => el("button.tab-btn", {
    type: "button", role: "tab", id: `tab-${id}`, "aria-selected": "false", tabindex: "-1",
    onclick: () => select(id),
  }, label));
  function select(id) {
    buttons.forEach((b, i) => {
      const on = items[i].id === id;
      b.setAttribute("aria-selected", on ? "true" : "false");
      b.tabIndex = on ? 0 : -1;
      b.classList.toggle("active", on);
    });
    onSelect(id);
  }
  container.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const idx = buttons.indexOf(document.activeElement);
    if (idx === -1) return;
    const next = buttons[(idx + (e.key === "ArrowRight" ? 1 : buttons.length - 1)) % buttons.length];
    next.focus();
    next.click();
  });
  buttons.forEach(b => container.appendChild(b));
  select(initial || items[0].id);
  return { select };
}
