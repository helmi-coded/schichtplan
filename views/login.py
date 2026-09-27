"""Anmeldung.

Ablauf:
  1. E-Mail + Passwort
  2. Nur Admins: zusätzlich 6-stelliger Code aus einer Authenticator-App (2FA).
     Beim ersten Admin-Login wird die App per QR-Code eingerichtet.
Neue Personen und Passwort-Reset: Einladungscode von der Leitung einlösen.
"""
from datetime import datetime

import streamlit as st

from core import auth, config, db


def current_user() -> dict | None:
    uid = st.session_state.get("user_id")
    if uid:
        user = db.get_user(uid)
        if user and user["active"]:
            return user
    return None


def logout(reason: str = "") -> None:
    uid = st.session_state.get("user_id")
    if uid:
        db.audit(uid, "Abmeldung", reason)
    st.session_state.clear()
    if reason:
        st.session_state["logout_reason"] = reason
    st.rerun()


def check_session_timeout() -> None:
    """Meldet nach X Minuten Inaktivität automatisch ab."""
    timeout = int(db.get_setting("session_timeout_min")) * 60
    last = st.session_state.get("last_activity")
    now = datetime.now().timestamp()
    if last and now - last > timeout:
        logout("Automatisch abgemeldet wegen Inaktivität.")
    st.session_state["last_activity"] = now


def _finish_login(user: dict) -> None:
    for k in ("pending_user_id", "pending_totp_secret"):
        st.session_state.pop(k, None)
    st.session_state["user_id"] = user["id"]
    st.session_state["last_activity"] = datetime.now().timestamp()
    db.audit(user["id"], "Anmeldung")
    st.rerun()


def render() -> None:
    st.title("🎭 Schichtplaner")
    if reason := st.session_state.pop("logout_reason", None):
        st.info(reason)

    if db.count_admins() == 0:
        _render_bootstrap()
        return
    if st.session_state.get("pending_user_id"):
        _render_second_factor(db.get_user(st.session_state["pending_user_id"]))
        return

    tab_login, tab_code = st.tabs(["Anmelden", "Code einlösen / Passwort vergessen"])
    with tab_login:
        with st.form("login"):
            email = st.text_input("E-Mail-Adresse", autocomplete="username")
            password = st.text_input("Passwort", type="password", autocomplete="current-password")
            if st.form_submit_button("Anmelden", type="primary"):
                user, error = auth.authenticate(email, password)
                if not user:
                    st.error(error)
                elif user["is_admin"]:
                    st.session_state["pending_user_id"] = user["id"]
                    st.rerun()
                else:
                    _finish_login(user)
    with tab_code:
        st.caption("Den Code bekommst du von der Theaterleitung – für den ersten Zugang "
                   "oder wenn du dein Passwort vergessen hast. Er ist 7 Tage gültig.")
        with st.form("redeem"):
            email = st.text_input("E-Mail-Adresse", key="r_email")
            code = st.text_input("Code (z. B. ABCDE-FGH23)")
            pw1 = st.text_input("Neues Passwort", type="password", autocomplete="new-password",
                                help="Mindestens 10 Zeichen, Buchstaben und Ziffern/Sonderzeichen gemischt.")
            pw2 = st.text_input("Passwort wiederholen", type="password", autocomplete="new-password")
            if st.form_submit_button("Passwort festlegen"):
                if pw1 != pw2:
                    st.error("Die Passwörter stimmen nicht überein.")
                else:
                    ok, msg = auth.redeem_invite(email, code, pw1)
                    (st.success if ok else st.error)(msg)


def _render_second_factor(user: dict | None) -> None:
    if not user:
        st.session_state.clear()
        st.rerun()
    st.subheader("Zwei-Faktor-Bestätigung")
    if not user.get("totp_secret"):
        # Ersteinrichtung der Authenticator-App
        secret = st.session_state.setdefault("pending_totp_secret", auth.new_totp_secret())
        st.write("Als Admin brauchst du einen zweiten Faktor. Scanne den QR-Code mit einer "
                 "Authenticator-App (z. B. Google Authenticator, Microsoft Authenticator, 2FAS).")
        st.image(auth.totp_qr_png(secret, user["email"]), width=220)
        st.caption(f"Manuelle Eingabe: `{secret}`")
    else:
        secret = user["totp_secret"]
        st.write("Gib den 6-stelligen Code aus deiner Authenticator-App ein.")
    with st.form("totp"):
        code = st.text_input("Code", max_chars=7, autocomplete="one-time-code")
        c1, c2 = st.columns(2)
        ok = c1.form_submit_button("Bestätigen", type="primary")
        cancel = c2.form_submit_button("Abbrechen")
    if cancel:
        st.session_state.clear()
        st.rerun()
    if ok:
        if auth.verify_totp(secret, code):
            if not user.get("totp_secret"):
                db.set_totp_secret(user["id"], secret)
                db.audit(user["id"], "2FA eingerichtet")
            _finish_login(user)
        else:
            db.audit(user["id"], "2FA-Code falsch")
            st.error("Code ungültig. Achte darauf, dass die Uhrzeit des Handys stimmt.")


def _render_bootstrap() -> None:
    st.info("Ersteinrichtung: Lege das erste Admin-Konto an. Danach richtest du die Zwei-Faktor-App ein.")
    setup_token = config.get("SETUP_TOKEN")
    with st.form("bootstrap"):
        name = st.text_input("Name")
        email = st.text_input("E-Mail-Adresse")
        pw1 = st.text_input("Passwort", type="password", autocomplete="new-password")
        pw2 = st.text_input("Passwort wiederholen", type="password", autocomplete="new-password")
        token = st.text_input("Einrichtungs-Token (aus der Server-Konfiguration)", type="password") \
            if setup_token else None
        plannable = st.checkbox("Ich übernehme selbst auch Schichten", value=False)
        if st.form_submit_button("Admin-Konto anlegen", type="primary"):
            problems = auth.password_problems(pw1, email)
            if setup_token and token != setup_token:
                st.error("Einrichtungs-Token falsch.")
            elif not name.strip() or "@" not in email:
                st.error("Bitte Name und gültige E-Mail-Adresse eingeben.")
            elif pw1 != pw2:
                st.error("Die Passwörter stimmen nicht überein.")
            elif problems:
                st.error("Passwort: " + ", ".join(problems) + ".")
            else:
                uid = db.upsert_user(None, email, name, "BEIDE", is_admin=True, plannable=plannable)
                db.set_password_hash(uid, auth.hash_password(pw1))
                db.audit(uid, "Admin-Konto angelegt (Ersteinrichtung)")
                st.session_state["pending_user_id"] = uid
                st.rerun()


def render_account(user: dict) -> None:
    st.header("Mein Konto")
    st.write(f"**{user['name']}** · {user['email']}")
    with st.form("pw_change"):
        cur = st.text_input("Aktuelles Passwort", type="password", autocomplete="current-password")
        new1 = st.text_input("Neues Passwort", type="password", autocomplete="new-password")
        new2 = st.text_input("Neues Passwort wiederholen", type="password", autocomplete="new-password")
        if st.form_submit_button("Passwort ändern"):
            if new1 != new2:
                st.error("Die neuen Passwörter stimmen nicht überein.")
            else:
                ok, msg = auth.change_password(user, cur, new1)
                (st.success if ok else st.error)(msg)
