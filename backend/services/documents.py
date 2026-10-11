"""
academic document intelligence — pure functions (no flask, no firestore, no network).

pipeline: file bytes → page texts → prompt → (model) → normalize_extraction()
→ detect_issues() → student review → validate_review_item() → saved data.

the model's output is never trusted as-is: every field is type-checked, dates
must be real ISO dates, weights must be 0–100, and every quoted source passage
is looked up in the actual page text. anything the document doesn't state stays
null and is surfaced as "missing" instead of being filled in.
"""

import io
import re
import hashlib
import unicodedata
from datetime import date

PROMPT_VERSION = "extract-v1"

DOC_TYPES = {
    "syllabus": "course syllabus",
    "assignment": "assignment instructions",
    "rubric": "grading rubric",
    "exam_review": "exam review sheet",
    "project_brief": "project brief",
    "lecture_notes": "lecture notes",
    "other": "course document",
}

ASSESSMENT_KINDS = ("assignment", "exam", "quiz", "project", "lab", "presentation", "participation", "other")

ALLOWED_UPLOAD_TYPES = {
    "application/pdf": "pdf",
    "text/plain": "text",
    "text/markdown": "text",
}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PAGES = 80
MAX_PROMPT_CHARS = 60000


class DocumentError(ValueError):
    pass


# -----------------------------------------------------------------------------
# reading files
# -----------------------------------------------------------------------------
def sniff_kind(data, filename=""):
    """decide how to read an upload from its bytes (not the client-supplied mime type)."""
    if data.startswith(b"%PDF-"):
        return "pdf"
    name = (filename or "").lower()
    if name.endswith((".txt", ".md")):
        try:
            data.decode("utf-8")
            return "text"
        except UnicodeDecodeError:
            return None
    return None


def extract_pages(data, kind):
    """→ list of page strings. raises DocumentError if the file can't be read."""
    if kind == "pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise DocumentError("this PDF is password-protected")
            pages = [(p.extract_text() or "") for p in reader.pages[:MAX_PAGES]]
        except DocumentError:
            raise
        except Exception:
            raise DocumentError("could not read this PDF — it may be damaged")
        return [clean_text(p) for p in pages]
    if kind == "text":
        text = clean_text(data.decode("utf-8", errors="replace"))
        # split long plain-text files into pseudo-pages so source references stay useful
        chunks, size = [], 3500
        paras = text.split("\n\n")
        cur = ""
        for para in paras:
            if len(cur) + len(para) > size and cur:
                chunks.append(cur.strip())
                cur = ""
            cur += para + "\n\n"
        if cur.strip():
            chunks.append(cur.strip())
        return chunks[:MAX_PAGES] or [""]
    raise DocumentError("unsupported file type — upload a PDF or a .txt/.md file")


def clean_text(text):
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


# -----------------------------------------------------------------------------
# prompt
# -----------------------------------------------------------------------------
def build_extraction_prompt(doc_type, pages, course_name="", term_name="", today=None, assessment_hint=None):
    label = DOC_TYPES.get(doc_type, "course document")
    body, used = [], 0
    for i, p in enumerate(pages, start=1):
        chunk = f"[[page {i}]]\n{p}\n"
        if used + len(chunk) > MAX_PROMPT_CHARS:
            body.append(f"[[remaining pages omitted for length]]")
            break
        body.append(chunk)
        used += len(chunk)

    focus = {
        "syllabus": "Focus on every graded assessment (assignments, quizzes, exams, projects, participation), their due dates and grade weights, and the course learning objectives.",
        "assignment": "This document describes one assignment. Extract it as a single assessment with its requirements, deliverables, milestones and any stated workload.",
        "project_brief": "This document describes a project. Extract it as one assessment with deliverables, milestones (intermediate deadlines) and requirements.",
        "rubric": "This is a grading rubric. Extract the assessment it grades and list each grading criterion as a requirement (include point values in the text).",
        "exam_review": "This is an exam review sheet. Extract the exam as an assessment and every listed topic or skill as a learning objective related to that exam.",
        "lecture_notes": "These are lecture notes. Extract stated learning objectives or key concepts as learning objectives. Only list assessments if the notes explicitly announce one.",
    }.get(doc_type, "Extract any assessments and learning objectives that are explicitly stated.")

    hint = f'\nThe student says this document belongs to the assessment titled "{assessment_hint}".' if assessment_hint else ""

    return f"""You extract structured academic information from a student's {label}.
Course: {course_name or "unknown"}. Term: {term_name or "unknown"}. Today's date: {(today or date.today()).isoformat()}.{hint}
{focus}

STRICT RULES
- Only report what the document states. Never guess deadlines, grade weights, requirements or objectives.
- If something is not stated, use null (or an empty list). Missing information is expected and fine.
- "quote" fields must be copied verbatim (exact characters) from the document, max 200 characters, and "page" must be the [[page N]] it appears on.
- dueDate must be YYYY-MM-DD only when the document gives a specific date. Put the document's original wording in dueDateText. If the year is not written, you may infer it from the term, but add "year inferred" to ambiguities.
- If a date is relative ("week 5", "TBA", "in class"), set dueDate to null and keep the wording in dueDateText.
- weightPercent is the percentage of the final grade only if the document states it.
- estimatedHours: the workload stated in the document; if the document does not state one you may suggest a rough number but then set estimatedHoursIsGuess to true.
- List anything unclear or contradictory in "ambiguities".

Respond with ONLY a JSON object of this shape:
{{
  "assessments": [{{
    "title": string, "kind": one of {list(ASSESSMENT_KINDS)},
    "dueDate": "YYYY-MM-DD" or null, "dueDateText": string or null,
    "weightPercent": number or null, "weightText": string or null,
    "estimatedHours": number or null, "estimatedHoursIsGuess": boolean,
    "deliverables": [string], "prerequisites": [string],
    "requirements": [{{"text": string, "quote": string or null, "page": number or null}}],
    "milestones": [{{"title": string, "dueDate": "YYYY-MM-DD" or null, "dueDateText": string or null, "quote": string or null, "page": number or null}}],
    "quote": string or null, "page": number or null,
    "ambiguities": [string]
  }}],
  "learningObjectives": [{{"text": string, "quote": string or null, "page": number or null, "relatedAssessments": [string]}}],
  "notes": [string]
}}

DOCUMENT
{"".join(body)}"""


# -----------------------------------------------------------------------------
# normalization
# -----------------------------------------------------------------------------
def _norm_space(s):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s or "")).strip().lower()


def normalize_title(title):
    t = _norm_space(title)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"\b(the|a|an)\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def stable_key(*parts):
    raw = "|".join(normalize_title(p or "") for p in parts)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def title_similarity(a, b):
    """token jaccard on normalized titles, with numbers required to match (PS1 ≠ PS2)."""
    ta, tb = normalize_title(a), normalize_title(b)
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    nums_a, nums_b = set(re.findall(r"\d+", ta)), set(re.findall(r"\d+", tb))
    if nums_a != nums_b:
        return 0.0
    sa, sb = set(ta.split()), set(tb.split())
    return len(sa & sb) / len(sa | sb)


def locate_quote(quote, pages, claimed_page=None):
    """find the page a quote really appears on (1-based) or None if it isn't in the text."""
    q = _norm_space(quote)
    if len(q) < 4:
        return None
    norm_pages = [_norm_space(p) for p in pages]
    order = list(range(len(pages)))
    if isinstance(claimed_page, int) and 1 <= claimed_page <= len(pages):
        order.remove(claimed_page - 1)
        order.insert(0, claimed_page - 1)
    for i in order:
        if q in norm_pages[i]:
            return i + 1
    # tolerate small extraction differences: most of the quote's words in order on one page
    words = q.split()
    if len(words) >= 4:
        for i in order:
            hits = sum(1 for w in words if w in norm_pages[i])
            if hits / len(words) >= 0.85:
                return i + 1
    return None


def _str_or_none(v, max_len=300):
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        v = str(v)
    if not isinstance(v, str):
        return None
    v = v.strip()
    return v[:max_len] if v else None


def parse_iso_date(v):
    if not isinstance(v, str):
        return None
    m = re.fullmatch(r"\s*(\d{4})-(\d{2})-(\d{2})\s*", v)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _num_or_none(v, lo, hi):
    if isinstance(v, bool):
        return None
    if isinstance(v, str):
        m = re.search(r"-?\d+(?:\.\d+)?", v)
        v = float(m.group()) if m else None
    if not isinstance(v, (int, float)):
        return None
    v = float(v)
    if v < lo or v > hi:
        return "out_of_range"
    return round(v, 2)


def _source(quote, page, pages):
    quote = _str_or_none(quote, 300)
    if not quote:
        return None
    found = locate_quote(quote, pages, page if isinstance(page, int) else None)
    return {"quote": quote, "page": found, "verified": found is not None}


def _issue(code, message, severity="warning", field=None):
    return {"code": code, "message": message, "severity": severity, "field": field}


def normalize_extraction(raw, pages):
    """model JSON → {"assessments": [...], "objectives": [...], "notes": [...], "dropped": n}.

    raises DocumentError if the response is not the expected shape at all.
    """
    if not isinstance(raw, dict):
        raise DocumentError("the AI response was not in the expected format")
    raw_assessments = raw.get("assessments", [])
    raw_objectives = raw.get("learningObjectives", raw.get("objectives", []))
    if not isinstance(raw_assessments, list) or not isinstance(raw_objectives, list):
        raise DocumentError("the AI response was not in the expected format")

    dropped = 0
    assessments = []
    for a in raw_assessments[:60]:
        if not isinstance(a, dict):
            dropped += 1
            continue
        title = _str_or_none(a.get("title"), 200)
        if not title:
            dropped += 1
            continue
        issues = []
        kind = a.get("kind") if a.get("kind") in ASSESSMENT_KINDS else "other"

        due_text = _str_or_none(a.get("dueDateText"), 120)
        due = parse_iso_date(a.get("dueDate"))
        if a.get("dueDate") and not due:
            issues.append(_issue("invalid_date", f"the extracted date \"{a.get('dueDate')}\" isn't a real date", "error", "dueDate"))
        if due is None:
            if due_text:
                issues.append(_issue("ambiguous_date", f"the document says \"{due_text}\" — set the exact date", "warning", "dueDate"))
            else:
                issues.append(_issue("missing_date", "no due date found in the document", "warning", "dueDate"))
        elif due_text and not re.search(r"\b(19|20)\d{2}\b", due_text):
            issues.append(_issue("year_inferred", f"the document says \"{due_text}\" without a year — check the year", "info", "dueDate"))

        weight = _num_or_none(a.get("weightPercent"), 0, 100)
        weight_text = _str_or_none(a.get("weightText"), 120)
        if weight == "out_of_range":
            issues.append(_issue("invalid_weight", "the extracted grade weight isn't between 0 and 100%", "error", "weight"))
            weight = None
        elif weight is None and kind not in ("other",):
            issues.append(_issue("missing_weight", "no grade weight found in the document", "info", "weight"))

        hours = _num_or_none(a.get("estimatedHours"), 0, 500)
        if hours == "out_of_range":
            hours = None
        hours_guess = bool(a.get("estimatedHoursIsGuess")) if hours is not None else False

        requirements = []
        for r in (a.get("requirements") or [])[:40]:
            if isinstance(r, str):
                r = {"text": r}
            if not isinstance(r, dict):
                continue
            text = _str_or_none(r.get("text"), 500)
            if not text:
                continue
            requirements.append({
                "key": stable_key(title, text),
                "text": text,
                "source": _source(r.get("quote"), r.get("page"), pages),
                "include": True,
            })

        milestones = []
        for m in (a.get("milestones") or [])[:20]:
            if not isinstance(m, dict):
                continue
            mt = _str_or_none(m.get("title"), 200)
            if not mt:
                continue
            md = parse_iso_date(m.get("dueDate"))
            milestones.append({
                "key": stable_key(title, mt),
                "title": mt,
                "dueDate": md.isoformat() if md else None,
                "dueDateText": _str_or_none(m.get("dueDateText"), 120),
                "source": _source(m.get("quote"), m.get("page"), pages),
                "include": True,
            })

        source = _source(a.get("quote"), a.get("page"), pages)
        if source is None:
            issues.append(_issue("no_source", "the AI didn't point to where this appears in the document", "warning"))
        elif not source["verified"]:
            issues.append(_issue("unverified_source", "the quoted passage couldn't be found in the document text — double-check this item", "warning"))

        for amb in (a.get("ambiguities") or [])[:5]:
            amb = _str_or_none(amb, 300)
            if amb:
                issues.append(_issue("ambiguous", amb, "info"))

        assessments.append({
            "key": stable_key(kind, title),
            "fields": {
                "title": title,
                "kind": kind,
                "dueDate": due.isoformat() if due else None,
                "weight": weight,
                "estimatedHours": hours,
            },
            "dueDateText": due_text,
            "weightText": weight_text,
            "estimatedHoursOrigin": ("ai_suggested" if hours_guess else "document") if hours is not None else None,
            "deliverables": [d for d in (_str_or_none(x, 300) for x in (a.get("deliverables") or [])[:20]) if d],
            "prerequisites": [d for d in (_str_or_none(x, 300) for x in (a.get("prerequisites") or [])[:20]) if d],
            "requirements": requirements,
            "milestones": milestones,
            "source": source,
            "issues": issues,
            "decision": "pending",
            "action": "create",
            "targetId": None,
        })

    objectives = []
    for o in raw_objectives[:80]:
        if isinstance(o, str):
            o = {"text": o}
        if not isinstance(o, dict):
            dropped += 1
            continue
        text = _str_or_none(o.get("text"), 500)
        if not text:
            dropped += 1
            continue
        related = [r for r in (_str_or_none(x, 200) for x in (o.get("relatedAssessments") or [])[:10]) if r]
        source = _source(o.get("quote"), o.get("page"), pages)
        issues = []
        if source is not None and not source["verified"]:
            issues.append(_issue("unverified_source", "the quoted passage couldn't be found in the document text", "warning"))
        objectives.append({
            "key": stable_key("objective", text),
            "text": text,
            "relatedAssessmentTitles": related,
            "assessmentKeys": [],
            "source": source,
            "issues": issues,
            "decision": "pending",
        })

    # de-duplicate objectives with identical keys
    seen, unique = set(), []
    for o in objectives:
        if o["key"] in seen:
            continue
        seen.add(o["key"])
        unique.append(o)

    notes = [n for n in (_str_or_none(x, 300) for x in (raw.get("notes") or [])[:10]) if n] if isinstance(raw.get("notes"), list) else []
    return {"assessments": assessments, "objectives": unique, "notes": notes, "dropped": dropped}


# -----------------------------------------------------------------------------
# cross-item checks: duplicates, conflicts, weights, matches with existing data
# -----------------------------------------------------------------------------
DUPLICATE_THRESHOLD = 0.75


def detect_issues(extraction, existing_assessments, document_id=None, term_bounds=None):
    """annotate an extraction in place with duplicate / conflict / weight issues and
    default actions (link to an existing assessment instead of creating a duplicate).

    existing_assessments: list of dicts with id, title, kind, dueDate (YYYY-MM-DD or None),
    sourceDocumentId, sourceKey.
    """
    items = extraction["assessments"]
    doc_warnings = []

    # 1. duplicates inside this extraction (same assessment listed twice)
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if title_similarity(a["fields"]["title"], b["fields"]["title"]) >= 0.999 or a["key"] == b["key"]:
                da, dbb = a["fields"]["dueDate"], b["fields"]["dueDate"]
                if da and dbb and da != dbb:
                    msg = f"the document gives two different dates for \"{a['fields']['title']}\" ({da} and {dbb})"
                    a["issues"].append(_issue("conflicting_dates", msg, "error", "dueDate"))
                    b["issues"].append(_issue("conflicting_dates", msg, "error", "dueDate"))
                b["issues"].append(_issue("duplicate_in_document", f"looks like a repeat of \"{a['fields']['title']}\" — reject one of them", "warning"))

    # 2. match against assessments the student already has
    for a in items:
        best, best_score = None, 0.0
        for e in existing_assessments:
            if document_id and e.get("sourceDocumentId") == document_id and e.get("sourceKey") == a["key"]:
                best, best_score = e, 1.01  # created from this very item on a previous run
                break
            s = title_similarity(a["fields"]["title"], e.get("title", ""))
            if s > best_score:
                best, best_score = e, s
        if best is not None and best_score >= DUPLICATE_THRESHOLD:
            a["action"] = "update"
            a["targetId"] = best["id"]
            a["match"] = {"id": best["id"], "title": best.get("title"), "dueDate": best.get("dueDate"), "score": round(min(best_score, 1.0), 2)}
            ed, ad = best.get("dueDate"), a["fields"]["dueDate"]
            if ed and ad and ed != ad:
                a["issues"].append(_issue(
                    "conflicts_existing",
                    f"you already have \"{best.get('title')}\" due {ed}; the document says {ad}",
                    "error", "dueDate"))
            else:
                a["issues"].append(_issue("matches_existing", f"matches your existing assessment \"{best.get('title')}\" — confirming will update it, not duplicate it", "info"))

    # 3. dates outside the term
    if term_bounds:
        start, end = term_bounds
        for a in items:
            d = parse_iso_date(a["fields"]["dueDate"] or "")
            if d and ((start and d < start) or (end and d > end)):
                a["issues"].append(_issue("outside_term", f"{d.isoformat()} is outside this term's dates", "warning", "dueDate"))

    # 4. grade weights should add up to ~100% when a syllabus lists them all
    weights = [a["fields"]["weight"] for a in items if isinstance(a["fields"]["weight"], (int, float))]
    if len(weights) >= 2:
        total = round(sum(weights), 2)
        if abs(total - 100) > 0.5:
            doc_warnings.append(_issue("weights_total", f"extracted grade weights add up to {total:g}%, not 100% — some may be missing or misread", "warning", "weight"))

    # 5. link objectives to assessments by title
    for o in extraction["objectives"]:
        keys = []
        for t in o.get("relatedAssessmentTitles", []):
            for a in items:
                if title_similarity(t, a["fields"]["title"]) >= DUPLICATE_THRESHOLD and a["key"] not in keys:
                    keys.append(a["key"])
        o["assessmentKeys"] = keys

    extraction["warnings"] = doc_warnings
    return extraction


# -----------------------------------------------------------------------------
# validating what the student confirms
# -----------------------------------------------------------------------------
def validate_review_fields(fields):
    """server-side validation of student-edited assessment fields → (clean, errors)."""
    errors = []
    clean = {}
    title = fields.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append("title is required")
    else:
        clean["title"] = title.strip()[:200]

    kind = fields.get("kind", "other")
    if kind not in ASSESSMENT_KINDS:
        errors.append(f"kind must be one of {', '.join(ASSESSMENT_KINDS)}")
    clean["kind"] = kind

    due = fields.get("dueDate")
    if due in (None, ""):
        clean["dueDate"] = None
    else:
        d = parse_iso_date(due)
        if not d:
            errors.append("dueDate must be YYYY-MM-DD")
        clean["dueDate"] = d.isoformat() if d else None

    for name, hi in (("weight", 100), ("estimatedHours", 500)):
        v = fields.get(name)
        if v in (None, ""):
            clean[name] = None
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            try:
                v = float(v)
            except (TypeError, ValueError):
                errors.append(f"{name} must be a number")
                continue
        if v < 0 or v > hi:
            errors.append(f"{name} must be between 0 and {hi}")
            continue
        clean[name] = round(float(v), 2)
    return clean, errors
