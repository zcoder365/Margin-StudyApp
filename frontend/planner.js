/*
  margin — planner: workload forecast, risks, availability and recovery
  =====================================================================
  sits on top of the weekly calendar. all numbers come from the server's
  deterministic forecast; this module only explains and displays them.
*/

import {
  api, nav, el, clear, toast, loading, errorBox, emptyState, confirmDialog, openModal,
  statusChip, formatDate, formatDateLong, formatHours, relativeDays, todayISO, nowMinutes,
} from "./core.js";
import { registerOverviewSection } from "./assessment.js";

let forecast = null;        // last forecast response
let deadlines = [];         // from /calendar
let loadingPromise = null;

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];


// ---------------------------------------------------------------------------
// data
// ---------------------------------------------------------------------------
export async function loadForecast(termId) {
  const q = new URLSearchParams({ today: todayISO(), nowMinutes: String(nowMinutes()) });
  if (termId) q.set("termId", termId);
  loadingPromise = api(`/planning/forecast?${q}`).then((data) => { forecast = data; return data; });
  try { return await loadingPromise; } finally { loadingPromise = null; }
}

export function setDeadlines(list) { deadlines = list || []; }
export function getForecast() { return forecast; }

function termId() {
  return document.getElementById("term-select")?.value || nav.state?.currentTermId || "";
}


// ---------------------------------------------------------------------------
// planner header (above the week grid)
// ---------------------------------------------------------------------------
export async function renderPlannerTop() {
  const root = document.getElementById("planner-top");
  if (!root) return;
  if (!forecast) {
    clear(root).appendChild(loading("Working out your workload…"));
    try { await loadForecast(termId()); } catch (err) {
      clear(root).appendChild(errorBox(`Couldn't load the forecast: ${err.message}`, renderPlannerTop));
      return;
    }
  }
  clear(root);
  const f = forecast;

  // summary + actions
  const free = f.summary?.freeMinutesNext14 ?? 0;
  const due = f.summary?.demandMinutesDueNext14 ?? 0;
  root.appendChild(el("div.planner-summary",
    el("div.summary-stats",
      stat(formatHours(free), "free in the next 14 days", "after classes and commitments, within your study limits"),
      stat(formatHours(due), "of work due in that time", "estimated work left on assessments due in the next 14 days"),
      stat(free ? `${Math.round((due / free) * 100)}%` : "—", "of your free time", "how much of that free time the work needs"),
    ),
    el("div.planner-actions",
      el("button.btn.btn-primary", { type: "button", id: "rebuild-plan-btn", onclick: openRecovery }, "I'm behind — rebuild my plan"),
      el("button.btn.btn-ghost", { type: "button", onclick: openAvailability }, "Study availability"),
      el("button.btn.btn-ghost", { type: "button", onclick: () => document.getElementById("schedule-btn").click() }, "Weekly commitments"),
    ),
  ));

  // risks
  const active = f.risks.filter(r => !r.ack);
  const dismissed = f.risks.filter(r => r.ack);
  const riskBox = el("section.risk-list", { "aria-label": "things to look at" });
  if (!active.length) {
    riskBox.appendChild(el("p.muted.small", f.assessments.length
      ? "Nothing needs attention right now."
      : "Add assessments with due dates (or upload a syllabus) to see a forecast."));
  }
  active.slice(0, 6).forEach(r => riskBox.appendChild(buildRisk(r)));
  if (active.length > 6) riskBox.appendChild(el("p.muted.small", `+ ${active.length - 6} more`));
  if (dismissed.length) {
    riskBox.appendChild(el("details.dismissed-risks",
      el("summary", `${dismissed.length} dismissed`),
      ...dismissed.map(r => buildRisk(r))));
  }
  root.appendChild(riskBox);

  // per-assessment forecast
  const rows = [...f.assessments]
    .filter(a => a.status !== "done")
    .sort((a, b) => (a.dueDate || "9999").localeCompare(b.dueDate || "9999"));
  const table = el("section.forecast-list", { "aria-label": "assessment forecast" },
    el("div.forecast-head",
      el("h3", "Assessments"),
      el("details.definitions",
        el("summary", "What do these labels mean?"),
        el("dl", ...Object.entries(f.definitions).flatMap(([k, v]) => [el("dt", statusChip(k)), el("dd", v)])),
        el("p.small.muted", "Assumptions: ", f.assumptions.join("; "), "."),
      ),
    ),
  );
  if (!rows.length) table.appendChild(emptyState("No upcoming assessments."));
  rows.forEach(a => table.appendChild(buildForecastRow(a)));
  root.appendChild(table);
}

function stat(value, label, hint) {
  return el("div.summary-stat", { title: hint }, el("div.summary-value", value), el("div.summary-label", label));
}

function buildRisk(r) {
  const actions = el("div.risk-actions");
  for (const a of r.actions) {
    const btn = riskActionButton(a, r);
    if (btn) actions.appendChild(btn);
  }
  if (r.ack) {
    actions.appendChild(el("button.link-btn", { type: "button", onclick: () => ackRisk(r, null) }, "restore"));
  } else {
    actions.appendChild(el("button.link-btn", { type: "button", onclick: () => ackRisk(r, "dismissed"),
      title: "Hide until something changes" }, "dismiss"));
  }
  return el(`div.risk.risk-${r.severity}`,
    el("div.risk-text", el("strong", r.title), el("span", r.explanation)),
    actions);
}

// risk types whose `related[0]` is an assessment id
const ASSESSMENT_RISKS = ["at_risk", "tight", "should_start", "insufficient_information", "past_due"];

function riskActionButton(action, risk) {
  const pid = ASSESSMENT_RISKS.includes(risk.type) ? risk.related?.[0] : null;
  const open = pid ? ["Open assessment", () => nav.openAssignmentById(pid)] : null;
  const map = {
    rebuild_plan: ["Rebuild plan", openRecovery],
    edit_availability: ["Availability", openAvailability],
    edit_schedule: ["Add commitments", () => document.getElementById("schedule-btn").click()],
    enable_calibration: ["Adjust for my history", () => setCalibration(true)],
    edit_estimates: open,
    mark_complete: open,
  };
  const entry = map[action];
  if (!entry) return null;
  return el("button.btn.btn-ghost.btn-sm", { type: "button", onclick: entry[1] }, entry[0]);
}

async function ackRisk(r, state) {
  try {
    await api("/planning/acks", { method: "POST", body: JSON.stringify({ riskId: r.id, state, fingerprint: r.fingerprint }) });
    r.ack = state;
    renderPlannerTop();
  } catch (err) {
    toast(`Couldn't update: ${err.message}`, "error");
  }
}

function buildForecastRow(a) {
  const numbers = a.status === "insufficient_information" || a.status === "past_due" ? null : el("span.forecast-numbers",
    `${formatHours(a.remainingMinutes)} left · ${formatHours(a.availableMinutes)} free before it`);
  return el("details.forecast-row",
    el("summary",
      el("span.course-dot", { style: { background: a.courseColor || "#ccc" }, "aria-hidden": "true" }),
      el("span.forecast-title", a.title),
      el("span.forecast-due", a.dueDate ? `${formatDate(a.dueDate)} · ${relativeDays(a.dueDate)}` : "no date"),
      numbers,
      statusChip(a.status),
    ),
    el("div.forecast-detail",
      el("ul.plain-list", ...a.drivers.map(d => el("li", d))),
      a.latestStart && a.status !== "done" ? el("p.small.muted", `Latest start if you did nothing else: ${formatDateLong(a.latestStart)}`) : null,
      el("button.btn.btn-ghost.btn-sm", { type: "button", onclick: () => nav.openAssignmentById(a.id) }, "Open"),
    ),
  );
}


// ---------------------------------------------------------------------------
// week-grid decorations (called by app.js while rendering each day)
// ---------------------------------------------------------------------------
export function buildDayInfo(dayStr) {
  const wrap = el("div.day-info");
  const day = forecast?.days?.find(d => d.date === dayStr);
  if (day) {
    const pct = day.freeMinutes ? Math.min(100, Math.round((day.plannedMinutes / day.freeMinutes) * 100)) : (day.plannedMinutes ? 100 : 0);
    wrap.appendChild(el(`div.capacity${day.overloaded ? ".over" : ""}`, {
      title: `${formatHours(day.freeMinutes)} free, ${formatHours(day.plannedMinutes)} planned`,
    },
      el("div.capacity-track", el("div.capacity-fill", { style: { width: `${pct}%` } })),
      el("div.capacity-label", day.freeMinutes || day.plannedMinutes
        ? `${formatHours(day.plannedMinutes)} / ${formatHours(day.freeMinutes)} free`
        : "no study time"),
    ));
  }
  for (const d of deadlines.filter(x => x.date === dayStr)) {
    wrap.appendChild(el(`button.deadline-pill${d.type === "milestone" ? ".milestone" : ""}`, {
      type: "button", style: { "--task-color": d.courseColor || "#ccc" },
      title: `${d.courseName}: ${d.title}`, onclick: () => nav.openAssignmentById(d.projectId),
    }, d.type === "milestone" ? "◆ " : "⚑ ", d.title));
  }
  return wrap;
}


// ---------------------------------------------------------------------------
// availability settings
// ---------------------------------------------------------------------------
async function openAvailability() {
  const m = openModal("Study availability", { wide: true });
  m.body.appendChild(loading());
  let av;
  try { av = await api("/planning/availability"); } catch (err) {
    clear(m.body).appendChild(errorBox(`Couldn't load settings: ${err.message}`));
    return;
  }
  clear(m.body);
  const start = el("input", { type: "time", value: av.dayStart, required: true });
  const end = el("input", { type: "time", value: av.dayEnd, required: true });
  const maxDay = el("input", { type: "number", min: "0", max: "16", step: "0.5", value: av.maxStudyMinutesPerDay / 60 });
  const maxSession = el("input", { type: "number", min: "15", max: "480", step: "5", value: av.maxSessionMinutes });
  const daysOff = WEEKDAYS.map((d, i) => el("label.check-chip",
    el("input", { type: "checkbox", value: String(i), checked: av.daysOff.includes(i) }), el("span", d)));
  const blackoutList = el("div.blackout-list");
  const addBlackout = (b = {}) => {
    const row = el("div.blackout-row",
      el("input", { type: "date", value: b.start || "", "aria-label": "from" }),
      el("input", { type: "date", value: b.end || "", "aria-label": "to" }),
      el("input", { type: "text", value: b.label || "", placeholder: "e.g. reading week trip", "aria-label": "label" }),
      el("button.remove-block-btn", { type: "button", "aria-label": "remove", onclick: () => row.remove() }, "✕"));
    blackoutList.appendChild(row);
  };
  av.blackouts.forEach(addBlackout);
  const status = el("p.form-status", { "aria-live": "polite" });

  m.body.append(
    el("p.modal-hint", "margin plans study time inside this window, minus your weekly commitments. Be realistic — a plan you can keep beats an ambitious one."),
    el("div.form-grid",
      el("label", "Earliest study time", start),
      el("label", "Latest study time", end),
      el("label", "Max study hours per day", maxDay),
      el("label", "Longest single session (min)", maxSession),
    ),
    el("fieldset.days-off", el("legend", "Days off (no study planned)"), ...daysOff),
    el("fieldset", el("legend", "Unavailable dates (trips, busy weeks)"), blackoutList,
      el("button.btn.btn-ghost.btn-sm", { type: "button", onclick: () => addBlackout() }, "+ Add dates")),
    status,
  );
  m.footer.append(
    el("button.btn.btn-ghost", { type: "button", onclick: m.close }, "Cancel"),
    el("button.btn.btn-primary", { type: "button", onclick: save }, "Save"),
  );

  async function save() {
    const blackouts = [...blackoutList.querySelectorAll(".blackout-row")].map(r => {
      const [s, e, l] = r.querySelectorAll("input");
      return { start: s.value, end: e.value || s.value, label: l.value.trim() };
    }).filter(b => b.start);
    const body = {
      dayStart: start.value, dayEnd: end.value,
      maxStudyMinutesPerDay: Math.round(Number(maxDay.value) * 60),
      maxSessionMinutes: Number(maxSession.value),
      daysOff: daysOff.map(l => l.querySelector("input")).filter(i => i.checked).map(i => Number(i.value)),
      blackouts,
    };
    try {
      await api("/planning/availability", { method: "PUT", body: JSON.stringify(body) });
      m.close();
      toast("Availability saved — forecast updated", "success");
      await refreshPlanner();
    } catch (err) {
      status.textContent = `Couldn't save: ${err.message}`;
    }
  }
}

async function setCalibration(on) {
  try {
    await api("/planning/availability", { method: "PUT", body: JSON.stringify({ useCalibration: on }) });
    toast(on ? "Estimates now adjusted for your history" : "Estimate adjustment turned off", "success");
    await refreshPlanner();
  } catch (err) {
    toast(`Couldn't update: ${err.message}`, "error");
  }
}
export { setCalibration };


// ---------------------------------------------------------------------------
// recovery ("i'm behind")
// ---------------------------------------------------------------------------
export async function openRecovery() {
  const m = openModal("Rebuild my plan", { wide: true });
  m.overlay.querySelector(".modal-content").classList.add("recovery-modal");
  m.body.appendChild(loading("Finding realistic slots for your remaining work…"));
  let plan;
  try {
    plan = await api("/planning/recovery/preview", {
      method: "POST", body: JSON.stringify({ today: todayISO(), nowMinutes: nowMinutes(), termId: termId() }),
    });
  } catch (err) {
    clear(m.body).appendChild(errorBox(`Couldn't build a plan: ${err.message}`));
    return;
  }
  clear(m.body);

  const changes = [...plan.moves.map(c => ({ ...c, kind: "move" })), ...plan.splits.map(c => ({ ...c, kind: "split" }))]
    .sort((a, b) => (a.to || a.parts?.[0]?.date || "").localeCompare(b.to || b.parts?.[0]?.date || ""));

  m.body.appendChild(el("p.modal-hint",
    "margin kept your completed work, fixed tasks and commitments where they are, and found new slots for missed or overbooked tasks — earliest deadline first. Untick anything you'd rather not change."));

  if (!changes.length && !plan.unplaceable.length) {
    m.body.appendChild(emptyState("Your plan already fits — nothing needs to move."));
    m.footer.appendChild(el("button.btn.btn-primary", { type: "button", onclick: m.close }, "Close"));
    return;
  }

  if (changes.length) {
    const list = el("div.change-list", { role: "group", "aria-label": "proposed changes" });
    for (const c of changes) {
      const box = el("input", { type: "checkbox", checked: c.selected, onchange: (e) => { c.selected = e.target.checked; } });
      const what = c.kind === "move"
        ? `${c.from ? formatDate(c.from) : "unscheduled"} → ${formatDateLong(c.to)}`
        : `split into ${c.parts.length}: ${c.parts.map(p => `${formatDate(p.date)} (${p.minutes}m)`).join(", ")}`;
      list.appendChild(el(`label.change-row${c.warning ? ".warn" : ""}`,
        box,
        el("div.change-main",
          el("div.change-title", c.title, el("span.muted.small", ` · ${c.assessmentTitle}`)),
          el("div.change-what", what),
          el("div.change-reason", c.reason),
          c.warning ? el("div.issue.issue-warning", c.warning) : null,
        )));
    }
    m.body.append(el("h4.sub-heading", `Proposed changes (${changes.length})`), list);
  }

  if (plan.unplaceable.length) {
    m.body.append(
      el("h4.sub-heading", "Doesn't fit before its deadline"),
      el("div.unfit-list", ...plan.unplaceable.map(u => el("div.issue.issue-error",
        el("span", `${u.title} (${u.assessmentTitle}${u.deadline ? `, due ${formatDate(u.deadline)}` : ""}) — about ${formatHours(u.minutesShort)} short: ${u.reason}. It stays where it is.`)))),
      el("ul.plain-list.interventions", ...plan.interventions.slice(1).map(t => el("li", t))),
      el("p.small.muted", plan.interventions[0] || ""),
    );
  }
  if (plan.factor !== 1) m.body.appendChild(el("p.small.muted", `Task estimates were scaled ×${plan.factor} based on your history.`));

  const applyBtn = el("button.btn.btn-primary", { type: "button", disabled: !changes.length }, "Apply selected changes");
  m.footer.append(el("button.btn.btn-ghost", { type: "button", onclick: m.close }, "Cancel"), applyBtn);
  applyBtn.addEventListener("click", async () => {
    const moves = changes.filter(c => c.kind === "move" && c.selected).map(c => ({ taskId: c.taskId, to: c.to }));
    const splits = changes.filter(c => c.kind === "split" && c.selected).map(c => ({ taskId: c.taskId, parts: c.parts }));
    if (!moves.length && !splits.length) { toast("Nothing selected", "error"); return; }
    const ok = await confirmDialog({
      title: "Apply these changes?",
      body: `${moves.length} task${moves.length === 1 ? "" : "s"} will move${splits.length ? ` and ${splits.length} will be split into parts` : ""}. Nothing is deleted, and you can undo this right after.`,
      confirmLabel: "Apply",
    });
    if (!ok) return;
    applyBtn.disabled = true;
    try {
      await api("/planning/recovery/apply", {
        method: "POST", body: JSON.stringify({ today: todayISO(), basis: plan.basis, moves, splits }),
      });
      m.close();
      toast("Plan updated", "success", { label: "Undo", onClick: undoRecovery });
      await refreshPlanner(true);
    } catch (err) {
      applyBtn.disabled = false;
      toast(err.status === 409 ? `${err.message}` : `Couldn't apply: ${err.message}`, "error");
    }
  });
}

async function undoRecovery() {
  try {
    const r = await api("/planning/recovery/undo", { method: "POST", body: "{}" });
    toast(`Undone — ${r.restored} task${r.restored === 1 ? "" : "s"} restored`, "success");
    await refreshPlanner(true);
  } catch (err) {
    toast(`Couldn't undo: ${err.message}`, "error");
  }
}

// reload forecast (+ calendar tasks when asked) and redraw whatever is visible
export async function refreshPlanner(reloadCalendar = false) {
  forecast = null;
  try { await loadForecast(termId()); } catch { /* shown on next render */ }
  if (!document.getElementById("view-calendar")?.classList.contains("hidden")) {
    if (reloadCalendar) await nav.reloadCalendar?.();
    else { renderPlannerTop(); nav.rerenderCalendar?.(); }
  }
  nav.onForecastChanged?.();
}


// ---------------------------------------------------------------------------
// assessment overview section
// ---------------------------------------------------------------------------
registerOverviewSection(async (project, container) => {
  if (project.status === "completed") return;
  container.appendChild(loading("Checking the forecast…"));
  try {
    const f = forecast && forecast.assessments.some(a => a.id === project.id) ? forecast : await loadForecast(termId());
    const row = f.assessments.find(a => a.id === project.id);
    clear(container);
    if (!row) return;
    container.append(
      el("div.overview-forecast",
        el("div", el("h4", "Workload forecast"), statusChip(row.status)),
        el("ul.plain-list", ...row.drivers.map(d => el("li", d))),
        el("p.small.muted", f.definitions[row.status] || ""),
        ["at_risk", "tight"].includes(row.status)
          ? el("button.btn.btn-ghost.btn-sm", { type: "button", onclick: openRecovery }, "Rebuild my plan") : null,
      ),
    );
  } catch (err) {
    clear(container).appendChild(errorBox(`Couldn't load the forecast: ${err.message}`));
  }
});
