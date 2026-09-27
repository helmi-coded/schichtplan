"""Persönliches Dashboard: eigene Schichten, Sperrtage-Kalender, Präferenzen."""
import math

import pandas as pd
import streamlit as st

from core import db, deadline
from core.calendar_utils import (WEEKDAYS, WEEKDAYS_LONG, fmt_date, fmt_eur, fmt_num,
                                 month_label, month_weeks, parse_month, shift_hours, weekday)
from core.constants import (ALLOWED_TYPES, SHIFT_TYPES,
                            WEEKEND_EXCLUSION, WEIGHT_LABELS)
from core.planning import wage_for


def render(user: dict, month: str) -> None:
    st.header(f"Hallo {user['name'].split()[0]} – {month_label(month)}")
    status = db.get_plan_status(month)
    published = status == "VEROEFFENTLICHT"
    open_ = deadline.is_open(month)
    locked = published or not open_

    if published:
        _render_my_shifts(user, month)
        st.info("Der Plan für diesen Monat ist veröffentlicht.")
    elif open_:
        st.success(f"⏳ {deadline.describe(month)}. Bis dahin kannst du Sperrtage und Wünsche ändern.")
    else:
        st.warning(f"🔒 {deadline.describe(month)}. Die Leitung erstellt jetzt den Plan – "
                   "du siehst ihn hier, sobald er veröffentlicht ist.")

    st.subheader("Sperrtage")
    st.caption("Tippe auf einen Tag, um ihn zu sperren (rot) oder wieder freizugeben. "
               "🎭 = an diesem Tag gibt es passende Schichten.")
    _render_calendar(user, month, locked)

    st.subheader("Deine Angaben für den Monat")
    _render_preferences(user, month, locked)


# ------------------------------------------------------------------ Kalender
def _render_calendar(user: dict, month: str, locked: bool) -> None:
    blocked = db.get_blocked_days(user["id"], month)
    role = st.session_state.get(f"role_{month}") or db.get_preferences(user["id"], month)["role_choice"]
    shift_days = db.shift_dates_for_types(month, ALLOWED_TYPES[role])
    _, m = parse_month(month)

    with st.container(key="kalender"):
        header = st.columns(7)
        for i, wd in enumerate(WEEKDAYS):
            header[i].markdown(f"**{wd}**")
        for week in month_weeks(month):
            cols = st.columns(7)
            for i, d in enumerate(week):
                with cols[i]:
                    if d.month != m:
                        st.markdown("&nbsp;", unsafe_allow_html=True)
                        continue
                    iso = d.isoformat()
                    label = f"{d.day}{' 🎭' if iso in shift_days else ''}"
                    if st.button(label, key=f"day_{iso}", disabled=locked,
                                 type="primary" if iso in blocked else "secondary",
                                 help="gesperrt" if iso in blocked else "verfügbar"):
                        db.toggle_blocked_day(user["id"], iso)
                        st.rerun()
    st.caption(f"Gesperrte Tage: {len(blocked)}")


# ------------------------------------------------------------------ Präferenzen
CHOICE_LABELS = {"BEIDE": "Kasse & Einlass", "KASSE": "nur Kasse", "EINLASS": "nur Einlass"}


def _render_preferences(user: dict, month: str, locked: bool) -> None:
    p = db.get_preferences(user["id"], month)
    if p.get("carried_from") and not locked:
        st.info("Deine Angaben aus dem Vormonat sind vorausgefüllt. Bitte prüfen und speichern – "
                "erst dann gelten sie als abgegeben.")

    # Einsatzwunsch außerhalb des Formulars, damit sich die Felder darunter sofort anpassen
    choice = st.radio("**Was möchtest du diesen Monat machen?**", list(CHOICE_LABELS),
                      index=list(CHOICE_LABELS).index(p["role_choice"]), format_func=CHOICE_LABELS.get,
                      horizontal=True, disabled=locked, key=f"role_{month}")
    chosen = [t for t in SHIFT_TYPES if t in ALLOWED_TYPES[choice]]

    # Orientierung: wie viele Schichten passen unter die Minijob-Grenze?
    shifts = [s for s in db.list_shifts(month) if s["shift_type"] in chosen]
    if shifts and user["is_minijob"]:
        avg_h = sum(shift_hours(s["date"], s["start_time"], s["end_time"]) for s in shifts) / len(shifts)
        limit = float(db.get_setting("minijob_grenze"))
        cap = math.floor(limit / (wage_for(user) * avg_h)) if avg_h else None
        if cap:
            st.caption(f"Orientierung: Bei Ø {fmt_num(avg_h)} h pro Schicht passen rund **{cap} Schichten** "
                       f"unter die Minijob-Grenze von {fmt_eur(limit)}. Die App achtet automatisch darauf.")

    with st.form(f"prefs_{month}"):
        c1, c2 = st.columns(2)
        max_shifts = c1.number_input("Max. Schichten im Monat", min_value=0, max_value=31,
                                     value=int(p["max_shifts"] or 0), disabled=locked)
        max_hours = c2.number_input("Max. Stunden im Monat (optional)", min_value=0.0, max_value=200.0,
                                    value=None if p["max_hours"] is None else float(p["max_hours"]), step=1.0,
                                    format="%.2f", placeholder="keine Grenze", disabled=locked)
        needs_hours = st.checkbox("Ich brauche diesen Monat dringend Stunden", value=p["needs_hours"],
                                  disabled=locked, help="Wird bei der Verteilung bevorzugt berücksichtigt.")

        excl_keys = list(WEEKEND_EXCLUSION)
        weekend = st.radio("Wochenende (max. ein Tag ausschließbar)", excl_keys,
                           index=excl_keys.index(p["weekend_exclusion"]),
                           format_func=WEEKEND_EXCLUSION.get, horizontal=True, disabled=locked)

        st.markdown("**Wochentage**")
        wcols = st.columns(7)
        weights = []
        options = list(WEIGHT_LABELS)
        for i in range(7):
            weights.append(wcols[i].selectbox(WEEKDAYS_LONG[i], options,
                                              index=options.index(int(p["weekday_weights"][i])),
                                              format_func=WEIGHT_LABELS.get, disabled=locked,
                                              key=f"w_{month}_{i}"))

        st.markdown("**Aufteilung je Schichtart** (optional, leer = keine Obergrenze)")
        type_limits = {t: {"min": 0, "max": 0} for t in SHIFT_TYPES}   # nicht gewählte Arten ausschließen
        tcols = st.columns(len(chosen))
        for i, t in enumerate(chosen):
            with tcols[i]:
                st.markdown(SHIFT_TYPES[t])
                lim = p["type_limits"].get(t) or {}
                prev_max = lim.get("max")
                mn = st.number_input("mind.", 0, 31, int(lim.get("min") or 0), key=f"min_{month}_{t}",
                                     disabled=locked)
                mx = st.number_input("max.", 0, 31, None if prev_max in (None, 0) else int(prev_max),
                                     key=f"max_{month}_{t}", disabled=locked, placeholder="kein Limit")
                type_limits[t] = {"min": int(mn), "max": None if mx is None else int(mx)}

        if st.form_submit_button("Angaben speichern", type="primary", disabled=locked):
            errors = [f"{SHIFT_TYPES[t]}: Minimum größer als Maximum" for t, lim in type_limits.items()
                      if lim["max"] is not None and lim["min"] > lim["max"]]
            if sum(lim["min"] for lim in type_limits.values()) > max_shifts:
                errors.append("Die Summe der Mindestschichten übersteigt deine Schicht-Obergrenze.")
            if errors:
                for e in errors:
                    st.error(e)
            else:
                db.save_preferences(user["id"], month, {
                    "role_choice": choice, "max_shifts": int(max_shifts),
                    "max_hours": None if not max_hours else float(max_hours), "needs_hours": needs_hours,
                    "weekend_exclusion": weekend, "weekday_weights": [int(w) for w in weights],
                    "type_limits": type_limits,
                })
                st.success("Angaben gespeichert.")
    if p.get("saved"):
        st.caption(f"Zuletzt gespeichert: {p['updated_at'][:16].replace('T', ' ')}")
    elif not locked:
        st.caption("Noch nicht gespeichert.")


# ------------------------------------------------------------------ Eigene Schichten
def _render_my_shifts(user: dict, month: str) -> None:
    st.subheader("Meine Schichten")
    rows = db.get_assignments(month, user_id=user["id"])
    if not rows:
        st.write("Für diesen Monat bist du nicht eingeplant.")
        return
    wage = wage_for(user)
    hours = [shift_hours(r["date"], r["start_time"], r["end_time"]) for r in rows]
    df = pd.DataFrame([{
        "Datum": fmt_date(r["date"]),
        "Tag": WEEKDAYS[weekday(r["date"])],
        "Schicht": SHIFT_TYPES[r["shift_type"]],
        "Zeit": f"{r['start_time']}–{r['end_time']}",
        "Titel": r["title"] or "",
        "Stunden": fmt_num(h),
    } for r, h in zip(rows, hours)])
    st.dataframe(df, hide_index=True)
    st.write(f"**{len(rows)} Schichten · {fmt_num(sum(hours))} Stunden · "
             f"voraussichtlich {fmt_eur(sum(hours) * wage)} brutto**")
    st.download_button("📅 In meinen Kalender übernehmen (.ics)", _ics(rows),
                       file_name=f"Schichten_{month}.ics", mime="text/calendar")


def _ics(rows: list[dict]) -> bytes:
    """Kalenderdatei (iCalendar) für Handy/Outlook/Google; Zeiten in UTC."""
    from datetime import datetime, timezone
    from core.calendar_utils import TZ, shift_interval
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Schichtplaner//DE", "CALSCALE:GREGORIAN"]
    for r in rows:
        s, e = shift_interval(r["date"], r["start_time"], r["end_time"])
        s, e = (x.replace(tzinfo=TZ).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ") for x in (s, e))
        title = f"{SHIFT_TYPES[r['shift_type']]}" + (f" – {r['title']}" if r["title"] else "")
        lines += ["BEGIN:VEVENT", f"UID:schicht-{r['shift_id']}-{r['user_id']}@schichtplaner", f"DTSTAMP:{stamp}",
                  f"DTSTART:{s}", f"DTEND:{e}", f"SUMMARY:{title}", "LOCATION:Wallgraben Theater", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines).encode("utf-8")
