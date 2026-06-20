# margin 🌿

give yourself margin.

margin is a study planner for university students that organizes coursework by term and class, breaks assignments into tasks, schedules them around your real availability, and tracks your grades — all in one quiet, focused tool.

## why margin?

most planners assume infinite time. margin starts with the time you actually have — your real weekly schedule — and fits your work into it. no streaks, no gamification, no nagging. just an honest tool for managing your semester.

## features

### organization

- terms → courses → assignments → tasks hierarchy
- color-coded course cards with a per-course color swatch
- full create, edit, and delete for terms, courses, assignments, and tasks
- term switching — set any term as current from the header or home page

### assignments

- upload an assignment PDF — margin shows it inline and extracts text for AI task breakdown
- add tasks manually or let Claude generate them from your PDF
- edit task title, estimated time, and due date at any time
- check off tasks as you complete them and log time spent per task
- mark entire assignments as complete — course view shows a done count at a glance
- auto-schedule tasks across your free days based on your weekly schedule and due date

### calendar

- weekly calendar view of all tasks across every course, color-coded by course
- drag a task pill to a different day to reschedule it instantly
- prev / next / today week navigation
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
- see your current weighted grade and letter grade in real time
- target grade calculator: enter a target and see what average you need on remaining work

### account & preferences

- google sign-in
- dark mode
- configurable week start day and study session length
- profile page: edit display name, send a password reset email

## stack

- **frontend:** vanilla JS, no framework
- **backend:** Flask (Python)
- **database:** Firestore (Firebase)
- **auth:** Firebase Google Auth
- **pdf parsing:** pypdf (text extraction)
- **ai:** Anthropic Claude API — PDF → task breakdown with time estimates

## local dev

**1. clone and set up**

```bash
git clone https://github.com/your-username/Margin-StudyApp.git
cd Margin-StudyApp
```

**2. environment variables**

create a `.env` file in the project root and fill in:

```text
FIREBASE_CREDENTIALS=/absolute/path/to/firebase-key.json
ANTHROPIC_API_KEY=sk-ant-...        # optional — only needed for AI task breakdown
FLASK_SECRET_KEY=your-secret
ALLOWED_ORIGINS=http://localhost:3000
PORT=5000
```

get `firebase-key.json` from: Firebase Console → Project settings → Service accounts → Generate new private key.

**3. run**

```bash
./dev.sh
```

that's it. `dev.sh` sets up the venv if needed, starts the backend and frontend, and opens `http://localhost:3000` automatically. press Ctrl+C to stop both servers.

### manual setup (without dev.sh)

```bash
python -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt

# terminal 1 — backend
cd backend && python app.py

# terminal 2 — frontend
cd frontend && python -m http.server 3000
```

## what's next

- **end-of-day check-in** — a daily prompt to mark what got done and reschedule what didn't
- **browser notifications** — reminders when a task is due today
- **calendar sync** — export your task schedule to Google Calendar or iCal
- **email / password auth** — alternative to Google sign-in
- **mobile app** — native iOS/Android or PWA

## built by

zoe droulias 💛
