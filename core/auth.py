"""Authentifizierung.

- Passwörter werden nie im Klartext gespeichert, sondern mit scrypt gehasht
  (speicherintensives Verfahren, bremst Brute-Force-Angriffe; Python-Standardbibliothek).
- Nach 5 Fehlversuchen wird das Konto 15 Minuten gesperrt.
- Zugang für neue Personen und Passwort-Reset über einmalige Einladungscodes
  (7 Tage gültig, nur als Hash gespeichert).
- Admins ("Chefs") benötigen zusätzlich einen Zwei-Faktor-Code (TOTP, z. B. Google
  Authenticator, Microsoft Authenticator, 2FAS). Auch das Veröffentlichen eines Plans
  wird mit einem aktuellen 2FA-Code bestätigt.
"""
import base64
import hashlib
import hmac
import io
import secrets
from datetime import datetime, timedelta

import pyotp
import segno

from . import db
from .calendar_utils import now_local
from .constants import INVITE_VALID_DAYS, LOCKOUT_MINUTES, MAX_FAILED_LOGINS, PASSWORD_MIN_LENGTH

_N, _R, _P = 2 ** 14, 8, 1
GENERIC_ERROR = "E-Mail-Adresse oder Passwort ist falsch."
_DUMMY_HASH = None  # für gleiche Antwortzeit bei unbekannten Adressen


# ------------------------------------------------------------------ Passwörter
def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        _, n, r, p, salt, dk = stored.split("$")
        test = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                              dklen=len(base64.b64decode(dk)))
        return hmac.compare_digest(test, base64.b64decode(dk))
    except (ValueError, TypeError):
        return False


def password_problems(password: str, email: str = "") -> list[str]:
    problems = []
    if len(password) < PASSWORD_MIN_LENGTH:
        problems.append(f"mindestens {PASSWORD_MIN_LENGTH} Zeichen")
    if password.isdigit() or password.isalpha():
        problems.append("Buchstaben und Ziffern oder Sonderzeichen mischen")
    if email and email.split("@")[0].lower() in password.lower():
        problems.append("darf nicht die E-Mail-Adresse enthalten")
    return problems


# ------------------------------------------------------------------ Login
def _is_locked(user: dict) -> bool:
    return bool(user.get("locked_until")) and datetime.fromisoformat(user["locked_until"]) > now_local()


def authenticate(email: str, password: str) -> tuple[dict | None, str]:
    """Prüft E-Mail + Passwort. Rückgabe: (Person oder None, Fehlermeldung)."""
    global _DUMMY_HASH
    user = db.get_user_by_email(email or "")
    if not user or not user["active"] or not user.get("password_hash"):
        _DUMMY_HASH = _DUMMY_HASH or hash_password("dummy-passwort")
        verify_password(password, _DUMMY_HASH)          # gleiche Rechenzeit, keine Info-Preisgabe
        return None, GENERIC_ERROR
    if _is_locked(user):
        return None, f"Zu viele Fehlversuche. Das Konto ist bis {user['locked_until'][11:16]} Uhr gesperrt."
    if verify_password(password, user["password_hash"]):
        db.register_successful_login(user["id"])
        return user, ""
    lock_until = (now_local() + timedelta(minutes=LOCKOUT_MINUTES)).isoformat(timespec="seconds")
    n = db.register_failed_login(user["id"], MAX_FAILED_LOGINS, lock_until)
    db.audit(user["id"], "Login fehlgeschlagen", f"Versuch {n}")
    return None, GENERIC_ERROR


# ------------------------------------------------------------------ Einladungscodes
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # ohne verwechselbare Zeichen (0/O, 1/I)


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").upper().encode()).hexdigest()


def create_invite(user_id: int, created_by: int | None = None) -> tuple[str, datetime]:
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(10))
    code = f"{raw[:5]}-{raw[5:]}"
    expires = now_local() + timedelta(days=INVITE_VALID_DAYS)
    db.add_invite_code(user_id, _code_hash(code), expires.isoformat(timespec="seconds"))
    db.audit(created_by, "Einladungscode erzeugt", f"für Person-ID {user_id}")
    return code, expires


def redeem_invite(email: str, code: str, new_password: str) -> tuple[bool, str]:
    user = db.get_user_by_email(email or "")
    error = "Code oder E-Mail-Adresse ungültig oder abgelaufen."
    if not user or not user["active"]:
        return False, error
    if _is_locked(user):
        return False, "Zu viele Fehlversuche. Bitte später erneut versuchen."
    invite = db.get_open_invite(user["id"])
    if (not invite or datetime.fromisoformat(invite["expires_at"]) < now_local()
            or not hmac.compare_digest(invite["code_hash"], _code_hash(code or ""))):
        lock_until = (now_local() + timedelta(minutes=LOCKOUT_MINUTES)).isoformat(timespec="seconds")
        db.register_failed_login(user["id"], MAX_FAILED_LOGINS, lock_until)
        return False, error
    problems = password_problems(new_password, email)
    if problems:
        return False, "Passwort: " + ", ".join(problems) + "."
    db.set_password_hash(user["id"], hash_password(new_password))
    db.mark_invite_used(invite["id"])
    db.audit(user["id"], "Passwort über Code gesetzt")
    return True, "Passwort gespeichert. Du kannst dich jetzt anmelden."


def change_password(user: dict, current: str, new: str) -> tuple[bool, str]:
    fresh = db.get_user(user["id"])
    if not verify_password(current, fresh.get("password_hash")):
        return False, "Aktuelles Passwort ist falsch."
    problems = password_problems(new, fresh["email"])
    if problems:
        return False, "Passwort: " + ", ".join(problems) + "."
    db.set_password_hash(user["id"], hash_password(new))
    db.audit(user["id"], "Passwort geändert")
    return True, "Passwort geändert."


# ------------------------------------------------------------------ Zwei-Faktor (TOTP)
def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_qr_png(secret: str, email: str) -> bytes:
    uri = pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="Schichtplaner")
    buf = io.BytesIO()
    segno.make(uri, error="m").save(buf, kind="png", scale=6, border=2)
    return buf.getvalue()


def verify_totp(secret: str | None, code: str) -> bool:
    if not secret or not code:
        return False
    return pyotp.TOTP(secret).verify(code.replace(" ", ""), valid_window=1)
