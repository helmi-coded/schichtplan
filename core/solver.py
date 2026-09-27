"""Automatische Schichtzuteilung als Optimierungsmodell (Google OR-Tools, CP-SAT).

Prinzip: Für jede zulässige Kombination (Person, Schicht) gibt es eine 0/1-Variable
("wird eingeteilt: ja/nein"). Harte Restriktionen werden als Nebenbedingungen
formuliert, weiche Restriktionen als Punkte in einer Zielfunktion, die maximiert wird.

HARTE Restriktionen (werden nie verletzt)
  - Sperrtage (ganztags, nur tagsüber oder nur abends), ausgeschlossener Wochenendtag
  - Einsatzwunsch des Monats (Kasse / Einlass / beides)
  - Schicht- und Stunden-Obergrenze pro Monat, Obergrenze je Schichtart
  - max. Schichten pro Tag, keine zeitlich überlappenden Schichten
  - Minijob-Verdienstgrenze (Stunden x Stundenlohn <= Grenze)

WEICHE Restriktionen (Zielfunktion, gewichtet)
  - Schichten möglichst vollständig besetzen (höchste Priorität)
  - Mindestanzahl je Schichtart erfüllen
  - Wochentags-Präferenzen, Priorisierung "braucht dringend Stunden"
  - Fairness: Wochenenddienste nach Vormonats-Historie rotieren,
    höchste Wochenendbelastung im Team klein halten, Last gleichmäßig verteilen

Unterbesetzung ist über Schlupfvariablen erlaubt -> das Modell hat IMMER eine
Lösung; Lücken werden ausgewiesen statt die Berechnung abzubrechen.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from ortools.sat.python import cp_model

from .constants import ALLOWED_TYPES, SHIFT_TYPES, TYPE_GROUPS

# Gewichte der Zielfunktion (Punkte). Größenordnungen bewusst gestaffelt.
W_UNFILLED = 10_000        # je unbesetztem Platz (dominiert alles andere)
W_TYPE_MIN = 500           # je fehlender Mindestschicht einer Schichtart
W_MAX_WEEKEND = 200        # höchste Wochenendbelastung einer Person im Team
W_NEEDS_HOURS = 30         # Bonus je Schicht für Personen, die Stunden brauchen (≈ +3 Schichten ggü. Ø)
W_WEEKDAY = 20             # je Präferenzstufe (-2 ... +2)
W_WEEKEND_BASE = 10        # Grundmalus je Wochenenddienst
W_WEEKEND_HISTORY = 15     # zusätzlicher Malus je Wochenenddienst im Vormonat
W_BALANCE = 5              # quadratischer Ausgleich der Gesamtlast (für alle gleich)


@dataclass
class EmployeeInput:
    id: int
    name: str
    role_permission: str
    wage: float
    is_minijob: bool = True
    min_shifts: int = 0
    max_shifts: int | None = None
    max_hours: float | None = None
    needs_hours: bool = False
    weekend_exclusion: str = "KEINE"
    weekday_weights: list[int] = field(default_factory=lambda: [0] * 7)
    type_limits: dict = field(default_factory=dict)
    blocked: dict = field(default_factory=dict)   # {Datum: GANZ | TAG | ABEND} (ein set = ganze Tage)
    prev_weekend_shifts: int = 0


@dataclass
class ShiftInput:
    id: int
    date: str
    shift_type: str
    start: datetime
    end: datetime
    required: int
    label: str = ""

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600

    @property
    def weekday(self) -> int:
        return self.start.weekday()

    @property
    def is_weekend(self) -> bool:
        return self.weekday >= 5


@dataclass
class SolveResult:
    status: str
    assignments: list[tuple[int, int]]      # (shift_id, employee_id)
    unfilled: dict[int, int]                # shift_id -> fehlende Plätze
    objective: float | None
    wall_time: float
    hints: list[str]


DAYTIME_TYPES = {"TAGESKASSE"}          # alles andere zählt als "abends"


def _blocked_part(blocked, date_iso: str) -> str | None:
    if isinstance(blocked, dict):
        return blocked.get(date_iso)
    return "GANZ" if date_iso in blocked else None


def blocks(part: str | None, shift_type: str) -> bool:
    """Sperrt ein Sperrtag (GANZ/TAG/ABEND) diese Schichtart?"""
    if part == "GANZ":
        return True
    if part == "TAG":
        return shift_type in DAYTIME_TYPES
    if part == "ABEND":
        return shift_type not in DAYTIME_TYPES
    return False


def is_eligible(e: EmployeeInput, s: ShiftInput) -> bool:
    """Prüft alle harten Einzel-Restriktionen einer Person für eine Schicht."""
    if s.shift_type not in ALLOWED_TYPES[e.role_permission]:
        return False
    if blocks(_blocked_part(e.blocked, s.date), s.shift_type):
        return False
    if e.weekend_exclusion == "SA" and s.weekday == 5:
        return False
    if e.weekend_exclusion == "SO" and s.weekday == 6:
        return False
    for key, lim in (e.type_limits or {}).items():
        if (lim or {}).get("max") == 0 and s.shift_type in _types_of(key):
            return False
    return True


def _types_of(key: str) -> set[str]:
    """Grenzen können für eine Gruppe (KASSE/EINLASS) oder eine einzelne Schichtart gelten."""
    return TYPE_GROUPS.get(key, {key})


def solve(employees: list[EmployeeInput], shifts: list[ShiftInput],
          minijob_limit_eur: float | None = None, max_shifts_per_day: int = 1,
          time_limit_s: float = 30, seed: int = 42) -> SolveResult:
    model = cp_model.CpModel()
    by_shift: dict[int, list] = defaultdict(list)
    by_emp: dict[int, list] = defaultdict(list)
    objective = []

    # 1) Entscheidungsvariablen nur für zulässige Kombinationen
    for e in employees:
        for s in shifts:
            if is_eligible(e, s):
                v = model.NewBoolVar(f"x_{e.id}_{s.id}")
                by_shift[s.id].append(v)
                by_emp[e.id].append((s, v))

    # 2) Besetzung: Ist + Lücke = Soll (Lücke wird stark bestraft)
    unfilled_vars = {}
    for s in shifts:
        gap = model.NewIntVar(0, s.required, f"gap_{s.id}")
        model.Add(sum(by_shift[s.id]) + gap == s.required)
        unfilled_vars[s.id] = gap
        objective.append(-W_UNFILLED * gap)

    max_weekend = model.NewIntVar(0, max(1, len(shifts)), "max_weekend")
    objective.append(-W_MAX_WEEKEND * max_weekend)

    # 3) Restriktionen und Präferenzen je Person
    for e in employees:
        items = by_emp[e.id]
        if not items:
            continue
        variables = [v for _, v in items]
        total = model.NewIntVar(0, len(variables), f"total_{e.id}")
        model.Add(total == sum(variables))

        if e.max_shifts is not None:
            model.Add(total <= int(e.max_shifts))
        if e.min_shifts:                           # Wunsch-Minimum: weich, wird möglichst erfüllt
            short_total = model.NewIntVar(0, int(e.min_shifts), f"short_total_{e.id}")
            model.Add(total + short_total >= int(e.min_shifts))
            objective.append(-W_TYPE_MIN * short_total)

        per_day = defaultdict(list)
        for s, v in items:
            per_day[s.date].append(v)
        for vs in per_day.values():
            if len(vs) > max_shifts_per_day:
                model.Add(sum(vs) <= max_shifts_per_day)

        # keine zeitlich überlappenden Schichten (auch über Mitternacht)
        ordered = sorted(items, key=lambda it: it[0].start)
        for i, (s1, v1) in enumerate(ordered):
            for s2, v2 in ordered[i + 1:]:
                if s2.start >= s1.end:
                    break
                model.AddBoolOr([v1.Not(), v2.Not()])

        # persönliche Stunden-Obergrenze (in Minuten, CP-SAT rechnet ganzzahlig)
        if e.max_hours:
            model.Add(sum(int(round(s.hours * 60)) * v for s, v in items) <= int(round(e.max_hours * 60)))

        # Minijob-Grenze in Cent (CP-SAT rechnet ganzzahlig)
        if e.is_minijob and minijob_limit_eur:
            model.Add(sum(int(round(s.hours * e.wage * 100)) * v for s, v in items)
                      <= int(round(minijob_limit_eur * 100)))

        # Unter-/Obergrenzen je Schichtart
        for t, lim in (e.type_limits or {}).items():
            lim = lim or {}
            tv = [v for s, v in items if s.shift_type in _types_of(t)]
            if lim.get("max") is not None:
                model.Add(sum(tv) <= int(lim["max"]))
            mn = int(lim.get("min") or 0)
            if mn > 0:
                short = model.NewIntVar(0, mn, f"short_{e.id}_{t}")
                model.Add(sum(tv) + short >= mn)
                objective.append(-W_TYPE_MIN * short)

        # Wochentags-Präferenz, Priorisierung, Wochenend-Rotation
        for s, v in items:
            coef = W_WEEKDAY * int(e.weekday_weights[s.weekday])
            if e.needs_hours:
                coef += W_NEEDS_HOURS
            if s.is_weekend:
                coef -= W_WEEKEND_BASE + W_WEEKEND_HISTORY * int(e.prev_weekend_shifts)
            if coef:
                objective.append(coef * v)

        weekend_vars = [v for s, v in items if s.is_weekend]
        if weekend_vars:
            model.Add(max_weekend >= sum(weekend_vars))

        # quadratischer Lastausgleich: 2 x 4 Schichten ist "billiger" als 1 x 8
        square = model.NewIntVar(0, len(variables) ** 2, f"sq_{e.id}")
        model.AddMultiplicationEquality(square, [total, total])
        objective.append(-W_BALANCE * square)

    model.Maximize(sum(objective))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit_s)
    solver.parameters.num_workers = 8
    solver.parameters.random_seed = seed
    status = solver.Solve(model)

    status_text = {
        cp_model.OPTIMAL: "Optimal",
        cp_model.FEASIBLE: "Gute Lösung (Zeitlimit erreicht)",
        cp_model.INFEASIBLE: "Keine Lösung",
        cp_model.MODEL_INVALID: "Modellfehler",
    }.get(status, "Unbekannt")

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return SolveResult(status_text, [], {s.id: s.required for s in shifts}, None, solver.WallTime(), [])

    assignments = [(s.id, e.id) for e in employees for s, v in by_emp[e.id] if solver.Value(v)]
    unfilled = {sid: solver.Value(g) for sid, g in unfilled_vars.items() if solver.Value(g) > 0}

    hints = []
    shift_by_id = {s.id: s for s in shifts}
    for sid, gap in unfilled.items():
        s = shift_by_id[sid]
        hints.append(f"{s.label or s.date} – {SHIFT_TYPES[s.shift_type]}: {gap} Platz/Plätze offen "
                     f"({len(by_shift[sid])} Person(en) grundsätzlich verfügbar)")
    return SolveResult(status_text, assignments, unfilled, solver.ObjectiveValue(), solver.WallTime(), hints)
