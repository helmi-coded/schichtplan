# Schichtplaner für Kulturbetriebe (Python & Streamlit)

**Online stellen ohne eigenen Server: siehe `DEPLOYMENT.md`.**

Monatliche Schichtplanung für 10–40 Minijobber (Tageskasse, Abendkasse, Einlass) mit
automatischer, fairer Zuteilung.

## 1. Installation

Voraussetzung: Python 3.10 oder neuer.

```bash
cd schichtplaner
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Optional Demo-Daten für einen Probelauf (24 Personen, ein Monat mit Aufführungen):

```bash
python seed_demo.py 2026-10
```

Start:

```bash
streamlit run app.py
```

Die App öffnet sich unter http://localhost:8501. Beim ersten Start ohne Demo-Daten
wird das erste Admin-Konto angelegt. Demo-Logins: `admin@theater.example`,
`anna@theater.example`, Passwort `Demo-Passwort-2026` (nur für den Probelauf).

Tests: `python tests/test_solver.py` und `python tests/test_security_spielplan.py`
(mit gesetzter `DATABASE_URL` laufen dieselben Tests gegen Postgres)

## 2. Ablauf pro Monat

1. **Admin → Termine & Schichten → „Spielplan abrufen":** Die Termine werden von
   https://www.wallgraben-theater.com/spielplan übernommen. Vorschau prüfen, dann
   „Änderungen übernehmen". Abendkasse und Einlass entstehen über die Vorlage,
   die Tageskasse (Mo–Sa 11–13 Uhr) wird einmal pro Monat ergänzt. Erneutes Abrufen
   gleicht ab: neue Termine kommen hinzu, entfallene werden entfernt.
2. **Team bis zur Anmeldefrist** (Standard: 7 Tage vor Monatsbeginn, 23:59 Uhr):
   Einsatz wählen, Sperrtage im Kalender antippen und Angaben speichern. Danach ist alles gesperrt.
3. **Admin → Planung nach Fristende:** „Plan erstellen" → prüfen, ggf. manuell anpassen →
   „Plan veröffentlichen" mit 2FA-Code. Danach sieht das Team seine Schichten.
   Ein Probelauf vor Fristende ist möglich, Veröffentlichen erst danach.
4. **Stundenkonto:** Stunden × Stundenlohn, Ampel gegen die Minijob-Grenze.

## 3. CSV-Formate (Trennzeichen `;` oder `,`)

Aufführungsliste (Schichten entstehen über die Vorlage):

```
datum;titel;beginn
02.10.2026;Hamlet;19:30
```

Schichtliste (direkte Übernahme):

```
datum;titel;schichtart;beginn;ende;anzahl
02.10.2026;;Tageskasse;11:00;14:00;1
```

Beispiele liegen in `beispieldaten/`.

## 4. Projektstruktur

```
app.py                  Einstieg, Navigation, CSS für den Kalender
core/constants.py       Einsatzarten, Schichtarten, Standardwerte (Mindestlohn, Minijob-Grenze)
core/db.py              SQLite-Schema und Datenzugriff
core/solver.py          Optimierungsmodell (OR-Tools CP-SAT), Gewichte oben in der Datei
core/planning.py        Service-Schicht: Solver-Eingaben, Veröffentlichung, Auswertungen
core/importer.py        CSV-Import
core/spielplan.py       Abruf und Abgleich des Online-Spielplans
core/auth.py            Passwörter, Sperre, Einladungscodes, 2FA
core/deadline.py        Anmeldefrist
core/mailer.py          optionaler E-Mail-Versand
core/config.py          Konfiguration (Umgebungsvariablen / Streamlit-Secrets)
core/calendar_utils.py  Monats-/Datumslogik, deutsche Zahlenformate
views/                  Login, Mitarbeiter-Dashboard, Admin-Bereich
seed_demo.py            Demo-Daten
tests/                  Tests (Solver, Sicherheit, Frist, Spielplan-Parser)
```

## 5. Zuteilungslogik

| Art | Regel |
|---|---|
| hart | Sperrtage, Einsatzwahl des Monats, ausgeschlossener Wochenendtag, Schicht- und Stunden-Obergrenze, Obergrenze je Schichtart, max. Schichten/Tag, keine Überlappung, Minijob-Verdienstgrenze |
| weich | Vollbesetzung (höchste Priorität), Mindestanzahl je Schichtart, Wochentags-Präferenz, Priorisierung „braucht Stunden", Wochenend-Rotation nach Vormonat, gleichmäßige Lastverteilung |

Unterbesetzung ist erlaubt und wird ausgewiesen – die Berechnung bricht nie ab.
Die Gewichte stehen als Konstanten in `core/solver.py` und lassen sich justieren.

## 6. Sicherheit

- Anmeldung mit E-Mail + Passwort. Passwörter werden mit scrypt gehasht, nie im Klartext gespeichert.
- Nach 5 Fehlversuchen ist das Konto 15 Minuten gesperrt; Fehlermeldungen verraten nicht,
  ob eine E-Mail-Adresse existiert.
- Neue Personen / Passwort vergessen: Admin erzeugt im Tab „Team" einen Einmal-Code
  (7 Tage gültig), die Person legt ihr Passwort selbst fest.
- **Admins benötigen Zwei-Faktor-Authentifizierung** (Authenticator-App, QR-Code beim
  ersten Login). Das Veröffentlichen eines Plans wird zusätzlich mit einem 2FA-Code bestätigt.
- Automatische Abmeldung nach 30 Minuten Inaktivität (einstellbar).
- Protokoll aller sicherheitsrelevanten Aktionen im Admin-Tab „Protokoll".

### Für den Online-Betrieb zwingend

- **Nur über HTTPS betreiben** (z. B. Streamlit Community Cloud, oder eigener Server hinter
  einem Reverse-Proxy wie Caddy/nginx mit Let's-Encrypt-Zertifikat). Ohne HTTPS können
  Passwörter mitgelesen werden.
- Vor dem ersten Start `SETUP_TOKEN` setzen – sonst könnte, wer die Adresse
  zuerst aufruft, das erste Admin-Konto anlegen.
- Optionaler E-Mail-Versand (Codes, Erinnerungen): `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`,
  `SMTP_PASSWORD`, `SMTP_FROM`, `APP_URL`.
- Konfiguration über Umgebungsvariablen oder `.streamlit/secrets.toml`
  (Vorlage: `.streamlit/secrets.toml.example`).

### Datenbank

- Ohne `DATABASE_URL`: lokale SQLite-Datei (zum Testen).
- Mit `DATABASE_URL`: Postgres (z. B. Neon, Region Frankfurt) – nötig für Streamlit Community
  Cloud, weil dort lokale Dateien bei Neustarts verloren gehen. Tabellen werden automatisch angelegt.
- Admins: ersten Admin über die Ersteinrichtung anlegen, weitere im Tab „Team" unter
  „Weitere Admins ernennen" per E-Mail-Adresse freischalten.

## 7. Weitere Hinweise
- Mindestlohn (13,90 €/h) und Minijob-Grenze (603 €/Monat) sind Stand 01.01.2026 und
  unter „Einstellungen" anpassbar (2027 voraussichtlich 14,60 €/h bzw. 633 €/Monat).
- Die Minijob-Grenze wird je Monat als harte Obergrenze behandelt (konservativ).
- Der Spielplan-Parser erkennt Termine am Muster „Fr 19:30 Uhr 09 Okt 2026". Ändert das
  Theater das Format grundlegend, bleibt der CSV-Import als Ausweg.
- Abendkasse und Einlass beginnen 2 Stunden vor Vorstellungsbeginn. Der Einlassdienst ist
  pauschal mit 5 Stunden angesetzt (bis ca. 1 Stunde nach Vorstellungsende), die Abendkasse
  endet 15 Min. nach Beginn. Beides in der Vorlage anpassbar.
- Einsatz (Kasse / Einlass / beides), Obergrenzen und Wünsche legt jede Person selbst pro
  Monat fest; Vormonatswerte werden vorausgefüllt. Die Leitung gibt keine Rollen vor.
- Feiertage bei der Tageskasse manuell löschen.
- Die SQLite-Datei `schichtplaner.db` regelmäßig sichern. Pfad per Umgebungsvariable
  `SCHICHTPLANER_DB` änderbar.
