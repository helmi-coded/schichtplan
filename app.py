import streamlit as st
import pandas as pd
from datetime import datetime
from database import init_db, get_db_connection
from scheduler import generate_monthly_schedule

# Datenbank initialisieren
init_db()

st.set_page_config(page_title="Theater Schichtplaner", layout="wide")

st.title("🎭 Theater Schichtplanungs-App")

# Einfaches E-Mail-Login in der Sidebar
st.sidebar.header("Anmeldung")
user_email = st.sidebar.text_input("E-Mail-Adresse eingeben")

if user_email:
    conn = get_db_connection()
    user = pd.read_sql_query("SELECT * FROM users WHERE email = ?", conn, params=(user_email,))
    
    if user.empty:
        st.sidebar.warning("E-Mail nicht gefunden. Bitte als neuer User registrieren:")
        new_name = st.sidebar.text_input("Dein Name")
        new_role = st.sidebar.selectbox("Rolle", ["Einlass", "Kasse_Einlass"])
        if st.sidebar.button("Registrieren"):
            cursor = conn.cursor()
            cursor.execute("INSERT INTO users (email, name, role_type) VALUES (?, ?, ?)", (user_email, new_name, new_role))
            conn.commit()
            st.sidebar.success("Registriert! Bitte Seite neu laden.")
            st.rerun()
        conn.close()
    else:
        user_data = user.iloc[0]
        conn.close()
        
        st.sidebar.success(duğuher := f"Eingeloggt als: {user_data['name']} ({user_data['role_type']})")
        
        # Tabs für die Navigation
        tab1, tab2, tab3 = st.tabs(["📅 Meine Verfügbarkeiten & Sperrtage", "📋 Monats-Schichtplan", "⚙️ Admin-Bereich"])
        
        with tab1:
            st.header("Deine Sperrtage für den Monat eintragen")
            st.info("Klicke auf Tage, an denen du absolut nicht arbeiten kannst. Diese werden als harte Sperre hinterlegt.")
            
            selected_date = st.date_input("Datum auswählen, das gesperrt werden soll:")
            if st.button("Datum sperren"):
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("INSERT INTO blocked_days (user_email, date) VALUES (?, ?)", (user_email, str(selected_date)))
                conn.commit()
                conn.close()
                st.success(f"Datum {selected_date} erfolgreich gesperrt!")
                
            # Aktuelle Sperren anzeigen
            conn = get_db_connection()
            blocked_df = pd.read_sql_query("SELECT id, date FROM blocked_days WHERE user_email = ?", conn, params=(user_email,))
            conn.close()
            
            st.subheader("Deine aktiven Sperrtage:")
            if not blocked_df.empty:
                st.dataframe(blocked_df)
                block_id_to_delete = st.number_input("ID zum Entsperren eingeben:", min_value=0, step=1)
                if st.button("Sperre aufheben"):
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM blocked_days WHERE id = ?", (block_id_to_delete,))
                    conn.commit()
                    conn.close()
                    st.success("Sperre aufgehoben!")
                    st.rerun()
            else:
                st.write("Keine Sperrtage eingetragen.")
                
        with tab2:
            st.header("Aktueller Schichtplan")
            conn = get_db_connection()
            shifts_df = pd.read_sql_query("SELECT * FROM shifts ORDER BY date", conn)
            conn.close()
            if not shifts_df.empty:
                st.dataframe(shifts_df, use_container_width=True)
            else:
                st.write("Noch kein Schichtplan für diesen Monat berechnet.")
                
        with tab3:
            st.header("Admin-Bereich: Schichtplanung ausführen")
            col1, col2 = st.columns(2)
            with col1:
                plan_year = st.number_input("Jahr", value=2026, step=1)
            with col2:
                plan_month = st.number_input("Monat", value=6, min_value=1, max_value=12, step=1)
                
            if st.button("🚀 Schichtplan automatisch berechnen & zuweisen"):
                result = generate_monthly_schedule(int(plan_year), int(plan_month))
                st.success(result)
                st.rerun()
else:
    st.info("👈 Bitte gib links deine E-Mail-Adresse ein, um fortzufahren.")
