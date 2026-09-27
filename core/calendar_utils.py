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


# ---------- Gesetzliche Feiertage Baden-Württemberg (FTG BW § 1 + 3. Oktober per Bundesrecht)
def _easter(year: int) -> date:
    """Ostersonntag (Gaußsche Osterformel, gregorianisch)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def holidays_bw(year: int) -> dict[str, str]:
    """{ISO-Datum: Name} der gesetzlichen Feiertage in Baden-Württemberg."""
    e = _easter(year)
    days = {
        date(year, 1, 1): "Neujahr",
        date(year, 1, 6): "Heilige Drei Könige",
        e - timedelta(days=2): "Karfreitag",
        e + timedelta(days=1): "Ostermontag",
        date(year, 5, 1): "Tag der Arbeit",
        e + timedelta(days=39): "Christi Himmelfahrt",
        e + timedelta(days=50): "Pfingstmontag",
        e + timedelta(days=60): "Fronleichnam",
        date(year, 10, 3): "Tag der Deutschen Einheit",
        date(year, 11, 1): "Allerheiligen",
        date(year, 12, 25): "1. Weihnachtstag",
        date(year, 12, 26): "2. Weihnachtstag",
    }
    return {d.isoformat(): name for d, name in sorted(days.items())}
