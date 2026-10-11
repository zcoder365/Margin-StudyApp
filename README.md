# margin 🌿

<!-- potential URL: marginstudy.app -->
<!-- AI Model: Gemini 3.5 Flash (https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash) -->

give yourself margin.

margin is a study planner for university students that organizes coursework by term and class, breaks assignments into tasks using AI, schedules them around your real availability, and tracks your grades — all in one quiet, focused tool.

## why margin?

most planners assume infinite time. margin starts with the time you actually have — your real weekly schedule — and fits your work into it. no streaks, no gamification, no nagging. just an honest tool for managing your semester.

## features

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

### study sessions

- built-in study timer per task — hit ▶ on any task to start a session
- customizable work interval (default 25 min)
- auto-logs elapsed time to the task when the session completes

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
- **ai:** Google Gemini API (Gemini 3.5 Flash) — PDF → task breakdown with time estimates

## local dev

### 1. clone

```bash
git clone https://github.com/your-username/Margin-StudyApp.git
cd Margin-StudyApp
```

<!-- ### 2. environment variables

create a `.env` file in the project root:

```text
FIREBASE_CREDENTIALS=/absolute/path/to/firebase-key.json
FLASK_SECRET_KEY=your-secret
ALLOWED_ORIGINS=http://localhost:3000
PORT=5000
```

get `firebase-key.json` from: Firebase Console → Project settings → Service accounts → Generate new private key. -->

### 2. run

```bash
./dev.sh
```

`dev.sh` sets up the venv if needed, starts the backend and frontend, and opens `http://localhost:3000`. press Ctrl+C to stop.

## future development

- [ ] "Am I cooked?" radar that gives students an honest study plan before their final exam
- [ ] A study reflection after grades are received (which methods were used, how the student felt about the grade)
- [ ] Reminders for tasks and assignments

## built by

zoe droulias 💛
