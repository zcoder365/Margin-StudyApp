/*
  margin — course documents + extraction review
  =============================================
  upload syllabi / briefs / rubrics / review sheets / notes to a course, let the
  AI extract assessments and objectives in the background, then review every
  item (edit, confirm, reject) before anything reaches the planner.
*/

import {
  api, apiBlob, nav, el, clear, toast, loading, errorBox, emptyState, confirmDialog,
  formatDate, todayISO,
} from "./core.js";

export const DOC_TYPE_LABELS = {
  syllabus: "Syllabus",
  assignment: "Assignment instructions",
  rubric: "Grading rubric",
  exam_review: "Exam review sheet",
  project_brief: "Project brief",
  lecture_notes: "Lecture notes",
  other: "Other",
};

export const KIND_LABELS = {
  assignment: "Assignment", exam: "Exam", quiz: "Quiz", project: "Project", lab: "Lab",
  presentation: "Presentation", participation: "Participation", other: "Other",
};

const DOC_STATUS = {
  stored: ["Stored", "neutral"],
  processing: ["Extracting…", "busy"],
  needs_review: ["Needs review", "attention"],
  reviewed: ["Reviewed", "ok"],
  failed: ["Couldn't extract", "error"],
};

let pollTimer = null;
let currentPanel = null;
let currentCourse = null;


// ---------------------------------------------------------------------------
// course → documents tab
// ---------------------------------------------------------------------------
export async function renderCourseDocuments(panel, course) {
  currentPanel = panel;
  currentCourse = course;
  clear(panel);
  panel.appendChild(buildUploadCard(course));
  const list = el("div.doc-list", { id: "doc-list" }, loading("Loading documents…"));
  panel.appendChild(list);
  await refreshDocList();
}

async function refreshDocList() {
  const list = document.getElementById("doc-list");
  if (!list || !currentCourse) return;
  clearTimeout(pollTimer);
  try {
    const { documents } = await api(`/courses/${currentCourse.id}/documents`);
    renderDocList(list, documents);
    // keep polling while something is processing and the tab is visible
    if (documents.some(d => d.status === "processing") && currentPanel && !currentPanel.classList.contains("hidden")) {
      pollTimer = setTimeout(refreshDocList, 3000);
    }
  } catch (err) {
    clear(list).appendChild(errorBox(`Couldn't load documents: ${err.message}`, refreshDocList));
  }
}

export function stopDocumentPolling() {
  clearTimeout(pollTimer);
}

function buildUploadCard(course) {
  const fileInput = el("input", { type: "file", id: "doc-file-input", accept: ".pdf,.txt,.md,application/pdf,text/plain,text/markdown" });
  const typeSelect = el("select", { id: "doc-type-select" },
    ...Object.entries(DOC_TYPE_LABELS).map(([v, l]) => el("option", { value: v }, l)));
  const assessSelect = el("select", { id: "doc-assessment-select" }, el("option", { value: "" }, "— none / several —"));
  const extractBox = el("input", { type: "checkbox", id: "doc-extract-toggle", checked: true });
  const uploadBtn = el("button.btn.btn-primary", { type: "submit" }, "Upload");
  const status = el("p.form-status", { "aria-live": "polite" });

  api(`/projects?courseId=${course.id}&light=1`).then(({ projects }) => {
    for (const p of projects) assessSelect.appendChild(el("option", { value: p.id }, p.title));
  }).catch(() => {});

  const form = el("form.upload-card", {
    onsubmit: async (e) => {
      e.preventDefault();
      const file = fileInput.files[0];
      if (!file) { status.textContent = "Choose a file first."; return; }
      if (file.size > 10 * 1024 * 1024) { status.textContent = "That file is over the 10MB limit."; return; }
      const fd = new FormData();
      fd.append("file", file);
      fd.append("docType", typeSelect.value);
      if (assessSelect.value) fd.append("assessmentId", assessSelect.value);
      fd.append("extract", extractBox.checked ? "1" : "0");
      fd.append("today", todayISO());
      uploadBtn.disabled = true;
      status.textContent = "Uploading…";
      try {
        await api(`/courses/${course.id}/documents`, { method: "POST", body: fd });
        status.textContent = extractBox.checked
          ? "Uploaded. margin is reading it — you'll review everything before it's added."
          : "Uploaded.";
        form.reset();
        extractBox.checked = true;
        await refreshDocList();
      } catch (err) {
        status.textContent = `Upload failed: ${err.message}`;
      } finally {
        uploadBtn.disabled = false;
      }
    },
  },
    el("div.upload-card-head",
      el("h3", "Add a course document"),
      el("p.muted", "Syllabi, assignment briefs, rubrics, review sheets or notes (PDF, .txt or .md, up to 10MB). Nothing is added to your planner until you review it."),
    ),
    el("div.upload-grid",
      el("label", "File", fileInput),
      el("label", "Type", typeSelect),
      el("label", "Belongs to", assessSelect),
    ),
    el("div.upload-actions",
      el("label.toggle-row", extractBox, el("span.toggle-label", "Extract dates, weights and objectives with AI")),
      uploadBtn,
    ),
    status,
  );
  return form;
}

function renderDocList(list, documents) {
  clear(list);
  if (!documents.length) {
    list.appendChild(emptyState("No documents yet. Upload your syllabus to pull in every deadline and grade weight at once."));
    return;
  }
  for (const d of documents) list.appendChild(buildDocRow(d));
}

function buildDocRow(d) {
  const [label, tone] = DOC_STATUS[d.status] || [d.status, "neutral"];
  const actions = el("div.doc-actions");
  const add = (text, onclick, cls = "btn-ghost") =>
    actions.appendChild(el(`button.btn.btn-sm.${cls}`, { type: "button", onclick }, text));

  if (d.status === "needs_review") add("Review", () => openReview(d.id), "btn-primary");
  if (d.status === "reviewed") add(d.pendingItems ? `Review (${d.pendingItems} left)` : "Review again", () => openReview(d.id));
  if (d.status === "stored" && d.hasText) add("Extract details", () => processDoc(d, false));
  if (d.status === "failed" && d.hasText) add("Try again", () => processDoc(d, true));
  if (["reviewed", "needs_review"].includes(d.status)) add("Re-extract", () => processDoc(d, true));
  add("View", () => viewFile(d));
  add("Delete", () => deleteDoc(d), "btn-ghost-danger");

  return el("div.doc-row",
    el("div.doc-main",
      el("div.doc-name", d.filename),
      el("div.doc-meta",
        el("span", DOC_TYPE_LABELS[d.docType] || d.docType),
        el("span", `${d.pageCount} page${d.pageCount === 1 ? "" : "s"}`),
        d.createdAt ? el("span", `added ${formatDate(d.createdAt)}`) : null,
      ),
      d.status === "failed" && d.error ? el("div.doc-error", d.error) : null,
    ),
    el(`span.doc-status.tone-${tone}`, label),
    actions,
  );
}

async function processDoc(d, force) {
  if (force && d.status === "reviewed") {
    const ok = await confirmDialog({
      title: "Re-extract this document?",
      body: "margin will read it again. Assessments you already confirmed stay as they are — matching items will be offered as updates, not duplicates.",
      confirmLabel: "Re-extract",
    });
    if (!ok) return;
  }
  try {
    await api(`/documents/${d.id}/process`, { method: "POST", body: JSON.stringify({ force, today: todayISO() }) });
    toast("Extraction started");
  } catch (err) {
    toast(`Couldn't start extraction: ${err.message}`, "error");
  }
  await refreshDocList();
}

async function viewFile(d) {
  try {
    const blob = await apiBlob(`/documents/${d.id}/file`);
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank", "noopener");
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } catch (err) {
    toast(`Couldn't open file: ${err.message}`, "error");
  }
}

async function deleteDoc(d) {
  const ok = await confirmDialog({
    title: `Delete "${d.filename}"?`,
    body: "The file and its extracted text will be removed. Assessments and objectives you already confirmed are kept.",
    confirmLabel: "Delete", danger: true,
  });
  if (!ok) return;
  try {
    await api(`/documents/${d.id}`, { method: "DELETE" });
    toast("Document deleted");
  } catch (err) {
    toast(`Couldn't delete: ${err.message}`, "error");
  }
  await refreshDocList();
}


// ---------------------------------------------------------------------------
// review view
// ---------------------------------------------------------------------------
const reviewView = () => document.getElementById("view-review");
let review = null;  // { doc, ex, existing: [], pages: Map }

export async function openReview(docId) {
  stopDocumentPolling();
  nav.showView("review");
  const root = clear(reviewView());
  root.appendChild(loading("Loading extraction…"));
  try {
    const doc = await api(`/documents/${docId}`);
    const { projects } = await api(`/projects?courseId=${doc.courseId}&light=1`);
    review = { doc, ex: doc.extraction, existing: projects, pages: new Map() };
    renderReview();
  } catch (err) {
    clear(root).appendChild(errorBox(`Couldn't load this document: ${err.message}`, () => openReview(docId)));
  }
}

function renderReview() {
  const root = clear(reviewView());
  const { doc, ex } = review;

  root.appendChild(el("div.section-header",
    el("div",
      el("h2", `Review: ${doc.filename}`),
      el("p.muted", "Check each item against the document. Edit anything that's wrong, then confirm or reject it. Only confirmed items are added to your courses and planner."),
    ),
    el("div.header-actions",
      el("button.btn.btn-ghost", { type: "button", onclick: () => backToCourse() }, "Back to course"),
    ),
  ));

  if (!ex) {
    root.appendChild(emptyState("There's nothing to review for this document yet."));
    return;
  }

  const legend = el("div.review-legend",
    el("span.origin-badge.origin-document", "from document"),
    el("span", "facts the AI found in your file, with a page reference"),
    el("span.origin-badge.origin-ai", "AI suggestion"),
    el("span", "not stated in the document — an estimate you should check"),
  );
  root.appendChild(legend);

  for (const w of ex.warnings || []) root.appendChild(issueLine(w));
  if (ex.dropped) root.appendChild(issueLine({ severity: "info", message: `${ex.dropped} malformed item(s) from the AI were discarded.` }));

  const layout = el("div.review-layout");
  const listCol = el("div.review-items");
  const sourceCol = el("aside.review-source", { "aria-label": "source document" },
    el("h3", "Source"),
    el("div.source-body", { id: "source-body" }, el("p.muted", "Select “show source” on an item to see where it came from.")),
  );
  layout.append(listCol, sourceCol);
  root.appendChild(layout);

  listCol.appendChild(el("h3.review-heading", `Assessments (${ex.assessments.length})`));
  if (!ex.assessments.length) listCol.appendChild(emptyState("No assessments were found in this document."));
  ex.assessments.forEach(item => listCol.appendChild(buildAssessmentCard(item)));

  listCol.appendChild(el("h3.review-heading", `Learning objectives (${ex.objectives.length})`));
  if (!ex.objectives.length) listCol.appendChild(emptyState("No learning objectives were found."));
  ex.objectives.forEach(o => listCol.appendChild(buildObjectiveRow(o)));

  const status = el("p.form-status", { id: "review-status", "aria-live": "polite" });
  const footer = el("div.review-footer",
    el("button.btn.btn-ghost", { type: "button", onclick: () => setAll("confirm") }, "Confirm all"),
    el("button.btn.btn-ghost", { type: "button", onclick: saveDraft }, "Save progress"),
    el("button.btn.btn-primary", { type: "button", id: "apply-review-btn", onclick: applyReview }, "Save confirmed items"),
    status,
  );
  root.appendChild(footer);
}

function issueLine(issue) {
  const icon = { error: "!", warning: "△", info: "i" }[issue.severity] || "i";
  return el(`div.issue.issue-${issue.severity}`, el("span.issue-icon", { "aria-hidden": "true" }, icon), el("span", issue.message));
}

function decisionControl(item, onChange) {
  const name = `decision-${item.key}`;
  const opt = (value, label) => el("label.seg-option",
    el("input", { type: "radio", name, value, checked: item.decision === value, onchange: () => { item.decision = value; onChange(); } }),
    el("span", label));
  return el("div.segmented", { role: "radiogroup", "aria-label": "decision" },
    opt("confirm", "Confirm"), opt("pending", "Decide later"), opt("reject", "Reject"));
}

function buildAssessmentCard(item) {
  const f = item.fields;
  const card = el("article.review-card", { dataset: { key: item.key } });
  const refresh = () => {
    card.classList.toggle("is-confirmed", item.decision === "confirm");
    card.classList.toggle("is-rejected", item.decision === "reject");
  };

  const field = (label, input, note) => el("label.review-field", el("span.field-label", label), input, note ? el("span.field-note", note) : null);

  const titleIn = el("input", { type: "text", value: f.title || "", oninput: (e) => { f.title = e.target.value; } });
  const kindIn = el("select", { onchange: (e) => { f.kind = e.target.value; } },
    ...Object.entries(KIND_LABELS).map(([v, l]) => el("option", { value: v, selected: f.kind === v }, l)));
  const dueIn = el("input", { type: "date", value: f.dueDate || "", oninput: (e) => { f.dueDate = e.target.value || null; } });
  const weightIn = el("input", { type: "number", min: "0", max: "100", step: "0.5", value: f.weight ?? "", placeholder: "unknown",
    oninput: (e) => { f.weight = e.target.value === "" ? null : Number(e.target.value); } });
  const hoursIn = el("input", { type: "number", min: "0", max: "500", step: "0.5", value: f.estimatedHours ?? "", placeholder: "unknown",
    oninput: (e) => { f.estimatedHours = e.target.value === "" ? null : Number(e.target.value); item.estimatedHoursOrigin = "student"; hoursBadge.replaceWith(hoursBadge = originBadge("student")); } });
  let hoursBadge = originBadge(item.estimatedHoursOrigin);

  card.appendChild(el("div.review-card-head", decisionControl(item, refresh),
    item.source ? el("button.btn.btn-ghost.btn-sm", { type: "button", onclick: () => showSource(item.source) },
      item.source.page ? `show source (p. ${item.source.page})` : "show source") : el("span.muted.small", "no source reference")));

  for (const issue of item.issues || []) card.appendChild(issueLine(issue));

  card.appendChild(el("div.review-fields",
    field("Title", titleIn),
    field("Type", kindIn),
    field("Due date", dueIn, item.dueDateText ? `document says: “${item.dueDateText}”` : null),
    field("Grade weight (%)", weightIn, item.weightText ? `document says: “${item.weightText}”` : null),
    el("label.review-field", el("span.field-label", "Workload (hours) ", hoursBadge), hoursIn),
  ));

  if (item.match) {
    const select = el("select", { onchange: (e) => {
      item.action = e.target.value === "__new" ? "create" : "update";
      item.targetId = e.target.value === "__new" ? null : e.target.value;
    } },
      ...review.existing.map(p => el("option", { value: p.id, selected: item.action === "update" && item.targetId === p.id },
        `Update “${p.title}”${p.dueDate ? ` (due ${formatDate(p.dueDate)})` : ""}`)),
      el("option", { value: "__new", selected: item.action === "create" }, "Create as a new assessment"));
    card.appendChild(el("label.review-field.wide", el("span.field-label", "Already in margin?"), select));
  }

  if (item.requirements?.length) {
    card.appendChild(el("details.review-sub", { open: item.requirements.length <= 4 },
      el("summary", `Requirements (${item.requirements.length})`),
      ...item.requirements.map(r => el("label.check-row",
        el("input", { type: "checkbox", checked: r.include !== false, onchange: (e) => { r.include = e.target.checked; } }),
        el("span", r.text),
        r.source ? el("button.link-btn", { type: "button", onclick: () => showSource(r.source) }, r.source.page ? `p. ${r.source.page}` : "source") : null,
      )),
    ));
  }
  if (item.milestones?.length) {
    card.appendChild(el("details.review-sub", { open: true },
      el("summary", `Milestones (${item.milestones.length})`),
      ...item.milestones.map(m => el("div.check-row",
        el("input", { type: "checkbox", checked: m.include !== false, "aria-label": `include ${m.title}`, onchange: (e) => { m.include = e.target.checked; } }),
        el("span", m.title),
        el("input.small-date", { type: "date", value: m.dueDate || "", "aria-label": `${m.title} date`, oninput: (e) => { m.dueDate = e.target.value || null; } }),
        m.dueDateText ? el("span.field-note", `“${m.dueDateText}”`) : null,
      )),
    ));
  }
  if (item.deliverables?.length) {
    card.appendChild(el("details.review-sub",
      el("summary", `Deliverables (${item.deliverables.length})`),
      el("ul", ...item.deliverables.map(d => el("li", d))),
    ));
  }
  refresh();
  return card;
}

function originBadge(origin) {
  if (origin === "ai_suggested") return el("span.origin-badge.origin-ai", "AI suggestion");
  if (origin === "document") return el("span.origin-badge.origin-document", "from document");
  if (origin === "student") return el("span.origin-badge.origin-student", "your estimate");
  return el("span");
}

function buildObjectiveRow(o) {
  const titles = o.assessmentKeys
    .map(k => review.ex.assessments.find(a => a.key === k)?.fields.title)
    .filter(Boolean);
  const row = el("div.objective-row",
    el("input", { type: "checkbox", checked: o.decision === "confirm", "aria-label": "confirm objective",
      onchange: (e) => { o.decision = e.target.checked ? "confirm" : "pending"; } }),
    el("div.objective-main",
      el("input.objective-text", { type: "text", value: o.text, "aria-label": "objective text", oninput: (e) => { o.text = e.target.value; } }),
      el("div.objective-meta",
        titles.length ? el("span", `for: ${titles.join(", ")}`) : el("span.muted", "not linked to an assessment"),
        ...(o.issues || []).map(i => el(`span.issue-inline.issue-${i.severity}`, i.message)),
      ),
    ),
    o.source ? el("button.link-btn", { type: "button", onclick: () => showSource(o.source) }, o.source.page ? `p. ${o.source.page}` : "source") : null,
    el("button.link-btn.danger", { type: "button", onclick: () => { o.decision = "reject"; row.remove(); } }, "remove"),
  );
  return row;
}

function setAll(decision) {
  for (const a of review.ex.assessments) if (a.decision !== "reject") a.decision = decision;
  for (const o of review.ex.objectives) if (o.decision !== "reject") o.decision = decision;
  renderReview();
}

function buildPayload() {
  return {
    assessments: review.ex.assessments.map(a => ({
      key: a.key, decision: a.decision, action: a.action, targetId: a.targetId, fields: a.fields,
      requirements: (a.requirements || []).map(r => ({ key: r.key, include: r.include !== false })),
      milestones: (a.milestones || []).map(m => ({ key: m.key, include: m.include !== false, dueDate: m.dueDate })),
    })),
    objectives: review.ex.objectives.map(o => ({ key: o.key, decision: o.decision, text: o.text, assessmentKeys: o.assessmentKeys })),
  };
}

async function saveDraft() {
  const status = document.getElementById("review-status");
  try {
    await api(`/documents/${review.doc.id}/review`, { method: "PATCH", body: JSON.stringify(buildPayload()) });
    status.textContent = "Progress saved.";
  } catch (err) {
    status.textContent = `Couldn't save: ${err.message}`;
  }
}

async function applyReview() {
  const ex = review.ex;
  const confirmed = ex.assessments.filter(a => a.decision === "confirm");
  const objectives = ex.objectives.filter(o => o.decision === "confirm");
  const pending = ex.assessments.filter(a => a.decision === "pending").length;
  const rejected = ex.assessments.filter(a => a.decision === "reject").length;
  if (!confirmed.length && !objectives.length) {
    toast("Confirm at least one item first, or go back to the course.", "error");
    return;
  }
  const creates = confirmed.filter(a => a.action !== "update").length;
  const updates = confirmed.length - creates;
  const lines = [];
  if (creates) lines.push(el("li", `${creates} new assessment${creates === 1 ? "" : "s"} added to your course and planner`));
  if (updates) lines.push(el("li", `${updates} existing assessment${updates === 1 ? "" : "s"} updated with the document's details`));
  if (objectives.length) lines.push(el("li", `${objectives.length} learning objective${objectives.length === 1 ? "" : "s"} saved`));
  if (rejected) lines.push(el("li", `${rejected} rejected item${rejected === 1 ? "" : "s"} ignored`));
  if (pending) lines.push(el("li", `${pending} undecided item${pending === 1 ? "" : "s"} left for later`));
  const missingDates = confirmed.filter(a => !a.fields.dueDate).length;
  if (missingDates) lines.push(el("li", `${missingDates} confirmed item${missingDates === 1 ? " has" : "s have"} no due date and won't appear in the planner until you add one`));

  const ok = await confirmDialog({ title: "Save confirmed items?", body: el("ul.plain-list", ...lines), confirmLabel: "Save" });
  if (!ok) return;

  const btn = document.getElementById("apply-review-btn");
  const status = document.getElementById("review-status");
  btn.disabled = true;
  status.textContent = "Saving…";
  try {
    const res = await api(`/documents/${review.doc.id}/apply`, { method: "POST", body: JSON.stringify(buildPayload()) });
    toast(`Saved — ${res.created.length} added, ${res.updated.length} updated, ${res.objectives.length} objectives.`, "success");
    nav.refreshAfterPlanChange();
    backToCourse("assessments");
  } catch (err) {
    clear(status);
    status.appendChild(el("span", `Nothing was saved: ${err.message}`));
    if (err.details) status.appendChild(el("ul.plain-list", ...err.details.map(d => el("li", d))));
  } finally {
    btn.disabled = false;
  }
}

function backToCourse(tab = "documents") {
  const course = nav.state.currentCourse;
  if (course && course.id === review?.doc.courseId) nav.openCourse(course, tab);
  else nav.showView("home");
}

async function showSource(source) {
  const body = document.getElementById("source-body");
  if (!body) return;
  if (!source.page) {
    clear(body).append(
      el("p.issue.issue-warning", "This passage couldn't be found in the document text, so margin can't show where it came from. Check it against the original file."),
      el("blockquote", source.quote || ""),
    );
    return;
  }
  clear(body).appendChild(loading(`Loading page ${source.page}…`));
  try {
    let text = review.pages.get(source.page);
    if (text === undefined) {
      text = (await api(`/documents/${review.doc.id}/pages/${source.page}`)).text;
      review.pages.set(source.page, text);
    }
    clear(body);
    body.appendChild(el("div.source-page-label", `Page ${source.page}`));
    const pre = el("div.source-text");
    const idx = findLoose(text, source.quote || "");
    if (idx) {
      pre.append(text.slice(0, idx[0]), el("mark", text.slice(idx[0], idx[1])), text.slice(idx[1]));
    } else {
      pre.textContent = text;
    }
    body.appendChild(pre);
    pre.querySelector("mark")?.scrollIntoView({ block: "center", behavior: "smooth" });
  } catch (err) {
    clear(body).appendChild(errorBox(`Couldn't load the page: ${err.message}`));
  }
}

// whitespace/case-insensitive search returning [start, end] in the original text
function findLoose(text, quote) {
  const q = quote.trim().replace(/\s+/g, " ").toLowerCase();
  if (q.length < 4) return null;
  const map = [];
  let norm = "";
  for (let i = 0; i < text.length; i++) {
    const ch = /\s/.test(text[i]) ? " " : text[i].toLowerCase();
    if (ch === " " && norm.endsWith(" ")) continue;
    norm += ch;
    map.push(i);
  }
  const at = norm.indexOf(q);
  if (at === -1) return null;
  return [map[at], map[at + q.length - 1] + 1];
}
