"""Hilfsfunktionen für Monate, Kalenderwochen, Schichtdauern und deutsche Formate."""
import calendar
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")


def now_local() -> datetime:
    """Aktuelle deutsche Uhrzeit (ohne Zeitzonen-Anhang). Server in der Cloud laufen oft auf UTC."""
    return datetime.now(TZ).replace(tzinfo=None)


def today_local() -> date:
    return now_local().date()

WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
WEEKDAYS_LONG = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MONTHS_DE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
             "August", "September", "Oktober", "November", "Dezember"]


# ---------- Monate (Schlüssel im Format "YYYY-MM") ----------
def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def parse_month(key: str) -> tuple[int, int]:
    y, m = key.split("-")
    return int(y), int(m)


def month_label(key: str) -> str:
    y, m = parse_month(key)
    return f"{MONTHS_DE[m - 1]} {y}"


def add_months(key: str, n: int) -> str:
    y, m = parse_month(key)
    idx = y * 12 + (m - 1) + n
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def previous_month(key: str) -> str:
    return add_months(key, -1)


def month_range(start_key: str, n: int) -> list[str]:
    return [add_months(start_key, i) for i in range(n)]


def month_weeks(key: str) -> list[list[date]]:
    """Wochen des Monats (Mo–So), inkl. Randtage der Nachbarmonate."""
    y, m = parse_month(key)
    return calendar.Calendar(firstweekday=0).monthdatescalendar(y, m)


# ---------- Schichten ----------
def shift_interval(date_iso: str, start: str, end: str) -> tuple[datetime, datetime]:
    """Start/Ende als datetime. Endet eine Schicht nach Mitternacht, wird +1 Tag gerechnet."""
    d = date.fromisoformat(date_iso)
    s = datetime.combine(d, datetime.strptime(start, "%H:%M").time())
    e = datetime.combine(d, datetime.strptime(end, "%H:%M").time())
    if e <= s:
        e += timedelta(days=1)
    return s, e


def shift_hours(date_iso: str, start: str, end: str) -> float:
    s, e = shift_interval(date_iso, start, end)
    return (e - s).total_seconds() / 3600


def weekday(date_iso: str) -> int:
    return date.fromisoformat(date_iso).weekday()


def is_weekend(date_iso: str) -> bool:
    return weekday(date_iso) >= 5


# ---------- Deutsche Formate ----------
def fmt_date(date_iso: str) -> str:
    """ISO-Datum -> DD.MM.YY"""
    return date.fromisoformat(date_iso).strftime("%d.%m.%y")


def fmt_num(value: float, decimals: int = 2) -> str:
    """1234.5 -> '1.234,50'"""
    s = f"{value:,.{decimals}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_eur(value: float) -> str:
    return f"{fmt_num(value)} €"
