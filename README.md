# 🎭 Theater Schichtplanungs-App

Eine maßgeschneiderte Web-Anwendung zur automatisierten und fairen Schichtplanung für Theater- und Kulturbetriebe auf Minijob-Basis. Entwickelt in Python mit **Streamlit** und **SQLite**.

---

## ✨ Hauptfunktionen

- **E-Mail-Login & Dashboard:** Jedes Teammitglied loggt sich unkompliziert mit seiner E-Mail-Adresse ein und sieht seine persönlichen Einstellungen.
- **Rollen-Management:** Strenge Trennung und Zuweisung von Qualifikationen (z. B. "Nur Einlassdienst" vs. "Kasse & Einlass").
- **Harte Sperrtage:** Mitarbeitende können im Monatskalender Tage blockieren, an denen sie absolut nicht arbeiten können (werden als *Hard Constraints* vom Algorithmus berücksichtigt).
- **Workload- & Minijob-Grenzen:** Verwaltung von monatlichen Schicht-Obergrenzen zur Einhaltung rechtlicher Rahmenbedingungen.
- **Automatisierter Fairness-Algorithmus:** Ein Klick im Admin-Bereich berechnet den optimalen Monatsplan, gleicht Sperrtage ab und verteilt Schichten rollierend nach bisheriger Belastung.

---

## 🛠️ Technische Architektur

Das Projekt ist modular aufgebaut:
1. `app.py` – Das Streamlit-Frontend (Benutzeroberfläche, Login, Ansichten).
2. `database.py` – Die SQLite-Datenbankinitialisierung (Benutzer, Rollen, Sperrtage, Schichten).
3. `scheduler.py` – Die Zuteilungs- und Optimierungslogik für den monatlichen Schichtplan.
4. `requirements.txt` – Liste der benötigten Python-Bibliotheken.

---

## 🚀 Lokale Installation & Start

Falls du die App lokal auf deinem Computer testen möchtest:

1. Repository klonen oder herunterladen:
   ```bash
   git clone [https://github.com/DEIN-BENUTZERNAME/theater-schichtplaner.git](https://github.com/DEIN-BENUTZERNAME/theater-schichtplaner.git)
   cd theater-schichtplaner

