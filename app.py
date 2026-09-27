"""Schichtplaner – Einstiegspunkt.  Start:  streamlit run app.py"""
import streamlit as st

from core import db
from core.calendar_utils import add_months, month_key, month_label, month_range, today_local
from views import admin, employee, login

st.set_page_config(page_title="Schichtplaner", page_icon="🎭", layout="wide")

# Kalender: 7 Spalten auch auf dem Handy; gesperrte Tage färbt views/employee.py rot ein
CSS = """
<style>
.st-key-kalender [class*="st-key-cal_"] { padding: 0.25rem; border: 1px solid transparent; border-radius: 8px; }
.st-key-kalender [data-baseweb="select"] > div { min-height: 2.2rem; padding-left: 0.3rem; }
.st-key-kalender [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; gap: 0.25rem; }
.st-key-kalender [data-testid="stColumn"] { min-width: 0 !important; flex: 1 1 0 !important; width: auto !important; }
</style>
"""


def main() -> None:
    db.init_db()
    st.markdown(CSS, unsafe_allow_html=True)

    user = login.current_user()
    if user is None:
        login.render()
        return
    login.check_session_timeout()

    this_month = month_key(today_local())
    months = month_range(add_months(this_month, -2), 15)
    with st.sidebar:
        st.markdown(f"Angemeldet als **{user['name']}**")
        month = st.selectbox("Planungsmonat", months, index=3, format_func=month_label)  # Standard: Folgemonat
        areas = []
        if user["is_admin"]:
            areas.append("Verwaltung")
        if user["plannable"]:
            areas.append("Meine Planung")
        areas.append("Mein Konto")
        area = st.radio("Bereich", areas)
        st.divider()
        if st.button("Abmelden"):
            login.logout()

    if area == "Verwaltung":
        admin.render(user, month)
    elif area == "Meine Planung":
        employee.render(user, month)
    else:
        login.render_account(user)
        if not user["plannable"] and not user["is_admin"]:
            st.info("Dein Konto ist aktuell keiner Planung zugeordnet. Bitte wende dich an die Theaterleitung.")


main()
