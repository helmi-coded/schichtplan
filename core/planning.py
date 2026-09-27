"""Service-Schicht zwischen Datenbank, Solver und Oberfläche."""
import pandas as pd

from . import db
from .calendar_utils import (WEEKDAYS, add_months, fmt_date, fmt_eur, is_weekend, parse_month,
                             previous_month, shift_hours, shift_interval, weekday)
from .constants import SHIFT_TYPES
from .solver import EmployeeInput, ShiftInput, SolveResult, solve


def wage_for(user: dict) -> float:
    return float(user["hourly_wage"]) if user.get("hourly_wage") else float(db.get_setting("mindestlohn"))


def shift_label(s: dict) -> str:
    title = f" {s['title']}" if s.get("title") else ""
    return (f"{WEEKDAYS[weekday(s['date'])]} {fmt_date(s['date'])} {s['start_time']}–{s['end_time']} "
            f"{SHIFT_TYPES[s['shift_type']]}{title}")


def earnings_last_11_months(month: str, users: list[dict]) -> dict[int, float]:
    """Verdienst je Person in den 11 Monaten vor dem Planungsmonat (aus gespeicherten Plänen)."""
    first = f"{add_months(month, -11)}-01"
    last = f"{previous_month(month)}-31"
    wage = {u["id"]: wage_for(u) for u in users}
    out: dict[int, float] = {}
    for a in db.get_assignments_between(first, last):
        if a["user_id"] in wage:
            out[a["user_id"]] = out.get(a["user_id"], 0.0) + \
                shift_hours(a["date"], a["start_time"], a["end_time"]) * wage[a["user_id"]]
    return out


def build_inputs(month: str) -> tuple[list[EmployeeInput], list[ShiftInput]]:
    history = db.get_history(previous_month(month))
    blocked = db.get_all_blocked(month)
    users = db.list_users(plannable_only=True)
    prefs = db.get_all_preferences(month, [u["id"] for u in users])
    year_limit = 12 * float(db.get_setting("minijob_grenze"))
    past = earnings_last_11_months(month, users)
    employees = []
    for u in users:
        p = prefs[u["id"]]
        employees.append(EmployeeInput(
            id=u["id"], name=u["name"], role_permission=p["role_choice"],
            wage=wage_for(u), is_minijob=bool(u["is_minijob"]),
            min_shifts=p["min_shifts"], max_shifts=p["max_shifts"], max_hours=p["max_hours"], needs_hours=p["needs_hours"],
            weekend_exclusion=p["weekend_exclusion"], weekday_weights=p["weekday_weights"],
            type_limits=p["type_limits"], blocked=blocked.get(u["id"], {}),
            prev_weekend_shifts=history.get(u["id"], {}).get("weekend_shifts", 0),
            year_budget_left_eur=year_limit - past.get(u["id"], 0.0) if u["is_minijob"] else None,
        ))
    shifts = []
    for s in db.list_shifts(month):
        start, end = shift_interval(s["date"], s["start_time"], s["end_time"])
        shifts.append(ShiftInput(id=s["id"], date=s["date"], shift_type=s["shift_type"],
                                 start=start, end=end, required=s["required"], label=shift_label(s)))
    return employees, shifts


def run_planning(month: str) -> SolveResult:
    """Berechnet den Plan, speichert ihn als Entwurf und gibt das Solver-Ergebnis zurück."""
    employees, shifts = build_inputs(month)
    result = solve(
        employees, shifts,
        minijob_limit_eur=float(db.get_setting("minijob_grenze")),
        max_shifts_per_day=int(db.get_setting("max_schichten_pro_tag")),
        time_limit_s=float(db.get_setting("solver_zeitlimit_s")),
    )
    if result.assignments or not result.unfilled:
        db.save_assignments(month, result.assignments)
        db.set_plan_status(month, "ENTWURF")
    # Hinweis, wer die Monatsgrenze überschreitet (bewusst, um Lücken zu schließen)
    limit = float(db.get_setting("minijob_grenze"))
    by_id = {e.id: e for e in employees}
    shift_by_id = {s.id: s for s in shifts}
    pay: dict[int, float] = {}
    for sid, eid in result.assignments:
        pay[eid] = pay.get(eid, 0.0) + shift_by_id[sid].hours * by_id[eid].wage
    for eid, amount in sorted(pay.items(), key=lambda x: -x[1]):
        if by_id[eid].is_minijob and amount > limit:
            result.hints.append(f"{by_id[eid].name}: {fmt_eur(amount)} – über der Monatsgrenze von {fmt_eur(limit)}, "
                                "damit kein Platz offen bleibt (12-Monats-Budget wird eingehalten).")
    return result


def run_trial(month: str) -> SolveResult:
    """Probeplan: rechnet mit dem aktuellen Stand, speichert aber nichts (kein Entwurf wird überschrieben)."""
    employees, shifts = build_inputs(month)
    return solve(
        employees, shifts,
        minijob_limit_eur=float(db.get_setting("minijob_grenze")),
        max_shifts_per_day=int(db.get_setting("max_schichten_pro_tag")),
        time_limit_s=min(15.0, float(db.get_setting("solver_zeitlimit_s"))),
    )


def publish(month: str) -> None:
    db.set_plan_status(month, "VEROEFFENTLICHT")
    db.write_history_from_assignments(month)


def plan_overview(month: str, trial: list[tuple[int, int]] | None = None) -> pd.DataFrame:
    from .export_excel import assignment_names
    assigned = assignment_names(month, trial)
    rows = []
    for s in db.list_shifts(month):
        names = assigned.get(s["id"], [])
        rows.append({
            "Datum": fmt_date(s["date"]),
            "Tag": WEEKDAYS[weekday(s["date"])],
            "Titel": s["title"] or "",
            "Schicht": SHIFT_TYPES[s["shift_type"]],
            "Zeit": f"{s['start_time']}–{s['end_time']}",
            "Soll": s["required"],
            "Ist": len(names),
            "Lücke": max(0, s["required"] - len(names)),
            "Besetzung": ", ".join(sorted(names)),
        })
    return pd.DataFrame(rows)


def hours_account(month: str) -> pd.DataFrame:
    """Stundenkonto: geplante Stunden x Stundenlohn, abgeglichen mit Monatsgrenze und 12-Monats-Budget."""
    limit = float(db.get_setting("minijob_grenze"))
    users = db.list_users(plannable_only=True)
    prefs = db.get_all_preferences(month, [u["id"] for u in users])
    past = earnings_last_11_months(month, users)
    per_user: dict[int, dict] = {}
    for a in db.get_assignments(month):
        acc = per_user.setdefault(a["user_id"], {"n": 0, "we": 0, "h": 0.0})
        acc["n"] += 1
        acc["we"] += int(is_weekend(a["date"]))
        acc["h"] += shift_hours(a["date"], a["start_time"], a["end_time"])
    rows = []
    for u in users:
        acc = per_user.get(u["id"], {"n": 0, "we": 0, "h": 0.0})
        wage = wage_for(u)
        pay = acc["h"] * wage
        year = past.get(u["id"], 0.0) + pay
        if not u["is_minijob"]:
            status = "–"
        elif year > 12 * limit:
            status = "🔴 12-Monats-Budget überschritten"
        elif pay > limit:
            status = "🟡 über Monatsgrenze (Budget ok)"
        else:
            status = "🟢"
        rows.append({
            "Name": u["name"],
            "Einsatz": {"KASSE": "Kasse", "EINLASS": "Einlass", "BEIDE": "beides"}[prefs[u["id"]]["role_choice"]],
            "Schichten": acc["n"],
            "davon WE": acc["we"],
            "Stunden": round(acc["h"], 2),
            "Verdienst €": round(pay, 2),
            "12 Monate €": round(year, 2) if u["is_minijob"] else None,
            "Budget 12 Mon. %": round(year / (12 * limit) * 100, 2) if u["is_minijob"] and limit else None,
            "Status": status,
        })
    return pd.DataFrame(rows)
