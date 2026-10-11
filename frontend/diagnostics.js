/*
  margin — diagnostics: concept map, knowledge gaps, diagnostic sessions, practice
  ===============================================================================
  the course "Practice & gaps" tab, the one-question-at-a-time session runner,
  and a diagnostics summary on each assessment. mastery labels and the evidence
  behind them come from the server's transparent rules.
*/

import {
  api, nav, el, clear, toast, loading, errorBox, emptyState, confirmDialog, openModal,
  statusChip, formatDate, formatDateLong, relativeDays, todayISO,
} from "./core.js";
import { registerOverviewSection } from "./assessment.js";

const GROUP_ORDER = ["needs_practice", "insufficient_evidence", "developing", "demonstrated"];
const GROUP_TITLES = {
  needs_practice: "Needs practice",
  insufficient_evidence: "Not enough evidence yet",
  developing: "Developing",
  demonstrated: "Demonstrated reliably",
};
const CONFIDENCE = [[1, "Guessing"], [2, "Somewhat sure"], [3, "Sure"]];
const FLAG_REASONS = [["incorrect", "The answer key is wrong"], ["ambiguous", "It's ambiguous"],
  ["poorly_worded", "It's poorly worded"], ["off_topic", "Not part of this course"], ["other", "Something else"]];

let panelRef = null;
let courseRef = null;
let suggestTimer = null;


// ---------------------------------------------------------------------------
// course tab (registered by app.js)
// ---------------------------------------------------------------------------
export async function renderPracticeTab(panel, course) {
  panelRef = panel;
  courseRef = course;
  clearTimeout(suggestTimer);
  clear(panel).appendChild(loading("Loading your concepts…"));
  let dash, projects;
  try {
    [dash, { projects }] = await Promise.all([
      api(`/courses/${course.id}/diagnostics?today=${todayISO()}`),
      api(`/projects?courseId=${course.id}&light=1`),
    ]);
  } catch (err) {
    clear(panel).appendChild(errorBox(`Couldn't load diagnostics: ${err.message}`, () => renderPracticeTab(panel, course)));
    return;
  }
  clear(panel);
  const all = GROUP_ORDER.flatMap(k => dash.groups[k]);

  panel.appendChild(el("p.muted.tab-intro",
    "Short diagnostics show which ideas you can reliably apply — and which need more work — before an exam does. ",
    "Nothing here predicts your grade."));

  if (!all.length) {
    panel.appendChild(buildConceptSetup(course));
    return;
  }

  panel.appendChild(buildStartCard(course, projects));

  const gaps = el("section.gap-board", { "aria-label": "knowledge gaps" });
  for (const k of GROUP_ORDER) {
    const items = dash.groups[k];
    gaps.appendChild(el(`div.gap-col.gap-${k}`,
      el("h3", GROUP_TITLES[k], el("span.count", String(items.length))),
      items.length ? null : el("p.muted.small", k === "needs_practice" ? "Nothing flagged right now." : "—"),
      ...items.map(c => buildConceptCard(c, course, dash)),
    ));
  }
  panel.appendChild(gaps);
  panel.appendChild(el("details.rules-box",
    el("summary", "How margin decides these labels"),
    el("dl", ...GROUP_ORDER.flatMap(k => [el("dt", statusChip(k)), el("dd", dash.rules[k])])),
    el("ul.plain-list.small", ...dash.evidenceNotes.map(n => el("li", n))),
  ));

  const lower = el("div.practice-lower");
  if (dash.practicePlan.length) {
    lower.appendChild(el("section.panel-card",
      el("h3", "Suggested practice"),
      el("ol.practice-plan", ...dash.practicePlan.map(p => el("li",
        el("strong", p.name), el("span.muted.small", ` — ${p.reason} · ~${p.minutes} min`)))),
      el("div.row-actions",
        el("button.btn.btn-primary.btn-sm", { type: "button", onclick: () => openPracticeScheduler(course.id) }, "Add practice to my schedule…"),
        el("button.btn.btn-ghost.btn-sm", { type: "button", onclick: () => startSession(course.id, { conceptIds: dash.practicePlan.slice(0, 3).map(p => p.conceptId) }) }, "Practise the top 3 now"),
      ),
    ));
  }
  if (dash.patterns.length) {
    lower.appendChild(el("section.panel-card",
      el("h3", "Recurring patterns"),
      el("ul.plain-list", ...dash.patterns.map(p => el("li", p.message)))));
  }
  if (dash.recentMistakes.length) {
    lower.appendChild(el("section.panel-card",
      el("h3", "Revisit recent mistakes"),
      el("ul.mistake-list", ...dash.recentMistakes.map(m => el("li",
        el("button.link-btn", { type: "button", onclick: () => openMistake(m.questionId) }, m.prompt))))));
  }
  if (dash.recentSessions.length) {
    lower.appendChild(el("section.panel-card",
      el("h3", "Recent diagnostics"),
      el("ul.session-list", ...dash.recentSessions.map(s => el("li",
        el("span", s.createdAt ? formatDateLong(s.createdAt) : "—"),
        el("span.muted.small", s.summary ? `${s.summary.answered}/${s.summary.total} answered` : s.status.replace("_", " ")),
        s.status !== "completed" && s.status !== "failed"
          ? el("button.link-btn", { type: "button", onclick: () => openSession(s.id) }, "resume") : null)))));
  }
  panel.appendChild(lower);

  panel.appendChild(el("details.manage-concepts",
    el("summary", "Manage concepts"),
    el("div.row-actions",
      el("button.btn.btn-ghost.btn-sm", { type: "button", onclick: () => runSuggestions(course, panel.querySelector(".manage-concepts .suggest-area")) }, "Suggest more from my material"),
      el("button.btn.btn-ghost.btn-sm", { type: "button", onclick: () => editConcept(course, null) }, "+ Add a concept"),
    ),
    el("div.suggest-area"),
    dash.flaggedQuestions ? el("p.small.muted", `${dash.flaggedQuestions} flagged question(s) are excluded from your results.`) : null,
  ));
}

function buildStartCard(course, projects) {
  const upcoming = projects.filter(p => p.status !== "completed" && p.dueDate && p.dueDate.slice(0, 10) >= todayISO())
    .sort((a, b) => a.dueDate.localeCompare(b.dueDate));
  const which = el("select", { "aria-label": "focus" },
    el("option", { value: "" }, "Whole course"),
    ...upcoming.map(p => el("option", { value: p.id }, `${p.title} (${formatDate(p.dueDate)})`)));
  const size = el("select", { "aria-label": "number of questions" },
    ...[4, 6, 8].map(n => el("option", { value: String(n), selected: n === 6 }, `${n} questions`)));
  return el("section.start-card",
    el("div", el("h3", "Start a diagnostic"), el("p.muted.small", "About 1–2 minutes per question. Your weakest and least-practised concepts come first.")),
    el("div.start-controls", which, size,
      el("button.btn.btn-primary", { type: "button", onclick: () => startSession(course.id, { assessmentId: which.value || null, size: Number(size.value) }) }, "Start")),
  );
}

function evidenceLine(m) {
  const e = m.evidence;
  const parts = [`${e.attempts} answer${e.attempts === 1 ? "" : "s"}`];
  if (e.weightedAccuracy != null) parts.push(`${Math.round(e.weightedAccuracy * 100)}% recent accuracy`);
  if (e.lastAttemptAt) parts.push(`last ${relativeDays(e.lastAttemptAt.slice(0, 10))}`);
  if (e.confidentErrors) parts.push(`${e.confidentErrors} confident miss${e.confidentErrors === 1 ? "" : "es"}`);
  return parts.join(" · ");
}

function buildConceptCard(c, course, dash) {
  return el("article.concept-card",
    el("div.concept-name", c.name, c.origin === "ai_suggested" ? el("span.origin-badge.origin-ai", "AI-suggested") : null),
    c.description ? el("p.small.muted", c.description) : null,
    el("p.evidence", evidenceLine(c.mastery)),
    c.mastery.reasons.length ? el("p.small", c.mastery.reasons.join("; ")) : null,
    c.assessments.length ? el("p.small.muted", `for ${c.assessments.map(a => a.title).join(", ")}`) : null,
    el("div.row-actions",
      el("button.btn.btn-ghost.btn-sm", { type: "button", onclick: () => startSession(course.id, { conceptIds: [c.id], size: 4 }) }, "Practise"),
      el("button.link-btn", { type: "button", onclick: () => editConcept(course, c) }, "edit"),
    ),
  );
}


// ---------------------------------------------------------------------------
// concept setup / suggestions / editing
// ---------------------------------------------------------------------------
function buildConceptSetup(course) {
  const area = el("div.suggest-area");
  return el("section.panel-card.concept-setup",
    el("h3", "Set up this course's concepts"),
    el("p", "margin can propose a concept map from your confirmed learning objectives and uploaded course material. You review it first — keep what's right, drop what isn't. You can also add concepts yourself."),
    el("div.row-actions",
      el("button.btn.btn-primary", { type: "button", onclick: () => runSuggestions(course, area) }, "Suggest concepts from my material"),
      el("button.btn.btn-ghost", { type: "button", onclick: () => editConcept(course, null) }, "+ Add a concept"),
    ),
    area,
  );
}

async function runSuggestions(course, area) {
  clear(area).appendChild(loading("Reading your course material…"));
  try {
    await api(`/courses/${course.id}/concepts/suggest`, { method: "POST", body: "{}" });
  } catch (err) {
    if (err.status !== 409) { clear(area).appendChild(errorBox(err.message)); return; }
  }
  pollSuggestions(course, area);
}

async function pollSuggestions(course, area, tries = 0) {
  clearTimeout(suggestTimer);
  let sug;
  try { sug = await api(`/courses/${course.id}/concepts/suggestions`); } catch (err) {
    clear(area).appendChild(errorBox(`Couldn't load suggestions: ${err.message}`));
    return;
  }
  if (sug.status === "processing" && tries < 90) {
    suggestTimer = setTimeout(() => pollSuggestions(course, area, tries + 1), 2000);
    return;
  }
  if (sug.status === "failed") {
    clear(area).appendChild(errorBox(`Couldn't suggest concepts: ${sug.error}`, () => runSuggestions(course, area)));
    return;
  }
  renderSuggestions(course, area, sug.suggestions || []);
}

function renderSuggestions(course, area, suggestions) {
  clear(area);
  if (!suggestions.length) { area.appendChild(emptyState("No concepts could be found in your material.")); return; }
  const rows = suggestions.map((s, i) => {
    const box = el("input", { type: "checkbox", checked: !s.alreadyAdded, disabled: s.alreadyAdded });
    const name = el("input.objective-text", { type: "text", value: s.name, "aria-label": "concept name" });
    return { s, i, box, name, node: el("div.objective-row", box,
      el("div.objective-main", name,
        el("div.objective-meta",
          el("span", s.description),
          s.sourceRefs?.[0]?.verified ? el("span.origin-badge.origin-document", `from your material, p. ${s.sourceRefs[0].page}`) : el("span.origin-badge.origin-ai", "AI-suggested"),
          s.alreadyAdded ? el("span.muted", "already added") : null))) };
  });
  area.append(el("p.small.muted", "Untick anything that isn't really part of this course, and fix names if needed."),
    ...rows.map(r => r.node),
    el("button.btn.btn-primary.btn-sm", { type: "button", onclick: async () => {
      const chosen = rows.filter(r => r.box.checked && !r.box.disabled);
      if (!chosen.length) { toast("Select at least one concept", "error"); return; }
      try {
        await api(`/courses/${course.id}/concepts`, { method: "POST", body: JSON.stringify({ concepts: chosen.map(r => ({
          name: r.name.value.trim() || r.s.name, description: r.s.description, objectiveIds: r.s.objectiveIds, fromSuggestion: r.i })) }) });
        toast(`${chosen.length} concept${chosen.length === 1 ? "" : "s"} added`, "success");
        renderPracticeTab(panelRef, courseRef);
      } catch (err) { toast(`Couldn't add: ${err.message}`, "error"); }
    } }, "Add selected concepts"));
}

async function editConcept(course, concept) {
  const m = openModal(concept ? "Edit concept" : "Add a concept");
  let objectives = [];
  try { objectives = (await api(`/courses/${course.id}/objectives`)).objectives; } catch { /* optional */ }
  const name = el("input", { type: "text", value: concept?.name || "", placeholder: "e.g. Pumping lemma proofs" });
  const desc = el("input", { type: "text", value: concept?.description || "", placeholder: "one sentence (optional)" });
  const objs = el("select", { multiple: true, size: String(Math.min(Math.max(objectives.length, 2), 6)) },
    ...objectives.map(o => el("option", { value: o.id, selected: (concept?.objectiveIds || []).includes(o.id) }, o.text)));
  m.body.append(
    el("label", "Name", name),
    el("label", "Description", desc),
    objectives.length ? el("label", "Learning objectives it belongs to (links it to their assessments)", objs)
      : el("p.modal-hint", "Confirm learning objectives from a document to link concepts to assessments."),
  );
  if (concept) {
    m.footer.appendChild(el("button.btn.btn-ghost-danger", { type: "button", onclick: async () => {
      const ok = await confirmDialog({ title: `Remove “${concept.name}”?`, body: "Its questions are retired. Your past answers stay in your history.", confirmLabel: "Remove", danger: true });
      if (!ok) return;
      try { await api(`/concepts/${concept.id}`, { method: "DELETE" }); m.close(); renderPracticeTab(panelRef, courseRef); }
      catch (err) { toast(`Couldn't remove: ${err.message}`, "error"); }
    } }, "Remove"));
  }
  m.footer.append(
    el("button.btn.btn-ghost", { type: "button", onclick: m.close }, "Cancel"),
    el("button.btn.btn-primary", { type: "button", onclick: async () => {
      if (!name.value.trim()) { toast("Give the concept a name", "error"); return; }
      const objectiveIds = [...objs.selectedOptions].map(o => o.value);
      try {
        if (concept) {
          await api(`/concepts/${concept.id}`, { method: "PATCH", body: JSON.stringify({ name: name.value.trim(), description: desc.value.trim(), objectiveIds }) });
        } else {
          await api(`/courses/${course.id}/concepts`, { method: "POST", body: JSON.stringify({ concepts: [{ name: name.value.trim(), description: desc.value.trim(), objectiveIds }] }) });
        }
        m.close();
        renderPracticeTab(panelRef, courseRef);
      } catch (err) { toast(`Couldn't save: ${err.message}`, "error"); }
    } }, "Save"),
  );
}


// ---------------------------------------------------------------------------
// sessions
// ---------------------------------------------------------------------------
const view = () => document.getElementById("view-diagnostic");
let session = null;   // { id, data, index }
let pollTimer = null;

export async function startSession(courseId, opts = {}) {
  try {
    const r = await api("/diagnostics/sessions", { method: "POST", body: JSON.stringify({ courseId, size: opts.size || 6, assessmentId: opts.assessmentId || null, conceptIds: opts.conceptIds }) });
    openSession(r.id);
  } catch (err) {
    toast(`Couldn't start: ${err.message}`, "error");
  }
}

export async function openSession(id) {
  clearTimeout(pollTimer);
  nav.showView("diagnostic");
  const root = clear(view());
  root.appendChild(loading("Preparing your questions… (new questions can take up to a minute)"));
  let data;
  try { data = await api(`/diagnostics/sessions/${id}`); } catch (err) {
    clear(root).appendChild(errorBox(`Couldn't load this session: ${err.message}`, () => openSession(id)));
    return;
  }
  if (data.status === "preparing") { pollTimer = setTimeout(() => openSession(id), 2000); return; }
  if (data.status === "failed") {
    clear(root).append(errorBox(`This session couldn't be prepared: ${(data.warnings || []).join("; ")}`),
      el("button.btn.btn-ghost", { type: "button", onclick: backToCourse }, "Back to course"));
    return;
  }
  const firstOpen = data.items.findIndex(it => !it.attempt || (it.attempt.gradingMethod === "self" && !it.attempt.selfGrade));
  session = { id, data, index: firstOpen === -1 ? data.items.length : firstOpen };
  if (data.status === "completed") { finishSession(); return; }
  renderQuestion();
}

function renderQuestion() {
  const root = clear(view());
  const { data, index } = session;
  if (index >= data.items.length) { finishSession(); return; }
  const item = data.items[index];
  const q = item.question;

  root.appendChild(el("div.session-head",
    el("button.btn.btn-ghost.btn-sm", { type: "button", onclick: backToCourse }, "← Leave (progress is saved)"),
    el("div.session-progress", { role: "progressbar", "aria-valuemin": "0", "aria-valuemax": String(data.items.length), "aria-valuenow": String(index + 1) },
      `Question ${index + 1} of ${data.items.length}`,
      el("div.progress-track", el("div.progress-fill", { style: { width: `${(index / data.items.length) * 100}%` } })))));
  if (index === 0 && data.warnings?.length) root.appendChild(el("div.issue.issue-info", data.warnings.join(" · ")));

  const card = el("article.question-card");
  card.appendChild(el("div.question-concepts", ...q.conceptIds.map(c => el("span.tag", data.conceptNames[c] || "concept"))));
  card.appendChild(el("p.question-prompt", q.prompt));

  let getResponse;
  if (q.type === "mcq") {
    const name = `q-${q.id}`;
    const opts = q.choices.map((c, i) => el("label.choice", el("input", { type: "radio", name, value: String(i) }), el("span", c)));
    card.appendChild(el("div.choices", { role: "radiogroup", "aria-label": "answer choices" }, ...opts));
    getResponse = () => { const v = card.querySelector(`input[name="${name}"]:checked`); return v ? Number(v.value) : null; };
  } else if (q.type === "numeric") {
    const input = el("input.answer-input", { type: "text", inputmode: "decimal", placeholder: "e.g. 3/4, 0.75 or sqrt(2)", "aria-label": "your answer" });
    card.appendChild(el("div.numeric-row", input, q.unit ? el("span.unit", q.unit) : null));
    getResponse = () => input.value.trim() || null;
  } else {
    const ta = el("textarea.answer-text", { rows: "5", placeholder: "Write your answer, or what you'd try. It's fine to say you're unsure.", "aria-label": "your answer" });
    card.appendChild(ta);
    getResponse = () => ta.value.trim() || null;
  }

  const conf = { value: null };
  card.appendChild(el("div.confidence",
    el("span.small.muted", "How sure are you? (optional)"),
    el("div.segmented", { role: "radiogroup", "aria-label": "confidence" }, ...CONFIDENCE.map(([v, label]) =>
      el("label.seg-option", el("input", { type: "radio", name: `conf-${q.id}`, value: String(v), onchange: () => { conf.value = v; } }), el("span", label))))));

  const status = el("p.form-status", { "aria-live": "polite" });
  const submit = el("button.btn.btn-primary", { type: "button" }, "Check answer");
  card.append(el("div.row-actions", submit), status);
  root.appendChild(card);

  if (item.attempt) { showFeedback(card, item, { correct: item.attempt.correct, gradingMethod: item.attempt.gradingMethod, question: q }); return; }

  submit.addEventListener("click", async () => {
    const response = getResponse();
    if (response === null) { status.textContent = "Answer first — or pick your best guess."; return; }
    submit.disabled = true;
    try {
      const r = await api(`/diagnostics/sessions/${session.id}/answer`, { method: "POST", body: JSON.stringify({ questionId: q.id, response, confidence: conf.value }) });
      item.attempt = { response, correct: r.correct, confidence: conf.value, gradingMethod: r.gradingMethod };
      item.question = r.question;
      showFeedback(card, item, r);
    } catch (err) {
      submit.disabled = false;
      status.textContent = err.message;
    }
  });
}

function showFeedback(card, item, r) {
  card.querySelectorAll("input, textarea").forEach(i => { i.disabled = true; });
  card.querySelector(".row-actions")?.remove();
  const q = r.question;
  const fb = el("div.feedback", { role: "status" });
  if (r.gradingMethod === "self") {
    fb.appendChild(el("h4", "Compare with a model answer"));
    fb.appendChild(el("p.model-answer", q.modelAnswer));
    if (q.keyPoints?.length) fb.appendChild(el("ul.plain-list", ...q.keyPoints.map(k => el("li", k))));
    const grade = el("div.self-grade", el("span", "How did you do?"),
      ...[["correct", "Got it"], ["partial", "Partly"], ["incorrect", "Missed it"]].map(([v, label]) =>
        el(`button.btn.btn-sm.${item.attempt?.selfGrade === v ? "btn-primary" : "btn-ghost"}`, { type: "button", onclick: async () => {
          try {
            await api(`/diagnostics/sessions/${session.id}/self-grade`, { method: "POST", body: JSON.stringify({ questionId: q.id, result: v }) });
            item.attempt.selfGrade = v;
            grade.querySelectorAll("button").forEach(b => { b.className = "btn btn-sm btn-ghost"; });
            grade.querySelectorAll("button")[["correct", "partial", "incorrect"].indexOf(v)].className = "btn btn-sm btn-primary";
            next.disabled = false;
          } catch (err) { toast(err.message, "error"); }
        } }, label)));
    fb.appendChild(grade);
  } else {
    fb.classList.add(r.correct ? "is-correct" : "is-wrong");
    fb.appendChild(el("h4", r.correct ? "Correct" : "Not quite"));
    if (q.type === "mcq") fb.appendChild(el("p", "Answer: ", el("strong", q.choices[q.answerIndex])));
    if (q.type === "numeric") fb.appendChild(el("p", "Answer: ", el("strong", `${+Number(q.answer).toPrecision(6)}${q.unit ? ` ${q.unit}` : ""}`),
      q.tolerance ? el("span.muted.small", ` (± ${q.tolerance})`) : null));
    if (item.attempt?.confidence === 3 && !r.correct) fb.appendChild(el("p.small", "You felt sure about this one — worth a closer look at why the answer differs."));
  }
  if (q.explanation) fb.appendChild(el("p.explanation", q.explanation));
  fb.appendChild(groundingNote(q));
  fb.appendChild(el("button.link-btn", { type: "button", onclick: () => flagQuestion(q) }, "This question looks wrong or unclear — flag it"));
  const next = el("button.btn.btn-primary", { type: "button", disabled: r.gradingMethod === "self" && !item.attempt?.selfGrade,
    onclick: () => { session.index += 1; renderQuestion(); } },
  session.index + 1 >= session.data.items.length ? "Finish" : "Next question");
  fb.appendChild(el("div.row-actions", next));
  card.appendChild(fb);
  next.focus();
}

function groundingNote(q) {
  if (q.grounding === "source" && q.source) {
    return el("p.grounding",
      el("span.origin-badge.origin-document", `from ${q.source.filename || "your course material"}${q.source.page ? `, p. ${q.source.page}` : ""}`),
      q.source.quote ? el("q", q.source.quote) : null,
      el("span.small.muted", " The answer key itself was written by AI — check it against your notes."));
  }
  if (q.grounding === "fallback") return el("p.grounding.small.muted", "Self-check prompt — grade yourself against your notes.");
  return el("p.grounding", el("span.origin-badge.origin-ai", "AI-generated example"),
    el("span.small.muted", " Not taken from your course material, and the answer key is AI-written — flag it if it seems wrong."));
}

function flagQuestion(q) {
  const m = openModal("Flag this question");
  const reason = el("select", ...FLAG_REASONS.map(([v, l]) => el("option", { value: v }, l)));
  const note = el("input", { type: "text", placeholder: "what's wrong? (optional)" });
  m.body.append(el("p.modal-hint", "Flagged questions are withdrawn, and your answers to them no longer count toward your results."),
    el("label", "Reason", reason), el("label", "Note", note));
  m.footer.append(el("button.btn.btn-ghost", { type: "button", onclick: m.close }, "Cancel"),
    el("button.btn.btn-primary", { type: "button", onclick: async () => {
      try {
        await api(`/questions/${q.id}/flag`, { method: "POST", body: JSON.stringify({ reason: reason.value, note: note.value }) });
        m.close();
        toast("Thanks — that question won't count or come up again", "success");
      } catch (err) { toast(`Couldn't flag: ${err.message}`, "error"); }
    } }, "Flag"));
}

async function finishSession() {
  const root = clear(view());
  root.appendChild(loading("Summarising…"));
  let res;
  try {
    res = await api(`/diagnostics/sessions/${session.id}/complete`, { method: "POST", body: JSON.stringify({ today: todayISO() }) });
  } catch (err) {
    clear(root).appendChild(errorBox(`Couldn't finish: ${err.message}`, finishSession));
    return;
  }
  clear(root);
  const s = res.summary;
  root.append(
    el("div.section-header", el("h2", "Diagnostic summary"),
      el("button.btn.btn-ghost", { type: "button", onclick: backToCourse }, "Back to course")),
    el("p.muted", `${s.answered} of ${s.total} answered. This is a snapshot of today — use it to decide what to practise, not as a verdict.`),
    el("div.summary-concepts", ...s.concepts.map(c => el("div.summary-concept",
      el("div", el("strong", c.name), c.masteryAfter ? statusChip(c.masteryAfter) : null),
      el("p.small", `${c.score % 1 ? c.score.toFixed(1) : c.score} of ${c.answered} right in this session${c.ungraded ? ` · ${c.ungraded} not self-graded` : ""}`),
      c.reasons?.length ? el("p.small.muted", c.reasons.join("; ")) : null))),
  );
  if (res.practicePlan.length) {
    root.append(el("section.panel-card",
      el("h3", "What to practise next"),
      el("ol.practice-plan", ...res.practicePlan.map(p => el("li", el("strong", p.name), el("span.muted.small", ` — ${p.reason}`)))),
      el("div.row-actions", el("button.btn.btn-primary", { type: "button", onclick: () => openPracticeScheduler(session.data.courseId, res.practicePlan.map(p => p.conceptId)) }, "Add practice to my schedule…"))));
  }
}

function backToCourse() {
  clearTimeout(pollTimer);
  const cid = session?.data?.courseId || courseRef?.id;
  if (nav.state.currentCourse && nav.state.currentCourse.id === cid) nav.openCourse(nav.state.currentCourse, "practice");
  else nav.showView("home");
}

async function openMistake(questionId) {
  const m = openModal("Revisit a mistake", { wide: true });
  m.body.appendChild(loading());
  try {
    const { question: q, attempts } = await api(`/diagnostics/mistakes/${questionId}`);
    clear(m.body);
    m.body.append(el("p.question-prompt", q.prompt));
    if (q.type === "mcq") m.body.append(el("ol.choice-review", ...q.choices.map((c, i) => el(`li${i === q.answerIndex ? ".right" : ""}`, c))));
    if (q.type === "numeric") m.body.append(el("p", "Answer: ", el("strong", String(+Number(q.answer).toPrecision(6)))));
    if (q.type === "short") m.body.append(el("p.model-answer", q.modelAnswer));
    m.body.append(el("p.explanation", q.explanation || ""), groundingNote(q),
      el("h4.sub-heading", "Your answers"),
      el("ul.plain-list", ...attempts.map(a => el("li", `${a.createdAt ? formatDate(a.createdAt) : ""}: `,
        q.type === "mcq" && typeof a.response === "number" ? q.choices[a.response] : String(a.response ?? ""),
        a.correct === true ? " ✓" : a.correct === false ? " ✗" : a.selfGrade ? ` (${a.selfGrade})` : ""))),
    );
    m.footer.append(el("button.link-btn", { type: "button", onclick: () => { m.close(); flagQuestion(q); } }, "flag this question"),
      el("button.btn.btn-primary", { type: "button", onclick: m.close }, "Close"));
  } catch (err) {
    clear(m.body).appendChild(errorBox(err.message));
  }
}


// ---------------------------------------------------------------------------
// practice → schedule
// ---------------------------------------------------------------------------
export async function openPracticeScheduler(courseId, conceptIds) {
  const m = openModal("Add practice to my schedule", { wide: true });
  m.body.appendChild(loading("Finding free time before your assessments…"));
  let prev;
  try {
    prev = await api("/diagnostics/practice/preview", { method: "POST", body: JSON.stringify({ courseId, conceptIds, today: todayISO() }) });
  } catch (err) {
    clear(m.body).appendChild(errorBox(`Couldn't plan practice: ${err.message}`));
    return;
  }
  clear(m.body);
  m.body.appendChild(el("p.modal-hint", prev.note));
  const rows = prev.proposed.map(p => {
    const box = el("input", { type: "checkbox", checked: true });
    const date = el("input.small-date", { type: "date", value: p.date, min: todayISO(), max: p.deadline || "", "aria-label": `date for ${p.name}` });
    const mins = el("input.small-num", { type: "number", min: "10", max: "180", step: "5", value: String(p.minutes), "aria-label": "minutes" });
    return { p, box, date, mins, node: el("div.change-row", box,
      el("div.change-main",
        el("div.change-title", `Practice: ${p.name}`),
        el("div.change-what", date, mins, el("span.small.muted", " min")),
        el("div.change-reason", `${p.reason}${p.attachedToNearest ? ` · attached to ${p.assessmentTitle} (nearest deadline)` : ` · for ${p.assessmentTitle}`}`))) };
  });
  if (rows.length) m.body.append(...rows.map(r => r.node));
  else m.body.appendChild(emptyState("There's no free time left for practice before the relevant deadlines."));
  if (prev.unallocated.length) {
    m.body.append(el("h4.sub-heading", "Couldn't be scheduled"),
      ...prev.unallocated.map(u => el("div.issue.issue-warning", `${u.name}: ${u.why}`)));
  }
  const add = el("button.btn.btn-primary", { type: "button", disabled: !rows.length }, "Add to schedule");
  m.footer.append(el("button.btn.btn-ghost", { type: "button", onclick: m.close }, "Cancel"), add);
  add.addEventListener("click", async () => {
    const items = rows.filter(r => r.box.checked).map(r => ({ conceptId: r.p.conceptId, assessmentId: r.p.assessmentId, date: r.date.value, minutes: Number(r.mins.value) }));
    if (!items.length) { toast("Nothing selected", "error"); return; }
    add.disabled = true;
    try {
      await api("/diagnostics/practice/apply", { method: "POST", body: JSON.stringify({ items, today: todayISO() }) });
      m.close();
      toast(`${items.length} practice session${items.length === 1 ? "" : "s"} added to your planner`, "success");
      nav.refreshAfterPlanChange();
    } catch (err) {
      add.disabled = false;
      toast(`Couldn't add: ${err.message}`, "error");
    }
  });
}


// ---------------------------------------------------------------------------
// assessment overview section
// ---------------------------------------------------------------------------
registerOverviewSection(async (project, container) => {
  container.appendChild(loading("Checking concept coverage…"));
  try {
    const { concepts } = await api(`/courses/${project.courseId}/concepts`);
    clear(container);
    const linked = concepts.filter(c => c.assessments.some(a => a.id === project.id));
    const head = el("div.overview-diag-head", el("h4", "Concepts & diagnostics"));
    container.appendChild(head);
    if (!concepts.length) {
      container.appendChild(el("p.small.muted", "No concepts for this course yet. Set them up in the course's Practice & gaps tab to diagnose what you can apply before this is due."));
      return;
    }
    const pool = linked.length ? linked : concepts;
    if (!linked.length) container.appendChild(el("p.small.muted", "No concepts are linked to this assessment yet (link them through learning objectives). Showing the whole course."));
    const counts = GROUP_ORDER.map(k => [k, pool.filter(c => c.mastery.status === k).length]).filter(([, n]) => n);
    container.appendChild(el("div.chip-row", ...counts.map(([k, n]) => el("span", statusChip(k, `${n} ${GROUP_TITLES[k].toLowerCase()}`)))));
    const weak = pool.filter(c => c.mastery.status === "needs_practice");
    if (weak.length) container.appendChild(el("p.small", "Weak spots: ", weak.map(c => c.name).join(", ")));
    container.appendChild(el("div.row-actions",
      el("button.btn.btn-ghost.btn-sm", { type: "button", onclick: () => startSession(project.courseId, { assessmentId: project.id, size: 6 }) }, "Start a diagnostic for this"),
      weak.length || pool.some(c => c.mastery.status === "insufficient_evidence")
        ? el("button.btn.btn-ghost.btn-sm", { type: "button", onclick: () => openPracticeScheduler(project.courseId, pool.map(c => c.id)) }, "Schedule practice") : null));
  } catch (err) {
    clear(container).appendChild(errorBox(`Couldn't load diagnostics: ${err.message}`));
  }
});
