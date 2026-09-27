"""Tests für Login-Sicherheit, Frist und Spielplan-Parser.  python tests/test_security_spielplan.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["SCHICHTPLANER_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")

import pyotp  # noqa: E402
from core import auth, db, deadline, spielplan  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "spielplan_wallgraben.html")


def setup():
    db.init_db()
    uid = db.upsert_user(None, "test@example.de", "Test", "BEIDE")
    return uid


def test_password_and_lockout(uid):
    assert auth.password_problems("kurz") and not auth.password_problems("Sicher-Passwort-7")
    db.set_password_hash(uid, auth.hash_password("Sicher-Passwort-7"))
    assert "scrypt$" in db.get_user(uid)["password_hash"]            # kein Klartext
    assert auth.authenticate("test@example.de", "Sicher-Passwort-7")[0]
    assert auth.authenticate("unbekannt@example.de", "x")[0] is None
    for _ in range(5):
        auth.authenticate("test@example.de", "falsch")
    user, msg = auth.authenticate("test@example.de", "Sicher-Passwort-7")
    assert user is None and "gesperrt" in msg                        # Sperre nach 5 Fehlversuchen


def test_invite():
    uid2 = db.upsert_user(None, "neu@example.de", "Neu", "KASSE")
    code, _ = auth.create_invite(uid2)
    assert not auth.redeem_invite("neu@example.de", "FALSCH-CODE", "Sicher-Passwort-7")[0]
    assert auth.redeem_invite("neu@example.de", code, "Sicher-Passwort-7")[0]
    assert not auth.redeem_invite("neu@example.de", code, "Anderes-Passwort-8")[0]   # nur einmal gültig


def test_totp():
    secret = auth.new_totp_secret()
    assert auth.verify_totp(secret, pyotp.TOTP(secret).now())
    assert not auth.verify_totp(secret, "000000") or pyotp.TOTP(secret).now() == "000000"
    assert auth.totp_qr_png(secret, "a@b.de")[:4] == b"\x89PNG"


def test_deadline():
    dl = deadline.deadline_for("2026-11")
    assert (dl.day, dl.month, dl.hour) == (25, 10, 23)               # 7 Tage vor dem 01.11.
    db.set_deadline_override("2026-11", "2026-10-28")
    assert deadline.deadline_for("2026-11").day == 28


def test_spielplan():
    perfs = spielplan.parse(open(FIXTURE, encoding="utf-8").read())
    assert [(p.date, p.time) for p in perfs][:2] == [("2026-10-09", "19:30"), ("2026-10-19", "19:30")]
    assert all("KOMBITICKET" not in p.title for p in perfs)
    pv = spielplan.preview("2026-10", perfs)
    assert len(pv.new) == 4
    spielplan.apply(pv, published=False)
    assert len(db.list_performances("2026-10")) == 4
    assert db.count_shifts_of_type("2026-10", "TAGESKASSE") == 26     # Mo–Sa im Oktober 2026 ohne 03.10.
    assert db.count_shifts_of_type("2026-10", "TECHNIK") == 4         # 1 Technik je Vorstellung
    pv2 = spielplan.preview("2026-10", perfs[1:])                    # ein Termin entfällt
    assert len(pv2.removed) == 1 and not pv2.new
    spielplan.apply(pv2, published=False)
    assert len(db.list_performances("2026-10")) == 3


if __name__ == "__main__":
    uid = setup()
    test_password_and_lockout(uid)
    test_invite()
    test_totp()
    test_deadline()
    test_spielplan()
    print("Alle Sicherheits-, Frist- und Spielplan-Tests bestanden.")
