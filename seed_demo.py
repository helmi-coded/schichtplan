"""Demo-Daten für einen Testlauf (optional).  Aufruf:  python seed_demo.py [YYYY-MM]

Legt an: 1 Admin (admin@theater.example), 24 Minijobber, Aufführungen + Tageskasse
für den angegebenen Monat (Standard: Folgemonat), zufällige Sperrtage/Präferenzen
und eine Vormonats-Historie für den Rotations-Score.
"""
import random
import sys
from datetime import date

from core import auth, db
from core.calendar_utils import add_months, month_key, month_weeks, parse_month, previous_month
from core.constants import SHIFT_TYPES
from core.importer import apply_offset
import json

DEMO_PASSWORD = "Demo-Passwort-2026"  # nur für den Probelauf!
FIRST = ["Anna", "Ben", "Clara", "David", "Elif", "Finn", "Greta", "Hannes", "Ida", "Jonas", "Klara", "Luca",
         "Mia", "Noah", "Olga", "Paul", "Rosa", "Sami", "Tilda", "Uwe", "Vera", "Willi", "Yara", "Zoe"]


def main(month: str) -> None:
    random.seed(7)
    db.init_db()
    demo_hash = auth.hash_password(DEMO_PASSWORD)
    if db.get_user_by_email("admin@theater.example") is None:
        aid = db.upsert_user(None, "admin@theater.example", "Theaterleitung", "BEIDE", is_admin=True, plannable=False)
        db.set_password_hash(aid, demo_hash)

    roles = ["KASSE"] * 6 + ["EINLASS"] * 9 + ["BEIDE"] * 7 + ["TECHNIK"] * 2
    ids = []
    for i, (first, role) in enumerate(zip(FIRST, roles)):
        email = f"{first.lower()}@theater.example"
        u = db.get_user_by_email(email)
        uid = u["id"] if u else db.upsert_user(None, email, f"{first} Muster")
        if not u:
            db.set_password_hash(uid, demo_hash)
        ids.append(uid)

    # Schichten: Aufführungen Mi–So, Tageskasse Di–Sa
    db.delete_shifts_of_month(month)
    template = json.loads(db.get_setting("schicht_vorlage"))
    _, m = parse_month(month)
    for week in month_weeks(month):
        for d in week:
            if d.month != m:
                continue
            iso = d.isoformat()
            if 1 <= d.weekday() <= 5:
                db.add_shift(iso, "TAGESKASSE", "11:00", "14:00", 1)
            if d.weekday() >= 2:
                begin = "18:00" if d.weekday() == 6 else "19:30"
                title = random.choice(["Hamlet", "Die Physiker", "Nathan der Weise", "Woyzeck"])
                for t in template:
                    sd, st_ = apply_offset(iso, begin, t["start_offset_min"])
                    _, et = apply_offset(iso, begin, t["end_offset_min"])
                    db.add_shift(sd, t["schichtart"], st_, et, t["anzahl"], title)

    role_of = dict(zip(ids, roles))
    days = [d.isoformat() for w in month_weeks(month) for d in w if d.month == m]
    prev = previous_month(month)
    for uid in ids:
        db.set_blocked_days(uid, month, {d: random.choice(["GANZ", "GANZ", "TAG", "ABEND"])
                                         for d in random.sample(days, random.randint(2, 9))})
        prefs = db.default_preferences()
        prefs.update({
            "role_choice": role_of[uid],
            "max_shifts": random.choice([4, 5, 6, 8, 10]),
            "needs_hours": random.random() < 0.2,
            "weekend_exclusion": random.choice(["KEINE", "KEINE", "SA", "SO"]),
            "weekday_weights": [random.choice([-1, 0, 0, 1, 2]) for _ in range(7)],
        })
        db.save_preferences(uid, month, prefs)
        db.set_history(uid, prev, random.randint(0, 4), random.randint(3, 8))
    db.set_plan_status(month, "OFFEN")
    print(f"Demo-Daten für {month} angelegt: {len(ids)} Personen, {len(db.list_shifts(month))} Schichten.")
    print(f"Login Admin: admin@theater.example · Team z. B.: anna@theater.example · Passwort: {DEMO_PASSWORD}")
    print("Admin richtet beim ersten Login die 2FA-App per QR-Code ein.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else add_months(month_key(date.today()), 1))
