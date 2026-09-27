"""Anmeldefrist: Präferenzen und Sperrtage können bis X Tage vor Monatsbeginn
(Standard 7, einstellbar) abgegeben werden. Danach sind sie gesperrt, und die
Leitung erstellt den Plan. Pro Monat kann die Frist individuell verschoben werden."""
from datetime import date, datetime, time, timedelta
from . import db
from .calendar_utils import TZ, WEEKDAYS, parse_month


def now() -> datetime:
    return datetime.now(TZ)


def deadline_for(month: str) -> datetime:
    """Letzter Moment (23:59 Uhr) für Präferenzen des Monats."""
    override = db.get_deadline_override(month)
    if override:
        d = date.fromisoformat(override)
    else:
        y, m = parse_month(month)
        d = date(y, m, 1) - timedelta(days=int(db.get_setting("anmeldeschluss_tage")))
    return datetime.combine(d, time(23, 59, 59), tzinfo=TZ)


def is_open(month: str) -> bool:
    return now() <= deadline_for(month)


def describe(month: str) -> str:
    dl = deadline_for(month)
    label = f"{WEEKDAYS[dl.weekday()]}, {dl.strftime('%d.%m.%y')}, 23:59 Uhr"
    if not is_open(month):
        return f"Anmeldefrist abgelaufen ({label})"
    days = (dl.date() - now().date()).days
    rest = "heute letzter Tag" if days == 0 else f"noch {days} Tag{'e' if days != 1 else ''}"
    return f"Anmeldefrist bis {label} ({rest})"
