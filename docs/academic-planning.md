# margin — developer guide: academic planning features

This covers the document intelligence, workload forecasting, diagnostics and feedback-loop features: how they're built, where the data lives, and the rules behind every label a student sees.

## Architecture

```
frontend/                      vanilla JS ES modules, no build step
  core.js        firebase auth, api() helper, date helpers, el() DOM builder, modals, toasts
  app.js         existing SPA shell: views, terms/courses/assessments/tasks, calendar, grades, timer
  documents.js   course Documents tab + extraction review screen
  assessment.js  assessment overview panel (other modules register sections into it)
  planner.js     forecast, risks, availability, "I'm behind" recovery (sits above the calendar)
  diagnostics.js Practice & gaps tab, diagnostic session runner, practice scheduling
  insights.js    study-session logging, results, assessment reviews, Progress insights
  home.js        semester overview on the home page

backend/
  app.py         Flask app + the original routes (terms, courses, projects, tasks, grades, schedule)
  core.py        lazy firebase init, auth decorator, injectable db/token verifier, helpers,
                 background jobs, per-user AI rate limits
  ai.py          Gemini provider: JSON mode, timeouts, retry, error classification (mockable)
  services/      pure logic — no Flask, Firestore or network; unit-tested directly
    documents.py   file reading, extraction prompt, normalization, quote verification,
                   duplicate/conflict detection, review validation
    workload.py    availability, remaining work, EDF forecast, statuses, risks, estimate calibration
    recovery.py    schedule recovery planner
    diagnostics.py safe numeric evaluator, question validation, mastery rules, error patterns,
                   practice prioritisation/allocation
    feedback.py    assessment reviews, adaptive recommendations, weekly study
  routes/        Flask blueprints: documents, planning, diagnostics, feedback
  tests/         pytest + an in-memory Firestore fake (enforces field-path and type rules
                 of the real client)
```

Core calculations never call the AI provider. The model is only used to (1) extract structured data from documents, (2) suggest concepts and (3) write diagnostic questions, and every response is validated before anyone sees it.

### Background work

Document extraction, concept suggestions and diagnostic-session preparation run in a daemon thread (`core.run_job`). Status lives on the Firestore document (`processing` → `needs_review` / `ready` / `failed`) and the client polls. A job that dies with the process is treated as stale after 5 minutes and can be retried. Tests run jobs synchronously (`MARGIN_SYNC_JOBS=1`).

If you deploy with multiple worker processes or an autoscaler that kills idle instances mid-request, consider moving jobs to a task queue — the status fields already support that.

## Data model (Firestore, all under `users/{uid}`)

Everything is scoped to the signed-in user's subtree, so ownership is structural: a request can only ever reach `users/{its own uid}/...`. Cross-collection references (assessment ↔ objective ↔ concept, task ↔ assessment) are additionally checked to belong to the same course.

| collection | purpose | notes |
|---|---|---|
| `projects/{id}` | **assessments** (existing collection, extended) | new optional fields: `kind`, `weight`, `estimatedHours`, `estimatedHoursOrigin` (`document` / `ai_suggested` / `student`), `requirements[]`, `milestones[]`, `deliverables[]`, `prerequisites[]`, `source{documentId,page,quote,verified}`, `sourceDocumentId`, `sourceKey`, `verification`, `origin`, `documentId`, `outcome` |
| `tasks/{id}` | tasks (existing) | `dueDate` still means *the day the task is planned for*. New optional: `pinned`, `dependsOn[]`, `kind` (`practice`), `conceptIds[]`, `splitFrom`, `splitParts` |
| `documents/{id}` | uploaded course documents | `status`, `extraction` (review state), `extractionRaw` (cache), `applied{itemKey: projectId}`; subcollections `pages/{n}` (text) and `chunks/{i}` (original bytes, <1MB each) |
| `objectives/{id}` | confirmed learning objectives | `assessmentIds[]`, `source` |
| `concepts/{id}` | course concept map | id = `c-{hash(courseId,name)}`; `objectiveIds[]`, `assessmentIds[]`, `sourceRefs[]`, `origin` |
| `conceptSuggestions/{courseId}` | latest suggestion job per course | |
| `questions/{id}` | diagnostic question bank | answer keys live here and are only returned after the student answers; `status` `active` / `flagged` / `retired` |
| `diagnosticSessions/{id}` | one diagnostic | `status` `preparing` / `ready` / `in_progress` / `completed` / `failed` |
| `attempts/{sessionId}-{questionId}` | one answer | deterministic id makes answering idempotent |
| `studySessions/{clientId}` | logged study time | client-generated id makes logging idempotent |
| `planning/availability` | study window, daily limit, session length, days off, blackouts, `useCalibration` | |
| `planning/acks` | dismissed risks/recommendations | keyed by hash of the risk id, with the fingerprint it was dismissed at |
| `planning/history` | one forecast snapshot per assessment per day (last 60) | written when the forecast is computed; used by reviews |
| `planning/lastRecovery` | undo snapshot for the last applied recovery | |

**Migration:** none needed. Every new field is optional and read with a default, so existing users' data works unchanged. Existing assessments simply have no `kind`/`weight` until the student adds one or confirms a document.

**Indexes:** new queries use a single equality filter and sort in memory, so Firestore's automatic single-field indexes are enough. The existing `tasks where projectId == X order by order` query still needs its composite index (unchanged).

## The rules behind the labels

### Document extraction
- The model must quote its source. Each quote is searched for in the real page text (whitespace/case-insensitive, with a fuzzy fallback); unverifiable quotes are flagged, not trusted.
- Dates must be real `YYYY-MM-DD` dates. "TBA", "week 5" and similar stay `null` with the original wording shown. Dates written without a year are flagged "check the year".
- Weights must be 0–100. If the extracted weights don't add up to ~100%, that's flagged.
- Duplicates inside a document and matches with existing assessments (title similarity; numbers must match, so "PS1" ≠ "PS2") become updates rather than new records, with date conflicts highlighted.
- Nothing is written to `projects`/`objectives` until the student confirms it. The apply step validates every confirmed item server-side and writes everything in one batch, or nothing.

### Workload forecast (`services/workload.py`)
- free time on a day = study window − overlapping weekly commitments, capped at the daily limit; 0 on days off/blackouts; today only counts the rest of the window
- work left = Σ open tasks of max(estimate − logged, 0) (15 min if an open task already used its estimate; 30 min per task with no estimate, flagged). An assessment with no tasks uses its expected workload.
- assignments/projects can be worked on through the due date; exams, quizzes and presentations by the day before
- earliest-deadline-first allocation. **at risk**: doesn't fit; **tight**: fits but uses >80% of the free time before the deadline; **on track**: otherwise; **insufficient information**: no due date or no way to estimate work (or most tasks lack estimates)
- estimate calibration: median actual/estimate over ≥5 finished tasks with logged time, shrunk toward 1 (`1 + (m−1)·n/(n+5)`), clamped to [0.5, 2.5], applied only if the student opts in

### Recovery (`services/recovery.py`)
Done and pinned tasks never move; weekly commitments and blackouts are never touched. Future tasks keep their day if it still has room. Missed/displaced tasks are re-placed earliest-deadline-first on the earliest day with room, after their dependencies, split into ≥30-min parts if they exceed a session or don't fit in one day. Work that can't fit before its deadline is reported with options and left where it is. Tasks already moved 3+ times are proposed unselected with advice to change the task instead. Apply re-checks a per-task fingerprint, so a stale plan is rejected (409), and stores an undo snapshot.

### Mastery (`services/diagnostics.py`)
Per concept, over answers to non-flagged questions; each answer is weighted by recency (half-life 21 days), difficulty (0.8 / 1.0 / 1.2) and grading method (self-graded ×0.6; "partial" = 0.5).
- **not enough evidence**: <3 answers or <2 different questions
- **needs practice**: weighted accuracy <60%, 2 of the last 3 wrong, or a confident wrong answer in the last 2
- **demonstrated**: ≥80%, latest answer correct, ≥2 different questions answered correctly, no confident misses in the last 3
- **developing**: everything else

Confidence is recorded but never raises a status. Multiple-choice and numeric answers are graded deterministically (numeric answers through a small AST-based evaluator: numbers, `+ − × ÷ ^ %`, `sqrt ln log sin cos tan exp`, `pi e`). Short answers are self-graded against a model answer. AI answer keys are labelled as such, and flagged questions stop counting.

### Feedback loop (`services/feedback.py`)
Reviews only describe what happened. They avoid causal wording, label suggestions as coming from a single assessment, and need ≥3 comparable tasks before judging estimates. Recommendations need minimum data (≥5 tasks for calibration, ≥8 planned tasks in 14 days for the "plan too full" check, ≥2 reviewed assessments for timeline patterns), always show their evidence, and can be dismissed.

## API

All routes require `Authorization: Bearer <firebase id token>` except `GET /api/health`.

| area | routes |
|---|---|
| documents | `POST/GET /api/courses/<cid>/documents` · `GET/DELETE /api/documents/<id>` · `GET /api/documents/<id>/pages/<n>` · `GET /api/documents/<id>/file` · `POST /api/documents/<id>/process` · `PATCH /api/documents/<id>/review` · `POST /api/documents/<id>/apply` |
| objectives | `GET/POST /api/courses/<cid>/objectives` · `PATCH/DELETE /api/objectives/<id>` |
| assessments | existing `/api/projects…` plus `GET /api/projects/<id>`; `kind`, `weight`, `estimatedHours` accepted on create/update |
| planning | `GET /api/planning/forecast` · `GET/PUT /api/planning/availability` · `POST /api/planning/acks` · `POST /api/planning/recovery/{preview,apply,undo}` · `GET /api/calendar` (now also returns `deadlines`) |
| diagnostics | `GET/POST /api/courses/<cid>/concepts` · `POST /api/courses/<cid>/concepts/suggest` · `GET /api/courses/<cid>/concepts/suggestions` · `PATCH/DELETE /api/concepts/<id>` · `POST /api/diagnostics/sessions` · `GET /api/diagnostics/sessions/<id>` · `POST /api/diagnostics/sessions/<id>/{answer,self-grade,complete}` · `POST /api/questions/<id>/flag` · `GET /api/courses/<cid>/diagnostics` · `GET /api/diagnostics/mistakes/<qid>` · `POST /api/diagnostics/practice/{preview,apply}` |
| feedback | `POST/GET /api/study-sessions` · `PATCH /api/study-sessions/<id>` · `PUT/DELETE /api/projects/<id>/outcome` · `GET /api/projects/<id>/review` · `GET /api/insights` · `GET /api/overview/pending` |

Date-sensitive endpoints accept `today=YYYY-MM-DD` (the student's local date, accepted within ±1 day of the server's UTC date) because the server doesn't know the student's timezone.

## Security & privacy

- **Authorization:** every route verifies the Firebase ID token and only reads/writes under `users/{uid}`; ids from the request are always resolved inside that subtree, and cross-references are checked to be in the same course.
- **Firestore rules:** the browser never talks to Firestore directly (the backend uses the Admin SDK), so client access should be denied entirely:
  ```
  rules_version = '2';
  service cloud.firestore {
    match /databases/{database}/documents {
      match /{document=**} { allow read, write: if false; }
    }
  }
  ```
  Check that the project's rules look like this — the repository doesn't contain them, so I couldn't verify them.
- **Uploads:** 10MB cap (`MAX_CONTENT_LENGTH` 12MB including multipart overhead); type decided by magic bytes (`%PDF-`) or `.txt`/`.md` + valid UTF-8, never by the client's MIME type; encrypted PDFs are rejected; files are served back only to their owner with `Cache-Control: private, no-store` and `nosniff`.
- **AI provider:** the API key is server-side only. Prompts contain document text and are sent to Google's Gemini API; nothing is logged except error kinds. Calls time out (90s), retry once, and are classified (rate limited / timeout / malformed / unavailable). Per-user hourly limits on AI-triggering endpoints default to 20 extractions, 20 task breakdowns, 10 concept suggestions and 30 diagnostic sessions (`MARGIN_AI_*_PER_HOUR`). These limits are kept in memory per process, so with several instances each one enforces its own.
- **Werkzeug debugger:** only enabled with `FLASK_DEBUG=1` or `FLASK_ENV=development`.
- **Frontend:** document and AI text is rendered with `textContent` (via `el()`), never `innerHTML`.

### Deletion and retention
- Deleting a **document** removes its file, page text and extraction. Assessments and objectives the student already confirmed from it are kept (they're the student's data now); their source link then reports the document as deleted.
- Deleting an **assessment** removes its tasks. Study sessions logged against it are kept as study history until the course is deleted.
- Deleting a **concept** retires questions that only belonged to it; past answers are kept so history isn't rewritten.
- Deleting a **course** (or the term containing it) removes everything course-scoped: assessments, tasks, documents (files and text), objectives, concepts, questions, diagnostic sessions, answers, study sessions, suggestions and grades.
- Flagged questions are kept (marked) so their answers can be excluded; they're never shown again.

## Testing

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cd backend && python -m pytest
```

The suite (148 tests) needs no Firebase credentials and makes no AI calls: `tests/conftest.py` injects the in-memory Firestore, a fake token verifier (`Bearer uid:<name>`) and a queue-based fake AI provider. `tests/test_e2e.py` walks the whole student loop through the HTTP API.

The frontend has no automated test runner in the repo. During development it was exercised end-to-end in jsdom against the real Flask app (fake Firestore, stubbed Firebase Auth, canned AI), but it hasn't been checked in a real browser — see the manual checklist in the README.
