"""CSV-Import der Aufführungstermine bzw. Schichten.

Zwei Formate werden automatisch erkannt (Trennzeichen ; oder , egal):

A) Aufführungsliste  ->  Schichten werden über die Schicht-Vorlage erzeugt
   datum;titel;beginn
   03.10.2026;Hamlet;19:30

B) Schichtliste (direkt)  ->  wird 1:1 übernommen
   datum;titel;schichtart;beginn;ende;anzahl
   03.10.2026;;Tageskasse;11:00;14:00;1
"""
import io
from datetime import date, datetime, timedelta

import pandas as pd

TYPE_ALIASES = {
    "tageskasse": "TAGESKASSE",
    "abendkasse": "ABENDKASSE",
    "kasse": "ABENDKASSE",
    "einlass": "EINLASS",
    "einlassdienst": "EINLASS",
}
DATE_FORMATS = ["%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"]


def read_csv(file) -> pd.DataFrame:
    raw = file.read() if hasattr(file, "read") else file
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raw = raw.decode("cp1252")  # Excel-Export unter Windows
    df = pd.read_csv(io.StringIO(raw), sep=None, engine="python", dtype=str).fillna("")
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


def parse_date(value: str) -> str:
    value = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"Datum '{value}' nicht erkannt (erwartet TT.MM.JJJJ)")


def parse_time(value: str) -> str:
    v = str(value).strip().replace(".", ":")
    if v.isdigit() and len(v) in (3, 4):          # 1930 -> 19:30
        v = f"{v[:-2]}:{v[-2:]}"
    if v.isdigit():                                # 19 -> 19:00
        v = f"{v}:00"
    try:
        return datetime.strptime(v, "%H:%M").strftime("%H:%M")
    except ValueError as exc:
        raise ValueError(f"Uhrzeit '{value}' nicht erkannt (erwartet HH:MM)") from exc


def apply_offset(date_iso: str, time_str: str, minutes: int) -> tuple[str, str]:
    """Verschiebt Datum+Uhrzeit um x Minuten (inkl. Tageswechsel)."""
    dt = datetime.combine(date.fromisoformat(date_iso), datetime.strptime(time_str, "%H:%M").time())
    dt += timedelta(minutes=int(minutes))
    return dt.date().isoformat(), dt.strftime("%H:%M")


def build_shifts(df: pd.DataFrame, template: list[dict]) -> tuple[list[dict], list[str]]:
    """Wandelt das eingelesene CSV in Schicht-Datensätze um. Rückgabe: (schichten, fehler)."""
    shifts, errors = [], []
    cols = set(df.columns)
    if "datum" not in cols or "beginn" not in cols:
        return [], ["Pflichtspalten fehlen: 'datum' und 'beginn' werden benötigt."]

    direct_mode = "schichtart" in cols
    if direct_mode and "ende" not in cols:
        return [], ["Für eine Schichtliste wird zusätzlich die Spalte 'ende' benötigt."]

    for idx, row in df.iterrows():
        line = idx + 2  # +1 Kopfzeile, +1 weil Excel bei 1 beginnt
        try:
            d = parse_date(row["datum"])
            start = parse_time(row["beginn"])
            title = str(row.get("titel", "")).strip()
            if direct_mode:
                t_raw = str(row["schichtart"]).strip().lower()
                if t_raw not in TYPE_ALIASES:
                    raise ValueError(f"Schichtart '{row['schichtart']}' unbekannt")
                anzahl = int(row.get("anzahl") or 1)
                shifts.append({"date": d, "title": title, "shift_type": TYPE_ALIASES[t_raw],
                               "start": start, "end": parse_time(row["ende"]), "required": anzahl})
            else:
                for tpl in template:
                    s_date, s_time = apply_offset(d, start, tpl["start_offset_min"])
                    _, e_time = apply_offset(d, start, tpl["end_offset_min"])
                    shifts.append({"date": s_date, "title": title, "shift_type": tpl["schichtart"],
                                   "start": s_time, "end": e_time, "required": int(tpl["anzahl"])})
        except (ValueError, KeyError) as exc:
            errors.append(f"Zeile {line}: {exc}")
    return shifts, errors


SAMPLE_PERFORMANCES = (
    "datum;titel;beginn\n"
    "02.10.2026;Hamlet;19:30\n"
    "03.10.2026;Hamlet;19:30\n"
    "04.10.2026;Die Physiker;18:00\n"
)

SAMPLE_SHIFTS = (
    "datum;titel;schichtart;beginn;ende;anzahl\n"
    "02.10.2026;;Tageskasse;11:00;14:00;1\n"
    "02.10.2026;Hamlet;Abendkasse;18:30;20:00;1\n"
    "02.10.2026;Hamlet;Einlass;18:45;19:50;2\n"
)
