import sqlite3
import pandas as pd
from datetime import datetime

DB_NAME = "theater_schichten.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Tabelle für Benutzer
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            role_type TEXT NOT NULL, -- 'Einlass' oder 'Kasse_Einlass'
            max_shifts INTEGER DEFAULT 4,
            max_kasse_shifts INTEGER DEFAULT 2,
            prefer_weekend_off INTEGER DEFAULT 1
        )
    ''')
    
    # Tabelle für harte Sperrtage (Format: YYYY-MM-DD)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS blocked_days (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT NOT NULL,
            date TEXT NOT NULL
        )
    ''')
    
    # Tabelle für Spieltermine & Schichten
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            shift_type TEXT NOT NULL, -- 'Tageskasse', 'Abendkasse', 'Einlass_1', 'Einlass_2', 'Putzen'
            assigned_email TEXT
        )
    ''')
    
    conn.commit()
    conn.close()

def get_db_connection():
    return sqlite3.connect(DB_NAME)
