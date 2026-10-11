# margin 🌿

<!-- potential URL: marginstudy.app -->
<!-- AI Model: Gemini 3.5 Flash (https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash) -->

give yourself margin.

margin is a study planner for university students. it helps you answer three questions: **what do i need to do? what do i actually understand? do i have enough time to prepare properly?**

it reads your syllabus and assignment briefs (you confirm everything it finds), forecasts whether your remaining work fits into the time you actually have, rebuilds your plan when you fall behind, diagnoses which concepts you can't yet apply before an exam does, and compares what you planned with what happened so the next plan is more realistic — all in one quiet, focused tool.

## why margin?

most planners assume infinite time. margin starts with the time you actually have — your real weekly schedule — and fits your work into it. no streaks, no gamification, no nagging. just an honest tool for managing your semester.

## features

### home: semester overview

- the home page opens on your current term: next steps (documents to review, assessments that don't fit, unfinished diagnostics, near-term items margin can't forecast yet), what's coming up in the next three weeks with its workload label, free vs. planned time for the next 7 days, and study logged this week
- every item links straight to where it's handled; your terms are listed below

### organization

- terms → courses → assignments → tasks hierarchy
- color-coded course cards
- full create, edit, view, and delete for terms, courses, assignments, and tasks
- term switching from the navigation bar or home page

### assignments & AI

- upload an assignment PDF — margin extracts the text and sends it to Gemini to generate a task list with time estimates automatically
- add tasks manually if you prefer
- edit task title, estimated time, and due date at any time
- check off tasks as you complete them and log time spent per task
- auto-schedule tasks across your free days based on your weekly schedule and due date

### course documents (syllabus intelligence)

- upload a syllabus, assignment brief, rubric, exam review sheet, project brief or lecture notes (PDF, .txt or .md, up to 10MB) to a course
- margin extracts assessments, due dates, grade weights, workload, requirements, milestones and learning objectives in the background
- every extracted item is shown in a review screen with its page reference and the highlighted source passage — edit, confirm, reject or leave it for later
- missing or ambiguous information (no date, "TBA", year not written, weights that don't add to 100%, quotes that can't be found in the file) is flagged, never filled in
- AI-suggested values (like a workload guess) are labelled separately from facts stated in the document
- matching assessments you already have are offered as updates (with date conflicts highlighted), so re-extracting a document never creates duplicates
- only confirmed items reach your courses and planner; the original file is kept and viewable
- assignment PDFs uploaded on an assignment page are now stored too, so they're still there after a reload

### planner: workload forecast & schedule recovery

- the planner shows how much free study time you have in the next two weeks (your study window minus recurring commitments, within your daily limit), how much work is due in that time, and what share of your free time it needs
- every assessment gets an explicitly defined label — **on track**, **tight**, **at risk** or **not enough info** — with the numbers behind it (work left, free time before the deadline, time needed for earlier deadlines)
- risks are flagged with a reason: work that doesn't fit, tasks that should start today, overbooked days, missed tasks, tasks moved 3+ times, crunch weeks, an empty weekly schedule, or estimates your history says are optimistic. dismiss any of them; they come back only if the numbers change
- **"I'm behind — rebuild my plan"** proposes a realistic new schedule: missed and overbooked tasks move to the earliest days with room, long tasks are split into sessions, dependencies ("do after") and fixed tasks are respected, completed work never moves, and anything that can't fit before its deadline is reported with options instead of being pushed past it. you pick which changes to apply, and can undo them
- study availability settings: study window, max hours per day, longest session, days off, and unavailable dates (trips, busy weeks)
- optional estimate adjustment: after at least 5 finished tasks with logged time, margin can scale estimates by how long your tasks really take (shrunk toward 1× so a few tasks can't swing it)
- assessment deadlines and milestones appear on the calendar; each day shows planned vs. free time

### practice & knowledge gaps (diagnostics)

- each course gets a concept map: margin proposes concepts from your confirmed learning objectives and course material, and you keep, rename or drop them (or add your own). concepts link to assessments through their objectives
- short diagnostics (4–8 questions) focus on your weakest and least-practised concepts, or on one upcoming assessment
- question types: multiple choice, numeric (checked exactly — `3/4`, `0.75` and `sqrt(9)/4` all count) and short answer (you compare with a model answer and grade yourself — the AI never grades you)
- optional confidence rating before seeing the answer; confident mistakes are highlighted as possible misconceptions
- every question shows whether it's grounded in your material (with the quoted passage and page) or an AI-generated example, and that its answer key is AI-written. flag wrong or unclear questions — they're withdrawn and stop counting
- generated questions are validated before you see them (structure, answer key, concept links, no "this will be on the exam" claims); if generation fails margin reuses your question bank or falls back to self-check prompts
- concept status — **needs practice**, **not enough evidence**, **developing**, **demonstrated** — comes from published rules over your recent answers (recency- and difficulty-weighted). one right answer is never "mastery"; confidence never counts as evidence
- the knowledge-gap board shows the evidence behind every label, recurring error patterns, recent mistakes to revisit, and a practice plan prioritised by weakness and how soon the assessment is
- "add practice to my schedule" proposes sessions in free time left after your existing plan (max 2 a day, before the assessment) — you adjust dates and minutes before anything is added

### calendar

- weekly calendar view of all tasks across every course, color-coded by course
- overdue tasks highlighted in red
- drag a task pill to a different day to reschedule it instantly
- click a task pill to edit it inline
- click an empty day to add a task for that date
- mark tasks complete directly from the calendar
- prev / next / today week navigation + keyboard arrow shortcuts
- configurable week start day (Monday or Sunday)

### progress

- per-course progress dashboard: tasks done vs. remaining, time spent vs. estimated
- progress bars colored to match each course

### study sessions & the feedback loop

- built-in study timer per task — hit ▶ on any task to start a session
- customizable work interval (default 25 min)
- auto-logs elapsed time to the task when the session completes; **stop & log** keeps a shorter session, and closing the timer offers to log what you did
- after a session, an optional one-tap follow-up: finished the task? how did it go? a short note
- "log study time" on any assessment for prep that isn't tied to a task (e.g. exam review)
- add an assessment's result (optional) — with how it went compared with what you expected, and optionally copy it into the grade calculator
- each finished assessment gets a review: planned vs. logged time, how your estimates compared, when recorded work started, what the forecast said along the way, which linked concepts had evidence by the due date, and one or two things to try next time — always labelled as observations from a single assessment, never as causes
- the progress page adds suggestions backed by your own data (only once there's enough of it): adjust estimates, lower an over-full daily limit, run a diagnostic before an upcoming assessment, practise concepts that still need work. each shows its evidence and can be dismissed
- planned vs. logged study by week, how long tasks really take (overall and per course), and concept status now vs. two weeks ago

### schedule

- enter your recurring weekly commitments (classes, work, etc.)
- margin uses your schedule to find your free time and distribute task due dates intelligently

### grades

- grade calculator per course
- set up weighted grading categories (e.g. Homework 40%, Midterm 30%, Final 30%)
- log individual grades under each category
- see your current weighted percentage grade in real time
- target grade calculator: enter a target and see what average you need on remaining work

### account & preferences

- Google sign-in with profile photo
- dark mode
- configurable week start day and study session length
- browser notifications for tasks due today and overdue tasks
- profile page: edit display name

### other

- keyboard shortcuts (`T` add task, `←` `→` navigate calendar, `Esc` close, `?` help)
- mobile-responsive layout with hamburger nav
- onboarding flow for first-time users

## stack

- **frontend:** vanilla JS, HTML, CSS — no framework
- **backend:** Flask (Python)
- **database:** Firestore (Firebase)
- **auth:** Firebase Google Auth
- **pdf parsing:** pypdf
- **ai:** Google Gemini API (`google-genai`, model set by `GEMINI_MODEL`, default `gemini-2.5-flash`) — used only to extract data from documents, suggest concepts, write diagnostic questions and break assignments into tasks. every AI output is validated, labelled, and confirmed by you before it counts
- **tests:** pytest with an in-memory Firestore fake (no credentials or paid AI calls needed)

developer guide (architecture, data model, the rules behind every label, API, security and data-retention policy): [docs/academic-planning.md](docs/academic-planning.md)

## local dev

### 1. clone

```bash
git clone https://github.com/your-username/Margin-StudyApp.git
cd Margin-StudyApp
```

### 2. environment variables

create a `.env` file in the project root:

```text
FIREBASE_CREDENTIALS=/absolute/path/to/firebase-key.json   # or the base64-encoded JSON
GEMINI_API_KEY=your-gemini-key                             # AI features show a clear error without it
GEMINI_MODEL=gemini-2.5-flash                              # optional
FLASK_SECRET_KEY=your-secret
ALLOWED_ORIGINS=http://localhost:3000
PORT=5000
FLASK_DEBUG=1                                              # local only — enables the werkzeug debugger
# optional per-user hourly AI limits (defaults shown)
# MARGIN_AI_EXTRACT_PER_HOUR=20
# MARGIN_AI_TASKS_PER_HOUR=20
# MARGIN_AI_CONCEPTS_PER_HOUR=10
# MARGIN_AI_QUESTIONS_PER_HOUR=30
```

get `firebase-key.json` from: Firebase Console → Project settings → Service accounts → Generate new private key. never commit it (it's in `.gitignore`).

the frontend calls `http://localhost:5000/api` by default. when deploying, set `window.MARGIN_API_BASE = "https://your-backend/api"` in a script tag before `app.js`.

### 3. run

```bash
./dev.sh
```

`dev.sh` sets up the venv if needed, starts the backend and frontend, and opens `http://localhost:3000`. press Ctrl+C to stop.

if `venv/bin/python` fails with "bad CPU type" (a venv copied from a different machine), recreate it: `python3 -m venv --clear venv && venv/bin/pip install -r requirements.txt`.

### 4. tests

```bash
venv/bin/pip install -r requirements-dev.txt
cd backend && ../venv/bin/python -m pytest
```

no Firebase credentials or AI key are needed — tests use an in-memory Firestore and a fake AI provider.

### deploying

- no database migration is needed: every new field is optional
- set Firestore security rules to deny all client access (the backend uses the Admin SDK) — see the developer guide
- long-running AI work runs in background threads; run the backend as a long-lived process (not a short-lived serverless function)

## future development

- [x] "Am I cooked?" radar — the workload forecast, risks and "I'm behind" recovery
- [x] A study reflection after grades are received — results, reflections and assessment reviews
- [ ] Reminders for tasks and assignments (browser notifications exist; email/push don't yet)

## built by

zoe droulias 💛
