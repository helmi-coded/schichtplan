"""Admin-Bereich: Team, Termine & Schichten, Planung, Stundenkonto, Rotation, Einstellungen."""
import json
from datetime import date

import pandas as pd
import streamlit as st

from core import auth, db, deadline, importer, mailer, planning, spielplan
from core.calendar_utils import (WEEKDAYS, now_local, fmt_date, fmt_eur, month_label, month_weeks,
                                 parse_month, previous_month, weekday)
from core.constants import ALLOWED_TYPES, PLAN_STATUS, SHIFT_TYPES

TYPE_BY_LABEL = {v: k for k, v in SHIFT_TYPES.items()}


def _is_empty(v) -> bool:
    return v is None or (isinstance(v, float) and pd.isna(v)) or (isinstance(v, str) and not v.strip())


def render(user: dict, month: str) -> None:
    st.header(f"Verwaltung – {month_label(month)}")
    status = db.get_plan_status(month)
    st.caption(f"Planstatus: **{PLAN_STATUS[status]}**")
    tabs = st.tabs(["Planung", "Termine & Schichten", "Team", "Stundenkonto", "Rotation", "Einstellungen",
                    "Protokoll"])
    with tabs[0]:
        _tab_planning(month, status, user)
    with tabs[1]:
        _tab_shifts(month)
    with tabs[2]:
        _tab_team(user)
    with tabs[3]:
        _tab_hours(month)
    with tabs[4]:
        _tab_history(month)
    with tabs[5]:
        _tab_settings()
    with tabs[6]:
        st.caption("Sicherheitsrelevante Aktionen (Anmeldungen, Fehlversuche, Planerstellung, Veröffentlichung).")
        st.dataframe(pd.DataFrame(db.list_audit()), hide_index=True)


# ------------------------------------------------------------------ Planung
def _tab_planning(month: str, status: str, admin: dict) -> None:
    team = db.list_users(plannable_only=True)
    shifts = db.list_shifts(month)
    submitted = db.users_with_preferences(month)
    missing = [u["name"] for u in team if u["id"] not in submitted]

    c1, c2, c3 = st.columns(3)
    c1.metric("Schichten", len(shifts))
    c2.metric("Plätze gesamt", sum(s["required"] for s in shifts))
    c3.metric("Präferenzen abgegeben", f"{len(team) - len(missing)} / {len(team)}")
    if missing:
        with st.expander(f"Noch keine Präferenzen von {len(missing)} Person(en)"):
            st.write(", ".join(missing))
            st.caption("Für diese Personen gelten Standardwerte; Sperrtage werden trotzdem berücksichtigt.")
            if mailer.is_configured() and deadline.is_open(month) and st.button("Erinnerung per E-Mail senden"):
                emails = [u["email"] for u in team if u["id"] not in submitted]
                for e in emails:
                    mailer.send(e, f"Schichtplan {month_label(month)}: bitte Wünsche eintragen",
                                f"Hallo,\n\nbitte trage deine Sperrtage und Wünsche für {month_label(month)} ein. "
                                f"{deadline.describe(month)}.\n\n{mailer.app_url()}")
                db.audit(admin["id"], "Erinnerung gesendet", f"{month}: {len(emails)} Personen")
                st.success(f"Erinnerung an {len(emails)} Person(en) gesendet.")

    # Anmeldefrist
    open_ = deadline.is_open(month)
    (st.info if open_ else st.success)(("⏳ " if open_ else "✅ ") + deadline.describe(month) +
                                       (" – danach kann der Plan erstellt werden." if open_ else " – der Plan kann erstellt werden."))
    with st.expander("Frist für diesen Monat verschieben"):
        new_dl = st.date_input("Letzter Tag für Präferenzen", value=deadline.deadline_for(month).date(),
                               format="DD.MM.YYYY", key=f"dl_{month}")
        c1, c2 = st.columns(2)
        if c1.button("Frist speichern"):
            db.set_deadline_override(month, new_dl.isoformat())
            db.audit(admin["id"], "Frist geändert", f"{month}: {new_dl.isoformat()}")
            st.rerun()
        if c2.button("Standardfrist wiederherstellen"):
            db.set_deadline_override(month, None)
            st.rerun()

    if not shifts or not team:
        st.info("Lege zuerst Schichten (Tab „Termine & Schichten“) und Team-Mitglieder (Tab „Team“) an.")
        return

    early = False
    if open_ and status != "VEROEFFENTLICHT":
        early = st.checkbox("Probelauf vor Fristende erlauben (Wünsche können sich noch ändern)")
    label = "Plan neu erstellen" if status != "OFFEN" else "Plan erstellen"
    if st.button(label, type="primary", disabled=status == "VEROEFFENTLICHT" or (open_ and not early)):
        with st.spinner("Optimiere die Zuteilung …"):
            result = planning.run_planning(month)
        db.audit(admin["id"], "Plan erstellt", f"{month}: {result.status}, Lücken {sum(result.unfilled.values())}"
                 + (" (Probelauf vor Fristende)" if open_ else ""))
        st.session_state[f"last_result_{month}"] = result
        st.rerun()

    result = st.session_state.get(f"last_result_{month}")
    if result:
        st.success(f"Ergebnis: {result.status} · Rechenzeit {result.wall_time:.1f} s")
        for h in result.hints:
            st.warning(h)

    overview = planning.plan_overview(month)
    if db.get_assignments(month):
        gaps = int(overview["Lücke"].sum())
        st.markdown(f"**Planübersicht** · offene Plätze: {gaps}")
        st.dataframe(overview, hide_index=True, column_config={
            "Lücke": st.column_config.NumberColumn(format="%d"),
        })
        _manual_correction(month, status)

        csv = overview.to_csv(sep=";", index=False).encode("utf-8-sig")
        st.download_button("Plan als CSV exportieren", csv,
                           file_name=f"{date.today().isoformat()}_Schichtplan_{month}.csv", mime="text/csv")

        if status == "ENTWURF":
            if gaps:
                st.caption("Hinweis: Es sind noch Plätze offen. Du kannst trotzdem veröffentlichen.")
            if open_:
                st.caption("Veröffentlichen ist erst nach Ablauf der Anmeldefrist möglich.")
            with st.form("publish"):
                st.markdown("**Plan veröffentlichen** – Bestätigung mit deinem 2FA-Code")
                code = st.text_input("2FA-Code", max_chars=7, autocomplete="one-time-code")
                if st.form_submit_button("Plan veröffentlichen", type="primary", disabled=open_):
                    if auth.verify_totp(db.get_user(admin["id"])["totp_secret"], code):
                        planning.publish(month)
                        db.audit(admin["id"], "Plan veröffentlicht", month)
                        st.rerun()
                    else:
                        db.audit(admin["id"], "Veröffentlichung abgelehnt (2FA falsch)", month)
                        st.error("2FA-Code ungültig.")
        if status == "VEROEFFENTLICHT" and st.button("Veröffentlichung zurücknehmen"):
            db.set_plan_status(month, "ENTWURF")
            db.audit(admin["id"], "Veröffentlichung zurückgenommen", month)
            st.rerun()


def _manual_correction(month: str, status: str) -> None:
    with st.expander("Einzelne Schicht manuell anpassen"):
        shifts = db.list_shifts(month)
        users = db.list_users(plannable_only=True)
        name_by_id = {u["id"]: u["name"] for u in users}
        shift = st.selectbox("Schicht", shifts, format_func=planning.shift_label, key="mc_shift")
        current = [a["user_id"] for a in db.get_assignments(month) if a["shift_id"] == shift["id"]]
        chosen = st.multiselect("Eingeteilt", list(name_by_id), default=[u for u in current if u in name_by_id],
                                format_func=name_by_id.get, key=f"mc_users_{shift['id']}")
        blocked = db.get_all_blocked(month)
        for uid in chosen:
            u = next(x for x in users if x["id"] == uid)
            if shift["shift_type"] not in ALLOWED_TYPES[db.get_preferences(uid, month)["role_choice"]]:
                st.warning(f"{u['name']} möchte diesen Monat keine {SHIFT_TYPES[shift['shift_type']]}-Schichten.")
            if shift["date"] in blocked.get(uid, set()):
                st.warning(f"{u['name']} hat diesen Tag gesperrt.")
        if len(chosen) > shift["required"]:
            st.warning(f"Mehr Personen ({len(chosen)}) als benötigt ({shift['required']}).")
        if st.button("Zuteilung speichern", key="mc_save"):
            db.set_shift_assignment(shift["id"], chosen)
            if status == "VEROEFFENTLICHT":
                db.write_history_from_assignments(month)
            st.success("Gespeichert.")
            st.rerun()


# ------------------------------------------------------------------ Termine & Schichten
def _tab_shifts(month: str) -> None:
    template = json.loads(db.get_setting("schicht_vorlage"))
    status = db.get_plan_status(month)

    st.subheader("Online-Spielplan übernehmen")
    url = db.get_setting("spielplan_url")
    st.caption(f"Quelle: {url}. Neue Aufführungen werden mit Schichten laut Vorlage angelegt, entfallene "
               "entfernt. Die Tageskasse wird für den Monat einmalig ergänzt.")
    if st.button(f"Spielplan für {month_label(month)} abrufen", type="primary"):
        try:
            st.session_state["spielplan_perfs"] = spielplan.parse(spielplan.fetch_html(url))
        except Exception as exc:  # noqa: BLE001 – Netzwerkfehler verständlich anzeigen
            st.error(f"Spielplan konnte nicht abgerufen werden: {exc}. Alternativ CSV-Import unten nutzen.")
    perfs = st.session_state.get("spielplan_perfs")
    if perfs is not None:
        pv = spielplan.preview(month, perfs)
        if not pv.fetched:
            st.warning(f"Auf der Website stehen (noch) keine Termine für {month_label(month)}. "
                       f"Insgesamt gefunden: {len(perfs)} Termine.")
        else:
            st.write(f"{len(pv.fetched)} Termine gefunden · neu: {len(pv.new)} · entfallen: {len(pv.removed)} · "
                     f"unverändert: {pv.unchanged}")
            st.dataframe(pd.DataFrame([{
                "Datum": fmt_date(p.date), "Tag": WEEKDAYS[weekday(p.date)], "Beginn": p.time, "Titel": p.title,
                "Status": "neu" if p in pv.new else "vorhanden",
            } for p in pv.fetched] + [{
                "Datum": fmt_date(p["date"]), "Tag": WEEKDAYS[weekday(p["date"])], "Beginn": p["time"],
                "Titel": p["title"], "Status": "entfällt",
            } for p in pv.removed]), hide_index=True)
        if st.button("Änderungen übernehmen", disabled=not (pv.new or pv.removed or pv.fetched)):
            notes = spielplan.apply(pv, published=status == "VEROEFFENTLICHT")
            db.set_setting(f"spielplan_abgleich_{month}", now_local().strftime("%d.%m.%y %H:%M"))
            db.audit(st.session_state.get("user_id"), "Spielplan übernommen",
                     f"{month}: +{len(pv.new)} / -{len(pv.removed)}")
            st.session_state.pop("spielplan_perfs", None)
            st.session_state["spielplan_notes"] = notes or ["Keine Änderungen."]
            st.rerun()
    for n in st.session_state.pop("spielplan_notes", []):
        st.info(n)
    last = db.get_setting(f"spielplan_abgleich_{month}")
    if last:
        st.caption(f"Letzter Abgleich: {last}")

    with st.expander("Tageskasse-Vorlage"):
        tk = json.loads(db.get_setting("tageskasse_vorlage"))
        with st.form("tk_form"):
            aktiv = st.checkbox("Tageskasse automatisch anlegen", value=tk["aktiv"])
            days = st.multiselect("Wochentage", list(range(7)), default=tk["wochentage"],
                                  format_func=lambda i: WEEKDAYS[i])
            c1, c2, c3 = st.columns(3)
            beginn = c1.text_input("Beginn", tk["beginn"])
            ende = c2.text_input("Ende", tk["ende"])
            anzahl = c3.number_input("Anzahl", 1, 5, int(tk["anzahl"]))
            if st.form_submit_button("Vorlage speichern"):
                try:
                    beginn, ende = importer.parse_time(beginn), importer.parse_time(ende)
                    db.set_setting("tageskasse_vorlage", json.dumps(
                        {"aktiv": aktiv, "wochentage": days, "beginn": beginn, "ende": ende, "anzahl": int(anzahl)}))
                    st.success("Gespeichert.")
                except ValueError as exc:
                    st.error(str(exc))

    st.subheader("Import (CSV) – Alternative")
    st.caption("Aufführungsliste (datum;titel;beginn) → Schichten werden über die Vorlage erzeugt. "
               "Schichtliste (datum;titel;schichtart;beginn;ende;anzahl) → wird direkt übernommen.")
    c1, c2 = st.columns(2)
    c1.download_button("Beispiel Aufführungen", importer.SAMPLE_PERFORMANCES.encode("utf-8-sig"),
                       file_name="beispiel_auffuehrungen.csv", mime="text/csv")
    c2.download_button("Beispiel Schichtliste", importer.SAMPLE_SHIFTS.encode("utf-8-sig"),
                       file_name="beispiel_schichten.csv", mime="text/csv")

    upload = st.file_uploader("CSV-Datei", type=["csv", "txt"])
    if upload:
        try:
            df = importer.read_csv(upload)
        except Exception as exc:  # noqa: BLE001 – Nutzerfeedback statt Absturz
            st.error(f"Datei konnte nicht gelesen werden: {exc}")
            return
        new_shifts, errors = importer.build_shifts(df, template)
        for e in errors:
            st.error(e)
        if new_shifts:
            months = sorted({s["date"][:7] for s in new_shifts})
            st.write(f"{len(new_shifts)} Schichten erkannt · Monate: {', '.join(month_label(m) for m in months)}")
            st.dataframe(pd.DataFrame([{
                "Datum": fmt_date(s["date"]), "Schicht": SHIFT_TYPES[s["shift_type"]],
                "Zeit": f"{s['start']}–{s['end']}", "Anzahl": s["required"], "Titel": s["title"],
            } for s in new_shifts]), hide_index=True)
            replace = st.checkbox("Vorhandene Schichten dieser Monate vorher löschen "
                                  "(inkl. bestehender Zuteilungen)")
            if st.button("Importieren", type="primary"):
                if replace:
                    for m in months:
                        db.delete_shifts_of_month(m)
                for s in new_shifts:
                    db.add_shift(s["date"], s["shift_type"], s["start"], s["end"], s["required"], s["title"])
                st.success(f"{len(new_shifts)} Schichten importiert.")
                st.rerun()

    with st.expander("Schicht-Vorlage für Aufführungen bearbeiten"):
        st.caption("Offsets in Minuten relativ zum Vorstellungsbeginn (−60 = eine Stunde vorher).")
        tdf = pd.DataFrame([{**t, "schichtart": SHIFT_TYPES[t["schichtart"]]} for t in template])
        edited = st.data_editor(tdf, num_rows="dynamic", hide_index=True, key="tpl_editor", column_config={
            "schichtart": st.column_config.SelectboxColumn("Schichtart", options=list(TYPE_BY_LABEL), required=True),
            "start_offset_min": st.column_config.NumberColumn("Beginn (min)", step=5, required=True),
            "end_offset_min": st.column_config.NumberColumn("Ende (min)", step=5, required=True),
            "anzahl": st.column_config.NumberColumn("Anzahl", min_value=1, step=1, required=True),
        })
        if st.button("Vorlage speichern"):
            rows = [r for r in edited.to_dict("records") if not _is_empty(r.get("schichtart"))]
            new_tpl = [{"schichtart": TYPE_BY_LABEL[r["schichtart"]], "start_offset_min": int(r["start_offset_min"]),
                        "end_offset_min": int(r["end_offset_min"]), "anzahl": int(r["anzahl"])} for r in rows]
            db.set_setting("schicht_vorlage", json.dumps(new_tpl))
            st.success("Vorlage gespeichert.")

    with st.expander("Schicht(en) manuell anlegen – auch als Serie, z. B. Tageskasse"):
        y, m = parse_month(month)
        with st.form("add_shift"):
            c1, c2, c3 = st.columns(3)
            stype = c1.selectbox("Schichtart", list(SHIFT_TYPES), format_func=SHIFT_TYPES.get)
            start = c2.time_input("Beginn", value=None, step=900)
            end = c3.time_input("Ende", value=None, step=900)
            c4, c5 = st.columns(2)
            required = c4.number_input("Anzahl Personen", 1, 20, 1)
            title = c5.text_input("Titel (optional)")
            mode = st.radio("Termin", ["Einzeltermin", "Serie im Monat"], horizontal=True)
            single = st.date_input("Datum (Einzeltermin)", value=date(y, m, 1), format="DD.MM.YYYY")
            series_days = st.multiselect("Wochentage (Serie)", list(range(7)), format_func=lambda i: WEEKDAYS[i])
            if st.form_submit_button("Anlegen"):
                if not start or not end:
                    st.error("Bitte Beginn und Ende angeben.")
                else:
                    if mode == "Einzeltermin":
                        dates = [single]
                    else:
                        dates = [d for w in month_weeks(month) for d in w if d.month == m and d.weekday() in series_days]
                    for d in dates:
                        db.add_shift(d.isoformat(), stype, start.strftime("%H:%M"), end.strftime("%H:%M"),
                                     required, title)
                    st.success(f"{len(dates)} Schicht(en) angelegt.")
                    st.rerun()

    st.subheader(f"Schichten im {month_label(month)}")
    shifts = db.list_shifts(month)
    if not shifts:
        st.write("Noch keine Schichten angelegt.")
        return
    df = pd.DataFrame([{
        "id": s["id"], "Datum": fmt_date(s["date"]), "Tag": WEEKDAYS[weekday(s["date"])],
        "Schicht": SHIFT_TYPES[s["shift_type"]], "Zeit": f"{s['start_time']}–{s['end_time']}",
        "Anzahl": s["required"], "Titel": s["title"] or "", "Löschen": False,
    } for s in shifts])
    edited = st.data_editor(df, hide_index=True, key="shift_list", disabled=[c for c in df.columns if c != "Löschen"],
                            column_config={"id": None})
    to_delete = edited.loc[edited["Löschen"], "id"].tolist()
    if to_delete and st.button(f"{len(to_delete)} Schicht(en) löschen"):
        for sid in to_delete:
            db.delete_shift(int(sid))
        st.rerun()


# ------------------------------------------------------------------ Team
def _tab_team(current_admin: dict) -> None:
    st.caption("Neue Zeilen unten hinzufügen. Gelöschte Zeilen werden deaktiviert (Historie bleibt erhalten). "
               "Ob jemand Kasse, Einlass oder beides macht, wählt jede Person selbst pro Monat.")
    users = db.list_users()
    cols = ["id", "Name", "E-Mail", "Admin", "Einplanbar", "Minijob", "Stundenlohn €", "Aktiv", "Zugang"]
    df = pd.DataFrame([{
        "id": u["id"], "Name": u["name"], "E-Mail": u["email"],
        "Admin": bool(u["is_admin"]), "Einplanbar": bool(u["plannable"]), "Minijob": bool(u["is_minijob"]),
        "Stundenlohn €": u["hourly_wage"], "Aktiv": bool(u["active"]),
        "Zugang": ("Passwort" + (" + 2FA" if u.get("totp_secret") else "")) if u.get("password_hash") else "offen",
    } for u in users], columns=cols)
    edited = st.data_editor(df, num_rows="dynamic", hide_index=True, key="team_editor", disabled=["Zugang"],
                            column_config={
        "id": None,
        "Admin": st.column_config.CheckboxColumn(default=False),
        "Einplanbar": st.column_config.CheckboxColumn(default=True),
        "Minijob": st.column_config.CheckboxColumn(default=True),
        "Aktiv": st.column_config.CheckboxColumn(default=True),
        "Stundenlohn €": st.column_config.NumberColumn(help="leer = gesetzlicher Mindestlohn",
                                                       min_value=0.0, format="%.2f"),
    })
    if st.button("Team speichern", type="primary"):
        errors, kept_ids = [], set()
        for i, r in enumerate(edited.to_dict("records"), start=1):
            if _is_empty(r.get("Name")) and _is_empty(r.get("E-Mail")):
                continue
            if _is_empty(r.get("Name")) or _is_empty(r.get("E-Mail")) or "@" not in str(r["E-Mail"]):
                errors.append(f"Zeile {i}: Name und gültige E-Mail-Adresse erforderlich.")
                continue
            uid = None if _is_empty(r.get("id")) else int(r["id"])
            if uid == current_admin["id"] and not r.get("Admin"):
                errors.append("Du kannst dir selbst die Admin-Rechte nicht entziehen.")
                continue
            try:
                new_id = db.upsert_user(
                    uid, r["E-Mail"], r["Name"], "BEIDE",
                    is_admin=bool(r.get("Admin")), plannable=bool(r.get("Einplanbar", True)),
                    is_minijob=bool(r.get("Minijob", True)),
                    hourly_wage=None if _is_empty(r.get("Stundenlohn €")) else float(r["Stundenlohn €"]),
                    active=bool(r.get("Aktiv", True)))
                kept_ids.add(new_id)
            except ValueError as exc:
                errors.append(str(exc))
        for u in users:
            if u["id"] not in kept_ids and u["id"] != current_admin["id"] and not errors:
                db.deactivate_user(u["id"])
        if errors:
            for e in errors:
                st.error(e)
        else:
            db.audit(current_admin["id"], "Team gespeichert")
            st.success("Team gespeichert.")
            st.rerun()

    st.subheader("Weitere Admins ernennen")
    st.caption("Admins dürfen Termine abrufen, Pläne erstellen und veröffentlichen und das Team verwalten. "
               "Sie melden sich mit Passwort und Zwei-Faktor-Code an.")
    with st.form("new_admin"):
        c1, c2 = st.columns(2)
        a_name = c1.text_input("Name")
        a_email = c2.text_input("E-Mail-Adresse")
        a_plan = st.checkbox("Übernimmt auch selbst Schichten", value=False)
        if st.form_submit_button("Als Admin freischalten"):
            if "@" not in a_email:
                st.error("Bitte eine gültige E-Mail-Adresse eingeben.")
            else:
                existing = db.get_user_by_email(a_email)
                if existing:
                    uid = db.upsert_user(existing["id"], existing["email"], existing["name"],
                                         existing["role_permission"], is_admin=True,
                                         plannable=bool(existing["plannable"]), is_minijob=bool(existing["is_minijob"]),
                                         hourly_wage=existing["hourly_wage"], active=True)
                elif not a_name.strip():
                    st.error("Für neue Personen bitte auch den Namen angeben.")
                    uid = None
                else:
                    uid = db.upsert_user(None, a_email, a_name, "BEIDE", is_admin=True, plannable=a_plan)
                if uid:
                    db.audit(current_admin["id"], "Admin ernannt", a_email.strip().lower())
                    if not db.get_user(uid).get("password_hash"):
                        code, expires = auth.create_invite(uid, current_admin["id"])
                        st.session_state["last_invite"] = (a_email.strip().lower(), code,
                                                           expires.strftime("%d.%m.%y %H:%M"))
                    st.success(f"{a_email} ist jetzt Admin.")

    st.subheader("Zugang vergeben / Passwort zurücksetzen")
    st.caption("Erzeugt einen Einmal-Code (7 Tage gültig). Die Person löst ihn auf der Anmeldeseite "
               "unter „Code einlösen“ ein und legt ihr Passwort selbst fest. Ältere Codes werden ungültig.")
    active = [u for u in db.list_users(active_only=True)]
    if not active:
        return
    person = st.selectbox("Person", active, format_func=lambda u: f"{u['name']} ({u['email']})", key="inv_person")
    c1, c2 = st.columns(2)
    if c1.button("Einladungscode erzeugen"):
        code, expires = auth.create_invite(person["id"], current_admin["id"])
        st.session_state["last_invite"] = (person["email"], code, expires.strftime("%d.%m.%y %H:%M"))
    if person["is_admin"] and person["id"] != current_admin["id"] and person.get("totp_secret") \
            and c2.button("2FA dieser Person zurücksetzen"):
        db.set_totp_secret(person["id"], None)
        db.audit(current_admin["id"], "2FA zurückgesetzt", f"für {person['email']}")
        st.success("2FA zurückgesetzt – wird beim nächsten Login neu eingerichtet.")
    if inv := st.session_state.get("last_invite"):
        email, code, until = inv
        st.success(f"Code für {email}: **{code}** (gültig bis {until}). Wird nur jetzt angezeigt.")
        if mailer.is_configured() and st.button("Code per E-Mail senden"):
            mailer.send(email, "Dein Zugang zum Schichtplaner",
                        f"Hallo,\n\ndein Code lautet: {code}\nGültig bis {until}.\n\n"
                        f"Löse ihn unter „Code einlösen“ ein und lege dein Passwort fest:\n{mailer.app_url()}")
            st.session_state.pop("last_invite")
            st.success("E-Mail versendet.")


# ------------------------------------------------------------------ Stundenkonto
def _tab_hours(month: str) -> None:
    df = planning.hours_account(month)
    if df.empty:
        st.write("Noch keine Team-Mitglieder.")
        return
    limit = float(db.get_setting("minijob_grenze"))
    st.caption(f"Geplante Stunden × Stundenlohn (Standard: gesetzlicher Mindestlohn "
               f"{fmt_eur(float(db.get_setting('mindestlohn')))}/h), abgeglichen mit der Minijob-Grenze "
               f"von {fmt_eur(limit)}.")
    st.dataframe(df, hide_index=True, column_config={
        "Stunden": st.column_config.NumberColumn(format="%.2f"),
        "Stundenlohn €": st.column_config.NumberColumn(format="%.2f"),
        "Verdienst €": st.column_config.NumberColumn(format="%.2f"),
        "Auslastung Minijob %": st.column_config.NumberColumn(format="%.2f"),
    })
    st.write(f"**Summe Personalkosten (brutto, ohne Arbeitgeberpauschalen): {fmt_eur(df['Verdienst €'].sum())}**")


# ------------------------------------------------------------------ Rotation / Historie
def _tab_history(month: str) -> None:
    prev = previous_month(month)
    st.caption(f"Grundlage des Rotations-Scores: Wochenenddienste im {month_label(prev)}. "
               "Wird beim Veröffentlichen automatisch geschrieben; für den Start hier manuell pflegbar.")
    hist = db.get_history(prev)
    users = db.list_users(plannable_only=True)
    df = pd.DataFrame([{
        "id": u["id"], "Name": u["name"],
        "WE-Dienste Vormonat": hist.get(u["id"], {}).get("weekend_shifts", 0),
        "Schichten Vormonat": hist.get(u["id"], {}).get("total_shifts", 0),
    } for u in users])
    if df.empty:
        st.write("Noch keine Team-Mitglieder.")
        return
    edited = st.data_editor(df, hide_index=True, key="hist_editor", disabled=["Name"], column_config={
        "id": None,
        "WE-Dienste Vormonat": st.column_config.NumberColumn(min_value=0, step=1),
        "Schichten Vormonat": st.column_config.NumberColumn(min_value=0, step=1),
    })
    if st.button("Historie speichern"):
        for r in edited.to_dict("records"):
            db.set_history(int(r["id"]), prev, int(r["WE-Dienste Vormonat"] or 0), int(r["Schichten Vormonat"] or 0))
        st.success("Historie gespeichert.")


# ------------------------------------------------------------------ Einstellungen
def _tab_settings() -> None:
    with st.form("settings"):
        c1, c2 = st.columns(2)
        wage = c1.number_input("Gesetzlicher Mindestlohn (€/h)", 0.0, 100.0,
                               float(db.get_setting("mindestlohn")), step=0.01, format="%.2f")
        limit = c2.number_input("Minijob-Verdienstgrenze (€/Monat)", 0.0, 5000.0,
                                float(db.get_setting("minijob_grenze")), step=1.0, format="%.2f")
        c3, c4 = st.columns(2)
        per_day = c3.number_input("Max. Schichten pro Person und Tag", 1, 3,
                                  int(db.get_setting("max_schichten_pro_tag")))
        tlimit = c4.number_input("Rechenzeit-Limit Optimierung (Sekunden)", 5, 300,
                                 int(float(db.get_setting("solver_zeitlimit_s"))))
        c5, c6 = st.columns(2)
        frist = c5.number_input("Anmeldefrist: Tage vor Monatsbeginn", 1, 30,
                                int(db.get_setting("anmeldeschluss_tage")))
        timeout = c6.number_input("Automatische Abmeldung nach Minuten Inaktivität", 5, 240,
                                  int(db.get_setting("session_timeout_min")))
        url = st.text_input("Spielplan-URL", db.get_setting("spielplan_url"))
        st.caption("Stand 01.01.2026: Mindestlohn 13,90 €/h, Minijob-Grenze 603 €/Monat. "
                   "Ab 01.01.2027 gelten voraussichtlich 14,60 €/h bzw. 633 €/Monat.")
        if st.form_submit_button("Einstellungen speichern", type="primary"):
            db.set_setting("mindestlohn", f"{wage:.2f}")
            db.set_setting("minijob_grenze", f"{limit:.2f}")
            db.set_setting("max_schichten_pro_tag", str(int(per_day)))
            db.set_setting("solver_zeitlimit_s", str(int(tlimit)))
            db.set_setting("anmeldeschluss_tage", str(int(frist)))
            db.set_setting("session_timeout_min", str(int(timeout)))
            db.set_setting("spielplan_url", url.strip())
            st.success("Gespeichert.")

    st.subheader("Datensicherung")
    st.caption("Lädt alle Daten (ohne Passwörter und 2FA-Schlüssel) als JSON-Datei herunter. "
               "Empfehlung: einmal pro Monat nach dem Veröffentlichen sichern.")
    st.download_button("Datensicherung herunterladen",
                       json.dumps(db.export_all(), ensure_ascii=False, indent=1, default=str).encode("utf-8"),
                       file_name=f"{date.today().isoformat()}_Schichtplaner_Sicherung.json",
                       mime="application/json")
