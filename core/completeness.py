"""Schicht-Check: Sind für einen Monat alle Schichten angelegt?

- Tageskasse: an jedem Wochentag laut Vorlage (Standard Mo–Sa), außer an gesetzlichen
  Feiertagen in Baden-Württemberg. Fehlende Tage werden ergänzt, nicht alles-oder-nichts.
- Vorstellungen: Jede Vorstellung bekommt alle Schichten laut Vorlage (Abendkasse, Einlass,
  Technik). Fehlt eine Schichtart, z. B. Technik bei älteren Vorstellungen, wird sie ergänzt.
- Tageskasse an Feiertagen wird entfernt, sofern noch niemand eingeteilt ist.

Die Prüfung läuft automatisch beim Spielplan-Abruf, beim Probeplan und bei „Plan erstellen“
und kann im Bereich „Termine & Schichten“ jederzeit von Hand ausgelöst werden.
"""
import json
from dataclasses import dataclass, field

from . import db
from .calendar_utils import fmt_date, holidays_bw, month_weeks, parse_month
from .constants import SHIFT_TYPES
from .importer import apply_offset


@dataclass
class MonthCheck:
    holidays: dict[str, str] = field(default_factory=dict)            # Feiertage im Monat
    tk_expected: list[str] = field(default_factory=list)              # Tage mit Tageskasse (Soll)
    tk_missing: list[str] = field(default_factory=list)               # davon ohne Tageskasse
    tk_on_holiday: list[dict] = field(default_factory=list)           # Tageskasse an Feiertag
    performances: int = 0
    perf_missing: list[tuple[dict, str]] = field(default_factory=list)  # (Vorstellung, Schichtart)

    @property
    def ok(self) -> bool:
        return not (self.tk_missing or self.tk_on_holiday or self.perf_missing)


def _tk_config() -> dict:
    return json.loads(db.get_setting("tageskasse_vorlage"))


def check(month: str) -> MonthCheck:
    y, m = parse_month(month)
    hol = {d: n for d, n in holidays_bw(y).items() if d.startswith(month)}
    cfg = _tk_config()
    shifts = db.list_shifts(month)
    tk_by_date: dict[str, list[dict]] = {}
    types_by_perf: dict[int, set[str]] = {}
    for s in shifts:
        if s["shift_type"] == "TAGESKASSE":
            tk_by_date.setdefault(s["date"], []).append(s)
        if s.get("performance_id"):
            types_by_perf.setdefault(s["performance_id"], set()).add(s["shift_type"])

    res = MonthCheck(holidays=hol)
    if cfg.get("aktiv"):
        for d in (d for w in month_weeks(month) for d in w if d.month == m):
            iso = d.isoformat()
            if d.weekday() in cfg["wochentage"] and iso not in hol:
                res.tk_expected.append(iso)
                if iso not in tk_by_date:
                    res.tk_missing.append(iso)
    res.tk_on_holiday = [s for d in hol for s in tk_by_date.get(d, [])]

    template_types = [t["schichtart"] for t in json.loads(db.get_setting("schicht_vorlage"))]
    perfs = db.list_performances(month)
    res.performances = len(perfs)
    for p in perfs:
        have = types_by_perf.get(p["id"], set())
        for t in template_types:
            if t not in have:
                res.perf_missing.append((p, t))
    return res


def complete(month: str) -> list[str]:
    """Ergänzt fehlende Schichten und entfernt unbesetzte Tageskasse an Feiertagen. Rückgabe: Hinweise."""
    res = check(month)
    notes = []
    cfg = _tk_config()
    for iso in res.tk_missing:
        db.add_shift(iso, "TAGESKASSE", cfg["beginn"], cfg["ende"], int(cfg["anzahl"]))
    if res.tk_missing:
        notes.append(f"{len(res.tk_missing)} Tageskasse-Schicht(en) ergänzt.")

    template = {t["schichtart"]: t for t in json.loads(db.get_setting("schicht_vorlage"))}
    added: dict[str, int] = {}
    for p, t in res.perf_missing:
        tpl = template[t]
        s_date, s_time = apply_offset(p["date"], p["time"], tpl["start_offset_min"])
        _, e_time = apply_offset(p["date"], p["time"], tpl["end_offset_min"])
        db.add_shift(s_date, t, s_time, e_time, int(tpl["anzahl"]), p["title"], performance_id=p["id"])
        added[t] = added.get(t, 0) + 1
    for t, n in added.items():
        notes.append(f"{n} {SHIFT_TYPES[t]}-Schicht(en) für Vorstellungen ergänzt.")

    if res.tk_on_holiday:
        removed = db.delete_unassigned_shifts([s["id"] for s in res.tk_on_holiday])
        if removed:
            notes.append(f"{removed} Tageskasse-Schicht(en) an Feiertagen entfernt.")
        kept = len(res.tk_on_holiday) - removed
        if kept:
            notes.append(f"⚠️ {kept} Tageskasse-Schicht(en) an Feiertagen sind schon besetzt und "
                         "wurden nicht gelöscht – bitte prüfen.")
    free = [f"{fmt_date(d)} {n}" for d, n in res.holidays.items()]
    if free and (res.tk_missing or res.tk_on_holiday):
        notes.append("Feiertage ohne Tageskasse: " + ", ".join(free) + ".")
    return notes
