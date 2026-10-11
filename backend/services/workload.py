"""
workload forecasting — pure, deterministic functions.

the model is deliberately simple so a student can check it by hand:

  free time on a day   = your study window (e.g. 09:00–22:00)
                         − recurring commitments that overlap it
                         capped at your daily study limit, 0 on days off / blackouts
  work left            = Σ over open tasks of max(estimate − time already logged, 0)
                         (an assessment with no tasks uses its expected workload)
  feasibility          = earliest-deadline-first: work for the soonest deadline uses
                         the earliest free time; whatever can't be fitted before its
                         deadline is "unallocated"

statuses (definitions are returned to the client verbatim):
  on track                  fits, and ≥ 20% of the free time before the deadline is left over
  tight                     fits, but with less than 20% to spare
  at risk                   does not fit before the deadline alongside earlier deadlines
  insufficient information  no due date, or no way to estimate the remaining work
"""

from datetime import date, timedelta
from statistics import median

DEFAULT_AVAILABILITY = {
    "dayStart": "09:00",
    "dayEnd": "22:00",
    "maxStudyMinutesPerDay": 240,
    "maxSessionMinutes": 120,
    "daysOff": [],          # weekday ints, 0 = Monday
    "blackouts": [],        # [{start: YYYY-MM-DD, end: YYYY-MM-DD, label}]
    "useCalibration": False,
}

DEFAULT_TASK_MINUTES = 30      # used (and flagged) when a task has no estimate
MIN_OPEN_TASK_MINUTES = 15     # an unfinished task that already used its estimate still needs something
TIGHT_THRESHOLD = 0.8          # demand / capacity above this is "tight"
HORIZON_DAYS = 120
END_OF_DAY_KINDS = ("assignment", "project", "lab", "other", "participation", None)

STATUS_DEFINITIONS = {
    "on_track": "The remaining work fits in your free time before the deadline (after earlier deadlines), with at least 20% to spare.",
    "tight": "The remaining work fits, but uses more than 80% of your free time before the deadline — little room for surprises.",
    "at_risk": "The remaining work does not fit in your free time before the deadline once earlier deadlines are accounted for.",
    "insufficient_information": "margin can't judge this yet: it needs a due date and either tasks with time estimates or an expected workload.",
    "done": "Marked complete, or no work left.",
    "past_due": "The due date has passed and it isn't marked complete.",
}


# -----------------------------------------------------------------------------
# availability
# -----------------------------------------------------------------------------
def parse_hhmm(s, default=None):
    try:
        h, m = str(s).split(":")[:2]
        v = int(h) * 60 + int(m)
        if 0 <= v <= 24 * 60:
            return v
    except (ValueError, AttributeError):
        pass
    return default


def normalize_availability(raw):
    """merge stored settings over defaults and validate; returns (settings, errors)."""
    raw = raw or {}
    out = dict(DEFAULT_AVAILABILITY)
    errors = []
    for k in ("dayStart", "dayEnd"):
        if k in raw:
            if parse_hhmm(raw[k]) is None:
                errors.append(f"{k} must be HH:MM")
            else:
                out[k] = raw[k]
    if parse_hhmm(out["dayEnd"]) <= parse_hhmm(out["dayStart"]):
        errors.append("the study window must end after it starts")
    for k, lo, hi in (("maxStudyMinutesPerDay", 0, 16 * 60), ("maxSessionMinutes", 15, 8 * 60)):
        if k in raw:
            v = raw[k]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
                errors.append(f"{k} must be between {lo} and {hi}")
            else:
                out[k] = int(v)
    if "daysOff" in raw:
        v = raw["daysOff"]
        if not isinstance(v, list) or any(not isinstance(d, int) or isinstance(d, bool) or not 0 <= d <= 6 for d in v):
            errors.append("daysOff must be a list of weekday numbers 0–6")
        else:
            out["daysOff"] = sorted(set(v))
    if "blackouts" in raw:
        v = raw["blackouts"]
        clean = []
        if not isinstance(v, list):
            errors.append("blackouts must be a list")
        else:
            for b in v[:50]:
                try:
                    s, e = date.fromisoformat(b["start"]), date.fromisoformat(b["end"])
                except (KeyError, TypeError, ValueError):
                    errors.append("each blackout needs start and end dates (YYYY-MM-DD)")
                    continue
                if e < s:
                    errors.append("a blackout can't end before it starts")
                    continue
                clean.append({"start": s.isoformat(), "end": e.isoformat(), "label": str(b.get("label") or "")[:80]})
            out["blackouts"] = clean
    if "useCalibration" in raw:
        out["useCalibration"] = bool(raw["useCalibration"])
    return out, errors


def _busy_intervals(day, busy_blocks):
    wd = day.weekday()
    out = []
    for b in busy_blocks or []:
        try:
            if int(b.get("day")) != wd:
                continue
        except (TypeError, ValueError):
            continue
        s, e = parse_hhmm(b.get("startTime")), parse_hhmm(b.get("endTime"))
        if s is None or e is None or e <= s:
            continue
        out.append((s, e))
    return out


def _is_blackout(day, availability):
    for b in availability.get("blackouts", []):
        if b["start"] <= day.isoformat() <= b["end"]:
            return True
    return day.weekday() in availability.get("daysOff", [])


def free_minutes(day, availability, busy_blocks, today=None, now_minutes=None):
    """free study minutes on `day` (see module docstring)."""
    if _is_blackout(day, availability):
        return 0
    start = parse_hhmm(availability["dayStart"], 9 * 60)
    end = parse_hhmm(availability["dayEnd"], 22 * 60)
    if today is not None and day == today and now_minutes is not None:
        start = max(start, now_minutes)
    if end <= start:
        return 0
    # merge busy intervals clipped to the window
    clipped = sorted((max(s, start), min(e, end)) for s, e in _busy_intervals(day, busy_blocks) if e > start and s < end)
    busy, cur_s, cur_e = 0, None, None
    for s, e in clipped:
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                busy += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        busy += cur_e - cur_s
    free = (end - start) - busy
    return max(0, min(free, availability["maxStudyMinutesPerDay"]))


def capacity_by_day(start, end, availability, busy_blocks, now_minutes=None):
    out = {}
    d = start
    while d <= end:
        out[d] = free_minutes(d, availability, busy_blocks, today=start, now_minutes=now_minutes)
        d += timedelta(days=1)
    return out


# -----------------------------------------------------------------------------
# estimates
# -----------------------------------------------------------------------------
def estimate_calibration(tasks, min_observations=5):
    """how long tasks actually take relative to their estimates.

    uses completed tasks with both an estimate and logged time. the median ratio
    is shrunk toward 1.0 so a handful of tasks can't swing it far:
        factor = 1 + (median − 1) × n / (n + 5)
    """
    ratios = []
    for t in tasks:
        est, spent = t.get("estimatedMinutes"), t.get("timeSpent")
        if t.get("status") != "done" or not isinstance(est, (int, float)) or not isinstance(spent, (int, float)):
            continue
        if est <= 0 or spent <= 0:
            continue
        ratios.append(min(max(spent / est, 0.25), 4.0))
    n = len(ratios)
    if n < min_observations:
        return {"n": n, "sufficient": False, "median": round(median(ratios), 2) if ratios else None, "factor": 1.0,
                "minObservations": min_observations}
    med = median(ratios)
    factor = 1 + (med - 1) * n / (n + 5)
    factor = min(max(factor, 0.5), 2.5)
    return {"n": n, "sufficient": True, "median": round(med, 2), "factor": round(factor, 2), "minObservations": min_observations}


def task_remaining(task, factor=1.0):
    """→ (minutes, flags). minutes is 0 for done tasks."""
    if task.get("status") == "done":
        return 0, []
    flags = []
    est = task.get("estimatedMinutes")
    if not isinstance(est, (int, float)) or est <= 0:
        est = DEFAULT_TASK_MINUTES
        flags.append("missing_estimate")
    spent = task.get("timeSpent") or 0
    rem = est * factor - spent
    if rem <= 0:
        flags.append("over_estimate")
        rem = MIN_OPEN_TASK_MINUTES
    return int(round(rem)), flags


def assessment_demand(assessment, tasks, factor=1.0, logged_minutes=0):
    """→ dict(minutes|None, openTasks, missingEstimates, overEstimate, basis)."""
    own = [t for t in tasks if t.get("projectId") == assessment["id"]]
    open_tasks = [t for t in own if t.get("status") != "done"]
    if own:
        total, missing, over = 0, 0, 0
        for t in open_tasks:
            m, flags = task_remaining(t, factor)
            total += m
            missing += "missing_estimate" in flags
            over += "over_estimate" in flags
        return {"minutes": total, "openTasks": len(open_tasks), "totalTasks": len(own),
                "missingEstimates": missing, "overEstimate": over, "basis": "tasks"}
    hours = assessment.get("estimatedHours")
    if isinstance(hours, (int, float)) and hours > 0:
        minutes = max(int(round(hours * 60 * factor - (logged_minutes or 0))), 0)
        return {"minutes": minutes, "openTasks": 0, "totalTasks": 0, "missingEstimates": 0,
                "overEstimate": 0, "basis": "expected_workload"}
    return {"minutes": None, "openTasks": 0, "totalTasks": 0, "missingEstimates": 0, "overEstimate": 0, "basis": "none"}


def last_work_day(assessment):
    """last calendar day work can count toward an assessment (see STATUS rules)."""
    due = assessment.get("dueDate")
    if due is None:
        return None
    if assessment.get("kind") in END_OF_DAY_KINDS:
        return due
    return due - timedelta(days=1)  # exams, quizzes, presentations: prepare by the day before


# -----------------------------------------------------------------------------
# forecast
# -----------------------------------------------------------------------------
def _h(minutes):
    if minutes is None:
        return "?"
    h = minutes / 60
    return f"{h:.1f}h".replace(".0h", "h")


def forecast(today, assessments, tasks, availability, busy_blocks, calibration=None,
             now_minutes=None, logged_by_assessment=None):
    """→ {assessments: [...], days: [...], summary, risks, definitions, assumptions}."""
    availability, _ = normalize_availability(availability)
    factor = 1.0
    if availability.get("useCalibration") and calibration and calibration.get("sufficient"):
        factor = calibration["factor"]
    logged_by_assessment = logged_by_assessment or {}

    rows, active = [], []
    for a in assessments:
        row = {"id": a["id"], "title": a.get("title", ""), "courseId": a.get("courseId"),
               "courseName": a.get("courseName", ""), "courseColor": a.get("courseColor"),
               "kind": a.get("kind"), "weight": a.get("weight"),
               "dueDate": a["dueDate"].isoformat() if a.get("dueDate") else None}
        demand = assessment_demand(a, tasks, factor, logged_by_assessment.get(a["id"], 0))
        row["demand"] = demand
        row["remainingMinutes"] = demand["minutes"]
        if a.get("status") == "completed":
            row["status"] = "done"
        elif a.get("dueDate") and a["dueDate"] < today:
            row["status"] = "past_due"
        elif demand["minutes"] == 0 and demand["basis"] == "tasks":
            row["status"] = "done"
        elif not a.get("dueDate") or demand["minutes"] is None or (
                demand["totalTasks"] and demand["missingEstimates"] * 2 > max(demand["openTasks"], 1)):
            row["status"] = "insufficient_information"
        else:
            row["status"] = None  # decided below
            active.append((a, row))
        rows.append(row)

    end = today + timedelta(days=HORIZON_DAYS)
    last_days = [last_work_day(a) for a, _ in active]
    horizon = min(max([d for d in last_days if d] + [today + timedelta(days=13)]), end)
    caps = capacity_by_day(today, horizon, availability, busy_blocks, now_minutes)
    remaining_cap = dict(caps)

    # earliest deadline first; ties broken by weight (heavier first) then title
    active.sort(key=lambda ar: (last_work_day(ar[0]), -(ar[0].get("weight") or 0), ar[0].get("title", "")))
    cum_demand = 0
    for a, row in active:
        lwd = last_work_day(a)
        need = row["remainingMinutes"]
        cum_demand += need
        cum_capacity = sum(m for d, m in caps.items() if d <= lwd)
        allocated, finish = 0, None
        for d in sorted(remaining_cap):
            if d > lwd or allocated >= need:
                break
            take = min(remaining_cap[d], need - allocated)
            if take > 0:
                remaining_cap[d] -= take
                allocated += take
                finish = d
        unallocated = need - allocated
        own_capacity = cum_capacity
        # latest day to start if this were the only thing to do
        latest_start, acc = None, 0
        d = lwd
        while d >= today and need > 0:
            acc += caps.get(d, 0)
            if acc >= need:
                latest_start = d
                break
            d -= timedelta(days=1)

        ratio = (cum_demand / cum_capacity) if cum_capacity else None
        if need == 0:
            status = "done"
        elif unallocated > 0:
            status = "at_risk"
        elif ratio is not None and ratio > TIGHT_THRESHOLD:
            status = "tight"
        else:
            status = "on_track"
        earlier_demand = cum_demand - need
        row.update({
            "status": status,
            "availableMinutes": own_capacity,
            "competingMinutes": earlier_demand,
            "unallocatedMinutes": unallocated,
            "projectedFinish": finish.isoformat() if finish else None,
            "latestStart": latest_start.isoformat() if latest_start else None,
            "lastWorkDay": lwd.isoformat(),
            "loadRatio": round(ratio, 2) if ratio is not None else None,
        })

    for row in rows:
        row["drivers"] = _drivers(row, factor)

    days = []
    planned = {}
    for t in tasks:
        if t.get("status") == "done" or not t.get("plannedDate"):
            continue
        planned.setdefault(t["plannedDate"], []).append(t)
    for d in sorted(caps):
        load = sum(task_remaining(t, factor)[0] for t in planned.get(d, []))
        days.append({"date": d.isoformat(), "freeMinutes": caps[d], "plannedMinutes": load,
                     "overloaded": load > caps[d] + 15})

    next14 = [d for d in caps if d < today + timedelta(days=14)]
    free14 = sum(caps[d] for d in next14)
    due14 = sum(r["remainingMinutes"] or 0 for r in rows
                if r["status"] not in ("done", "past_due") and r["dueDate"] and r["dueDate"] < (today + timedelta(days=14)).isoformat())
    summary = {
        "freeMinutesNext14": free14,
        "demandMinutesDueNext14": due14,
        "counts": {s: sum(1 for r in rows if r["status"] == s) for s in STATUS_DEFINITIONS},
        "calibrationFactorApplied": factor,
    }
    assumptions = [
        f"study window {availability['dayStart']}–{availability['dayEnd']}, at most {_h(availability['maxStudyMinutesPerDay'])} of study a day",
        "recurring commitments from your weekly schedule are subtracted from that window",
        "assignments/projects can be worked on through the due date; exams, quizzes and presentations need to be prepared by the day before",
        f"tasks without an estimate count as {DEFAULT_TASK_MINUTES} min",
    ]
    if factor != 1.0:
        assumptions.append(f"task estimates are multiplied by {factor} based on your logged history")
    risks = detect_risks(today, rows, days, tasks, availability, busy_blocks, calibration, factor)
    return {"today": today.isoformat(), "assessments": rows, "days": days, "summary": summary,
            "risks": risks, "definitions": STATUS_DEFINITIONS, "assumptions": assumptions,
            "availability": availability, "calibration": calibration}


def _drivers(row, factor):
    d = row["demand"]
    out = []
    if row["status"] in ("done",):
        return out
    if d["basis"] == "tasks":
        out.append(f"{_h(d['minutes'])} of work left across {d['openTasks']} open task{'s' if d['openTasks'] != 1 else ''}")
    elif d["basis"] == "expected_workload":
        out.append(f"{_h(d['minutes'])} left of the expected workload (no tasks yet)")
    else:
        out.append("no tasks and no expected workload — add either so margin can estimate the work")
    if not row["dueDate"]:
        out.append("no due date")
    if d["missingEstimates"]:
        out.append(f"{d['missingEstimates']} task{'s have' if d['missingEstimates'] != 1 else ' has'} no time estimate (counted as {DEFAULT_TASK_MINUTES} min)")
    if d["overEstimate"]:
        out.append(f"{d['overEstimate']} unfinished task{'s' if d['overEstimate'] != 1 else ''} already took longer than estimated")
    if row.get("availableMinutes") is not None:
        out.append(f"{_h(row['availableMinutes'])} of free time until {row['lastWorkDay']}")
        if row.get("competingMinutes"):
            out.append(f"{_h(row['competingMinutes'])} of that is needed for earlier deadlines")
        if row.get("unallocatedMinutes"):
            out.append(f"{_h(row['unallocatedMinutes'])} doesn't fit before the deadline")
    if factor != 1.0:
        out.append(f"estimates scaled ×{factor} from your history")
    return out


# -----------------------------------------------------------------------------
# risks
# -----------------------------------------------------------------------------
def _risk(rid, rtype, severity, title, explanation, fingerprint, related=None, actions=None):
    return {"id": rid, "type": rtype, "severity": severity, "title": title, "explanation": explanation,
            "fingerprint": str(fingerprint), "related": related or [], "actions": actions or []}


def detect_risks(today, rows, days, tasks, availability, busy_blocks, calibration, factor):
    risks = []
    for r in rows:
        if r["status"] == "at_risk":
            risks.append(_risk(
                f"at_risk:{r['id']}", "at_risk", "high",
                f"{r['title']} may not fit before {r['dueDate']}",
                f"About {_h(r['unallocatedMinutes'])} of the {_h(r['remainingMinutes'])} left doesn't fit in your free time before the deadline.",
                f"{r['dueDate']}|{r['remainingMinutes'] // 30}|{r['unallocatedMinutes'] // 30}",
                [r["id"]], ["rebuild_plan", "edit_estimates", "edit_availability"]))
        elif r["status"] == "tight":
            risks.append(_risk(
                f"tight:{r['id']}", "tight", "medium",
                f"{r['title']} has little slack",
                f"It needs {_h(r['remainingMinutes'])}; together with earlier deadlines that's {round((r['loadRatio'] or 0) * 100)}% of your free time until {r['lastWorkDay']}.",
                f"{r['dueDate']}|{r['remainingMinutes'] // 30}", [r["id"]], ["rebuild_plan"]))
        if r["status"] in ("tight", "at_risk", "on_track") and r.get("latestStart") and r["latestStart"] <= today.isoformat() \
                and r["demand"]["basis"] == "tasks" and r["remainingMinutes"]:
            risks.append(_risk(
                f"start_now:{r['id']}", "should_start", "medium" if r["status"] == "on_track" else "high",
                f"Start {r['title']} today",
                f"Even if it were the only thing you worked on, you'd need every free minute from today to {r['lastWorkDay']}.",
                f"{r['dueDate']}|{r['remainingMinutes'] // 30}", [r["id"]], ["rebuild_plan"]))
        if r["status"] == "insufficient_information" and (
                not r["dueDate"] or r["dueDate"] <= (today + timedelta(days=21)).isoformat()):
            missing = "a due date" if not r["dueDate"] else "tasks with time estimates or an expected workload"
            risks.append(_risk(
                f"info:{r['id']}", "insufficient_information", "low",
                f"Can't forecast {r['title']} yet",
                f"Add {missing} so margin can tell whether it fits.",
                f"{r['dueDate']}|{r['demand']['basis']}|{r['demand']['missingEstimates']}", [r["id"]], ["edit_estimates"]))
        if r["status"] == "past_due":
            risks.append(_risk(
                f"past_due:{r['id']}", "past_due", "low",
                f"{r['title']} is past its due date",
                "Mark it complete (and optionally log your result), or update the date if it moved.",
                r["dueDate"], [r["id"]], ["mark_complete"]))

    overloaded = [d for d in days if d["overloaded"]]
    if overloaded:
        worst = max(overloaded, key=lambda d: d["plannedMinutes"] - d["freeMinutes"])
        risks.append(_risk(
            "overloaded_days", "overloaded_day", "medium",
            f"{len(overloaded)} day{'s are' if len(overloaded) != 1 else ' is'} overbooked",
            f"For example {worst['date']}: {_h(worst['plannedMinutes'])} planned but only {_h(worst['freeMinutes'])} free.",
            "|".join(d["date"] for d in overloaded[:10]), [d["date"] for d in overloaded], ["rebuild_plan"]))

    missed = [t for t in tasks if t.get("status") != "done" and t.get("plannedDate") and t["plannedDate"] < today]
    if missed:
        risks.append(_risk(
            "missed_tasks", "missed", "medium",
            f"{len(missed)} planned task{'s were' if len(missed) != 1 else ' was'} missed",
            "They were planned for days that have passed. Rebuild your plan to find realistic new slots.",
            len(missed), [t["id"] for t in missed], ["rebuild_plan"]))

    stuck = [t for t in tasks if t.get("status") != "done" and (t.get("rescheduleCount") or 0) >= 3]
    if stuck:
        risks.append(_risk(
            "repeatedly_moved", "repeatedly_moved", "medium",
            f"{len(stuck)} task{'s have' if len(stuck) != 1 else ' has'} been moved 3+ times",
            "Moving them again probably won't help. Consider splitting them, lowering the estimate, asking for help, or dropping them.",
            len(stuck), [t["id"] for t in stuck], ["edit_estimates"]))

    if calibration and calibration.get("sufficient") and factor == 1.0 and calibration["factor"] >= 1.25:
        risks.append(_risk(
            "estimates_optimistic", "unrealistic_assumption", "medium",
            "Your plan assumes tasks take as long as estimated",
            f"Across {calibration['n']} finished tasks, work took about {calibration['median']}× the estimate. Turn on estimate adjustment to plan with that in mind.",
            calibration["factor"], [], ["enable_calibration"]))

    if not busy_blocks:
        risks.append(_risk(
            "no_schedule", "unrealistic_assumption", "low",
            "Your weekly schedule is empty",
            "margin assumes your whole study window is free. Add classes, work and other commitments for a realistic forecast.",
            "empty", [], ["edit_schedule"]))

    deadlines = sorted(r["dueDate"] for r in rows if r["dueDate"] and r["status"] not in ("done", "past_due"))
    for i, d0 in enumerate(deadlines):
        window_end = (date.fromisoformat(d0) + timedelta(days=6)).isoformat()
        in_window = [d for d in deadlines[i:] if d <= window_end]
        if len(in_window) >= 3:
            risks.append(_risk(
                f"crunch:{d0}", "crunch", "low",
                f"{len(in_window)} deadlines between {d0} and {in_window[-1]}",
                "Work on these early — they compete for the same days.", "|".join(in_window), [], ["rebuild_plan"]))
            break

    order = {"high": 0, "medium": 1, "low": 2}
    risks.sort(key=lambda r: order[r["severity"]])
    return risks
