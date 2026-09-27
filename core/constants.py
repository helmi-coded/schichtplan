"""Zentrale Konstanten: Rollen, Schichtarten, Standard-Einstellungen.

Alle fachlichen Schlüssel (z. B. "KASSE", "ABENDKASSE") werden in der
Datenbank gespeichert; die deutschen Anzeigetexte stehen nur hier.
"""
import json

# Einsatzwunsch: wählt jede Person selbst pro Monat. Kasse und Einlass sind strikt getrennte Schichten.
ROLE_PERMISSIONS = {
    "KASSE": "Nur Kasse",
    "EINLASS": "Nur Einlass",
    "BEIDE": "Kasse & Einlass",
}

# Schichtarten
SHIFT_TYPES = {
    "TAGESKASSE": "Tageskasse",
    "ABENDKASSE": "Abendkasse",
    "EINLASS": "Einlass",
}

# Welche Rolle darf welche Schichtart übernehmen (harte Restriktion)
ALLOWED_TYPES = {
    "KASSE": {"TAGESKASSE", "ABENDKASSE"},
    "EINLASS": {"EINLASS"},
    "BEIDE": {"TAGESKASSE", "ABENDKASSE", "EINLASS"},
}

# Wochenend-Ausschluss: maximal EIN Wochenendtag kann hart ausgeschlossen werden
WEEKEND_EXCLUSION = {
    "KEINE": "Kein Ausschluss",
    "SA": "Samstag ausschließen",
    "SO": "Sonntag ausschließen",
}

# Gewichtung der Wochentage (weiche Restriktion)
WEIGHT_LABELS = {
    -2: "sehr ungern",
    -1: "eher ungern",
    0: "neutral",
    1: "eher gern",
    2: "sehr gern",
}

PLAN_STATUS = {
    "OFFEN": "Präferenzen werden gesammelt",
    "ENTWURF": "Entwurf berechnet (noch nicht sichtbar für das Team)",
    "VEROEFFENTLICHT": "Veröffentlicht (Präferenzen gesperrt)",
}

# Vorlage: aus einer Aufführung (Datum + Beginn) werden Schichten erzeugt.
# Offsets in Minuten relativ zum Vorstellungsbeginn.
# Abendkasse: laut Website 2 Stunden vor Vorstellungsbeginn geöffnet.
# Einlass: beginnt 2 Stunden vorher und dauert bis ca. 1 Stunde nach Vorstellungsende
# -> pauschal 5 Stunden (Beginn -120 Min., Ende +180 Min.). In der Vorlage anpassbar.
DEFAULT_TEMPLATE = [
    {"schichtart": "ABENDKASSE", "start_offset_min": -120, "end_offset_min": 15, "anzahl": 1},
    {"schichtart": "EINLASS", "start_offset_min": -120, "end_offset_min": 180, "anzahl": 2},
]

# Tageskasse: laut Website Mo–Sa 11:00–13:00 Uhr (Wochentage 0 = Mo ... 5 = Sa)
DEFAULT_TAGESKASSE = {"aktiv": True, "wochentage": [0, 1, 2, 3, 4, 5], "beginn": "11:00", "ende": "13:00", "anzahl": 1}

SPIELPLAN_URL = "https://www.wallgraben-theater.com/spielplan"

# Stand 01.01.2026: Mindestlohn 13,90 €/h, Minijob-Grenze 603 €/Monat.
# Beide Werte sind in der App unter "Einstellungen" anpassbar (z. B. 2027).
DEFAULT_SETTINGS = {
    "mindestlohn": "13.90",
    "minijob_grenze": "603.00",
    "max_schichten_pro_tag": "1",
    "solver_zeitlimit_s": "30",
    "schicht_vorlage": json.dumps(DEFAULT_TEMPLATE),
    "tageskasse_vorlage": json.dumps(DEFAULT_TAGESKASSE),
    "spielplan_url": SPIELPLAN_URL,
    "anmeldeschluss_tage": "7",       # Präferenzen bis X Tage vor Monatsbeginn
    "session_timeout_min": "30",      # automatische Abmeldung bei Inaktivität
}

# Sicherheit
PASSWORD_MIN_LENGTH = 10
MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15
INVITE_VALID_DAYS = 7

DEFAULT_MAX_SHIFTS = 8
