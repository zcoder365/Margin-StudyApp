/*
  margin — assessment overview
  ============================
  the panel at the top of an assessment: what it is, what it requires, where the
  facts came from, and (in later sections) how preparation is going.
*/

import { api, apiBlob, el, clear, formatDate, relativeDays, toast } from "./core.js";
import { KIND_LABELS } from "./documents.js";

// extra sections registered by other modules: (project, container) => void
const sectionBuilders = [];
export function registerOverviewSection(fn) { sectionBuilders.push(fn); }

export function renderAssessmentOverview(project) {
  const root = document.getElementById("assessment-overview");
  clear(root);
  root.classList.remove("hidden");

  const facts = el("div.overview-facts");
  const fact = (label, value, note) => facts.appendChild(el("div.fact",
    el("div.fact-label", label), el("div.fact-value", value), note ? el("div.fact-note", note) : null));

  fact("Type", KIND_LABELS[project.kind] || (project.kind ? project.kind : "Assignment"));
  fact("Due", project.dueDate ? formatDate(project.dueDate) : "not set", project.dueDate ? relativeDays(project.dueDate) : "add a date so margin can plan for it");
  fact("Grade weight", project.weight != null ? `${project.weight}%` : "unknown");
  if (project.estimatedHours != null) {
    const origin = { ai_suggested: "AI suggestion — adjust if it looks off", document: "from the document", student: "your estimate" }[project.estimatedHoursOrigin] || "";
    fact("Expected workload", `${project.estimatedHours}h`, origin);
  }
  root.appendChild(facts);

  if (project.verification === "confirmed" && project.source?.documentId) {
    const src = project.source;
    root.appendChild(el("p.source-line",
      "Confirmed from a course document",
      src.page ? ` (page ${src.page})` : "",
      src.quote ? el("q", src.quote) : null,
      el("button.link-btn", { type: "button", onclick: () => openSourceFile(src.documentId) }, "open file"),
    ));
  }

  const details = el("div.overview-details");
  if (project.requirements?.length) {
    details.appendChild(el("section.overview-block",
      el("h4", "Requirements"),
      el("ul.req-list", ...project.requirements.map(r => el("li", r.text,
        r.source?.page ? el("span.muted.small", ` · p. ${r.source.page}`) : null))),
    ));
  }
  if (project.milestones?.length) {
    details.appendChild(el("section.overview-block",
      el("h4", "Milestones"),
      el("ul.req-list", ...project.milestones.map(m => el("li",
        el("strong", m.dueDate ? `${formatDate(m.dueDate)} — ` : "date not set — "), m.title))),
    ));
  }
  if (project.deliverables?.length) {
    details.appendChild(el("section.overview-block", el("h4", "Deliverables"),
      el("ul.req-list", ...project.deliverables.map(d => el("li", d)))));
  }
  if (project.prerequisites?.length) {
    details.appendChild(el("section.overview-block", el("h4", "Before you start"),
      el("ul.req-list", ...project.prerequisites.map(d => el("li", d)))));
  }
  if (details.childNodes.length) root.appendChild(details);

  for (const build of sectionBuilders) {
    const container = el("div.overview-section");
    root.appendChild(container);
    try { build(project, container); } catch (err) { console.error("overview section failed:", err); }
  }
}

export function hideAssessmentOverview() {
  document.getElementById("assessment-overview")?.classList.add("hidden");
}

async function openSourceFile(documentId) {
  try {
    const blob = await apiBlob(`/documents/${documentId}/file`);
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank", "noopener");
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } catch (err) {
    toast(err.status === 404 ? "The source document was deleted." : `Couldn't open file: ${err.message}`, "error");
  }
}

// load the stored PDF for an assignment so it survives page reloads
export async function loadStoredPdf(project) {
  if (!project.documentId) return null;
  try {
    const blob = await apiBlob(`/documents/${project.documentId}/file`);
    return URL.createObjectURL(blob);
  } catch {
    return null;
  }
}

export async function fetchProject(id) {
  return api(`/projects/${id}`);
}
