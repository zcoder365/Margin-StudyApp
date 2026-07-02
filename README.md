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
- full create, edit, and delete for terms, courses, assignments, and tasks
- term switching from the header or home page

### assignments & AI

- upload an assignment PDF — margin extracts the text and sends it to Claude to generate a task list with time estimates automatically
- add tasks manually if you prefer
- edit task title, estimated time, and due date at any time
- check off tasks as you complete them and log time spent per task
- auto-schedule tasks across your free days based on your weekly schedule and due date

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

- Google sign-in with profile photo (adjusting profile photo glitch)
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

## built by

zoe droulias 💛
