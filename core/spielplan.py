"""Online-Spielplan abrufen und mit der Datenbank abgleichen.

Quelle: https://www.wallgraben-theater.com/spielplan
Jeder Termin steht dort als Zeile der Form
    "Fr 19:30 Uhr  09 Okt 2026  Der Zauberberg  von Thomas Mann ...  Online-Tickets ..."
Der Parser sucht dieses Muster im Text. Er hängt deshalb nicht an CSS-Klassen und
übersteht kleinere Layout-Änderungen der Website.

Abgleich pro Monat:
  neu       -> Aufführung + Schichten laut Vorlage anlegen
  entfallen -> Aufführung inkl. Schichten löschen (bei veröffentlichtem Plan nur melden)
  Tageskasse wird einmalig pro Monat nach eigener Vorlage erzeugt (Mo–Sa 11–13 Uhr).
"""
import json
import re
from dataclasses import dataclass, field
from datetime import date

import requests
from bs4 import BeautifulSoup

from . import db
from .calendar_utils import month_weeks, parse_month
from .importer import apply_offset

MONTHS = {"jan": 1, "feb": 2, "mär": 3, "mae": 3, "mrz": 3, "mar": 3, "apr": 4, "mai": 5, "jun": 6,
          "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dez": 12}
DATE_RE = re.compile(
    r"\b(?:Mo|Di|Mi|Do|Fr|Sa|So)\.?\s*(?P<time>\d{1,2}[:.]\d{2})\s*Uhr\s*"
    r"(?P<day>\d{1,2})\.?\s*(?P<mon>[A-Za-zÄÖÜäöü]{3,9})\.?\s*(?P<year>\d{4})")
TICKET_MARKER = re.compile(r"Online-?Tickets|Tickets\s*\*", re.I)


@dataclass
class Performance:
    date: str      # YYYY-MM-DD
    time: str      # HH:MM
    title: str
    subtitle: str = ""

    @property
    def key(self) -> str:
        return f"{self.date}T{self.time}|{re.sub(r'[^a-z0-9]', '', self.title.lower())}"


@dataclass
class SyncPreview:
    month: str
    fetched: list[Performance]
    new: list[Performance] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)
    unchanged: int = 0


def clean_title(title: str) -> str:
    title = re.sub(r"\*+.*$", "", title)            # Zusätze wie "***KOMBITICKET ..." entfernen
    return re.sub(r"\s+", " ", title).strip(" -–|")


def fetch_html(url: str, timeout: int = 20) -> str:
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Schichtplaner/1.0 (Dienstplanung)"})
    resp.raise_for_status()
    resp.encoding = resp.encoding or "utf-8"
    return resp.text


def parse(html: str) -> list[Performance]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    blocks = [tr for tr in soup.find_all("tr") if not tr.find("tr")] or [soup]
    found: dict[str, Performance] = {}
    for block in blocks:
        text = re.sub(r"\s+", " ", block.get_text(" ", strip=True))
        matches = list(DATE_RE.finditer(text))
        # Titel bevorzugt aus dem ersten Link (ohne Ticket-Link), sonst aus dem Text nach dem Datum
        link_title = ""
        if len(matches) == 1:
            for a in block.find_all("a"):
                t = a.get_text(" ", strip=True)
                if t and not TICKET_MARKER.search(t):
                    link_title = t
                    break
        for i, m in enumerate(matches):
            mon = MONTHS.get(m["mon"][:3].lower())
            if not mon:
                continue
            try:
                d = date(int(m["year"]), mon, int(m["day"])).isoformat()
            except ValueError:
                continue
            hh, mm = re.split(r"[:.]", m["time"])
            rest_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            rest = TICKET_MARKER.split(text[m.end():rest_end])[0].strip()
            title = clean_title(link_title or rest)
            subtitle = rest.replace(link_title, "", 1).strip() if link_title else ""
            if not title:
                continue
            p = Performance(d, f"{int(hh):02d}:{mm}", title, subtitle[:120])
            found.setdefault(p.key, p)
    return sorted(found.values(), key=lambda p: (p.date, p.time))


def preview(month: str, performances: list[Performance]) -> SyncPreview:
    fetched = [p for p in performances if p.date.startswith(month)]
    existing = {p["external_key"]: p for p in db.list_performances(month) if p["source"] == "web"}
    fetched_keys = {p.key for p in fetched}
    pv = SyncPreview(month, fetched)
    pv.new = [p for p in fetched if p.key not in existing]
    pv.removed = [p for k, p in existing.items() if k not in fetched_keys]
    pv.unchanged = len(fetched) - len(pv.new)
    return pv


def apply(pv: SyncPreview, published: bool) -> list[str]:
    """Übernimmt die Vorschau in die Datenbank. Rückgabe: Hinweise für die Oberfläche."""
    notes = []
    template = json.loads(db.get_setting("schicht_vorlage"))
    for p in pv.new:
        pid = db.add_performance(p.date, p.time, p.title, p.key)
        for t in template:
            s_date, s_time = apply_offset(p.date, p.time, t["start_offset_min"])
            _, e_time = apply_offset(p.date, p.time, t["end_offset_min"])
            db.add_shift(s_date, t["schichtart"], s_time, e_time, int(t["anzahl"]), p.title, performance_id=pid)
    for p in pv.removed:
        if published and db.assignments_for_performance(p["id"]):
            notes.append(f"{p['date']} {p['time']} {p['title']}: steht nicht mehr im Spielplan, ist aber "
                         "bereits eingeplant – bitte manuell prüfen.")
            continue
        db.delete_performance(p["id"])
    if pv.new or pv.removed:
        notes.insert(0, f"{len(pv.new)} Aufführung(en) neu, {len(pv.removed)} entfallen.")
    notes += ensure_tageskasse(pv.month)
    return notes


def ensure_tageskasse(month: str) -> list[str]:
    """Legt die Tageskasse-Schichten des Monats an, falls noch keine existieren."""
    cfg = json.loads(db.get_setting("tageskasse_vorlage"))
    if not cfg.get("aktiv") or db.count_shifts_of_type(month, "TAGESKASSE"):
        return []
    _, m = parse_month(month)
    n = 0
    for week in month_weeks(month):
        for d in week:
            if d.month == m and d.weekday() in cfg["wochentage"]:
                db.add_shift(d.isoformat(), "TAGESKASSE", cfg["beginn"], cfg["ende"], int(cfg["anzahl"]))
                n += 1
    return [f"{n} Tageskasse-Schichten angelegt (Feiertage bitte manuell löschen)."] if n else []
