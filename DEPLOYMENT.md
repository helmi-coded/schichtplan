# Online stellen ohne eigenen Server – Schritt für Schritt

Die App läuft kostenlos bei **Streamlit Community Cloud**, die Daten liegen dauerhaft in einer
kostenlosen **Neon-Postgres-Datenbank in Frankfurt**. Du brauchst drei kostenlose Konten:
GitHub, Neon und Streamlit. Dauer: ca. 30–45 Minuten. Programmierkenntnisse sind nicht nötig.

Warum die zusätzliche Datenbank? Streamlit Community Cloud hat keinen dauerhaften Speicher.
Eine lokale Datei würde bei Neustarts gelöscht – Sperrtage, Pläne und Zugänge wären weg.

---

## Schritt 1 – Code auf GitHub ablegen

1. Konto anlegen auf https://github.com (kostenlos).
2. Oben rechts **„+" → „New repository"**.
   - Name: z. B. `schichtplaner`
   - **Private** auswählen (wichtig – der Code ist dann nicht öffentlich).
   - „Create repository".
3. Auf der leeren Repository-Seite **„uploading an existing file"** anklicken.
4. Das ZIP auf dem Computer entpacken und den **Inhalt** des Ordners `schichtplaner`
   (nicht den Ordner selbst) ins Browserfenster ziehen. Mit Chrome/Edge werden
   Unterordner mit übernommen.
5. Prüfen, dass `app.py`, `requirements.txt` und die Ordner `core`, `views`, `.streamlit`
   auf der obersten Ebene liegen. Unten **„Commit changes"**.

> Der Ordner `.streamlit` ist unter macOS unsichtbar (Punkt am Anfang). Im Finder
> `Cmd + Shift + .` drücken, dann erscheint er.
> Eine Datei `secrets.toml` oder `schichtplaner.db` darf **nie** hochgeladen werden.

## Schritt 2 – Datenbank bei Neon anlegen

1. Konto anlegen auf https://neon.com (kostenlos, ohne Kreditkarte).
2. **„Create project"**:
   - Name: `schichtplaner`
   - Region: **AWS Europe Central 1 (Frankfurt)**
3. Im Projekt auf **„Connect"** klicken.
   - Den Schalter **„Connection pooling" ausschalten** (direkte Verbindung).
   - Den angezeigten Verbindungstext kopieren. Er sieht so aus:
     `postgresql://neondb_owner:xxxx@ep-....eu-central-1.aws.neon.tech/neondb?sslmode=require`
4. Diesen Text sicher aufbewahren (enthält das Datenbank-Passwort).

Die Tabellen legt die App beim ersten Start selbst an.

## Schritt 3 – App bei Streamlit Community Cloud starten

1. Auf https://share.streamlit.io mit dem **GitHub-Konto** anmelden und den Zugriff erlauben
   (auch auf private Repositories).
2. **„Create app" → „Deploy a public app from GitHub"** (das Wort „public" bezieht sich nur
   auf die Erreichbarkeit der Adresse – die App ist durch den eigenen Login geschützt).
3. Ausfüllen:
   - Repository: `dein-name/schichtplaner`
   - Branch: `main`
   - Main file path: `app.py`
   - App URL: z. B. `wallgraben-schichtplan` → Adresse `https://wallgraben-schichtplan.streamlit.app`
4. **„Advanced settings"**:
   - Python version: **3.12**
   - Bei **Secrets** folgendes einfügen (Werte ersetzen):
     ```toml
     DATABASE_URL = "postgresql://...(Text aus Schritt 2)..."
     SETUP_TOKEN = "ein-langes-zufaelliges-wort-das-nur-du-kennst"
     ```
5. **„Deploy"**. Der erste Start dauert einige Minuten (Pakete werden installiert).

## Schritt 4 – Dich selbst als Admin einrichten

1. App-Adresse öffnen. Es erscheint die **Ersteinrichtung**.
2. Name, E-Mail, Passwort (mind. 10 Zeichen) und den **SETUP_TOKEN** aus Schritt 3 eingeben.
3. Authenticator-App auf dem Handy installieren (z. B. Microsoft Authenticator,
   Google Authenticator, 2FAS), den **QR-Code scannen**, 6-stelligen Code eingeben.
4. Fertig – du bist Admin. Der SETUP_TOKEN wird ab jetzt nicht mehr gebraucht.

## Schritt 5 – Grundeinrichtung in der App

1. **Einstellungen:** Anmeldefrist (Standard 7 Tage), Mindestlohn, Minijob-Grenze prüfen.
2. **Termine & Schichten → Schicht-Vorlage:** Anzahl Personen je Schicht prüfen.
   Voreingestellt: Abendkasse und Einlass beginnen 2 Stunden vor Vorstellungsbeginn,
   der Einlassdienst dauert pauschal 5 Stunden.
3. **Team:** Alle Minijobber mit Name und E-Mail eintragen. Ob jemand Kasse, Einlass oder
   beides macht, wählt jede Person selbst pro Monat.
4. **Team → Weitere Admins ernennen:** E-Mail-Adresse der Chefs eingeben → „Als Admin freischalten".
5. **Team → Zugang vergeben:** Für jede Person einen Code erzeugen und weitergeben
   (persönlich, per Messenger oder – falls eingerichtet – per E-Mail). Die Person öffnet die App,
   wählt „Code einlösen" und legt ihr Passwort fest. Admins richten danach die 2FA-App ein.

## Schritt 6 – Monatlicher Ablauf

| Wann | Wer | Was |
|---|---|---|
| sobald der Spielplan online ist | Admin | Termine & Schichten → „Spielplan abrufen" → „Änderungen übernehmen" |
| bis Anmeldefrist | Team | Einsatz wählen (Kasse / Einlass / beides), Sperrtage antippen, Angaben speichern |
| nach Fristende | Admin | Planung → „Plan erstellen" → prüfen → mit 2FA-Code veröffentlichen |
| danach | Team | Schichten ansehen, per 📅-Button in den Handykalender übernehmen |
| nach Veröffentlichung | Admin | Einstellungen → „Datensicherung herunterladen" |

## Schritt 7 (optional) – E-Mail-Versand

Damit Codes und Erinnerungen automatisch verschickt werden, ein eigenes Postfach nutzen
(z. B. neues Gmail-Konto mit aktivierter 2-Faktor-Anmeldung und einem „App-Passwort") und in
den Secrets ergänzen: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`,
`APP_URL` (Vorlage: `.streamlit/secrets.toml.example`).

## Änderungen am Code später

Datei auf GitHub öffnen → Stift-Symbol → ändern → „Commit changes". Streamlit übernimmt die
Änderung automatisch nach ca. einer Minute.

---

## Gut zu wissen

- **Schlafmodus:** Wird die App länger nicht aufgerufen, schläft sie ein. Beim nächsten Aufruf
  erscheint ein Button zum Aufwecken, das dauert etwa eine Minute. Daten gehen dabei nicht verloren.
- **Kein Service-Level:** Die kostenlose Streamlit-Cloud gibt keine Verfügbarkeitsgarantie.
  Für einen Dienstplan mit Monatsrhythmus ist das in der Regel vertretbar.
- **Datenschutz (DSGVO):** Die Datenbank liegt in Frankfurt, die App selbst läuft bei Streamlit
  aber **in den USA**. Es werden Beschäftigtendaten verarbeitet (Namen, E-Mail, Verfügbarkeiten,
  Stunden). Vor dem echten Einsatz bitte mit der Theaterleitung klären, ob das so in Ordnung ist
  (Stichworte: Auftragsverarbeitungsvertrag, Drittlandübermittlung). Falls nicht: dieselbe App
  lässt sich unverändert bei einem kostenpflichtigen Anbieter mit EU-Rechenzentrum betreiben.
- **Lokal testen:** siehe README (`python seed_demo.py` und `streamlit run app.py`).
