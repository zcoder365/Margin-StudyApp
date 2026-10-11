"""
schedule recovery ("i'm behind") — pure, deterministic planning.

given the current plan, propose a realistic new one:

  1. done tasks and fixed ("pinned") tasks never move; pinned tasks still use up
     their day's free time
  2. future tasks stay where they are if their day has room, they're before the
     deadline, and anything they depend on comes first
  3. missed tasks (planned on a past day) and displaced tasks are re-placed,
     most urgent deadline first, on the earliest day with room
  4. tasks longer than one session, or that don't fit in any single day, are
     split into parts across days (each part ≥ 30 min)
  5. work that can't fit before its deadline is reported, not silently pushed past it
  6. tasks moved 3+ times already are proposed but unselected, with a suggestion
     to change the task itself rather than move it again

nothing here writes data; the route applies only what the student accepts.
"""

from datetime import date, timedelta

from services.workload import task_remaining, last_work_day

MIN_PART_MINUTES = 30
REPEAT_LIMIT = 3


def task_fingerprint(t):
    """changes whenever something recovery depends on changes (used to reject stale plans)."""
    planned = t.get("plannedDate")
    return "|".join(str(x) for x in (
        t.get("status"), planned.isoformat() if planned else "", t.get("estimatedMinutes"),
        t.get("timeSpent"), bool(t.get("pinned")), ",".join(sorted(t.get("dependsOn") or [])),
    ))


def plan_recovery(today, tasks, assessments, capacity, max_session_minutes=120, factor=1.0):
    """
    tasks: dicts with id, projectId, title, estimatedMinutes, timeSpent, status,
           plannedDate (date|None), dependsOn [ids], pinned, rescheduleCount, order
    assessments: dicts with id, title, dueDate (date|None), kind, status, weight
    capacity: {date: free minutes} from today forward
    """
    by_project = {a["id"]: a for a in assessments}
    remaining_cap = dict(capacity)
    horizon = max(capacity) if capacity else today
    task_by_id = {t["id"]: t for t in tasks}

    def deadline(t):
        a = by_project.get(t.get("projectId"))
        if not a or a.get("status") == "completed":
            return None
        lwd = last_work_day(a)
        return lwd

    def priority(t):
        dl = deadline(t)
        a = by_project.get(t.get("projectId"), {})
        return (dl or date.max, -(a.get("weight") or 0), t.get("order") or 0, t.get("title", ""))

    open_tasks = [t for t in tasks if t.get("status") != "done"
                  and by_project.get(t.get("projectId"), {}).get("status") != "completed"]
    placed_day = {}     # task id -> last day it occupies in the new plan
    for t in tasks:
        if t.get("status") == "done":
            placed_day[t["id"]] = date.min

    # 1. pinned tasks hold their place
    for t in open_tasks:
        if t.get("pinned") and t.get("plannedDate"):
            d = t["plannedDate"]
            if d in remaining_cap:
                remaining_cap[d] -= task_remaining(t, factor)[0]
            placed_day[t["id"]] = d

    def dep_ready_day(t):
        """earliest day t may be scheduled given its dependencies (None = a dependency can't be placed)."""
        latest = today
        for dep in t.get("dependsOn") or []:
            if dep not in task_by_id:
                continue
            if dep not in placed_day:
                return "pending"
            if placed_day[dep] is None:
                return None
            latest = max(latest, placed_day[dep])
        return latest

    # 2. keep future tasks that still fit
    to_place = []
    keep_candidates = sorted([t for t in open_tasks if not t.get("pinned")], key=priority)
    for t in keep_candidates:
        d = t.get("plannedDate")
        need = task_remaining(t, factor)[0]
        dl = deadline(t)
        ready = dep_ready_day(t)
        if (d is not None and d >= today and d in remaining_cap and remaining_cap[d] >= need
                and (dl is None or d <= dl) and need <= max_session_minutes
                and ready not in (None, "pending") and d >= ready):
            remaining_cap[d] -= need
            placed_day[t["id"]] = d
        else:
            to_place.append(t)

    moves, splits, unplaceable, flagged = [], [], [], []

    def reason_for(t):
        d = t.get("plannedDate")
        if d is not None and d < today:
            return f"missed on {d.isoformat()}"
        if d is not None and deadline(t) and d > deadline(t):
            return "was planned after its deadline"
        if d is not None and d in capacity and task_remaining(t, factor)[0] > capacity[d]:
            return f"{d.isoformat()} doesn't have enough free time"
        if d is not None and d >= today:
            return f"{d.isoformat()} is overbooked"
        return "wasn't scheduled"

    # 3–5. re-place in priority order; repeat passes so dependents follow their dependencies
    queue = sorted(to_place, key=priority)
    for _ in range(len(queue) + 1):
        if not queue:
            break
        deferred = []
        for t in queue:
            ready = dep_ready_day(t)
            if ready == "pending":
                deferred.append(t)
                continue
            need = task_remaining(t, factor)[0]
            dl = deadline(t) or horizon
            a = by_project.get(t.get("projectId"), {})
            if ready is None:
                placed_day[t["id"]] = None
                unplaceable.append(_unfit(t, a, need, need, "it depends on a task that can't be scheduled in time"))
                continue
            days = sorted(d for d in remaining_cap if ready <= d <= dl)

            # whole task on a single day, if it fits in one session (its current day first, to avoid churn)
            single = None
            if need <= max_session_minutes:
                cur = t.get("plannedDate")
                if cur in days and remaining_cap[cur] >= need:
                    single = cur
                else:
                    single = next((d for d in days if remaining_cap[d] >= need), None)
            if single is not None:
                remaining_cap[single] -= need
                placed_day[t["id"]] = single
                if single != t.get("plannedDate"):
                    moves.append(_move(t, single, reason_for(t)))
                continue

            # split across days
            parts, left = [], need
            for d in days:
                if left <= 0:
                    break
                room = min(remaining_cap[d], max_session_minutes)
                if room < min(MIN_PART_MINUTES, left):
                    continue
                take = min(room, left)
                rest = left - take
                if 0 < rest < MIN_PART_MINUTES:
                    # don't leave a tiny last part: shift a little from this one onto it
                    adjust = MIN_PART_MINUTES - rest
                    if take - adjust >= MIN_PART_MINUTES:
                        take -= adjust
                parts.append((d, take))
                left -= take
            if left > 0:
                placed_day[t["id"]] = None
                unplaceable.append(_unfit(t, a, need, left, "not enough free time before the deadline"))
                continue
            for d, m in parts:
                remaining_cap[d] -= m
            placed_day[t["id"]] = parts[-1][0]
            if len(parts) == 1:
                moves.append(_move(t, parts[0][0], reason_for(t)))
            else:
                splits.append({
                    "taskId": t["id"], "title": t.get("title", ""), "projectId": t.get("projectId"),
                    "from": t["plannedDate"].isoformat() if t.get("plannedDate") else None,
                    "parts": [{"date": d.isoformat(), "minutes": m} for d, m in parts],
                    "reason": f"{need} min is more than fits in one session or day",
                    "selected": True,
                })
        if len(deferred) == len(queue):
            for t in deferred:  # circular or unresolved dependencies
                placed_day[t["id"]] = None
                a = by_project.get(t.get("projectId"), {})
                need = task_remaining(t, factor)[0]
                unplaceable.append(_unfit(t, a, need, need, "its dependencies form a loop — check “do after” settings"))
            break
        queue = deferred

    for change in moves + splits:
        t = task_by_id[change["taskId"]]
        if (t.get("rescheduleCount") or 0) >= REPEAT_LIMIT:
            change["selected"] = False
            change["warning"] = (f"moved {t.get('rescheduleCount')} times already — instead of moving it again, "
                                 "consider splitting it up, lowering the estimate, asking for help, or dropping it")
            flagged.append(change["taskId"])

    interventions = []
    if unplaceable:
        short = sum(u["minutesShort"] for u in unplaceable)
        interventions = [
            f"About {round(short / 60, 1)}h of work doesn't fit before its deadline. Options:",
            "lower the estimate or reduce the scope of a task (and decide what “good enough” looks like)",
            "add study time (extend your study window or daily limit) — only if that's realistic",
            "ask your instructor or TA about an extension early, or get help with the hardest part",
        ]

    used_before = {d: capacity[d] - remaining_cap[d] for d in capacity}
    return {
        "moves": moves,
        "splits": splits,
        "unplaceable": unplaceable,
        "repeatedlyMoved": flagged,
        "interventions": interventions,
        "keptInPlace": sum(1 for t in open_tasks if t["id"] not in [c["taskId"] for c in moves + splits]
                           and placed_day.get(t["id"]) is not None),
        "basis": {t["id"]: task_fingerprint(t) for t in open_tasks},
        "dayLoad": [{"date": d.isoformat(), "freeMinutes": capacity[d], "plannedMinutes": used_before[d]}
                    for d in sorted(capacity)][:28],
    }


def _move(t, to, reason):
    return {"taskId": t["id"], "title": t.get("title", ""), "projectId": t.get("projectId"),
            "from": t["plannedDate"].isoformat() if t.get("plannedDate") else None,
            "to": to.isoformat(), "reason": reason, "selected": True}


def _unfit(t, a, need, short, why):
    return {"taskId": t["id"], "title": t.get("title", ""), "projectId": t.get("projectId"),
            "assessmentTitle": a.get("title", ""),
            "deadline": a["dueDate"].isoformat() if a.get("dueDate") else None,
            "minutesNeeded": need, "minutesShort": short, "reason": why}
