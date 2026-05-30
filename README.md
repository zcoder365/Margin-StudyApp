# margin 🌿

give yourself margin.

margin is a study planner for university students that organizes coursework by term and class, breaks assignments into tasks, schedules them around your real availability, and tracks your grades — all in one quiet, focused tool.

## why margin?

most planners assume infinite time. margin starts with the time you actually have — your real weekly schedule — and fits your work into it. no streaks, no gamification, no nagging. just an honest tool for managing your semester.

## features

**organization**
- terms → courses → assignments → tasks hierarchy
- color-coded course cards
- full create, edit, and delete for terms, courses, and assignments

**assignments**
- upload an assignment PDF — margin extracts the text so you can read it alongside your tasks
- add tasks manually or have them generated automatically (AI breakdown coming)
- check off tasks as you complete them
- log time spent per task
- auto-schedule tasks across your free days based on your weekly schedule and the assignment due date

**schedule**
- enter your recurring weekly commitments (classes, work, etc.)
- margin uses your schedule to find your free time and distribute task due dates intelligently

**grades**
- grade calculator per course
- set up weighted grading categories (e.g. Homework 40%, Midterm 30%, Final 30%)
- log individual grades under each category
- see your current weighted grade and letter grade in real time
- target grade calculator: enter a target and see what average you need on remaining work

**account**
- google sign-in
- profile page: edit display name, send a password reset email

## stack

- **frontend:** vanilla JS, no framework
- **backend:** Flask (Python), deployable on Railway
- **database:** Firestore (Firebase)
- **auth:** Firebase Google Auth
- **pdf parsing:** pypdf (text extraction)
- **ai:** Anthropic Claude API — integrated, ready to enable for PDF → task breakdown

## local dev

**1. clone and set up**
```bash
git clone https://github.com/your-username/Margin-StudyApp.git
cd Margin-StudyApp
python -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt
```

**2. environment variables**

copy `.env.example` to `.env` and fill in:

```
FIREBASE_CREDENTIALS=/absolute/path/to/firebase-key.json
ANTHROPIC_API_KEY=sk-ant-...        # optional — only needed for AI task breakdown
FLASK_SECRET_KEY=your-secret
ALLOWED_ORIGINS=http://localhost:3000
PORT=5000
```

get `firebase-key.json` from: Firebase Console → Project settings → Service accounts → Generate new private key.

**3. run**

```bash
# terminal 1 — backend
cd backend && python app.py

# terminal 2 — frontend
cd frontend && python -m http.server 3000
```

open `http://localhost:3000`.

## what's next

- **AI task breakdown** — swap pypdf extraction for a Claude or Gemini call to automatically generate tasks from an uploaded assignment PDF, with per-task time estimates
- **end-of-day check-in** — a daily prompt to mark what got done and reschedule what didn't
- **notifications** — reminders when a task is due soon
- **calendar sync** — export your task schedule to Google Calendar or iCal
- **email / password auth** — alternative to Google sign-in
- **mobile app** — native iOS/Android or PWA

## built by

zoe droulias 💛
