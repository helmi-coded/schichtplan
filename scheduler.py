import pandas as pd
from datetime import datetime, timedelta
from database import get_db_connection

def generate_monthly_schedule(year, month):
    conn = get_db_connection()
    
    # 1. Benutzer laden
    users_df = pd.read_sql_query("SELECT * FROM users", conn)
    if users_df.empty:
        conn.close()
        return "Keine Benutzer in der Datenbank gefunden!"
        
    # 2. Gesperrte Tage laden
    blocked_df = pd.read_sql_query("SELECT * FROM blocked_days", conn)
    blocked_dict = {}
    for _, row in blocked_df.iterrows():
        blocked_dict.setdefault(row['user_email'], set()).add(row['date'])
        
    # 3. Schichten für den Monat generieren (Beispiel: Alle Tage Tageskasse, Fr/Sa Abendkasse + Einlass)
    # In der echten App würden hier die importierten Theater-Termine stehen.
    start_date = datetime(year, month, 1)
    if month == 12:
        end_date = datetime(year + 1, 1, 1) - timedelta(days=1)
    else:
        end_date = datetime(year, month + 1, 1) - timedelta(days=1)
        
    cursor = conn.cursor()
    # Alte Schichten für diesen Monat löschen, um neu zu generieren
    cursor.execute("DELETE FROM shifts WHERE date LIKE ?", (f"{year}-{month:02d}%",))
    
    current = start_date
    while current <= end_date:
        date_str = current.strftime("%Y-%m-%d")
        is_weekend = current.weekday() >= 5 # 5=Samstag, 6=Sonntag
        
        # Tageskasse (Mo-Sa, 11-13 Uhr)
        if current.weekday() != 6: # Kein Sonntag
            cursor.execute("INSERT INTO shifts (date, shift_type) VALUES (?, ?)", (date_str, 'Tageskasse'))
            
        # Wenn Freitag/Samstag, angenommener Aufführungstag
        if is_weekend:
            cursor.execute("INSERT INTO shifts (date, shift_type) VALUES (?, ?)", (date_str, 'Abendkasse'))
            cursor.execute("INSERT INTO shifts (date, shift_type) VALUES (?, ?)", (date_str, 'Einlass_1'))
            cursor.execute("INSERT INTO shifts (date, shift_type) VALUES (?, ?)", (date_str, 'Einlass_2'))
            
        current += timedelta(days=1)
        
    conn.commit()
    
    # 4. Offene Schichten holen und verteilen
    shifts_df = pd.read_sql_query("SELECT * FROM shifts WHERE date LIKE ? AND assigned_email IS NULL", conn, params=(f"{year}-{month:02d}%",))
    
    # Einfacher Fairness-Zuteilungs-Algorithmus
    user_shift_counts = {email: 0 for email in users_df['email']}
    
    for idx, shift in shifts_df.iterrows():
        shift_date = shift['date']
        shift_type = shift['shift_type']
        
        # Welche Rolle wird gebraucht?
        needed_role = 'Kasse' if 'kasse' in shift_type.lower() else 'Einlass'
        
        # Passende Kandidaten filtern
        candidates = []
        for _, user in users_df.iterrows():
            email = user['email']
            # Rollen-Check
            if needed_role == 'Kasse' and user['role_type'] != 'Kasse_Einlass':
                continue
            # Sperrtag-Check
            if email in blocked_dict and shift_date in blocked_dict[email]:
                continue
            # Obergrenzen-Check
            if user_shift_counts[email] >= user['max_shifts']:
                continue
                
            candidates.append(email)
            
        if candidates:
            # Wähle den Kandidaten mit den bisher wenigsten Schichten (Fairness)
            candidates.sort(key=lambda e: user_shift_counts[e])
            chosen_email = candidates[0]
            
            cursor.execute("UPDATE shifts SET assigned_email = ? WHERE id = ?", (chosen_email, shift['id']))
            user_shift_counts[chosen_email] += 1
            
    conn.commit()
    conn.close()
    return "Schichtplan erfolgreich berechnet!"
