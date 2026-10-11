"""
the feedback loop — pure functions: plan → study → test → compare → adapt.

principles baked in here:
  - observations describe what happened; they never claim one thing caused another
  - a single assessment is one observation — suggestions from it are labelled as such
  - recommendations need a minimum amount of data and always show it
  - wording is neutral and specific; a disappointing result is information, not a verdict
"""

from datetime import date, datetime, timedelta, timezone
from statistics import median

from services import diagnostics as dx
from services.workload import estimate_calibration

MIN_TASKS_FOR_ESTIMATES = 3      # per-assessment estimate comparison
MIN_TASKS_FOR_CALIBRATION = 5    # global / per-course recommendations
MIN_PLANNED_FOR_MISS_RATE = 8
MISS_RATE_THRESHOLD = 0.4
SINGLE_OBSERVATION = "This is based on one assessment, so treat it as something to try rather than a conclusion."


def _d(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _h(minutes):
    h = (minutes or 0) / 60
    return f"{h:.1f}h".replace(".0h", "h")


# -----------------------------------------------------------------------------
# per-assessment review
# -----------------------------------------------------------------------------
def assessment_review(assessment, tasks, sessions, forecast_history, concept_attempts, concepts, today):
    """
    assessment: dict with id, title, kind, dueDate (date|None), status, estimatedHours, outcome, completedAt
    tasks: this assessment's tasks; sessions: study sessions for it (dicts with actualMinutes, plannedMinutes, endedAt)
    forecast_history: [{d, s, rem, avail}] daily snapshots; concept_attempts: {conceptId: [attempts]}
    concepts: linked concepts [{id, name}]
    """
    due = assessment.get("dueDate")
    obs, suggestions = [], []

    # --- plan vs. actual time ---------------------------------------------------
    estimated = sum(t.get("estimatedMinutes") or 0 for t in tasks)
    if not tasks and assessment.get("estimatedHours"):
        estimated = assessment["estimatedHours"] * 60
    task_minutes = sum(t.get("timeSpent") or 0 for t in tasks)
    untasked = sum(s.get("actualMinutes") or 0 for s in sessions if not s.get("taskId"))
    actual = task_minutes + untasked
    done = [t for t in tasks if t.get("status") == "done"]
    comparable = [t for t in done if (t.get("estimatedMinutes") or 0) > 0 and (t.get("timeSpent") or 0) > 0]
    ratio = None
    if len(comparable) >= MIN_TASKS_FOR_ESTIMATES:
        ratio = round(median(t["timeSpent"] / t["estimatedMinutes"] for t in comparable), 2)
    over = sorted(comparable, key=lambda t: t["timeSpent"] / t["estimatedMinutes"], reverse=True)[:3]
    time_block = {
        "estimatedMinutes": round(estimated) if estimated else None,
        "actualMinutes": round(actual) if actual else None,
        "tasksDone": len(done), "tasksTotal": len(tasks),
        "medianRatio": ratio, "comparedTasks": len(comparable),
        "biggestOverruns": [{"title": t.get("title"), "estimated": t["estimatedMinutes"], "actual": t["timeSpent"]}
                            for t in over if t["timeSpent"] > t["estimatedMinutes"] * 1.25],
        "loggedWithoutTasks": round(untasked) if untasked else 0,
    }
    if estimated and actual:
        obs.append(f"You logged {_h(actual)} against a plan of {_h(estimated)}.")
    elif estimated:
        obs.append(f"The plan was {_h(estimated)}, but no study time was logged, so planned and actual time can't be compared.")
    if ratio is not None:
        if ratio >= 1.25:
            obs.append(f"Finished tasks took a median {ratio}× their estimates ({len(comparable)} tasks).")
            suggestions.append({"type": "estimates", "text": f"For similar work, plan about {round((ratio - 1) * 100)}% more time than your first estimate.",
                                "evidence": f"{len(comparable)} finished tasks with logged time"})
        elif ratio <= 0.8:
            obs.append(f"Finished tasks took a median {ratio}× their estimates ({len(comparable)} tasks) — quicker than planned.")
        else:
            obs.append(f"Your time estimates were close: a median {ratio}× across {len(comparable)} tasks.")
    elif comparable:
        obs.append(f"Only {len(comparable)} finished task(s) have both an estimate and logged time — not enough to judge your estimates yet.")

    # --- when preparation happened ----------------------------------------------
    work_days = sorted(d for d in ([_d(s.get("endedAt") or s.get("createdAt")) for s in sessions] +
                                   [_d(t.get("completedAt")) for t in done]) if d)
    prep = {"firstWorkDay": work_days[0].isoformat() if work_days else None,
            "daysBeforeDue": (due - work_days[0]).days if work_days and due else None,
            "activeDays": len(set(work_days))}
    days_word = lambda n: f"{n} day{'s' if n != 1 else ''}"  # noqa: E731
    if prep["daysBeforeDue"] is not None:
        n = prep["daysBeforeDue"]
        if n > 0:
            obs.append(f"Recorded work started {days_word(n)} before the due date, across {days_word(prep['activeDays'])}.")
        elif n == 0:
            obs.append("Recorded work started on the due date.")
        else:
            obs.append("All recorded work was logged after the due date, so it says little about how preparation went.")

    # --- what the forecast said ---------------------------------------------------
    hist = [h for h in forecast_history if not due or h.get("d", "") <= due.isoformat()]
    flagged = [h for h in hist if h.get("s") == "at_risk"]
    forecast = {"snapshots": len(hist), "firstStatus": hist[0]["s"] if hist else None,
                "lastStatus": hist[-1]["s"] if hist else None,
                "daysAtRisk": len(flagged), "firstAtRisk": flagged[0]["d"] if flagged else None,
                "completedOnTime": None}
    completed_on = _d(assessment.get("completedAt"))
    if due and assessment.get("status") == "completed" and completed_on:
        forecast["completedOnTime"] = completed_on <= due
    if hist:
        if flagged:
            obs.append(f"The forecast flagged this as at risk on {len(flagged)} day{'s' if len(flagged) != 1 else ''}, starting {flagged[0]['d']}.")
            if forecast["lastStatus"] in ("on_track", "tight", "done"):
                obs.append("By the end it was back to fitting your available time.")
        else:
            obs.append(f"The forecast never flagged this as at risk (last status: {forecast['lastStatus'].replace('_', ' ')}).")
        if flagged and prep["firstWorkDay"] and prep["firstWorkDay"] >= flagged[0]["d"]:
            suggestions.append({"type": "start_earlier", "text": "When a forecast turns “at risk”, try rebuilding the plan that day rather than waiting.",
                                "evidence": "recorded work began after the first at-risk warning"})
    else:
        obs.append("No forecast history was recorded for this assessment (forecasts are saved when you open the planner).")

    # --- learning evidence --------------------------------------------------------
    learning = []
    cutoff = datetime.combine(due, datetime.min.time(), tzinfo=timezone.utc) if due else None
    for c in concepts:
        atts = concept_attempts.get(c["id"], [])
        before = [a for a in atts if cutoff is None or (a.get("createdAt") and _as_dt(a["createdAt"]) < cutoff)]
        after = [a for a in atts if cutoff is not None and a.get("createdAt") and _as_dt(a["createdAt"]) >= cutoff]
        m_before = dx.mastery(before, now=cutoff) if before else None
        first_half = before[: len(before) // 2] if len(before) >= 4 else []
        learning.append({
            "conceptId": c["id"], "name": c["name"],
            "statusAtDue": m_before["status"] if m_before else "insufficient_evidence",
            "attemptsBefore": len(before), "attemptsAfter": len(after),
            "earlyStatus": dx.mastery(first_half, now=cutoff)["status"] if first_half else None,
        })
    if learning:
        counts = {}
        for row in learning:
            counts[row["statusAtDue"]] = counts.get(row["statusAtDue"], 0) + 1
        parts = [f"{n} {k.replace('_', ' ')}" for k, n in counts.items()]
        obs.append(f"By the due date, linked concepts were: {', '.join(parts)}.")
        weak = [r["name"] for r in learning if r["statusAtDue"] == "needs_practice"]
        unknown = [r["name"] for r in learning if r["statusAtDue"] == "insufficient_evidence"]
        if unknown and len(unknown) == len(learning):
            suggestions.append({"type": "diagnose_earlier", "text": "Run a short diagnostic 1–2 weeks before the next assessment, so there's evidence about what to practise.",
                                "evidence": "no linked concept had enough diagnostic evidence before the due date"})
        elif weak:
            suggestions.append({"type": "practice_weak", "text": f"Schedule practice on {', '.join(weak[:3])} earlier next time.",
                                "evidence": "these still needed practice by the due date"})

    # --- outcome --------------------------------------------------------------------
    outcome = assessment.get("outcome")
    if outcome and outcome.get("percent") is not None:
        obs.append(f"You recorded a result of {outcome['percent']:g}%.")
    caveat = SINGLE_OBSERVATION
    if outcome and learning and any(r["statusAtDue"] != "insufficient_evidence" for r in learning):
        caveat += " A result and your diagnostic evidence can disagree for many reasons (question style, time pressure, partial marks)."

    for s in suggestions:
        s["caveat"] = SINGLE_OBSERVATION
    return {"time": time_block, "prep": prep, "forecast": forecast, "learning": learning,
            "outcome": outcome, "observations": obs, "suggestions": suggestions, "caveat": caveat}


def _as_dt(v):
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(v))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


# -----------------------------------------------------------------------------
# adaptive recommendations
# -----------------------------------------------------------------------------
def _rec(rid, rtype, title, detail, evidence, action=None, fingerprint=""):
    return {"id": rid, "type": rtype, "title": title, "detail": detail, "evidence": evidence,
            "action": action, "fingerprint": str(fingerprint)}


def recommendations(today, tasks, courses, availability, upcoming_concepts, recent_concept_activity,
                    weak_concepts_by_assessment, reviews=None):
    """
    tasks: all of the student's tasks (dicts with courseId, status, estimatedMinutes, timeSpent, plannedDate (date), completedAt)
    courses: {courseId: name}
    upcoming_concepts: [{conceptId, name, assessmentId, assessmentTitle, dueDate (date)}] for assessments due ≤ 14 days
    recent_concept_activity: {conceptId: last attempt date or None}
    weak_concepts_by_assessment: {assessmentId: (title, dueDate, [concept names])}
    reviews: past assessment reviews (for prep-timeline patterns)
    """
    recs = []

    # 1. estimate calibration (global)
    cal = estimate_calibration(tasks, MIN_TASKS_FOR_CALIBRATION)
    if cal["sufficient"]:
        if not availability.get("useCalibration") and abs(cal["factor"] - 1) >= 0.15:
            more = cal["factor"] > 1
            recs.append(_rec(
                "rec:calibration", "estimates",
                f"Adjust task estimates by ×{cal['factor']}",
                ("Your tasks usually take longer than you estimate. " if more else "Your tasks usually take less time than you estimate. ")
                + "margin can scale estimates in your forecast and plan (your original estimates stay as they are).",
                f"median {cal['median']}× across {cal['n']} finished tasks with logged time; the adjustment is shrunk toward 1× for caution",
                {"type": "enable_calibration"}, cal["factor"]))
        elif availability.get("useCalibration") and abs(cal["factor"] - 1) < 0.08:
            recs.append(_rec(
                "rec:calibration_off", "estimates", "Your estimates have become accurate",
                "You could turn off estimate adjustment — recent tasks take about as long as you estimate.",
                f"median {cal['median']}× across {cal['n']} tasks", {"type": "disable_calibration"}, cal["factor"]))

    # 2. per-course estimate differences
    for cid, name in courses.items():
        ct = [t for t in tasks if t.get("courseId") == cid]
        c_cal = estimate_calibration(ct, MIN_TASKS_FOR_CALIBRATION)
        if c_cal["sufficient"] and cal["sufficient"] and abs(c_cal["median"] - cal["median"]) >= 0.3:
            direction = "longer" if c_cal["median"] > cal["median"] else "less time"
            recs.append(_rec(
                f"rec:course_estimates:{cid}", "estimates",
                f"{name} tasks take {direction} than your others",
                f"When estimating {name} work, compare with past {name} tasks rather than your usual sense of time.",
                f"{name}: median {c_cal['median']}× over {c_cal['n']} tasks; everything else: {cal['median']}×",
                None, c_cal["median"]))

    # 3. missed planned sessions → plan may be too full
    window = [t for t in tasks if t.get("plannedDate") and today - timedelta(days=14) <= t["plannedDate"] < today]
    if len(window) >= MIN_PLANNED_FOR_MISS_RATE:
        missed = [t for t in window if t.get("status") != "done" or (_d(t.get("completedAt")) or today) > t["plannedDate"] + timedelta(days=1)]
        rate = len(missed) / len(window)
        cur = availability.get("maxStudyMinutesPerDay", 240)
        if rate >= MISS_RATE_THRESHOLD and cur > 60:
            new = max(60, int((cur * (1 - rate / 2)) // 30 * 30))
            recs.append(_rec(
                "rec:daily_limit", "allocation",
                "Your plan may be fuller than your days allow",
                f"Planning with a lower daily study limit ({_h(new)} instead of {_h(cur)}) makes the forecast more realistic. "
                "This is about the plan, not about effort — busy weeks happen.",
                f"{len(missed)} of {len(window)} tasks planned in the last 14 days weren't done on (or the day after) their planned day",
                {"type": "set_daily_limit", "minutes": new}, f"{len(missed)}/{len(window)}"))

    # 4. diagnostic frequency: concepts for soon-due assessments without recent evidence
    stale = {}
    for c in upcoming_concepts:
        last = recent_concept_activity.get(c["conceptId"])
        if last is None or (today - last).days > 10:
            stale.setdefault(c["assessmentId"], (c["assessmentTitle"], c["dueDate"], []))[2].append(c["name"])
    for aid, (title, due, names) in stale.items():
        recs.append(_rec(
            f"rec:diagnose:{aid}", "diagnostics",
            f"Check what you can apply before {title}",
            f"A short diagnostic on {', '.join(names[:3])}{' and others' if len(names) > 3 else ''} would show what to practise in the {(due - today).days} days left.",
            f"{len(names)} linked concept(s) without a diagnostic answer in the last 10 days",
            {"type": "start_diagnostic", "assessmentId": aid}, f"{due}|{len(names)}"))

    # 5. concepts that need practice before an upcoming assessment
    for aid, (title, due, names) in weak_concepts_by_assessment.items():
        if names:
            recs.append(_rec(
                f"rec:practice:{aid}", "practice",
                f"{len(names)} concept{'s need' if len(names) != 1 else ' needs'} practice before {title}",
                f"{', '.join(names[:4])}. Scheduling short practice sessions now spreads them out.",
                "from your recent diagnostic answers", {"type": "schedule_practice", "assessmentId": aid},
                f"{due}|{'|'.join(sorted(names))}"))

    # 6. preparation timeline across past assessments (needs ≥ 2 reviews)
    reviews = [r for r in (reviews or []) if r.get("forecast", {}).get("snapshots")]
    if len(reviews) >= 2:
        flagged = [r for r in reviews if r["forecast"]["daysAtRisk"]]
        if len(flagged) >= 2:
            recs.append(_rec(
                "rec:timeline", "timeline",
                "Recent assessments got tight near the deadline",
                "Starting preparation a few days earlier, or rebuilding your plan as soon as something turns “at risk”, gives more room. "
                "This is a pattern across assessments, not proof of what caused any result.",
                f"{len(flagged)} of your last {len(reviews)} reviewed assessments were flagged at risk before their due date",
                None, f"{len(flagged)}/{len(reviews)}"))
    return recs


# -----------------------------------------------------------------------------
# progress over time
# -----------------------------------------------------------------------------
def weekly_study(sessions, tasks, today, weeks=6, week_start=0):
    """planned vs logged minutes per week (most recent last). planned = estimates of tasks planned that week."""
    start_of_this = today - timedelta(days=(today.weekday() - week_start) % 7)
    rows = []
    for i in range(weeks - 1, -1, -1):
        ws = start_of_this - timedelta(days=7 * i)
        we = ws + timedelta(days=7)
        planned = sum(t.get("estimatedMinutes") or 0 for t in tasks if t.get("plannedDate") and ws <= t["plannedDate"] < we)
        logged = sum(s.get("actualMinutes") or 0 for s in sessions if (_d(s.get("endedAt") or s.get("createdAt")) or date.min) >= ws
                     and (_d(s.get("endedAt") or s.get("createdAt")) or date.min) < we)
        rows.append({"weekStart": ws.isoformat(), "plannedMinutes": round(planned), "loggedMinutes": round(logged)})
    return rows
