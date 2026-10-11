/*
  margin — the feedback loop: study sessions, results, reviews, insights
  ======================================================================
  plan → study → test → compare → adapt. everything here is optional for the
  student and phrased as observations, never verdicts.
*/

import {
  api, nav, el, clear, toast, loading, errorBox, emptyState, openModal,
  formatDate, formatHours, formatMinutes, todayISO,
} from "./core.js";
import { registerOverviewSection } from "./assessment.js";
import { startSession, openPracticeScheduler } from "./diagnostics.js";
import { setCalibration } from "./planner.js";

function newClientId() {
  return (crypto.randomUUID?.() || `${Date.now()}${Math.random()}`).replace(/[^A-Za-z0-9]/g, "").slice(0, 32);
}

// ---------------------------------------------------------------------------
// study sessions
// ---------------------------------------------------------------------------
export async function logSession({ taskId = null, projectId = null, actualMinutes, plannedMinutes = null, startedAt = null, completedTask = false, confidence = null, note = null }) {
  return api("/study-sessions", {
    method: "POST",
    body: JSON.stringify({ clientId: newClientId(), taskId, projectId, actualMinutes, plannedMinutes,
      startedAt, endedAt: new Date().toISOString(), completedTask, confidence, note }),
  });
}

// lightweight follow-up after a timer session: all optional, one tap each
export function renderSessionFollowUp(container, session, { onTaskDone } = {}) {
  clear(container);
  const state = { confidence: null, completed: false };
  const note = el("input.followup-note", { type: "text", placeholder: "anything that tripped you up? (optional)", "aria-label": "note" });
  const confButtons = [[1, "Struggled"], [3, "OK"], [5, "Confident"]].map(([v, label]) =>
    el("button.btn.btn-ghost.btn-sm", { type: "button", "aria-pressed": "false", onclick: (e) => {
      state.confidence = state.confidence === v ? null : v;
      confButtons.forEach(b => { b.setAttribute("aria-pressed", "false"); b.classList.remove("btn-primary"); b.classList.add("btn-ghost"); });
      if (state.confidence) { e.currentTarget.setAttribute("aria-pressed", "true"); e.currentTarget.classList.add("btn-primary"); e.currentTarget.classList.remove("btn-ghost"); }
    } }, label));
  const done = el("input", { type: "checkbox", onchange: (e) => { state.completed = e.target.checked; } });
  container.append(
    session.taskId ? el("label.toggle-row.small", done, el("span", "I finished this task")) : null,
    el("div.followup-row", el("span.small.muted", "How did it go?"), ...confButtons),
    note,
    el("div.row-actions",
      el("button.btn.btn-primary.btn-sm", { type: "button", onclick: async () => {
        const body = {};
        if (state.confidence) body.confidence = state.confidence;
        if (note.value.trim()) body.note = note.value.trim();
        if (state.completed) body.completedTask = true;
        if (Object.keys(body).length) {
          try {
            await api(`/study-sessions/${session.id}`, { method: "PATCH", body: JSON.stringify(body) });
            if (state.completed) onTaskDone?.();
          } catch (err) { toast(`Couldn't save: ${err.message}`, "error"); return; }
        }
        clear(container).appendChild(el("p.small.muted", "Saved 💛"));
      } }, "Save"),
      el("button.link-btn", { type: "button", onclick: () => clear(container) }, "skip"),
    ),
  );
}

export function openLogStudyModal(project, tasks = []) {
  const m = openModal("Log study time");
  const minutes = el("input", { type: "number", min: "1", max: "600", step: "5", value: "30" });
  const taskSel = el("select", el("option", { value: "" }, "General preparation (no specific task)"),
    ...tasks.filter(t => t.status !== "done").map(t => el("option", { value: t.id }, t.title)));
  const finished = el("input", { type: "checkbox" });
  const note = el("input", { type: "text", placeholder: "optional" });
  m.body.append(
    el("p.modal-hint", `For ${project.title}. Only minutes are required.`),
    el("label", "Minutes studied", minutes),
    tasks.length ? el("label", "Task", taskSel) : null,
    tasks.length ? el("label.toggle-row", finished, el("span.toggle-label", "I finished that task")) : null,
    el("label", "Note", note),
  );
  m.footer.append(el("button.btn.btn-ghost", { type: "button", onclick: m.close }, "Cancel"),
    el("button.btn.btn-primary", { type: "button", onclick: async () => {
      const mins = Number(minutes.value);
      if (!mins || mins < 1 || mins > 600) { toast("Enter 1–600 minutes", "error"); return; }
      try {
        await logSession({ taskId: taskSel.value || null, projectId: project.id, actualMinutes: mins,
          completedTask: finished.checked && !!taskSel.value, note: note.value.trim() || null });
        m.close();
        toast(`Logged ${formatMinutes(mins)}`, "success");
        nav.onStudyLogged?.();
      } catch (err) { toast(`Couldn't log: ${err.message}`, "error"); }
    } }, "Log"));
}


// ---------------------------------------------------------------------------
// assessment: study & results
// ---------------------------------------------------------------------------
registerOverviewSection((project, container) => {
  const isPast = project.dueDate && project.dueDate.slice(0, 10) < todayISO();
  const finished = project.status === "completed" || isPast;
  container.append(el("div.results-head", el("h4", finished ? "Results & review" : "Study log"),
    el("div.row-actions",
      el("button.btn.btn-ghost.btn-sm", { type: "button", onclick: () => openLogStudyModal(project, nav.state.tasks || []) }, "Log study time"),
      finished ? el("button.btn.btn-ghost.btn-sm", { type: "button", onclick: () => openOutcomeModal(project) },
        project.outcome ? "Edit result" : "Add your result (optional)") : null)));
  if (project.outcome) {
    const o = project.outcome;
    container.appendChild(el("p.result-line", `Result: ${o.percent}% (${o.score}/${o.total})`,
      o.reflection?.feeling ? el("span.muted.small", ` · felt ${o.reflection.feeling.replace("_", " ")}`) : null));
  }
  if (finished) {
    const reviewBox = el("div.review-box");
    container.appendChild(reviewBox);
    loadReview(project, reviewBox);
  }
});

async function loadReview(project, box) {
  box.appendChild(loading("Comparing your plan with what happened…"));
  try {
    const r = await api(`/projects/${project.id}/review?today=${todayISO()}`);
    clear(box);
    const t = r.time;
    if (t.estimatedMinutes || t.actualMinutes) {
      const max = Math.max(t.estimatedMinutes || 0, t.actualMinutes || 0, 1);
      box.appendChild(el("div.compare-bars", { "aria-label": "planned versus logged time" },
        barRow("Planned", t.estimatedMinutes, max), barRow("Logged", t.actualMinutes, max)));
    }
    box.appendChild(el("ul.plain-list.observations", ...r.observations.map(o => el("li", o))));
    if (r.learning.length) {
      box.appendChild(el("details.review-sub",
        el("summary", `Concept evidence by the due date (${r.learning.length})`),
        el("ul.plain-list", ...r.learning.map(l => el("li", `${l.name}: ${l.statusAtDue.replace(/_/g, " ")}`,
          el("span.muted.small", ` · ${l.attemptsBefore} answer${l.attemptsBefore === 1 ? "" : "s"} before${l.attemptsAfter ? `, ${l.attemptsAfter} after` : ""}`))))));
    }
    if (r.suggestions.length) {
      box.appendChild(el("div.next-time",
        el("h5", "Something to try next time"),
        el("ul.plain-list", ...r.suggestions.map(s => el("li", s.text, el("span.muted.small", ` (${s.evidence})`))))));
    }
    box.appendChild(el("p.small.muted", r.caveat));
  } catch (err) {
    clear(box).appendChild(errorBox(`Couldn't load the review: ${err.message}`));
  }
}

function barRow(label, minutes, max) {
  return el("div.bar-row", el("span.bar-label", label),
    el("div.bar-track", el("div.bar-fill", { style: { width: `${Math.round(((minutes || 0) / max) * 100)}%` } })),
    el("span.bar-value", minutes ? formatHours(minutes) : "—"));
}

async function openOutcomeModal(project) {
  const m = openModal(project.outcome ? "Edit result" : "Add your result");
  const o = project.outcome || {};
  const score = el("input", { type: "number", min: "0", step: "0.5", value: o.score ?? "" });
  const total = el("input", { type: "number", min: "0.5", step: "0.5", value: o.total ?? "100" });
  const feel = el("select", el("option", { value: "" }, "—"),
    ...[["better", "Better than I expected"], ["as_expected", "About what I expected"], ["worse", "Worse than I expected"]]
      .map(([v, l]) => el("option", { value: v, selected: o.reflection?.feeling === v }, l)));
  const note = el("input", { type: "text", value: o.reflection?.note || "", placeholder: "what would you keep or change? (optional)" });
  let cats = [];
  try { cats = (await api(`/courses/${project.courseId}/grading`)).categories; } catch { /* optional */ }
  const catSel = el("select", el("option", { value: "" }, "Don't add to grade calculator"),
    ...cats.map(c => el("option", { value: c.id }, `Add to “${c.name}”`)));
  m.body.append(
    el("p.modal-hint", "Optional. Results help margin show how your preparation compared — they're never used to judge you."),
    el("div.form-grid", el("label", "Score", score), el("label", "Out of", total)),
    el("label", "How did it go compared with what you expected?", feel),
    el("label", "Note to yourself", note),
    cats.length ? el("label", "Grade calculator", catSel) : null,
  );
  if (project.outcome) {
    m.footer.appendChild(el("button.btn.btn-ghost-danger", { type: "button", onclick: async () => {
      try { await api(`/projects/${project.id}/outcome`, { method: "DELETE" }); project.outcome = null; m.close(); nav.onForecastChanged?.(); }
      catch (err) { toast(err.message, "error"); }
    } }, "Remove result"));
  }
  m.footer.append(el("button.btn.btn-ghost", { type: "button", onclick: m.close }, "Cancel"),
    el("button.btn.btn-primary", { type: "button", onclick: async () => {
      const body = { score: Number(score.value), total: Number(total.value),
        reflection: { feeling: feel.value || null, note: note.value.trim() || null } };
      if (score.value === "" || !body.total) { toast("Enter a score and total", "error"); return; }
      if (catSel.value) body.addToGrades = { categoryId: catSel.value };
      try {
        const r = await api(`/projects/${project.id}/outcome`, { method: "PUT", body: JSON.stringify(body) });
        project.outcome = r.outcome;
        project.status = r.status;
        m.close();
        toast("Result saved", "success");
        nav.onForecastChanged?.();
      } catch (err) { toast(`Couldn't save: ${err.message}`, "error"); }
    } }, "Save"));
}


// ---------------------------------------------------------------------------
// progress view: insights
// ---------------------------------------------------------------------------
export async function renderInsights(container, termId) {
  clear(container).appendChild(loading("Looking at your recent study…"));
  let d;
  try {
    d = await api(`/insights?today=${todayISO()}${termId ? `&termId=${termId}` : ""}`);
  } catch (err) {
    clear(container).appendChild(errorBox(`Couldn't load insights: ${err.message}`, () => renderInsights(container, termId)));
    return;
  }
  clear(container);

  // recommendations
  const recs = d.recommendations.filter(r => !r.ack);
  const recBox = el("section.panel-card", el("h3", "Suggestions"));
  if (!recs.length) {
    recBox.appendChild(el("p.small.muted", "No suggestions right now. They appear once there's enough evidence — e.g. 5+ finished tasks with logged time, or diagnostics before an upcoming assessment."));
  }
  for (const r of recs) recBox.appendChild(buildRec(r, container, termId));
  container.appendChild(recBox);

  // weekly planned vs logged
  const max = Math.max(1, ...d.weekly.map(w => Math.max(w.plannedMinutes, w.loggedMinutes)));
  container.appendChild(el("section.panel-card",
    el("h3", "Planned vs. logged study, by week"),
    el("div.week-table", { role: "table", "aria-label": "planned versus logged study by week" },
      ...d.weekly.map(w => el("div.week-row", { role: "row" },
        el("span.week-label", { role: "cell" }, `wk of ${formatDate(w.weekStart)}`),
        el("div.week-bars", { role: "cell" },
          el("div.bar-track.planned", el("div.bar-fill", { style: { width: `${(w.plannedMinutes / max) * 100}%` } })),
          el("div.bar-track.logged", el("div.bar-fill", { style: { width: `${(w.loggedMinutes / max) * 100}%` } }))),
        el("span.week-values", { role: "cell" }, `${formatHours(w.plannedMinutes)} planned · ${formatHours(w.loggedMinutes)} logged`)))),
    el("p.small.muted", d.notes.join(" ")),
  ));

  // estimates
  const c = d.calibration;
  container.appendChild(el("section.panel-card",
    el("h3", "How long tasks really take"),
    c.sufficient
      ? el("p", `Across ${c.n} finished tasks with logged time, work took a median ${c.median}× the estimate.`)
      : el("p.small.muted", `${c.n} of ${c.minObservations} finished tasks with logged time so far — margin needs at least ${c.minObservations} before comparing estimates.`),
    ...d.calibrationByCourse.filter(x => x.sufficient).map(x => el("p.small", `${x.name}: ${x.median}× (${x.n} tasks)`)),
  ));

  // concept progress
  if (d.conceptProgress.length) {
    container.appendChild(el("section.panel-card",
      el("h3", "Concepts: now vs. two weeks ago"),
      ...d.conceptProgress.map(cp => el("div.concept-progress",
        el("strong", cp.name),
        el("span.small", ` demonstrated ${cp.twoWeeksAgo.demonstrated} → ${cp.now.demonstrated}`,
          ` · needs practice ${cp.twoWeeksAgo.needs_practice} → ${cp.now.needs_practice}`,
          ` · not enough evidence ${cp.twoWeeksAgo.insufficient_evidence} → ${cp.now.insufficient_evidence}`))),
      el("p.small.muted", "Counts come from your diagnostic answers. A concept with too few answers stays in “not enough evidence” rather than being guessed."),
    ));
  }

  // past assessments
  if (d.pastAssessments.length) {
    container.appendChild(el("section.panel-card",
      el("h3", "Look back at recent assessments"),
      el("ul.session-list", ...d.pastAssessments.map(a => el("li",
        el("button.link-btn", { type: "button", onclick: () => nav.openAssignmentById(a.id) }, a.title),
        el("span.muted.small", `${a.courseName} · ${formatDate(a.dueDate)}${a.outcome ? ` · ${a.outcome.percent}%` : ""}`))))));
  }
}

function buildRec(r, container, termId) {
  const act = r.action;
  let btn = null;
  const reload = () => renderInsights(container, termId);
  if (act?.type === "enable_calibration") btn = ["Adjust my estimates", async () => { await setCalibration(true); reload(); }];
  if (act?.type === "disable_calibration") btn = ["Turn off adjustment", async () => { await setCalibration(false); reload(); }];
  if (act?.type === "set_daily_limit") btn = [`Use ${formatHours(act.minutes)} a day`, async () => {
    try {
      await api("/planning/availability", { method: "PUT", body: JSON.stringify({ maxStudyMinutesPerDay: act.minutes }) });
      toast("Daily study limit updated", "success");
      nav.refreshAfterPlanChange();
      reload();
    } catch (err) { toast(err.message, "error"); }
  }];
  if (act?.type === "start_diagnostic") btn = ["Start a diagnostic", async () => {
    const p = await api(`/projects/${act.assessmentId}`);
    const { courses } = await api("/courses?termId=all");
    nav.state.currentCourse = courses.find(c => c.id === p.courseId) || nav.state.currentCourse;
    startSession(p.courseId, { assessmentId: act.assessmentId });
  }];
  if (act?.type === "schedule_practice") btn = ["Schedule practice", async () => {
    const p = await api(`/projects/${act.assessmentId}`);
    openPracticeScheduler(p.courseId);
  }];
  return el("div.rec",
    el("div.rec-text", el("strong", r.title), el("span", r.detail), el("span.small.muted", `Based on: ${r.evidence}`)),
    el("div.risk-actions",
      btn ? el("button.btn.btn-ghost.btn-sm", { type: "button", onclick: btn[1] }, btn[0]) : null,
      el("button.link-btn", { type: "button", onclick: async () => {
        try {
          await api("/planning/acks", { method: "POST", body: JSON.stringify({ riskId: r.id, state: "dismissed", fingerprint: r.fingerprint }) });
          reload();
        } catch (err) { toast(err.message, "error"); }
      } }, "not for me")));
}
