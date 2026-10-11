/*
  margin — home: semester overview
  ================================
  answers the three questions at a glance: what do i need to do, what do i
  actually understand, do i have enough time. every item links to where it's handled.
*/

import { api, nav, el, clear, loading, errorBox, statusChip, formatDate, formatDateLong, formatHours, relativeDays, todayISO } from "./core.js";
import { loadForecast, openRecovery } from "./planner.js";
import { openReview } from "./documents.js";
import { openSession } from "./diagnostics.js";

export async function renderHomeOverview(container, term, courseCount) {
  if (!term) { clear(container); return; }
  clear(container).appendChild(loading("Getting your semester overview…"));
  if (!courseCount) {
    clear(container).appendChild(el("section.panel-card.home-empty",
      el("h3", `${term.name}: let's set it up`),
      el("p.muted", "Add your courses, then upload each syllabus — margin pulls in every deadline and grade weight for you to confirm, and starts forecasting your workload."),
      el("div.row-actions", el("button.btn.btn-primary", { type: "button", onclick: () => nav.openTerm?.(term) }, "Add courses"))));
    return;
  }
  let forecast, pending;
  try {
    [forecast, pending] = await Promise.all([
      loadForecast(term.id),
      api(`/overview/pending?termId=${term.id}`),
    ]);
  } catch (err) {
    clear(container).appendChild(errorBox(`Couldn't load your overview: ${err.message}`, () => renderHomeOverview(container, term, courseCount)));
    return;
  }
  clear(container);
  container.appendChild(el("div.home-head",
    el("h2", term.name),
    el("span.muted", new Date().toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" }))));

  const grid = el("div.home-grid");
  container.appendChild(grid);

  // next steps
  const steps = el("section.panel-card.home-steps", el("h3", "Next steps"));
  const list = el("ol.step-list");
  const add = (text, sub, label, onclick, tone = "") => list.appendChild(el(`li.step${tone ? `.${tone}` : ""}`,
    el("div.step-text", el("span", text), sub ? el("span.small.muted", sub) : null),
    el("button.btn.btn-ghost.btn-sm", { type: "button", onclick }, label)));
  for (const d of pending.documents.filter(d => d.status === "needs_review" || d.pendingItems)) {
    add(`Review what margin found in ${d.filename}`, `${d.courseName} — nothing is added until you confirm it`, "Review", async () => { await nav.selectCourseById?.(d.courseId); openReview(d.id); });
  }
  const risks = forecast.risks.filter(r => !r.ack);
  for (const r of risks.filter(r => r.severity === "high").slice(0, 3)) {
    add(r.title, r.explanation, "Rebuild plan", openRecovery, "high");
  }
  for (const s of pending.sessions.slice(0, 1)) {
    add("Finish your diagnostic", `${s.courseName}, started ${s.createdAt ? formatDate(s.createdAt) : "recently"}`, "Resume", async () => { await nav.selectCourseById?.(s.courseId); openSession(s.id); });
  }
  for (const r of risks.filter(r => r.severity === "medium").slice(0, 2)) {
    add(r.title, r.explanation, "Open planner", () => document.getElementById("calendar-btn").click());
  }
  const weekAhead = addDaysISO(7);
  for (const a of forecast.assessments.filter(a => a.status === "insufficient_information" && a.dueDate && a.dueDate <= weekAhead).slice(0, 2)) {
    add(`Add tasks or an expected workload for ${a.title}`, `due ${relativeDays(a.dueDate)} — margin can't tell yet whether it fits`, "Open",
      () => nav.openAssignmentById(a.id));
  }
  for (const d of pending.documents.filter(d => d.status === "failed").slice(0, 2)) {
    add(`${d.filename} couldn't be read automatically`, d.courseName, "Open", () => nav.selectCourseById?.(d.courseId, "documents"));
  }
  if (!list.childNodes.length) steps.appendChild(el("p.muted.small", "Nothing needs attention right now. 💛"));
  else steps.appendChild(list);
  grid.appendChild(steps);

  // coming up
  const soon = forecast.assessments
    .filter(a => a.dueDate && a.dueDate >= todayISO() && a.status !== "done" && a.dueDate <= addDaysISO(21))
    .sort((a, b) => a.dueDate.localeCompare(b.dueDate));
  const upcoming = el("section.panel-card.home-upcoming", el("h3", "Coming up (3 weeks)"));
  if (!soon.length) upcoming.appendChild(el("p.muted.small", "No assessments due in the next three weeks."));
  for (const a of soon.slice(0, 8)) {
    upcoming.appendChild(el("button.upcoming-row", { type: "button", onclick: () => nav.openAssignmentById(a.id) },
      el("span.course-dot", { style: { background: a.courseColor || "#ccc" }, "aria-hidden": "true" }),
      el("span.upcoming-title", a.title, el("span.small.muted", ` · ${a.courseName}`)),
      el("span.small.muted", `${formatDate(a.dueDate)} · ${relativeDays(a.dueDate)}`),
      statusChip(a.status)));
  }
  const noInfo = forecast.assessments.filter(a => a.status === "insufficient_information").length;
  if (noInfo) upcoming.appendChild(el("p.small.muted", `${noInfo} assessment${noInfo === 1 ? " needs" : "s need"} a due date or workload estimate before margin can forecast it.`));
  grid.appendChild(upcoming);

  // workload
  const s = forecast.summary || {};
  const days = (forecast.days || []).slice(0, 7);
  const maxDay = Math.max(1, ...days.map(d => Math.max(d.freeMinutes, d.plannedMinutes)));
  grid.appendChild(el("section.panel-card.home-workload",
    el("h3", "Do I have enough time?"),
    el("p", el("strong", formatHours(s.freeMinutesNext14 || 0)), " free in the next 14 days for ",
      el("strong", formatHours(s.demandMinutesDueNext14 || 0)), " of work due in that time."),
    el("div.mini-days", { "aria-label": "free and planned time for the next 7 days" }, ...days.map(d => el("div.mini-day", {
      title: `${formatDateLong(d.date)}: ${formatHours(d.plannedMinutes)} planned, ${formatHours(d.freeMinutes)} free`,
    },
      el("div.mini-bars",
        el("div.mini-free", { style: { height: `${(d.freeMinutes / maxDay) * 100}%` } }),
        el(`div.mini-planned${d.overloaded ? ".over" : ""}`, { style: { height: `${(d.plannedMinutes / maxDay) * 100}%` } })),
      el("span.mini-label", new Date(`${d.date}T00:00`).toLocaleDateString("en-US", { weekday: "narrow" }))))),
    el("p.small.muted", "Light bars: free time · dark bars: planned work"),
    el("div.row-actions", el("button.btn.btn-ghost.btn-sm", { type: "button", onclick: () => document.getElementById("calendar-btn").click() }, "Open planner")),
  ));

  // this week's study
  grid.appendChild(el("section.panel-card.home-study",
    el("h3", "This week"),
    el("p", el("strong", formatHours(pending.loggedThisWeekMinutes)), " of study logged so far."),
    el("p.small.muted", "Log time with the study timer on any task, or “Log study time” on an assessment."),
    el("div.row-actions", el("button.btn.btn-ghost.btn-sm", { type: "button", onclick: () => document.getElementById("progress-btn").click() }, "See progress")),
  ));
}

function addDaysISO(n) {
  const d = new Date();
  d.setDate(d.getDate() + n);
  return d.toLocaleDateString("en-CA");
}
