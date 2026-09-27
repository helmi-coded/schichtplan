"""Service-Schicht zwischen Datenbank, Solver und Oberfläche."""
import pandas as pd

from . import db
from .calendar_utils import (WEEKDAYS, fmt_date, is_weekend, previous_month,
                             shift_hours, shift_interval, weekday)
from .constants import SHIFT_TYPES
from .solver import EmployeeInput, ShiftInput, SolveResult, solve


def wage_for(user: dict) -> float:
    return float(user["hourly_wage"]) if user.get("hourly_wage") else float(db.get_setting("mindestlohn"))


def shift_label(s: dict) -> str:
    title = f" {s['title']}" if s.get("title") else ""
    return (f"{WEEKDAYS[weekday(s['date'])]} {fmt_date(s['date'])} {s['start_time']}–{s['end_time']} "
            f"{SHIFT_TYPES[s['shift_type']]}{title}")


def build_inputs(month: str) -> tuple[list[EmployeeInput], list[ShiftInput]]:
    history = db.get_history(previous_month(month))
    blocked = db.get_all_blocked(month)
    employees = []
    for u in db.list_users(plannable_only=True):
        p = db.get_preferences(u["id"], month)
        employees.append(EmployeeInput(
            id=u["id"], name=u["name"], role_permission=p["role_choice"],
            wage=wage_for(u), is_minijob=bool(u["is_minijob"]),
            max_shifts=p["max_shifts"], max_hours=p["max_hours"], needs_hours=p["needs_hours"],
            weekend_exclusion=p["weekend_exclusion"], weekday_weights=p["weekday_weights"],
            type_limits=p["type_limits"], blocked=blocked.get(u["id"], set()),
            prev_weekend_shifts=history.get(u["id"], {}).get("weekend_shifts", 0),
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
    return result


def publish(month: str) -> None:
    db.set_plan_status(month, "VEROEFFENTLICHT")
    db.write_history_from_assignments(month)


def plan_overview(month: str) -> pd.DataFrame:
    assigned: dict[int, list[str]] = {}
    for a in db.get_assignments(month):
        assigned.setdefault(a["shift_id"], []).append(a["name"])
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
    """Stundenkonto: geplante Stunden x Stundenlohn, abgeglichen mit der Minijob-Grenze."""
    limit = float(db.get_setting("minijob_grenze"))
    per_user: dict[int, dict] = {}
    for a in db.get_assignments(month):
        acc = per_user.setdefault(a["user_id"], {"n": 0, "we": 0, "h": 0.0})
        acc["n"] += 1
        acc["we"] += int(is_weekend(a["date"]))
        acc["h"] += shift_hours(a["date"], a["start_time"], a["end_time"])
    rows = []
    for u in db.list_users(plannable_only=True):
        acc = per_user.get(u["id"], {"n": 0, "we": 0, "h": 0.0})
        wage = wage_for(u)
        pay = acc["h"] * wage
        share = pay / limit if (u["is_minijob"] and limit) else None
        if share is None:
            ampel = "–"
        elif share > 1:
            ampel = "🔴 über Grenze"
        elif share >= 0.9:
            ampel = "🟡 ≥ 90 %"
        else:
            ampel = "🟢"
        rows.append({
            "Name": u["name"],
            "Einsatz": {"KASSE": "Kasse", "EINLASS": "Einlass", "BEIDE": "beides"}[
                db.get_preferences(u["id"], month)["role_choice"]],
            "Schichten": acc["n"],
            "davon WE": acc["we"],
            "Stunden": round(acc["h"], 2),
            "Stundenlohn €": round(wage, 2),
            "Verdienst €": round(pay, 2),
            "Auslastung Minijob %": round(share * 100, 2) if share is not None else None,
            "Status": ampel,
        })
    return pd.DataFrame(rows)
