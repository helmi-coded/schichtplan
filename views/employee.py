"""Persönliches Dashboard: Angaben für den Monat, Verfügbarkeits-Kalender, eigene Schichten."""
import math

import pandas as pd
import streamlit as st

from core import db, deadline
from core.calendar_utils import (WEEKDAYS, WEEKDAYS_LONG, fmt_date, fmt_eur, fmt_num,
                                 month_label, month_weeks, parse_month, shift_hours, weekday)
from core.constants import (ALLOWED_TYPES, GROUP_LABELS, SHIFT_TYPES, TYPE_GROUPS,
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
        st.success(f"⏳ {deadline.describe(month)}. Bis dahin kannst du alles ändern.")
    else:
        st.warning(f"🔒 {deadline.describe(month)}. Die Leitung erstellt jetzt den Plan – "
                   "du siehst ihn hier, sobald er veröffentlicht ist.")

    st.subheader(f"Deine Angaben für {month_label(month)}")
    prefs, errors, p, k = _render_preferences(user, month, locked)

    number = 5 if prefs["role_choice"] == "BEIDE" else 4
    st.markdown(f"**{number}. An welchen Tagen kannst du nicht – oder nur teilweise?**")
    _render_calendar(user, month, locked, prefs["role_choice"])

    _render_summary_and_save(user, month, prefs, errors, p, k, locked)


# ------------------------------------------------------------------ Kalender
# Auswahl je Tag -> gespeicherter Tagesteil (None = kann ganztags)
OPT_TO_PART = {"✓": None, "☀": "ABEND", "🌙": "TAG", "✕": "GANZ"}
OPTS_FULL = ["✓", "☀", "🌙", "✕"]          # bei Kasse: Tageskasse (tagsüber) vs. abends
OPTS_SIMPLE = ["✓", "✕"]                    # nur Einlass: gibt es nur abends
PART_COLORS = {"GANZ": ("#FDE2E1", "#C62828"), "TAG": ("#FFF4D6", "#E0A100"), "ABEND": ("#FFF4D6", "#E0A100")}


def _option_for(part: str | None, simple: bool) -> str:
    if simple:   # nur Einlass (abends): "nur tagsüber" heißt praktisch "kann nicht"
        return "✕" if part in ("GANZ", "ABEND") else "✓"
    return {None: "✓", "ABEND": "☀", "TAG": "🌙", "GANZ": "✕"}[part]


def _blocked_keys(user_id: int, month: str) -> tuple[str, str]:
    return f"blocked_{month}_{user_id}", f"blocked_saved_{month}_{user_id}"


def _render_calendar(user: dict, month: str, locked: bool, role: str) -> None:
    """Verfügbarkeiten werden im Browser gesammelt und erst beim Speichern in die
    Datenbank geschrieben – dadurch reagiert der Kalender sofort."""
    cur_key, saved_key = _blocked_keys(user["id"], month)
    if cur_key not in st.session_state:
        saved = db.get_blocked_days(user["id"], month)
        st.session_state[saved_key] = dict(saved)
        st.session_state[cur_key] = dict(saved)
    simple = role in ("EINLASS", "TECHNIK")          # beides gibt es nur abends bei Vorstellungen
    if simple:
        st.caption("Stell bei jedem Tag, an dem du **nicht** kannst, **✕** ein – der Tag wird rot. "
                   "🎭 = Vorstellung (nur an diesen Tagen wird "
                   f"{'Technik' if role == 'TECHNIK' else 'Einlass'} gebraucht).")
    else:
        st.caption("Pro Tag: **✓** kann ganztags · **☀** nur tagsüber (Tageskasse) · "
                   "**🌙** nur abends (Abendkasse/Einlass) · **✕** gar nicht. "
                   "Rot = gar nicht, gelb = nur teilweise. 🎭 = Vorstellung.")
    shift_days = db.shift_dates_for_types(month, {"ABENDKASSE", "EINLASS", "TECHNIK"})
    _calendar_fragment(user["id"], month, shift_days, locked, simple)


@st.fragment
def _calendar_fragment(user_id: int, month: str, shift_days: set[str], locked: bool, simple: bool) -> None:
    """Fragment: Änderungen laden nur den Kalender neu, nicht die ganze Seite."""
    cur_key, saved_key = _blocked_keys(user_id, month)
    cur: dict[str, str] = st.session_state[cur_key]
    options = OPTS_SIMPLE if simple else OPTS_FULL
    mode = "s" if simple else "f"
    _, m = parse_month(month)
    days_in_month = [d for w in month_weeks(month) for d in w if d.month == m]

    def _free_all() -> None:  # noqa: D401
        cur.clear()
        for d in days_in_month:
            st.session_state[f"day_{month}_{user_id}_{mode}_{d.isoformat()}"] = "✓"

    colored: dict[str, str] = {}
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
                    wkey = f"day_{month}_{user_id}_{mode}_{iso}"
                    if wkey not in st.session_state:
                        st.session_state[wkey] = _option_for(cur.get(iso), simple)
                    with st.container(key=f"cal_{iso}"):
                        st.markdown(f"**{d.day}**{' 🎭' if iso in shift_days else ''}")
                        val = st.selectbox(f"{d.day}.", options, key=wkey, disabled=locked,
                                           label_visibility="collapsed")
                    part = OPT_TO_PART[val]
                    if part:
                        cur[iso] = part
                        colored[iso] = part
                    else:
                        cur.pop(iso, None)

    # Tage einfärben: rot = gar nicht, gelb = nur teilweise
    css = [f".st-key-cal_{iso} {{ background:{PART_COLORS[p][0]}; border:1px solid {PART_COLORS[p][1]}; "
           f"border-radius:8px; }}" for iso, p in colored.items()]
    if css:
        st.markdown("<style>" + " ".join(css) + "</style>", unsafe_allow_html=True)

    n_full = sum(1 for p in cur.values() if p == "GANZ")
    n_part = len(cur) - n_full
    text = f"**{n_full} Tag(e) gar nicht**" + (f", **{n_part} Tag(e) nur teilweise**" if n_part and not simple else "")
    if cur != st.session_state[saved_key]:
        text += " – noch nicht gespeichert"
    c1, c2 = st.columns([3, 2])
    c1.caption(text)
    if not locked and cur:
        c2.button("Alle Tage freigeben", on_click=_free_all, key=f"free_all_{month}_{user_id}")


# ------------------------------------------------------------------ Präferenzen
CHOICE_LABELS = {"BEIDE": "Kasse & Einlass", "KASSE": "nur Kasse", "EINLASS": "nur Einlass", "TECHNIK": "Technik"}
EXCLUDED_DAY = {"SA": 5, "SO": 6}


def summary_text(pr: dict) -> str:
    """Fasst die Angaben in einem Satz zusammen – so, wie die Planung sie versteht."""
    parts = [{"BEIDE": "Du machst diesen Monat **Kasse und Einlass**",
              "KASSE": "Du machst diesen Monat **nur Kasse** (Tages- und Abendkasse)",
              "EINLASS": "Du machst diesen Monat **nur Einlass**",
              "TECHNIK": "Du machst diesen Monat **Technik**"}[pr["role_choice"]]]
    mn, mx = pr["min_shifts"], pr["max_shifts"]
    if mx == 0:
        parts.append("aber **keine Schichten** (du setzt diesen Monat aus)")
    elif mn:
        parts.append(f"**{mn} bis {mx} Schichten** ({mn} als Wunsch, {mx} garantiert höchstens)")
    else:
        parts.append(f"**höchstens {mx} Schichten**")
    if pr["max_hours"]:
        parts.append(f"höchstens **{fmt_num(pr['max_hours'])} Stunden**")
    text = ", ".join(parts) + "."

    days = []
    if pr["weekend_exclusion"] == "SA":
        days.append("**samstags nie**")
    if pr["weekend_exclusion"] == "SO":
        days.append("**sonntags nie**")
    gern = [WEEKDAYS_LONG[i] for i, w in enumerate(pr["weekday_weights"]) if w > 0]
    ungern = [WEEKDAYS_LONG[i] for i, w in enumerate(pr["weekday_weights"])
              if w < 0 and EXCLUDED_DAY.get(pr["weekend_exclusion"]) != i]
    if gern:
        days.append("gern " + ", ".join(gern))
    if ungern:
        days.append("möglichst nicht " + ", ".join(ungern))
    if days:
        joined = "; ".join(days)
        idx = next(i for i, ch in enumerate(joined) if ch.isalpha())      # ersten Buchstaben groß
        text += " " + joined[:idx] + joined[idx].upper() + joined[idx + 1:] + "."
    if pr["role_choice"] == "BEIDE":
        split = []
        for g, lim in pr["type_limits"].items():
            if lim.get("min") or lim.get("max") is not None:
                rng = (f"{lim['min']}–{lim['max']}" if lim.get("min") and lim.get("max") is not None
                       else f"mind. {lim['min']}" if lim.get("min") else f"höchstens {lim['max']}")
                split.append(f"{GROUP_LABELS[g]} {rng}")
        if split:
            text += " Aufteilung: " + ", ".join(split) + "."
    if pr["needs_hours"]:
        text += " Du wirst **bevorzugt eingeplant**, weil du dringend Stunden brauchst."
    return text


def _render_preferences(user: dict, month: str, locked: bool) -> tuple[dict, list[str], dict, str]:
    """Fragen 1–4. Gibt (Angaben, Fehler, gespeicherte Angaben, Schlüssel) zurück."""
    p = db.get_preferences(user["id"], month)
    k = f"{month}_{user['id']}"                       # eindeutige Schlüssel je Monat
    if p.get("carried_from") and not locked:
        st.info("Deine Angaben aus dem Vormonat sind vorausgefüllt. Bitte prüfen und speichern – "
                "erst dann gelten sie als abgegeben.")

    # 1) Einsatz
    choice = st.radio("**1. Was möchtest du diesen Monat machen?**", list(CHOICE_LABELS),
                      index=list(CHOICE_LABELS).index(p["role_choice"]), format_func=CHOICE_LABELS.get,
                      horizontal=True, disabled=locked, key=f"role_{month}",
                      help="Kasse umfasst Tages- und Abendkasse. Technik gibt es nur an Vorstellungstagen.")
    chosen = [t for t in SHIFT_TYPES if t in ALLOWED_TYPES[choice]]

    # 2) Umfang
    st.markdown("**2. Wie viel möchtest du arbeiten?**")
    c1, c2, c3 = st.columns(3)
    min_shifts = c1.number_input("Wunsch: mindestens … Schichten", 0, 31, int(p["min_shifts"] or 0),
                                 disabled=locked, key=f"min_total_{k}",
                                 help="Die Planung versucht das zu erreichen – garantiert ist es nicht, "
                                      "z. B. wenn es an deinen freien Tagen zu wenige Schichten gibt.")
    max_shifts = c2.number_input("Höchstens … Schichten", 0, 31, int(p["max_shifts"] or 0),
                                 disabled=locked, key=f"max_total_{k}",
                                 help="Feste Grenze – mehr Schichten bekommst du garantiert nicht. 0 = aussetzen.")
    max_hours = c3.number_input("Höchstens … Stunden (optional)", min_value=0.0, max_value=200.0,
                                value=None if p["max_hours"] is None else float(p["max_hours"]),
                                step=1.0, format="%.2f", placeholder="keine Grenze", disabled=locked,
                                key=f"max_hours_{k}", help="Feste Grenze, z. B. wegen eines zweiten Jobs.")
    shifts = [s for s in db.list_shifts(month) if s["shift_type"] in chosen]
    if shifts and user["is_minijob"]:
        avg_h = sum(shift_hours(s["date"], s["start_time"], s["end_time"]) for s in shifts) / len(shifts)
        limit = float(db.get_setting("minijob_grenze"))
        cap = math.floor(limit / (wage_for(user) * avg_h)) if avg_h else None
        if cap:
            st.caption(f"Zur Orientierung: Eine Schicht dauert im Schnitt {fmt_num(avg_h)} Stunden. "
                       f"Unter die Minijob-Grenze von {fmt_eur(limit)} passen rund {cap} Schichten – "
                       "darauf achtet die App automatisch.")
    needs_hours = st.checkbox("Ich brauche diesen Monat dringend Stunden (werde bevorzugt eingeplant)",
                              value=p["needs_hours"], disabled=locked, key=f"needs_{k}")

    # 3) Tage
    st.markdown("**3. Wann kannst du?**")
    st.caption("Einzelne Tage, an denen du nicht oder nur teilweise kannst, trägst du unten im Kalender ein.")
    excl_keys = list(WEEKEND_EXCLUSION)
    weekend = st.radio("Wochenende", excl_keys, index=excl_keys.index(p["weekend_exclusion"]),
                       format_func=WEEKEND_EXCLUSION.get, horizontal=True, disabled=locked,
                       key=f"weekend_{k}", help="Ein Wochenendtag kann komplett ausgeschlossen werden.")
    st.markdown("Wie gern an welchem Wochentag?")
    wcols = st.columns(7)
    weights, options = [], list(WEIGHT_LABELS)
    for i in range(7):
        if EXCLUDED_DAY.get(weekend) == i:              # ausgeschlossener Tag: nur Anzeige
            wcols[i].text_input(WEEKDAYS_LONG[i], "nie", disabled=True, key=f"w_fix_{k}_{i}")
            weights.append(-2)
            continue
        weights.append(wcols[i].selectbox(WEEKDAYS_LONG[i], options,
                                          index=options.index(int(p["weekday_weights"][i])),
                                          format_func=WEIGHT_LABELS.get, disabled=locked, key=f"w_{k}_{i}"))

    # 4) Aufteilung – nur bei "Kasse & Einlass"
    type_limits = {g: {"min": 0, "max": None} for g in TYPE_GROUPS}
    if choice == "BEIDE":
        st.markdown("**4. Aufteilung zwischen Kasse und Einlass** (optional)")
        st.caption("Leer lassen = egal, die Planung verteilt frei.")
        gcols = st.columns(2)
        for i, g in enumerate(TYPE_GROUPS):
            lim = p["type_limits"].get(g) or {}
            with gcols[i]:
                st.markdown(f"*{GROUP_LABELS[g]}*")
                a, b = st.columns(2)
                g_min = a.number_input("Wunsch: mind.", 0, 31, int(lim.get("min") or 0),
                                       key=f"gmin_{k}_{g}", disabled=locked)
                g_max = b.number_input("höchstens", 0, 31, lim.get("max"), key=f"gmax_{k}_{g}",
                                       disabled=locked, placeholder="egal")
                type_limits[g] = {"min": int(g_min), "max": None if g_max is None else int(g_max)}

    prefs = {"role_choice": choice, "min_shifts": int(min_shifts), "max_shifts": int(max_shifts),
             "max_hours": float(max_hours) if max_hours else None, "needs_hours": needs_hours,
             "weekend_exclusion": weekend, "weekday_weights": [int(w) for w in weights],
             "type_limits": type_limits}

    # Prüfungen
    errors = []
    if min_shifts > max_shifts:
        errors.append("„Mindestens“ ist größer als „höchstens“.")
    for g, lim in type_limits.items():
        if lim["max"] is not None and lim["min"] > lim["max"]:
            errors.append(f"{GROUP_LABELS[g]}: „mind.“ ist größer als „höchstens“.")
    if sum(lim["min"] for lim in type_limits.values()) > max_shifts:
        errors.append("Die Wunsch-Mindestzahlen bei Kasse und Einlass sind zusammen größer als deine Höchstzahl.")

    return prefs, errors, p, k


def _save(user_id: int, month: str, prefs: dict, k: str) -> None:
    cur_key, saved_key = _blocked_keys(user_id, month)
    blocked = dict(st.session_state.get(cur_key, {}))     # aktueller Stand aus dem Kalender
    db.save_preferences(user_id, month, prefs)
    db.set_blocked_days(user_id, month, blocked)
    st.session_state[saved_key] = dict(blocked)
    st.session_state[f"just_saved_{k}"] = True


def _render_summary_and_save(user: dict, month: str, prefs: dict, errors: list[str], p: dict, k: str,
                             locked: bool) -> None:
    st.markdown("**So wirst du eingeplant:**")
    st.info(summary_text(prefs) + " Einzelne Tage wie im Kalender angegeben.")
    for e in errors:
        st.error(e)
    # Speichern als Callback: läuft VOR dem nächsten Seitenaufbau, damit alle Anzeigen sofort stimmen
    st.button("Angaben speichern", type="primary", disabled=locked or bool(errors), key=f"save_{k}",
              on_click=_save, args=(user["id"], month, prefs, k))
    if st.session_state.pop(f"just_saved_{k}", False):
        st.success("Gespeichert – danke! Du kannst bis zur Anmeldefrist noch alles ändern.")
        p = db.get_preferences(user["id"], month)
    if p.get("saved"):
        st.caption(f"Zuletzt gespeichert: {p['updated_at'][:16].replace('T', ' ')} Uhr")
    elif not locked:
        st.warning("Noch nicht gespeichert.")


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
