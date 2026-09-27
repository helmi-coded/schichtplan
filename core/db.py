"""Datenbankschicht (SQLite).

Tabellen:
  users          Mitarbeitende inkl. Rollen-Berechtigung und Admin-Flag
  shifts         Einzelne Schichten (Datum, Schichtart, Zeit, Soll-Besetzung)
  blocked_days   Harte Sperrtage je Person
  preferences    Monatliche Präferenzen je Person
  assignments    Ergebnis der Zuteilung (Schicht <-> Person)
  plan_status    Status je Monat (OFFEN / ENTWURF / VEROEFFENTLICHT)
  history        Kennzahlen je Person und Monat (Basis für den Rotations-Score)
  settings       Globale Einstellungen (Mindestlohn, Minijob-Grenze, ...)
"""
import json
import os
import sqlite3
import threading
from contextlib import contextmanager

from . import config
from .calendar_utils import is_weekend, now_local
from .constants import DEFAULT_MAX_SHIFTS, DEFAULT_SETTINGS, SHIFT_TYPES

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("SCHICHTPLANER_DB", os.path.join(_BASE_DIR, "schichtplaner.db"))

# Ist DATABASE_URL gesetzt (z. B. Neon/Postgres in der Cloud), wird Postgres genutzt,
# sonst eine lokale SQLite-Datei. Der restliche Code ist für beide identisch.
DATABASE_URL = config.get("DATABASE_URL")
IS_POSTGRES = bool(DATABASE_URL)
_pool = None
_pool_lock = threading.Lock()

if IS_POSTGRES:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    INTEGRITY_ERRORS: tuple = (sqlite3.IntegrityError, psycopg.IntegrityError)
else:
    INTEGRITY_ERRORS = (sqlite3.IntegrityError,)


class Row(dict):
    """Datensatz, ansprechbar per Spaltenname (r["name"]) und per Position (r[0])."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class _Cursor:
    def __init__(self, cur):
        self._cur = cur

    @staticmethod
    def _row(r):
        return None if r is None else Row(dict(r))

    def fetchone(self):
        return self._row(self._cur.fetchone())

    def fetchall(self):
        return [self._row(r) for r in self._cur.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


class _Conn:
    """Dünne Hülle, die SQLite-Syntax (? als Platzhalter) auch für Postgres nutzbar macht."""

    def __init__(self, raw):
        self.raw = raw

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if IS_POSTGRES else sql

    def execute(self, sql: str, params=()):
        if IS_POSTGRES:
            return _Cursor(self.raw.execute(self._sql(sql), tuple(params) if params else None))
        return _Cursor(self.raw.execute(sql, tuple(params)))

    def executemany(self, sql: str, seq) -> None:
        seq = list(seq)
        if not seq:
            return
        if IS_POSTGRES:
            with self.raw.cursor() as cur:
                cur.executemany(self._sql(sql), seq)
        else:
            self.raw.executemany(sql, seq)

    def executescript(self, script: str) -> None:
        if IS_POSTGRES:
            for stmt in script.split(";"):
                if stmt.strip():
                    self.raw.execute(stmt)
        else:
            self.raw.executescript(script)


def _get_pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=5, open=True,
                                   kwargs={"row_factory": dict_row, "prepare_threshold": None},
                                   check=ConnectionPool.check_connection)
        return _pool


@contextmanager
def get_conn():
    if IS_POSTGRES:
        with _get_pool().connection() as raw:   # commit bei Erfolg, rollback bei Fehler
            yield _Conn(raw)
        return
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield _Conn(conn)
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return now_local().isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    role_permission TEXT NOT NULL CHECK (role_permission IN ('KASSE','EINLASS','BEIDE')),
    is_admin        INTEGER NOT NULL DEFAULT 0,
    plannable       INTEGER NOT NULL DEFAULT 1,   -- wird bei der Zuteilung berücksichtigt
    is_minijob      INTEGER NOT NULL DEFAULT 1,   -- Minijob-Grenze als harte Obergrenze
    hourly_wage     REAL,                         -- NULL = gesetzlicher Mindestlohn
    active          INTEGER NOT NULL DEFAULT 1    -- Login erlaubt
);
CREATE TABLE IF NOT EXISTS shifts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,                    -- YYYY-MM-DD
    title       TEXT,
    shift_type  TEXT NOT NULL CHECK (shift_type IN ('TAGESKASSE','ABENDKASSE','EINLASS')),
    start_time  TEXT NOT NULL,                    -- HH:MM
    end_time    TEXT NOT NULL,                    -- HH:MM (kleiner als Start = nach Mitternacht)
    required    INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_shifts_date ON shifts(date);
CREATE TABLE IF NOT EXISTS blocked_days (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date    TEXT NOT NULL,
    PRIMARY KEY (user_id, date)
);
CREATE TABLE IF NOT EXISTS preferences (
    user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    month             TEXT NOT NULL,              -- YYYY-MM
    max_shifts        INTEGER,
    needs_hours       INTEGER NOT NULL DEFAULT 0,
    weekend_exclusion TEXT NOT NULL DEFAULT 'KEINE',
    weekday_weights   TEXT NOT NULL,              -- JSON: 7 Werte von -2 bis +2
    type_limits       TEXT NOT NULL,              -- JSON: {Schichtart: {"min": int, "max": int|null}}
    updated_at        TEXT,
    PRIMARY KEY (user_id, month)
);
CREATE TABLE IF NOT EXISTS assignments (
    shift_id INTEGER NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (shift_id, user_id)
);
CREATE TABLE IF NOT EXISTS plan_status (
    month      TEXT PRIMARY KEY,
    status     TEXT NOT NULL,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS history (
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    month          TEXT NOT NULL,
    weekend_shifts INTEGER NOT NULL DEFAULT 0,
    total_shifts   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, month)
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS performances (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    date         TEXT NOT NULL,
    time         TEXT NOT NULL,
    title        TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'web',   -- web = aus dem Online-Spielplan
    external_key TEXT UNIQUE                    -- Datum+Uhrzeit+Titel für den Abgleich
);
CREATE TABLE IF NOT EXISTS invite_codes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash  TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used       INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS audit_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    user_id INTEGER,
    action  TEXT NOT NULL,
    details TEXT
);
"""

# Spalten, die in älteren Datenbanken nachgerüstet werden (Migration ohne Datenverlust)
MIGRATIONS = {
    "users": {
        "password_hash": "TEXT",
        "totp_secret": "TEXT",
        "failed_attempts": "INTEGER NOT NULL DEFAULT 0",
        "locked_until": "TEXT",
        "last_login": "TEXT",
    },
    "shifts": {"performance_id": "INTEGER REFERENCES performances(id) ON DELETE CASCADE"},
    "plan_status": {"deadline_override": "TEXT"},
    "preferences": {"role_choice": "TEXT NOT NULL DEFAULT 'BEIDE'", "max_hours": "REAL"},
}


def init_db() -> None:
    schema = SCHEMA
    if IS_POSTGRES:
        schema = schema.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY").replace(
            " REAL", " DOUBLE PRECISION")
    with get_conn() as c:
        c.executescript(schema)
        for table, cols in MIGRATIONS.items():
            if IS_POSTGRES:
                rows = c.execute("SELECT column_name AS name FROM information_schema.columns "
                                 "WHERE table_name = ?", (table,))
            else:
                rows = c.execute(f"PRAGMA table_info({table})")
            existing = {r["name"] for r in rows}
            for col, ddl in cols.items():
                if col not in existing:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        c.execute("CREATE INDEX IF NOT EXISTS idx_shifts_perf ON shifts(performance_id)")
        for k, v in DEFAULT_SETTINGS.items():
            c.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING", (k, v))


# ---------------------------------------------------------------- Settings
def get_setting(key: str) -> str:
    with get_conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else DEFAULT_SETTINGS.get(key, "")


def set_setting(key: str, value: str) -> None:
    with get_conn() as c:
        c.execute("INSERT INTO settings(key, value) VALUES (?, ?) "
                  "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))


# ---------------------------------------------------------------- Users
def list_users(plannable_only: bool = False, active_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM users WHERE 1=1"
    if plannable_only:
        sql += " AND plannable = 1 AND active = 1"
    if active_only:
        sql += " AND active = 1"
    with get_conn() as c:
        return [dict(r) for r in c.execute(sql + " ORDER BY lower(name)")]


def get_user(user_id: int) -> dict | None:
    with get_conn() as c:
        r = c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(r) if r else None


def get_user_by_email(email: str) -> dict | None:
    with get_conn() as c:
        r = c.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
    return dict(r) if r else None


def count_admins() -> int:
    with get_conn() as c:
        return c.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND active = 1").fetchone()[0]


def upsert_user(user_id: int | None, email: str, name: str, role_permission: str = "BEIDE",
                is_admin: bool = False, plannable: bool = True, is_minijob: bool = True,
                hourly_wage: float | None = None, active: bool = True) -> int:
    """Legt eine Person an (user_id=None) oder aktualisiert sie. Gibt die ID zurück."""
    values = (email.strip().lower(), name.strip(), role_permission, int(is_admin),
              int(plannable), int(is_minijob), hourly_wage, int(active))
    try:
        with get_conn() as c:
            if user_id:
                c.execute("""UPDATE users SET email=?, name=?, role_permission=?, is_admin=?,
                             plannable=?, is_minijob=?, hourly_wage=?, active=? WHERE id=?""",
                          values + (user_id,))
                return user_id
            return c.execute("""INSERT INTO users(email, name, role_permission, is_admin,
                                plannable, is_minijob, hourly_wage, active)
                                VALUES (?,?,?,?,?,?,?,?) RETURNING id""", values).fetchone()[0]
    except INTEGRITY_ERRORS as exc:
        raise ValueError(f"E-Mail-Adresse {email} ist bereits vergeben.") from exc


def deactivate_user(user_id: int) -> None:
    with get_conn() as c:
        c.execute("UPDATE users SET active = 0, plannable = 0 WHERE id = ?", (user_id,))


# ---------------------------------------------------------------- Sperrtage
def get_blocked_days(user_id: int, month: str) -> set[str]:
    with get_conn() as c:
        rows = c.execute("SELECT date FROM blocked_days WHERE user_id = ? AND date LIKE ?",
                         (user_id, f"{month}-%"))
        return {r["date"] for r in rows}


def get_all_blocked(month: str) -> dict[int, set[str]]:
    result: dict[int, set[str]] = {}
    with get_conn() as c:
        for r in c.execute("SELECT user_id, date FROM blocked_days WHERE date LIKE ?", (f"{month}-%",)):
            result.setdefault(r["user_id"], set()).add(r["date"])
    return result


def toggle_blocked_day(user_id: int, date_iso: str) -> bool:
    """Schaltet einen Sperrtag um. Rückgabe: True = jetzt gesperrt."""
    with get_conn() as c:
        exists = c.execute("SELECT 1 FROM blocked_days WHERE user_id=? AND date=?",
                           (user_id, date_iso)).fetchone()
        if exists:
            c.execute("DELETE FROM blocked_days WHERE user_id=? AND date=?", (user_id, date_iso))
            return False
        c.execute("INSERT INTO blocked_days(user_id, date) VALUES (?, ?)", (user_id, date_iso))
        return True


# ---------------------------------------------------------------- Präferenzen
def default_preferences() -> dict:
    return {
        "role_choice": "BEIDE",          # Einsatzwunsch des Monats: KASSE / EINLASS / BEIDE
        "max_shifts": DEFAULT_MAX_SHIFTS,
        "max_hours": None,               # optionale persönliche Stunden-Obergrenze
        "needs_hours": False,
        "weekend_exclusion": "KEINE",
        "weekday_weights": [0] * 7,
        "type_limits": {t: {"min": 0, "max": None} for t in SHIFT_TYPES},
        "saved": False,
        "carried_from": None,
    }


def _prefs_from_row(r) -> dict:
    p = default_preferences()
    p.update({
        "role_choice": r["role_choice"] or "BEIDE",
        "max_shifts": r["max_shifts"],
        "max_hours": r["max_hours"],
        "needs_hours": bool(r["needs_hours"]),
        "weekend_exclusion": r["weekend_exclusion"],
        "weekday_weights": json.loads(r["weekday_weights"]),
        "updated_at": r["updated_at"],
    })
    p["type_limits"].update(json.loads(r["type_limits"]))
    return p


def get_preferences(user_id: int, month: str) -> dict:
    """Präferenzen des Monats. Gibt es noch keine, werden die zuletzt gespeicherten als
    Vorschlag übernommen (saved=False) – so muss niemand jeden Monat alles neu eingeben."""
    with get_conn() as c:
        r = c.execute("SELECT * FROM preferences WHERE user_id=? AND month=?", (user_id, month)).fetchone()
        if r:
            p = _prefs_from_row(r)
            p["saved"] = True
            return p
        last = c.execute("SELECT * FROM preferences WHERE user_id=? AND month<? ORDER BY month DESC LIMIT 1",
                         (user_id, month)).fetchone()
    if last:
        p = _prefs_from_row(last)
        p["carried_from"] = last["month"]
        p["needs_hours"] = False          # Dringlichkeit gilt nur für den jeweiligen Monat
        return p
    return default_preferences()


def save_preferences(user_id: int, month: str, prefs: dict) -> None:
    with get_conn() as c:
        c.execute("""INSERT INTO preferences(user_id, month, role_choice, max_shifts, max_hours, needs_hours,
                                             weekend_exclusion, weekday_weights, type_limits, updated_at)
                     VALUES (?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(user_id, month) DO UPDATE SET
                        role_choice=excluded.role_choice, max_shifts=excluded.max_shifts,
                        max_hours=excluded.max_hours, needs_hours=excluded.needs_hours,
                        weekend_exclusion=excluded.weekend_exclusion,
                        weekday_weights=excluded.weekday_weights,
                        type_limits=excluded.type_limits, updated_at=excluded.updated_at""",
                  (user_id, month, prefs.get("role_choice", "BEIDE"), prefs["max_shifts"], prefs.get("max_hours"),
                   int(prefs["needs_hours"]), prefs["weekend_exclusion"], json.dumps(prefs["weekday_weights"]),
                   json.dumps(prefs["type_limits"]), _now()))


def users_with_preferences(month: str) -> set[int]:
    with get_conn() as c:
        return {r[0] for r in c.execute("SELECT user_id FROM preferences WHERE month=?", (month,))}


# ---------------------------------------------------------------- Schichten
def list_shifts(month: str) -> list[dict]:
    with get_conn() as c:
        rows = c.execute("SELECT * FROM shifts WHERE date LIKE ? ORDER BY date, start_time, shift_type",
                         (f"{month}-%",))
        return [dict(r) for r in rows]


def add_shift(date_iso: str, shift_type: str, start: str, end: str, required: int, title: str = "",
              performance_id: int | None = None) -> int:
    with get_conn() as c:
        return c.execute("""INSERT INTO shifts(date, title, shift_type, start_time, end_time, required,
                            performance_id) VALUES (?,?,?,?,?,?,?) RETURNING id""",
                         (date_iso, title, shift_type, start, end, int(required), performance_id)).fetchone()[0]


def delete_shift(shift_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM shifts WHERE id = ?", (shift_id,))


def delete_shifts_of_month(month: str) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM shifts WHERE date LIKE ?", (f"{month}-%",))
        c.execute("DELETE FROM performances WHERE date LIKE ?", (f"{month}-%",))


def shift_dates_for_types(month: str, types: set[str]) -> set[str]:
    """Tage im Monat, an denen es Schichten der angegebenen Arten gibt (Kalendermarkierung)."""
    if not types:
        return set()
    marks = ",".join("?" * len(types))
    with get_conn() as c:
        rows = c.execute(f"SELECT DISTINCT date FROM shifts WHERE date LIKE ? AND shift_type IN ({marks})",
                         (f"{month}-%", *types))
        return {r["date"] for r in rows}


# ---------------------------------------------------------------- Zuteilungen
def save_assignments(month: str, pairs: list[tuple[int, int]]) -> None:
    """Ersetzt alle Zuteilungen des Monats durch die übergebenen (shift_id, user_id)-Paare."""
    with get_conn() as c:
        c.execute("DELETE FROM assignments WHERE shift_id IN (SELECT id FROM shifts WHERE date LIKE ?)",
                  (f"{month}-%",))
        c.executemany("INSERT INTO assignments(shift_id, user_id) VALUES (?, ?)", pairs)


def set_shift_assignment(shift_id: int, user_ids: list[int]) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM assignments WHERE shift_id = ?", (shift_id,))
        c.executemany("INSERT INTO assignments(shift_id, user_id) VALUES (?, ?)",
                      [(shift_id, u) for u in user_ids])


def get_assignments(month: str, user_id: int | None = None) -> list[dict]:
    sql = """SELECT a.shift_id, a.user_id, u.name, s.date, s.title, s.shift_type,
                    s.start_time, s.end_time, s.required
             FROM assignments a
             JOIN shifts s ON s.id = a.shift_id
             JOIN users u ON u.id = a.user_id
             WHERE s.date LIKE ?"""
    params: list = [f"{month}-%"]
    if user_id is not None:
        sql += " AND a.user_id = ?"
        params.append(user_id)
    with get_conn() as c:
        return [dict(r) for r in c.execute(sql + " ORDER BY s.date, s.start_time", params)]


# ---------------------------------------------------------------- Planstatus
def get_plan_status(month: str) -> str:
    with get_conn() as c:
        r = c.execute("SELECT status FROM plan_status WHERE month = ?", (month,)).fetchone()
    return r["status"] if r else "OFFEN"


def set_plan_status(month: str, status: str) -> None:
    with get_conn() as c:
        c.execute("""INSERT INTO plan_status(month, status, updated_at) VALUES (?,?,?)
                     ON CONFLICT(month) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at""",
                  (month, status, _now()))


def get_deadline_override(month: str) -> str | None:
    with get_conn() as c:
        r = c.execute("SELECT deadline_override FROM plan_status WHERE month = ?", (month,)).fetchone()
    return r["deadline_override"] if r else None


def set_deadline_override(month: str, date_iso: str | None) -> None:
    with get_conn() as c:
        c.execute("""INSERT INTO plan_status(month, status, updated_at, deadline_override) VALUES (?, 'OFFEN', ?, ?)
                     ON CONFLICT(month) DO UPDATE SET deadline_override = excluded.deadline_override""",
                  (month, _now(), date_iso))


# ---------------------------------------------------------------- Historie
def get_history(month: str) -> dict[int, dict]:
    with get_conn() as c:
        rows = c.execute("SELECT * FROM history WHERE month = ?", (month,))
        return {r["user_id"]: {"weekend_shifts": r["weekend_shifts"], "total_shifts": r["total_shifts"]}
                for r in rows}


def set_history(user_id: int, month: str, weekend_shifts: int, total_shifts: int) -> None:
    with get_conn() as c:
        c.execute("""INSERT INTO history(user_id, month, weekend_shifts, total_shifts) VALUES (?,?,?,?)
                     ON CONFLICT(user_id, month) DO UPDATE SET
                        weekend_shifts=excluded.weekend_shifts, total_shifts=excluded.total_shifts""",
                  (user_id, month, int(weekend_shifts), int(total_shifts)))


def write_history_from_assignments(month: str) -> None:
    """Schreibt die Kennzahlen des veröffentlichten Monats in die Historie."""
    counts: dict[int, list[int]] = {u["id"]: [0, 0] for u in list_users(plannable_only=True)}
    for a in get_assignments(month):
        entry = counts.setdefault(a["user_id"], [0, 0])
        entry[1] += 1
        if is_weekend(a["date"]):
            entry[0] += 1
    for uid, (we, total) in counts.items():
        set_history(uid, month, we, total)


# ---------------------------------------------------------------- Aufführungen (Online-Spielplan)
def list_performances(month: str) -> list[dict]:
    with get_conn() as c:
        rows = c.execute("SELECT * FROM performances WHERE date LIKE ? ORDER BY date, time", (f"{month}-%",))
        return [dict(r) for r in rows]


def add_performance(date_iso: str, time: str, title: str, external_key: str, source: str = "web") -> int:
    with get_conn() as c:
        return c.execute("INSERT INTO performances(date, time, title, source, external_key) "
                         "VALUES (?,?,?,?,?) RETURNING id",
                         (date_iso, time, title, source, external_key)).fetchone()[0]


def delete_performance(performance_id: int) -> None:
    """Löscht die Aufführung inkl. ihrer Schichten und Zuteilungen (ON DELETE CASCADE)."""
    with get_conn() as c:
        c.execute("DELETE FROM performances WHERE id = ?", (performance_id,))


def assignments_for_performance(performance_id: int) -> int:
    with get_conn() as c:
        return c.execute("""SELECT COUNT(*) FROM assignments a JOIN shifts s ON s.id = a.shift_id
                            WHERE s.performance_id = ?""", (performance_id,)).fetchone()[0]


def count_shifts_of_type(month: str, shift_type: str) -> int:
    with get_conn() as c:
        return c.execute("SELECT COUNT(*) FROM shifts WHERE date LIKE ? AND shift_type = ?",
                         (f"{month}-%", shift_type)).fetchone()[0]


# ---------------------------------------------------------------- Zugangsdaten & Sicherheit
def set_password_hash(user_id: int, password_hash: str) -> None:
    with get_conn() as c:
        c.execute("UPDATE users SET password_hash = ?, failed_attempts = 0, locked_until = NULL WHERE id = ?",
                  (password_hash, user_id))


def set_totp_secret(user_id: int, secret: str | None) -> None:
    with get_conn() as c:
        c.execute("UPDATE users SET totp_secret = ? WHERE id = ?", (secret, user_id))


def register_failed_login(user_id: int, max_attempts: int, lock_until_iso: str) -> int:
    with get_conn() as c:
        c.execute("UPDATE users SET failed_attempts = failed_attempts + 1 WHERE id = ?", (user_id,))
        n = c.execute("SELECT failed_attempts FROM users WHERE id = ?", (user_id,)).fetchone()[0]
        if n >= max_attempts:
            c.execute("UPDATE users SET locked_until = ?, failed_attempts = 0 WHERE id = ?", (lock_until_iso, user_id))
        return n


def register_successful_login(user_id: int) -> None:
    with get_conn() as c:
        c.execute("UPDATE users SET failed_attempts = 0, locked_until = NULL, last_login = ? WHERE id = ?",
                  (_now(), user_id))


def add_invite_code(user_id: int, code_hash: str, expires_at: str) -> None:
    with get_conn() as c:
        c.execute("UPDATE invite_codes SET used = 1 WHERE user_id = ? AND used = 0", (user_id,))  # alte ungültig
        c.execute("INSERT INTO invite_codes(user_id, code_hash, expires_at) VALUES (?,?,?)",
                  (user_id, code_hash, expires_at))


def get_open_invite(user_id: int) -> dict | None:
    with get_conn() as c:
        r = c.execute("SELECT * FROM invite_codes WHERE user_id = ? AND used = 0 ORDER BY id DESC LIMIT 1",
                      (user_id,)).fetchone()
    return dict(r) if r else None


def mark_invite_used(invite_id: int) -> None:
    with get_conn() as c:
        c.execute("UPDATE invite_codes SET used = 1 WHERE id = ?", (invite_id,))


def audit(user_id: int | None, action: str, details: str = "") -> None:
    with get_conn() as c:
        c.execute("INSERT INTO audit_log(ts, user_id, action, details) VALUES (?,?,?,?)",
                  (_now(), user_id, action, details))


def list_audit(limit: int = 200) -> list[dict]:
    with get_conn() as c:
        rows = c.execute("""SELECT l.ts, COALESCE(u.name, '–') AS name, l.action, l.details
                            FROM audit_log l LEFT JOIN users u ON u.id = l.user_id
                            ORDER BY l.id DESC LIMIT ?""", (limit,))
        return [dict(r) for r in rows]


def export_all() -> dict:
    """Komplette Datensicherung als dict (für JSON-Download). Passwort-Hashes und 2FA-Schlüssel
    werden bewusst NICHT exportiert."""
    tables = ["users", "shifts", "performances", "blocked_days", "preferences", "assignments",
              "plan_status", "history", "settings", "audit_log"]
    out = {}
    with get_conn() as c:
        for t in tables:
            rows = [dict(r) for r in c.execute(f"SELECT * FROM {t}")]
            if t == "users":
                for r in rows:
                    r.pop("password_hash", None)
                    r.pop("totp_secret", None)
            out[t] = rows
    return out
