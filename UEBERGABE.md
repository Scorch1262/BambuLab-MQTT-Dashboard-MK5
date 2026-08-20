# Übergabedokument: 3D-Drucker Dashboard

Dieses Dokument fasst den aktuellen Stand des Projekts zusammen, damit die
Weiterentwicklung in einem neuen Chat (auch mit einem anderen Assistenten/
Modell) nahtlos möglich ist. Am besten diese Datei **zusammen mit `app.py`
und `README.md`** in den neuen Chat hochladen bzw. einfügen.

---

## 1. Was ist das Projekt

Ein lokal laufendes Web-Dashboard (Flask, Python) für 3D-Drucker im
eigenen Netzwerk. Zeigt Fortschritt, aktuelle Datei, Temperaturen, Kamera
und (je nach Hersteller) weitere Daten in dunkel gehaltenen Karten
(Design angelehnt an Anduril Lattice) an. Läuft als Python-Skript oder als
über PyInstaller/GitHub Actions gebaute Windows-.exe.

**Zielumgebung:** Wird typischerweise per PyInstaller in eine Windows-EXE
gepackt und läuft neben `config.json` im selben Ordner. GitHub-Actions-
Workflow zum automatischen Bauen ist bereits vorhanden.

---

## 2. Datei-/Repo-Struktur

```
app.py                          <- das komplette Programm (Backend + Frontend in einer Datei)
ftps_upload_helper.py           <- separate Hilfsanwendung fuer den Bambu-FTPS-Upload (seit v1.5.3)
config.example.json             <- Beispiel-Config mit allen Druckertypen
requirements.txt                <- flask, paho-mqtt, pyinstaller
README.md                       <- ausführliche Nutzer-Doku (Setup, alle Druckertypen, GitHub Actions)
.gitignore
.github/workflows/build-exe.yml <- GitHub-Actions-Workflow, baut DruckerDashboard + FtpsUploadHelper
```

`app.py` ist bewusst **eine einzige Datei** (~1900 Zeilen): Backend-Klassen,
Flask-Routen und das komplette Frontend (HTML/CSS/JS) als ein großer
Python-String (`INDEX_HTML`, per `render_template_string` ausgeliefert).
Das hat sich für PyInstaller-Kompatibilität bewährt (kein `--add-data`
nötig, keine externen Template-/Static-Ordner).

---

## 3. Architektur-Überblick

- **Ein Connection-Objekt pro Drucker**, in einem eigenen Daemon-Thread,
  das per Polling (HTTP) oder MQTT-Callback seinen `self.status`-Dict
  aktuell hält. Jede Connection-Klasse hat `start()`, `stop()`, ein
  `status`-Dict mit `connected`, `last_update`, `error` u.a.
- **`DashboardApp`** verwaltet alle Connections (`self.connections`,
  `id -> Connection`), lädt/speichert `config.json`, dispatcht beim
  Anlegen (`_start_printer`) und beim Hinzufügen (`add_printer`) nach
  `type` auf die passende Connection-Klasse.
- **Flask-Routen** (`GET/POST /api/printers`, `DELETE /api/printers/<id>`,
  `GET /api/status`, `POST /api/printers/<id>/extras/<extra_id>/command`,
  `GET /camera/<id>`, `GET /`) sind dünne Wrapper um `DashboardApp`.
- **Frontend** pollt `GET /api/status` alle 2,5 Sekunden und rendert pro
  Drucker-Typ eine eigene Karten-Renderfunktion in JS
  (`renderBambuCard`, `renderFormlabsCard`, `renderOctoPrintCard`,
  `renderCrealityCard`, `renderUltimakerCard`). Der Dispatch dazu steht in
  `refresh()`.
- **Config-Datei** (`config.json`) liegt neben der exe/dem Skript
  (`base_dir()`), wird beim ersten Start automatisch mit
  `DEFAULT_CONFIG` angelegt. `load_config()` ergänzt fehlende Felder via
  `setdefault` (rückwärtskompatibel zu älteren `config.json`-Ständen).

### Wichtigste Codestellen zum Wiederfinden

| Was | Wo (grep-Muster) |
|---|---|
| Bekannte Druckertypen | `KNOWN_TYPES`, `FORMLABS_TYPES`, `CREALITY_TYPES` |
| Default-Konfiguration | `DEFAULT_CONFIG = {` |
| Verbindungsklassen | `class PrinterConnection`, `class FormlabsLocalApiConnection`, `class OctoPrintConnection`, `class CrealityConnection`, `class UltimakerConnection`, `class ExtrasMqttManager` |
| Druckauftrag senden (Bambu) | `PrinterConnection.preview_print()`, `PrinterConnection.send_print()`, `PrinterConnection.pause_mqtt()`/`resume_mqtt()`/`wait_for_mqtt_reconnect()`, `class ImplicitFtpTls`, `_find_ftps_upload_helper()`, `ftps_upload_helper.py` (separate Datei/exe), `_run_ftps_upload_worker()` + Sentinel-Check `--ftps-upload-worker` (Fallback, ganz frueh im Modul), `DashboardApp.prepare_print_job()`/`start_confirm_print_job()`/`cancel_print_job()`/`get_print_progress()`, Routen `POST /api/printers/<id>/print/prepare`\|`/confirm`, `GET .../print/progress/<job_id>`, `POST .../print/cancel`, JS `renderDropZone()`/`dzDrop()`/`openAmsModal()`/`confirmAmsModal()`/`pollAmsProgress()` |
| AMS-Zuordnungsvorschlag | `PrinterConnection.preview_print()`, `_parse_3mf_filaments()`, `_parse_plate1_used_filament_indices()`, `_find_matching_tray()`, `_types_compatible()`, `_slot_to_flat_index()` |
| Versionsnummer | `APP_VERSION` (ganz oben in `app.py`), Route `GET /api/version`, `.github/workflows/build-exe.yml` (liest die Version per Regex aus) |
| Orchestrierung | `class DashboardApp` |
| REST-Routen | `@app.route(` |
| Frontend-HTML/JS | `INDEX_HTML = r"""` (ein einziger großer String bis zum Dateiende) |
| Kartenrenderer (JS) | `function render...Card(p){` |
| Typ-Formular (JS) | `function toggleTypeFields()`, `function submitAdd()` |

---

## 4. Unterstützte Druckertypen (Stand jetzt)

| `type`-Wert | Hersteller/Gerät | Anbindung | Auth nötig? | Kamera |
|---|---|---|---|---|
| `bambu` | Bambu Lab (P1/X1/A1) | MQTT/TLS, Port 8883, `device/{serial}/report` | Access Code + Seriennummer | Ja, eigenes reverse-engineertes Protokoll Port 6000 |
| `formlabs` | Formlabs Drucker (Form 3/4/Fuse) | Formlabs **Local API** über `PreFormServer` (Standard `http://localhost:44388`) | Nein, aber PreFormServer muss laufen | Nein |
| `formlabs_wash` | Form Wash L | wie `formlabs`, andere Labels | Nein | Nein |
| `formlabs_cure` | Form Cure L | wie `formlabs`, andere Labels | Nein | Nein |
| `octoprint` | Beliebiger Drucker mit OctoPrint | REST-API, `/api/printer`, `/api/job` | **API-Key Pflicht** | Ja, mjpg-streamer-Standard-URL (überschreibbar) |
| `creality_k1`, `creality_k1c`, `creality_k1max`, `creality_k1se`, `creality_other` | Creality Klipper-Drucker | Moonraker-API, Port 7125 | Optional (meist LAN-trusted) | Ja, Crowsnest-Standard-URL (überschreibbar) |
| `ultimaker` | Ultimaker UM3/S-Serie/Factor 4 | offizielle lokale REST-API `/api/v1/` | Nein (nur lesend) | Ja, mjpg-streamer-Standard-URL (überschreibbar) |

**Wichtiges Architekturprinzip:** Die 5 `creality_*`-Typen und die 3
`formlabs_*`-Typen nutzen **jeweils dieselbe Connection-Klasse** – der
`type`-Wert dient dort nur der Beschriftung/dem Label auf der Karte, nicht
einer unterschiedlichen technischen Anbindung. Bei einer neuen
Geräte-„Version" für einen bereits unterstützten Hersteller reicht es
daher meist, nur den Type-Tuple und die Frontend-Labels zu erweitern,
**keine neue Connection-Klasse** zu schreiben.

---

## 5. Weitere Features

- **Bambu Lab: Druckauftrag per Drag & Drop, zweistufig (prepare/confirm).**
  Jede Bambu-Karte hat ein Ablage-Feld unter der AMS-Anzeige
  (`renderDropZone()`). Akzeptiert werden ausschließlich bereits fertig
  gesclicte `.gcode.3mf`-Dateien — bewusst keine rohen `.gcode`-Dateien,
  da sich diese laut mehreren Community-Quellen nicht zuverlässig per
  MQTT starten lassen (siehe README, Abschnitt 4a). Ablauf:
  1. **Prepare:** `POST /api/printers/<id>/print/prepare` (multipart,
     Feld `file`) → `DashboardApp.prepare_print_job()` speichert die
     Datei serverseitig temporär (unter einer `job_id`, siehe
     `DashboardApp._print_jobs`) und ruft
     `PrinterConnection.preview_print()` auf: liest Farbe+Typ jedes
     Filaments aus `Metadata/project_settings.config` und schlägt anhand
     der live gemeldeten AMS-Fächer (`self.status["ams"]`) eine Zuordnung
     vor. **Es wird an dieser Stelle noch nichts an den Drucker
     gesendet.**
  2. **Frontend-Dialog** (`openAmsModal()`): zeigt die Vorschläge als
     editierbare Dropdowns pro Filament — analog zur AMS-Zuordnung in
     Bambu Studio. Der Nutzer kann jede Zuordnung von Hand ändern, bevor
     etwas passiert.
  3. **Confirm:** `POST /api/printers/<id>/print/confirm` ({job_id,
     mapping}) → `DashboardApp.confirm_print_job()` →
     `PrinterConnection.send_print()`, der zuerst per FTPS (Port 990,
     **implizites** TLS via `ImplicitFtpTls`, Login `bblp`/Access Code)
     hochlädt und danach über die MQTT-Verbindung das Kommando
     `project_file` mit der vom Nutzer bestätigten `ams_mapping` sendet.
     Alternativ `POST .../print/cancel` verwirft den Vorgang und räumt
     die Temp-Datei auf.
  Setzt **Developer Mode** am Drucker voraus (separate Einstellung
  zusätzlich zum LAN-Modus). Nicht bestätigte Jobs werden nach 20 Minuten
  automatisch aufgeräumt (`_purge_stale_print_jobs()`).
  **Nur benötigte Filamente:** `_parse_3mf_filaments()` filtert die
  vollständige Filamentliste aus `project_settings.config` (JSON) auf
  die Schnittmenge mit den in `Metadata/slice_info.config` (XML, nur in
  gesliceten Dateien vorhanden) für **Plate 1** tatsächlich verbrauchten
  Filamenten (`_parse_plate1_used_filament_indices()`, `<filament id=…>`-
  Elemente, id ist dort 1-basiert). Fehlt `slice_info.config` oder liefert
  keine Treffer, fällt die Funktion defensiv auf die volle Liste zurück
  (kein Risiko, nur weniger präzise) — Quelle für die Dateistruktur:
  siehe Kommentare in `app.py` bzw. README Abschnitt 4a.
  **Dialog-UX (v1.3.0):** pro benötigtem Filament zwei Radio-Optionen
  — "Vorschlag aus Datei verwenden" (automatischer Farbmatch oder
  "Extern/manuell") vs. "Anderes Material aus dem AMS wählen" (Dropdown
  mit den übrigen erkannten Fächern). `confirmAmsModal()` liest pro Zeile
  aus, welche Option gewählt ist, bevor `mapping` ans Backend geht.
  **v1.3.2:** Hex-Farbcode zusätzlich zum Farbfeld als Text angezeigt.
  **v1.4.0:** Hex-Text durch **Farbwort** ersetzt (`NAMED_COLORS`-Palette
  + `colorNameFor()`: nächster Treffer per RGB-Abstand zu einer festen
  Liste deutscher Farbnamen; exakter Hex-Wert bleibt als `title`-Tooltip
  erhalten, keine geratene 1:1-Übersetzung, sondern eine bewusste
  Näherung für die Anzeige) — betrifft benötigtes Filament, Vorschlagstext
  und "anderes Material"-Dropdown gleichermaßen.
  **Fortschrittsanzeige (v1.4.0):** `POST .../print/confirm` läuft jetzt
  asynchron — `DashboardApp.start_confirm_print_job()` startet
  `conn.send_print(..., on_progress=...)` in einem Hintergrund-Thread
  (`threading.Thread(daemon=True)`) und antwortet sofort. Fortschritt
  liegt in `DashboardApp._print_progress[job_id]` (eigener Lock), wird
  über `GET .../print/progress/<job_id>` abgefragt. Frontend
  (`pollAmsProgress()`) pollt alle 400ms und aktualisiert Balken +
  Prozent + übertragene/gesamte Bytes (`formatBytes()`).
  **Wichtige Verhaltensänderung:** Bei einem Fehler werden Job und
  Temp-Datei jetzt bewusst NICHT gelöscht (anders als in v1.2.0/v1.3.x) —
  ein erneuter Klick auf "Drucken starten" nutzt dieselbe `job_id` und
  denselben Datei-Inhalt erneut, ohne dass der Nutzer die Datei nochmal
  hochladen oder die AMS-Zuordnung neu auswählen muss. Nur bei Erfolg,
  explizitem Cancel, oder nach 20 Minuten Inaktivität
  (`_purge_stale_print_jobs()`) wird aufgeräumt.
  **FTPS-Drosselung (v1.4.0):** In `_ftps_upload_once()` nach jedem
  gesendeten 4096-Byte-Block `time.sleep(0.015)` ergänzt. Hintergrund:
  Nutzer-Feedback zeigte, dass zwei unabhängige Upload-Versuche exakt
  bei demselben Byte-Stand abbrachen (69632 Bytes, = 17×4096) — ein
  scheinbar reproduzierbarer Abbruch, der auf einen druckerseitigen
  Pufferüberlauf beim SD-Karten-Schreiben hindeutete.
  **v1.4.1 - bessere Diagnose statt weiterer Theorien:** Nach Einbau der
  Drosselung brach ein weiterer Upload-Versuch bereits nach 8192 Bytes
  ab (2×4096) — anders als der 69632-Byte-Abbruch aus v1.4.0. Die
  Retry-Logik wurde erweitert, um **alle 3 Versuche mit jeweiligem
  Byte-Stand** zu sammeln und gemeinsam in der Fehlermeldung anzuzeigen
  (`attempts`-Liste in `_ftps_upload()`), statt nur den letzten Versuch.
  **v1.4.2 - konkreter Bug gefunden und behoben:** Die v1.4.1-Diagnose
  zeigte, dass **alle 3 Versuche exakt bei 8192 Bytes** abbrachen -
  perfekt reproduzierbar, kein Netzwerk-Zufall. Beim Review von
  `_ftps_upload_once()` fiel auf: der manuelle Upload-Ablauf (seit
  v1.3.1, fuer stufenspezifische Fehlermeldungen) ruft
  `ftp.ntransfercmd(f"STOR {remote_name}")` direkt auf und **ueberspringt
  dabei das `TYPE I`-Kommando**, das `ftplib.FTP.storbinary()` in der
  Python-Standardbibliothek normalerweise automatisch VOR jedem Upload
  sendet (`self.voidcmd('TYPE I')`), um den Server explizit auf
  Binaermodus umzuschalten. Jetzt in `_ftps_upload_once()` vor
  `ntransfercmd()` ergaenzt (`ftp.voidcmd("TYPE I")`).

  **v1.4.3 - Zwischenschritt, spaeter widerlegt.** Der Fehler trat
  danach erneut exakt bei 8192 Bytes auf, alle 3 Versuche identisch.
  Vermutung zu diesem Zeitpunkt: TLS-inspizierende Sicherheitssoftware
  oder Firmen-Firewall (Rueckfrage beim Nutzer ergab beides zutreffend:
  AV mit HTTPS-Pruefung installiert + verwaltetes Netzwerk - passte zum
  Muster, war aber letztlich die falsche Fährte). `_ftps_upload()`
  bekam einen `same_offset`-Check, der bei identischem Byte-Stand ueber
  alle 3 Versuche einen AV/Firewall-Hinweis anhaengte.

  **v1.4.4 - TATSAECHLICHE URSACHE GEFUNDEN UND BEHOBEN.** Der Nutzer
  konnte einen direkten Vergleichstest mit FileZilla durchfuehren
  (identische Datei, Drucker, Netzwerk, Rechner) - **FileZilla hat die
  Datei problemlos uebertragen.** Das widerlegt die AV-/Firewall-Theorie
  aus v1.4.3 eindeutig (waere ein AV/Firewall-Problem, haette es auch
  FileZilla betroffen) und beweist: das Problem lag im **Dashboard-
  eigenen Code**, nicht im Netzwerk. Der manuelle Upload-Ablauf (seit
  v1.3.1, fuer stufenspezifische Fehlermeldungen eingefuehrt) sendet in
  ungewoehnlich kleinen 4-KB-Bloecken mit kuenstlicher `time.sleep()`-
  Pause zwischen jedem Block (seit v1.4.0) - ein Sende-Muster, das kein
  "normaler" FTP-Client wie FileZilla verwendet. **Fix:** kompletter
  Rueckbau von `_ftps_upload_once()` auf `ftp.storbinary()` (Pythons
  Standardmechanismus - Bytes werden in ueblichen 8-KB-Bloecken direkt
  hintereinander gesendet, kein Pacing, `TYPE I` automatisch inklusive).
  Das entspricht jetzt strukturell dem, was auch FileZilla macht.
  Die stufenspezifische Fehlerdiagnose (PASV vs. Transfer vs. Abschluss-
  Bestaetigung, eingefuehrt in v1.3.1) wurde dabei bewusst aufgegeben -
  sie hatte ihren Zweck erfuellt (den Bug einzugrenzen), aber die
  benoetigte Umsetzung (manueller Ablauf statt `storbinary()`) war die
  eigentliche Fehlerursache. `storbinary()` unterstuetzt einen
  `callback`-Parameter, ueber den die Fortschrittsanzeige
  (`on_progress`) unveraendert weiterfunktioniert. Bei einem Fehler
  wird weiterhin der Byte-Stand aus dem Callback-Zaehler in die
  Fehlermeldung eingebaut (`sent_state["sent"]`), nur ohne die
  Phasen-Unterscheidung von vorher.
  Der `same_offset`-Hinweis aus v1.4.3 (AV/Firewall-Vermutung) wurde
  wieder entfernt, da er sich als falsch herausgestellt hat.
  **Lesson Learned fuer die Weiterarbeit:** Ein ungewoehnliches,
  selbstgebautes Sende-/Zeitmuster (viele kleine Bloecke + kuenstliche
  Pausen) kann selbst OHNE erkennbaren Netzwerk-/TLS-Grund zu
  Verbindungsabbruechen fuehren, die sich wie ein Server-/Netzwerk-
  problem anfuehlen. Ein Vergleichstest mit einem bekannt funktionierenden
  Referenz-Client (hier: FileZilla) war der entscheidende Schritt, um
  das einzugrenzen - fuer aehnliche Debugging-Situationen in Zukunft
  eine gute erste Anlaufstelle, statt weiter an TLS-Parametern zu drehen.

  **v1.4.5 - zweite versteckte Abweichung gefunden und entfernt.**
  Trotz v1.4.4 trat der Fehler weiterhin auf, jetzt bei einem anderen,
  aber wieder festen Vielfachen der `storbinary()`-Blockgroesse
  (73728 = 9×8192 Bytes, alle 3 Versuche identisch). Der Rueckbau in
  v1.4.4 hatte nur die manuelle Sende-Schleife selbst ersetzt - die
  `ImplicitFtpTls`-Klasse (definiert seit v1.3.1, unveraendert durch
  v1.4.4) hatte aber IMMER NOCH eine ueberschriebene `ntransfercmd()`-
  Methode, die bewusst OHNE TLS-Session-Wiederverwendung fuer die
  Datenverbindung arbeitete (Theorie aus v1.3.1, nie bestaetigt).
  Zusaetzlich war weiterhin `ctx.maximum_version = TLSv1_2` gesetzt
  (aus v1.2.0, ebenfalls nie als tatsaechlich hilfreich bestaetigt).
  Beides sind Abweichungen vom Standardverhalten von Python/`ftplib`,
  die FileZilla mit hoher Wahrscheinlichkeit NICHT hat (FileZilla laesst
  TLS-Version frei aushandeln und nutzt vermutlich Session-Resumption
  wo sinnvoll). Beide Overrides entfernt:
  - `ImplicitFtpTls.ntransfercmd()`-Override komplett geloescht → Python
    nutzt wieder das eingebaute `ftplib.FTP_TLS.ntransfercmd()`
    (inklusive Session-Wiederverwendung via `session=self.sock.session`).
    `ImplicitFtpTls` ueberschreibt jetzt nur noch `sock` (Property) fuer
    das beim Verbindungsaufbau zwingend noetige Socket-Wrapping fuer
    implizites TLS - keine weiteren Anpassungen.
  - `ctx.maximum_version = ssl.TLSVersion.TLSv1_2` in `_ftps_upload_once()`
    entfernt → TLS-Version wird wieder frei ausgehandelt (typischerweise
    TLS 1.3, falls vom Drucker unterstuetzt).
  `ssl.OP_IGNORE_UNEXPECTED_EOF` (aus v1.2.0) wurde NICHT entfernt - das
  betrifft nur den sauberen Verbindungsabschluss (close_notify-
  Handling), nicht die Datenuebertragung selbst, und ist ein reines
  Sicherheitsnetz ohne beobachtete Nebenwirkungen.
  **Damit besteht der komplette FTPS-Upload-Pfad jetzt nur noch aus dem
  fuer implizites TLS absolut zwingenden Minimum plus Standard-
  `ftplib`-Verhalten** - keine weiteren spekulativen Anpassungen im
  Python-Code selbst mehr uebrig, die entfernt werden koennten. Das
  stellte sich jedoch als NICHT ausreichend heraus (siehe v1.4.6): der
  Fehler trat danach bei exakt demselben Byte-Wert wie zuvor erneut auf,
  was zeigt, dass die in v1.4.5 entfernten Overrides gar nicht die
  eigentliche Ursache waren.
  **v1.4.6/v1.4.7 - curl-Experiment (seit v1.4.9 wieder verworfen).**
  Nachdem v1.4.5 (minimaler Python-`ftplib`-Code) auf einem X1C erneut
  exakt beim selben Byte-Wert scheiterte, wurde der Upload testweise auf
  einen **`curl`-Unterprozess** umgestellt (curl bringt eine eigene,
  von Python unabhaengige TLS-Implementierung mit). v1.4.7 ergaenzte
  `--verbose`-Diagnose und ein `--tlsv1.2 --tls-max 1.2`-Experiment
  (curl unter Windows nutzt **Schannel**, nicht OpenSSL - ein anderer
  Code-Pfad als der vorherige Python-Versuch). **Ergebnis:** curl
  scheiterte auf X1C UND X1E genauso wie Python zuvor
  (`Send failure: Connection was reset` auf der Datenverbindung,
  Kontrollverbindung stets erfolgreich) - siehe v1.4.9 unten fuer die
  Konsequenz daraus. v1.4.8 war ein reiner Build-Fix ohne
  Verhaltensaenderung (f-string-Syntax, siehe Lessons Learned Punkt 6).

  **v1.4.9 - RUeCKBAU auf Python-`ftplib` (curl-Ansatz verworfen).**
  Der entscheidende neue Befund: curl (Schannel) scheitert auf X1C/X1E
  mit **exakt demselben Muster** wie Python zuvor - Kontrollverbindung
  (Port 990) baut sauber auf, die anschliessende PASV-Datenverbindung
  wird aber sofort zurueckgesetzt (`Send failure: Connection was
  reset`), noch bevor im `--verbose`-Log eine TLS-Aushandlung fuer diese
  zweite Verbindung sichtbar wird. **Das bedeutet: sowohl Python/OpenSSL
  als auch curl/Schannel scheitern an der X1-Serie, nur FileZilla
  (vermutlich GnuTLS) funktioniert.** Recherche ergab einen sehr
  aehnlichen dokumentierten Fall (anderer FTPS-Server, andere Clients):
  manche strikten FTPS-Server verlangen, dass die TLS-Sitzung der
  Datenverbindung nachweislich eine Fortsetzung der Kontrollverbindungs-
  Sitzung ist - FileZilla implementiert das korrekt, andere Clients
  (im gefundenen Fall FireFTP) nicht (Quelle: Adobe-Community-Forum,
  "FTPS TLS session resumption", 2015). Ob das exakt unsere Ursache
  ist, bleibt unbestaetigt - Python uebergibt zwar bereits
  `session=self.sock.session` beim Wrap der Datenverbindung (siehe
  `ftplib.FTP_TLS.ntransfercmd()`, Standardverhalten seit v1.4.5), was
  eigentlich Session-Resumption bedeuten sollte, scheiterte aber
  trotzdem - daher bleibt die genaue Ursache letztlich ungeklaert.
  **Entscheidung:** Da weder Python noch curl das X1-Problem loesen,
  aber curl eine zusaetzliche externe Abhaengigkeit (muss auf dem
  System vorhanden sein) einfuehrt OHNE einen Vorteil zu bringen, wurde
  bewusst auf den einfacheren Python-`ftplib`-Ansatz (Stand v1.4.5)
  zurueckgewechselt - der nachweislich fuer einen Teil der Druckerflotte
  (A1 Mini) zuverlaessig funktioniert, waehrend der curl-Ansatz keinen
  Mehrwert brachte. Konkrete Aenderungen:
  - `PrinterConnection._ftps_upload_once()`: komplett zurueckgebaut auf
    `ImplicitFtpTls` + `ftp.storbinary()` (identisch zum Code-Stand von
    v1.4.5 - kein `curl`, kein `subprocess`, keine `--verbose`-Log-
    Verarbeitung mehr).
  - `class ImplicitFtpTls`: Docstring aktualisiert, Klasse ist wieder
    aktiv (wird wieder instanziiert). Keine funktionale Aenderung
    gegenueber v1.4.5 (nur `sock`-Property-Override, kein
    `ntransfercmd()`-Override).
  - Nicht mehr benoetigte Imports entfernt: `subprocess`, `shutil`, `re`.
  - `_ftps_upload()` (Retry-Wrapper) faengt wieder
    `(ssl.SSLError, OSError, EOFError, RuntimeError, *ftplib.all_errors)`
    statt der curl-spezifischen `subprocess.SubprocessError`.
  - README Abschnitt 4a dokumentiert das X1-Problem jetzt offen als
    **bekannte, ungeloeste Einschraenkung** mit FileZilla als
    Workaround, statt eine (nicht mehr zutreffende) technische
    curl-Begruendung zu zeigen.
  **Getestet:** Regex-Sicherheitsscan auf die in v1.4.8 behobene
  f-string-Backslash-Problematik (keine neuen Faelle); vollstaendiger
  Async-Confirm-Flow inkl. Progress-Polling gegen `127.0.0.1:990`
  (erwartete Verbindungsverweigerung, aber korrekter Codepfad ohne
  curl-Referenzen bestaetigt); Verifikation per `inspect.getsource()`,
  dass `ImplicitFtpTls.ntransfercmd` wieder von `ftplib.FTP_TLS` geerbt
  wird (kein Override mehr) und `_ftps_upload_once` wieder
  `ImplicitFtpTls`/`storbinary` statt `curl` nutzt.
  **Fuer die Weiterarbeit:** Das X1-Problem ist damit NICHT geloest,
  nur die Zusatzkomplexitaet von curl ohne Nutzen wieder entfernt.
  Sinnvolle naechste Schritte, falls jemand weiter forschen moechte:
  (1) OpenSSL-basierter curl-Build (https://curl.se/windows/, nicht die
  Windows-eigene Schannel-Variante) manuell testen, um Schannel als
  Ursache ein- oder auszuschliessen (Ergebnis stand zum Zeitpunkt dieser
  Uebergabe noch aus); (2) FileZillas tatsaechlich ausgehandelte
  TLS-Version/Cipher-Suite aus dessen Nachrichtenprotokoll auslesen und
  versuchen, dieselbe explizit zu erzwingen; (3) Wireshark-Mitschnitt
  von FileZilla vs. Python/curl zum direkten ClientHello-Vergleich;
  (4) Firmware-Update-Check am X1C/X1E bzw. Bambu-Lab-Support-Kontakt,
  falls sich alle Client-seitigen Optionen als wirkungslos erweisen.

  **v1.5.0 - GRUNDURSACHE GEFUNDEN UND BEHOBEN (per Recherche-Auftrag).**
  Da alle bisherigen Client-seitigen Experimente (TLS-Version, Session-
  Reuse-An/Aus, curl statt Python, verschiedene Blockgroessen/Pacing)
  ergebnislos blieben, wurde ein dedizierter Recherche-Auftrag gestartet
  (Web-Suche nach bekannten Bambu-X1-FTPS-Problemen, Analyse bestehender
  Open-Source-Bambu-Python-Bibliotheken). Ergebnis, durch mehrere
  unabhaengige Quellen bestaetigt:
  - Die **X1-Serie (X1C/X1E) laeuft intern auf vsftpd** mit aktivierter
    Option `require_ssl_reuse` (laut vsftpd.conf(5)-Manpage seit v2.1.0,
    Standard: an). Diese Option verlangt, dass die TLS-Sitzung der
    PASV-Datenverbindung nachweislich eine Fortsetzung der Sitzung der
    Kontrollverbindung ist - ein Schutz gegen Session-Hijacking. Quelle:
    **greghesp/ha-bambulab, GitHub Discussion #1497** ("ftp TLS session
    copy?", Aug 2025): "we've determined that the X1C is running vsftpd
    and vsftpd forces ssl session context reuse as a form of avoiding
    session hijack." Der server-seitige Fehlertext dafuer (`522 SSL
    connection failed; session reuse required`) ist im TFyre/bambu-farm-
    Repo sowie in einem Bambu-Forum-Thread ("Issues with ftp connection")
    dokumentiert.
  - **Pythons `ftplib.FTP_TLS.ntransfercmd()` uebergibt bereits
    standardmaessig `session=self.sock.session`** beim Aufbau der
    Datenverbindung - die Sitzung wird also korrekt wiederverwendet.
    ABER: `ftplib.FTP.storbinary()` ruft am ENDE der Uebertragung
    automatisch `conn.unwrap()` auf, wenn `conn` eine `SSLSocket` ist
    (bestaetigt durch Einsicht in den tatsaechlichen CPython-Quellcode,
    `inspect.getsource(ftplib.FTP.storbinary)` in diesem Projekt selbst
    ausgefuehrt). Das zerstoert die gemeinsame TLS-Sitzung in einer
    Weise, die vsftpds `require_ssl_reuse`-Pruefung nicht toleriert -
    exakt das beobachtete `ssl.SSLEOFError` ("EOF occurred in violation
    of protocol").
  - **Warum A1 Mini nicht betroffen war:** die A1/P1-Serie nutzt einen
    leichteren, ESP32-basierten FTPS-Server ohne diese strikte vsftpd-
    Pruefung (Quelle: ha-bambulab-Maintainer-Aussage in derselben
    Diskussion).
  - **Warum FileZilla immer funktionierte:** es fuehrt TLS-Session-
    Wiederverwendung durchgehend korrekt durch und unterlaesst das
    problematische abschliessende `unwrap()`.
  - **Community-Bestaetigung des Fixes:** mehrere bestehende Open-Source-
    Bambu-Python-Bibliotheken loesen exakt dieses Problem identisch:
    `greghesp/ha-bambulab` (`pybambu/models.py`) mit einer eigenen
    `storbinary_no_unwrap()`-Funktion; `bambulabs_api` (PyPI/GitHub,
    Version 2.6.6, Commit `5bd1e84`) mit einer explizit als "Add unwrap
    connection option" bezeichneten Ergaenzung.
  - **Zusaetzliche Bestaetigung (unabhaengiger Beleg fuer generelles
    Bambu-FTPS-Datenkanal-Verhalten):** BambuStudio GitHub Issue #1404
    ("Uploading files to the P1P via ftps hangs/resets... at ~256kb")
    zeigt, dass Bambu-Drucker-Firmware generell fuer reproduzierbare
    Abbrueche bei groesseren FTPS-Uploads bekannt ist, unabhaengig vom
    Client - konsistent mit dem hier beobachteten Verhalten.
  **Implementierte Aenderung:**
  - Neue Modul-Funktion `_storbinary_no_unwrap(ftp, cmd, fp, blocksize,
    callback)`: Nachbau von `ftplib.FTP.storbinary()`, aber OHNE das
    abschliessende `conn.unwrap()`. Wird in `_ftps_upload_once()` anstelle
    von `ftp.storbinary()` aufgerufen.
  - `_ftps_upload_once()`: TLS zusaetzlich wieder auf maximal Version 1.2
    begrenzt (`ctx.maximum_version = TLSv1_2`) - laut Recherche ist
    Session-Reuse bei TLS 1.2 (Session-ID-basiert) in der Praxis
    zuverlaessiger als bei TLS 1.3 (Session-Ticket-basiert) ueber zwei
    getrennte Sockets hinweg. **Wichtig:** diese Einschraenkung allein
    hatte in fruehen Versuchen (v1.2.0-v1.4.4) NICHT geholfen - sie wirkt
    laut Recherche nur in Kombination mit dem No-Unwrap-Fix.
  - `ImplicitFtpTls`: unveraendert (kein `ntransfercmd()`-Override mehr
    noetig, da Pythons Standardverhalten die Session bereits korrekt
    uebergibt - das eigentliche Problem lag ausschliesslich im
    `unwrap()`-Aufruf innerhalb von `storbinary()`).
  **Getestet (ohne echten Drucker):** Ein Mock-`SSLSocket`-Objekt
  verifiziert, dass `_storbinary_no_unwrap()` `unwrap()` NIE aufruft, alle
  Daten korrekt sendet, und dieselben FTP-Kommandos (`TYPE I`, `STOR ...`)
  wie das Original sendet; Quellcode-Vergleich mit dem echten
  `ftplib.FTP.storbinary()` (per `inspect.getsource()`) bestaetigt, dass
  der einzige strukturelle Unterschied das fehlende `unwrap()` ist;
  vollstaendiger Async-Confirm-Flow inkl. Progress-Polling weiterhin
  funktionsfaehig.

  **v1.5.0 WAR EINE FALSCHE FAEHRTE - siehe v1.5.1 unten fuer die
  tatsaechliche Ursache.** Der No-Unwrap-Fix aenderte beim echten Test
  gegen X1C NICHTS (exakt derselbe Byte-Abbruch bei 73728 wie vorher).
  Der Python-Bugtracker-Issue-31727-Fix (der kanonische, im Web breit
  zitierte Ursprung des "no unwrap"-Patterns) behebt nachweislich einen
  ANDEREN Fehler (Abbruch am ENDE einer Uebertragung/bei `nlst()`),
  nicht einen Abbruch WAEHREND der Uebertragung bei 11% - die
  Diagnose war also fuer ein aehnlich aussehendes, aber tatsaechlich
  anderes Problem korrekt, traf aber nicht das hier vorliegende.

  **v1.5.1 - ZWISCHENSCHRITT: Prozess-Isolation (loeste das Problem noch
  NICHT vollstaendig, siehe v1.5.2 fuer die tatsaechliche Loesung).** Um
  die vsftpd-Theorie aus v1.5.0 endgueltig zu pruefen, wurde ein KOMPLETT
  EIGENSTAENDIGES Diagnose-Skript erstellt (`ftps_test_minimal.py`, als
  exe via eigenem GitHub-Actions-Workflow `build-ftps-test.yml` gebaut,
  da der Nutzer keine `.py`-Dateien direkt ausfuehren kann) - voellig
  unabhaengig von Flask/MQTT/Threading, nur reines `ftplib`. Ergebnis,
  in zwei Schritten:
  1. Erster Testlauf (mit No-Unwrap-Trick): Datei wurde **VOLLSTAENDIG
     (100%) uebertragen**, brach aber bei der Abschluss-Bestaetigung
     mit `426 Failure reading network stream` ab - ein voellig anderes
     Symptom als der bisherige 11%-Abbruch! Das allein war schon der
     entscheidende Hinweis: ausserhalb der App kommt der Upload viel
     weiter als innerhalb.
  2. Zweiter Testlauf (Vergleichsskript mit ZWEI automatischen Tests:
     Test A = normales `unwrap()`, Test B = No-Unwrap-Trick):
     **Test A war ein VOLLSTAENDIGER ERFOLG** ("226 Transfer complete."),
     Test B scheiterte wie erwartet am Abschluss (426-Fehler).
  **Schlussfolgerung (zum damaligen Zeitpunkt, spaeter praezisiert):**
  Standard-`ftplib`-Verhalten funktioniert einwandfrei, WENN es
  ausserhalb der App als eigener Prozess laeuft. Vermutung war zunaechst
  **Ressourcen-/Scheduling-Konkurrenz zwischen zwei Python-Threads im
  selben Prozess** (GIL-Kontention waehrend TLS-Handshake-Schritten).
  **Implementierte Aenderung (v1.5.1):**
  - `_storbinary_no_unwrap()` entfernt, `ImplicitFtpTls` wieder auf
    reines Standardverhalten zurueckgesetzt (kein Override mehr noetig).
  - **Der komplette FTPS-Upload laeuft seitdem in einem eigenen
    Betriebssystem-PROZESS statt einem Thread.** Neue Funktion
    `_run_ftps_upload_worker(argv)`: fuehrt NUR den Upload durch (kein
    Flask, kein MQTT, keine `DashboardApp`), meldet Fortschritt/Ergebnis
    als JSON-Zeilen auf stdout. Ausgeloest ueber einen Sentinel-
    Kommandozeilen-Parameter `--ftps-upload-worker <ip> <access_code>
    <datei> <ziel>`, mit dem sich die exe/das Skript selbst per
    `subprocess.Popen([sys.executable, ...])` erneut aufruft.
  - **Wichtig fuer die Code-Struktur:** Der Sentinel-Check
    (`if len(sys.argv) > 1 and sys.argv[1] == "--ftps-upload-worker":
    ... sys.exit(0)`) sitzt bewusst ganz frueh im Modul, DIREKT nach der
    `ImplicitFtpTls`-Klasse und VOR `dash = DashboardApp()` - andernfalls
    wuerde der Kindprozess unnoetig auch alle Drucker-Verbindungen
    aufbauen und einen zweiten Flask-Server versuchen zu starten. Bei
    kuenftigen Refactorings diese Reihenfolge unbedingt beibehalten.
  - `PrinterConnection._ftps_upload_once()` startet den Subprozess,
    liest dessen stdout zeilenweise (JSON: `{"type": "progress", ...}`,
    `{"type": "done", ...}`, `{"type": "error", ...}`) und uebersetzt
    das in `on_progress()`-Aufrufe bzw. eine `RuntimeError` mit Byte-
    Stand, exakt wie vorher aus Sicht des Aufrufers (`send_print()`,
    `DashboardApp.start_confirm_print_job()` etc. mussten NICHT
    angepasst werden - gilt weiterhin in v1.5.2).
  - Diagnose-Tool `ftps_test_minimal.py` (Version 2, mit automatischem
    A/B-Vergleichstest) plus `.github/workflows/build-ftps-test.yml`
    wurden dem Nutzer separat als eigenstaendiges Mini-Repo/Zip
    bereitgestellt - nicht Teil des Haupt-Dashboards, aber nuetzlich
    fuer aehnliche Diagnosen in Zukunft.

  **v1.5.2 - TATSAECHLICHE URSACHE: gleichzeitige MQTT- + FTPS-
  Verbindung, nicht Python-Threading.** Beim echten Test gegen X1C
  scheiterte der Upload trotz vollstaendiger Prozess-Isolation (v1.5.1)
  ERNEUT, mit demselben Byte-Bereich (65536-73728) wie zuvor. Das
  widerlegt die GIL-/Thread-Kontentions-Theorie aus v1.5.1 endgueltig:
  ein komplett separater Betriebssystem-Prozess kann per Definition
  nicht am Python-GIL des Hauptprozesses "hungern". Was in ALLEN
  bisherigen Versuchen (Thread wie Prozess) unveraendert blieb: die
  MQTT-Verbindung im Hauptprozess lief durchgehend weiter, waehrend die
  FTPS-Datenverbindung parallel dazu aufgebaut wurde. Das Standalone-
  Diagnoseskript aus v1.5.1 hatte dagegen NIE eine MQTT-Verbindung
  offen. **Schlussfolgerung: der Drucker (X1-Serie) kann eine aktive
  MQTT-Verbindung und eine neue, datenintensive FTPS-Verbindung
  offenbar nicht zuverlaessig gleichzeitig bedienen** - vermutlich eine
  Einschraenkung des eingebetteten Netzwerk-Stacks bei gleichzeitiger
  Auslastung durch zwei TLS-Sitzungen. Das erklaert auch, warum A1 Mini
  nicht betroffen ist (vermutlich robusterer Netzwerk-Stack) und warum
  FileZilla/das Standalone-Testskript immer funktionierten (dort war nie
  eine parallele MQTT-Verbindung zum selben Drucker aktiv).
  **Implementierte Aenderung:**
  - `PrinterConnection` bekommt drei neue Methoden: `pause_mqtt()`
    (trennt die MQTT-Verbindung und unterdrueckt automatisches
    Reconnect via ein neues `self._paused`-Flag, das `_connect_loop()`
    respektiert), `resume_mqtt()` (hebt die Pause wieder auf), und
    `wait_for_mqtt_reconnect(timeout)` (blockierendes Warten bis
    `status["connected"]` wieder `True` ist, mit Timeout).
  - `send_print()` ruft jetzt `pause_mqtt()` VOR dem FTPS-Upload auf.
    Bei **Erfolg** wird `resume_mqtt()` + `wait_for_mqtt_reconnect(15)`
    aufgerufen, BEVOR `_request_print()` (das MQTT-Kommando fuer den
    Druckstart) gesendet wird - die Verbindung muss dafuer wieder aktiv
    sein. Bei **Fehlschlag** wird `resume_mqtt()` zwar auch aufgerufen
    (Verbindung soll sich im Hintergrund von selbst erholen), aber
    bewusst NICHT blockierend auf den Reconnect gewartet - der Fehler
    soll dem Nutzer sofort angezeigt werden, nicht erst nach bis zu 15s
    zusaetzlicher Wartezeit (siehe Test unten, das hat die Fehler-
    Rueckmeldung von ca. 20s auf ca. 5s beschleunigt).
  - `_connect_loop()` prueft `self._paused` am Anfang jeder Iteration
    und ueberspringt in dem Fall den Verbindungsaufbau komplett (statt
    wie vorher automatisch nach wenigen Sekunden neu zu verbinden).
  **Getestet (ohne echten Drucker):** Isolierte Tests fuer
  `pause_mqtt()`/`resume_mqtt()`/`wait_for_mqtt_reconnect()` mit einem
  Fake-MQTT-Client (verifiziert: Disconnect wird ausgeloest, Status
  wird korrekt gesetzt, Timeout-Verhalten korrekt); Test, dass
  `_connect_loop()` waehrend der Pause KEINEN Verbindungsversuch
  unternimmt; Test, dass nach `resume_mqtt()` automatisch neu verbunden
  wird; Test der Aufrufreihenfolge in `send_print()` (pause → Upload →
  bei Erfolg: resume + wait, bei Fehler: nur resume, kein wait) per
  Mock; vollstaendiger Async-Confirm-Flow-Test bestaetigt sowohl die
  korrekte Funktion als auch die beschleunigte Fehler-Rueckmeldung
  (ca. 5s statt vorher ca. 20s bei einem fehlgeschlagenen Upload).
  **Ein Test gegen einen echten X1C/X1E mit dieser MQTT-Pause-Loesung
  konnte in dieser Umgebung nicht durchgefuehrt werden** - Bestaetigung
  durch den Nutzer steht zum Zeitpunkt dieser Übergabe noch aus. Sollte
  auch das nicht helfen, waere der naechste Verdacht ein generelleres
  Netzwerk-/Bandbreitenproblem statt einer reinen Verbindungsanzahl-
  Beschraenkung - dann waere ein Wireshark-Mitschnitt waehrend eines
  Uploads (mit UND ohne aktive MQTT-Verbindung, zum direkten Vergleich)
  der naechste sinnvolle Schritt.
  **v1.5.2 WAR ERNEUT EINE FALSCHE FAEHRTE - siehe v1.5.3 unten fuer die
  tatsaechliche, bestaetigte Ursache.** Trotz MQTT-Pause scheiterte der
  Upload beim echten Test auf X1C wieder exakt identisch (73728 Bytes),
  was die "gleichzeitige Verbindung"-Theorie ebenfalls widerlegte.

  **v1.5.3 - TATSAECHLICHE, BESTAETIGTE URSACHE: Selbstaufruf-Muster
  wird von Windows Defender als verdaechtig eingestuft.** Der
  entscheidende Vergleichstest: Nutzer fuehrte auf einem komplett
  isolierten Testnetzwerk (nur Windows Defender, kein Firmen-Proxy/AV)
  mit EXAKT DERSELBEN DATEI nacheinander (a) das Dashboard und (b) das
  eigenstaendige Diagnose-Tool (`ftps_test_minimal.py`/`FtpsTest.exe`)
  aus. Ergebnis: **Dashboard scheiterte identisch bei 73728 Bytes (11%),
  das Standalone-Tool uebertrug im selben Moment, im selben Netzwerk,
  mit derselben Datei erfolgreich 100% ("226 Transfer complete").** Das
  schliesst Netzwerk, Firmen-Sicherheitssoftware UND jede TLS-/Threading-
  /Verbindungs-Theorie endgueltig aus - der einzige verbleibende
  Unterschied zwischen beiden war der Programm-Aufrufmechanismus:
  - Standalone-Tool: normaler Programmstart durch den Nutzer (Doppelklick).
  - Dashboard (bis v1.5.2): rief sich SELBST mit einem versteckten
    Kommandozeilen-Argument (`--ftps-upload-worker`) erneut auf, um den
    Upload in einem separaten Prozess durchzufuehren (die Prozess-
    Isolation aus v1.5.1 war technisch korrekt umgesetzt - nur der
    AUFRUFMECHANISMUS war das eigentliche Problem, nicht die
    Prozess-Isolation an sich).
  **Erklaerung:** Ein Programm, das eine Kopie von sich selbst mit einem
  versteckten Kommandozeilen-Flag startet, ist ein Verhaltensmuster, das
  Sicherheitssoftware wie Windows Defender aehnlich wie manche
  Schadsoftware-Lademechanismen (z. B. Dropper/Loader-Patterns)
  behandeln kann - mit moeglichen Auswirkungen auf den Netzwerkverkehr
  des betroffenen Prozesses (z. B. durch Echtzeitueberwachung/AMSI-
  Scanning, das TLS-Handshake-Timing stoert), OHNE dass irgendetwas
  sichtbar blockiert oder eine Warnung angezeigt wird - was erklaert,
  warum dieses Verhalten so schwer zu diagnostizieren war.
  **Implementierte Aenderung:**
  - Neue Datei `ftps_upload_helper.py`: eigenstaendiges Skript mit der
    kompletten FTPS-Upload-Logik (eigene, minimale Kopie von
    `ImplicitFtpTls` + Hauptfunktion), das JSON-Fortschritts-/
    Ergebniszeilen auf stdout ausgibt - vom Aufruf-Interface her
    identisch zum bisherigen `_run_ftps_upload_worker()`.
  - Neue Funktion `_find_ftps_upload_helper()` in `app.py`: sucht nach
    `FtpsUploadHelper.exe` (Windows) bzw. `FtpsUploadHelper` (macOS/
    Linux) im selben Ordner wie die Haupt-exe (`base_dir()`).
  - `PrinterConnection._ftps_upload_once()`: ruft jetzt bevorzugt die
    gefundene Helfer-exe direkt auf (`subprocess.Popen([helper_path,
    ip, access_code, local_path, remote_name])`) - KEIN Selbstaufruf
    mehr. Der alte Sentinel-Mechanismus (`_run_ftps_upload_worker()` +
    der frueh im Modul plazierte `--ftps-upload-worker`-Check) bleibt
    als Fallback bestehen, falls die Helfer-exe (noch) nicht gefunden
    wird (z. B. im Entwicklungsbetrieb ohne vorherigen Build, oder
    falls jemand nur `app.py` ohne die separate Helfer-exe verteilt).
  - `pause_mqtt()`/`resume_mqtt()`/`wait_for_mqtt_reconnect()` aus
    v1.5.2 wurden NICHT zurueckgebaut - sie schaden nicht und bleiben
    als zusaetzliche, risikoarme Vorsichtsmassnahme bestehen (auch wenn
    sie sich als nicht ursaechlich fuer das eigentliche Problem erwiesen
    haben).
  - **Build-Workflow (`build-exe.yml`) grundlegend angepasst:** baut
    jetzt PRO PLATTFORM zwei Executables (`DruckerDashboard` +
    `FtpsUploadHelper`) und packt beide gemeinsam in ein Zip, da sie im
    selben Ordner ausgeliefert werden muessen (`_find_ftps_upload_helper()`
    sucht relativ zum Ordner der Haupt-exe). Windows nutzt dafuer
    PowerShells eingebautes `Compress-Archive` (keine zusaetzliche
    Tool-Abhaengigkeit), macOS weiterhin `zip -j` (bewahrt das
    Exec-Bit fuer beide Binaries).
  **Getestet (ohne echten Drucker):** `_find_ftps_upload_helper()` mit
  und ohne vorhandene Helfer-Datei verifiziert; `_ftps_upload_once()`
  mit einem Fake-Helfer-Skript, das die JSON-Kommunikation exakt
  nachbildet (Progress + Done) - bestaetigt, dass die Helfer-exe
  bevorzugt und korrekt angesprochen wird; Fallback-Pfad (kein Helfer
  vorhanden) weiterhin funktionsfaehig; vollstaendiger Async-Confirm-
  Flow-Test weiterhin erfolgreich. **Ein Test gegen einen echten
  X1C/X1E mit dieser Loesung konnte in dieser Umgebung nicht
  durchgefuehrt werden** - Bestaetigung durch den Nutzer stand zum
  Zeitpunkt dieser Übergabe noch aus, ist aber durch den vorangegangenen
  direkten A/B-Vergleichstest des Nutzers (Standalone-Tool vs.
  damaliges Dashboard, identische Datei/Netzwerk) außergewöhnlich gut
  abgesichert - das war der erste Fall in der gesamten Debugging-
  Chronologie, bei dem der einzige verbleibende Unterschied klar
  identifizierbar UND eine bekannte, dokumentierte Klasse von Sicherheits-
  software-Verhalten war (nicht nur eine plausible Theorie).

  **v1.5.3 WAR EBENFALLS EINE FALSCHE FAEHRTE - siehe v1.5.4 unten fuer
  die tatsaechliche, endgueltig bestaetigte Ursache.** Der Nutzer testete
  `FtpsUploadHelper.exe` komplett eigenstaendig (Dashboard vollstaendig
  geschlossen, Helfer-exe manuell von der Kommandozeile mit den
  Positions-Argumenten aufgerufen) - und selbst DANN scheiterte der
  Upload identisch bei 73728 Bytes. Das widerlegte die "Selbstaufruf-
  Muster wird als verdaechtig eingestuft"-Theorie endgueltig: die
  Helfer-exe ist ein voellig normales, unabhaengig gestartetes Programm
  ohne jeden Bezug zum Dashboard-Prozess, und scheiterte trotzdem exakt
  gleich.

  **v1.5.4 - TATSAECHLICHE, ENDGUELTIG BESTAETIGTE URSACHE: fehlende
  TLS-Session-Wiederverwendung - eine SELBST VERURSACHTE Regression.**
  Nachdem auch die separate Helfer-exe eigenstaendig scheiterte, blieb
  nur noch ein direkter Code-Vergleich zwischen der scheiternden
  `ftps_upload_helper.py` und dem erfolgreichen `ftps_test_minimal.py`
  (Test A). Der entscheidende Unterschied: `ftps_test_minimal.py`
  ueberschreibt `ntransfercmd()` explizit mit
  `session=self.sock.session`, waehrend `ftps_upload_helper.py` (und
  `app.py`s `ImplicitFtpTls` seit v1.4.5) KEIN solches Override hatte,
  in der Annahme, Pythons `ftplib.FTP_TLS.ntransfercmd()` wuerde das
  bereits automatisch tun. **Diese Annahme war schlicht falsch** -
  verifiziert per `inspect.getsource(ftplib.FTP_TLS.ntransfercmd)`
  direkt in diesem Projekt (Python 3.12): die eingebaute Methode
  uebergibt beim Wrap der Datenverbindung nur `server_hostname=self.host`,
  OHNE jedes `session=`-Argument. Es fand also seit v1.4.5 (als das
  urspruengliche `ntransfercmd()`-Override entfernt wurde) UEBERHAUPT
  KEINE TLS-Session-Wiederverwendung mehr fuer die Datenverbindung
  statt - fuer die X1-Serie (vsftpd mit `require_ssl_reuse`, siehe
  v1.5.0-Recherche weiter oben) fatal.
  **Das bedeutet: die urspruengliche vsftpd/Session-Reuse-Diagnose aus
  v1.5.0 war INHALTLICH KORREKT.** Sie wurde damals nur falsch
  umgesetzt und getestet: `_storbinary_no_unwrap()` (v1.5.0) entfernte
  zwar das problematische `unwrap()` am Ende, ergaenzte aber NIE die
  fehlende Session-Wiederverwendung beim Verbindungsaufbau (da zu dem
  Zeitpunkt faelschlich angenommen wurde, das sei bereits Standard-
  verhalten). Der v1.5.0-Test war also faktisch ein Test von "kein
  `unwrap()` UND weiterhin keine Session-Wiederverwendung" - beide
  fehlerhaft dokumentierten/ungetesteten Annahmen zusammen fuehrten zur
  falschen Schlussfolgerung "vsftpd-Theorie widerlegt".
  **Implementierte Aenderung:**
  - `ImplicitFtpTls` in `app.py` UND `ftps_upload_helper.py` bekommt
    wieder ein `ntransfercmd()`-Override, das `session=self.sock.session`
    beim Wrap der Datenverbindung uebergibt - exakt der Code, der im
    Diagnose-Testskript (Test A) nachweislich funktioniert hat. Normales
    `unwrap()`-Verhalten (kein Override von `storbinary()` mehr noetig)
    bleibt bestehen, ebenfalls wie in Test A.
  - Beide Docstrings wurden korrigiert, um die widerlegte "ist bereits
    Standardverhalten"-Annahme zu entfernen und stattdessen explizit auf
    die Verifikation per `inspect.getsource()` zu verweisen - falls
    jemand in Zukunft wieder versucht sein sollte, dieses Override als
    "unnoetig" zu entfernen, sollte der Docstring das verhindern.
  **Getestet (ohne echten Drucker):** `inspect.getsource()`-basierte
  Tests bestaetigen sowohl fuer `app.py` als auch `ftps_upload_helper.py`,
  dass `ntransfercmd` jetzt ueberschrieben ist und `session=self.sock.session`
  sowie `server_hostname=self.host` enthaelt; vollstaendiger Async-
  Confirm-Flow-Test weiterhin erfolgreich. **Ein Test gegen einen echten
  X1C/X1E mit dieser Loesung konnte in dieser Umgebung nicht durchgefuehrt
  werden**, ist aber die bislang am besten abgesicherte Loesung der
  gesamten Chronologie: sie entspricht buchstaeblich dem vom Nutzer selbst
  verifizierten, erfolgreichen Referenz-Code (Test A aus
  `ftps_test_minimal.py`), Zeile fuer Zeile.
  **KORRIGIERTES Lesson Learned (das vorherige, in v1.5.3 dokumentierte
  "Lesson Learned" war selbst Teil der Fehleinschaetzung und wird hier
  ersetzt):** Der eigentliche Fehler zog sich durch mehrere Versionen:
  in v1.4.5 wurde eine Code-Vereinfachung (Entfernen des `ntransfercmd()`-
  Overrides) vorgenommen, gestuetzt auf eine NICHT VERIFIZIERTE Annahme
  ueber Pythons Standardverhalten. Diese unbelegte Annahme wurde
  anschliessend ueber mehrere weitere Versionen (v1.4.5 bis v1.5.3)
  unhinterfragt fortgeschrieben, obwohl sie bei jeder folgenden
  Fehlersuche staendig neu haette challenged werden koennen. Erst der
  Vergleich mit dem Nutzer-eigenen, tatsaechlich funktionierenden Referenz-
  Code (nicht mit einer Web-Recherche oder einer weiteren Theorie) deckte
  die falsche Annahme auf. **Die zentrale Lektion:** Aussagen ueber das
  Verhalten von Standardbibliotheken ("X macht das bereits automatisch")
  sollten nicht aus Erinnerung/Training uebernommen, sondern bei
  sicherheitsrelevanten oder fehleranfaelligen Code-Pfaden aktiv mit
  `inspect.getsource()` oder aehnlichen Mitteln gegen die tatsaechlich
  installierte Version verifiziert werden - besonders wenn genau diese
  Annahme die Grundlage fuer das Entfernen von Code ist. Ein einziger
  `inspect.getsource(ftplib.FTP_TLS.ntransfercmd)`-Aufruf haette diesen
  gesamten Irrweg (v1.4.5 bis v1.5.3, mehrere fehlgeschlagene
  Alternativ-Theorien) von Anfang an vermieden.

  **v1.5.5 - Folgefehler nach erfolgreichem FTPS-Fix: falsche AMS-
  Zuordnung bei Verbundwerkstoffen.** Nachdem der FTPS-Upload seit
  v1.5.4 zuverlaessig funktioniert (vom Nutzer fuer PLA auf X1C und X1E
  bestaetigt), meldete der Nutzer ein neues, unabhaengiges Problem:
  Drucke mit **ASA-CF** wurden korrekt uebertragen und gestartet,
  blieben dann aber beim Materialladen haengen. Nach Ausschluss von
  Hardware-Ursachen (gehaertete Duese vorhanden, keine AMS-HT
  angeschlossen) fiel bei Code-Review auf: `_find_matching_tray()`
  verglich Materialtypen per reinem Teilstring-Test
  (`want_type not in tray_type and tray_type not in want_type`) - das
  hat einen gefaehrlichen blinden Fleck, da "ASA" ein Teilstring von
  "ASA-CF" ist (ebenso "PLA" in "PLA-CF", "PETG" in "PETG-CF" usw.).
  Ein Fach mit reinem ASA konnte dadurch bei uebereinstimmender Farbe
  faelschlich als passende automatische Zuordnung fuer ein ASA-CF-
  Filament vorgeschlagen werden. Wurde dieser Vorschlag vom Nutzer
  uebernommen (Standard-Verhalten im Dialog), sendete das Dashboard die
  falsche Fach-Nummer an den Drucker; beim Laden erkennt der RFID-Chip
  im Fach ein anderes Material als im Slicer hinterlegt, was am Drucker
  typischerweise eine Bestaetigungs-Abfrage auf dem Display ausloest -
  ohne jemanden vor Ort sieht das wie ein Haengenbleiben aus.
  **Implementierte Aenderung:** Neue Funktion `_types_compatible(want_type,
  tray_type)` ersetzt die rohe Teilstring-Pruefung in
  `_find_matching_tray()`. Logik: beide Typ-Strings werden am ersten "-"
  in Basis-Name und Suffix aufgeteilt (`"ASA-CF".partition("-")` →
  Basis `"ASA"`, Suffix `"CF"`); **die Suffixe muessen exakt
  uebereinstimmen** (leerer Suffix bei unverstaerkten Materialien zaehlt
  als eigener Wert, der nicht zu einem gefuellten Suffix passt), erst
  DANACH darf der Basis-Name weiterhin locker verglichen werden (fuer
  Faelle wie "PLA" vs. "PLA BASIC", die weiterhin funktionieren sollen).
  Das schliesst systematisch alle Verbundwerkstoff-Kombinationen aus
  (PLA-CF, PETG-CF, PA-CF, ABS-GF, PPS-CF usw. koennen nie mehr mit
  ihrer unverstaerkten Grundvariante verwechselt werden), nicht nur den
  konkret gemeldeten ASA/ASA-CF-Fall.
  **Getestet:** `_types_compatible()` isoliert fuer alle relevanten
  Faelle (ASA vs. ASA-CF in beide Richtungen falsch; PLA-CF, PETG-CF,
  PA-CF, ABS-GF vs. ihre Grundvariante ebenfalls falsch; exakte Matches
  weiterhin wahr; lockere Basis-Matches ohne Suffix wie "PLA"/"PLA
  BASIC" weiterhin wahr; gleiches Suffix mit lockerer Basis wie
  "PLA-CF"/"PLA BASIC-CF" weiterhin wahr; leere Typen weiterhin nicht
  blockierend). `_find_matching_tray()` mit dem exakten Bug-Szenario
  (schwarzes ASA-CF-Filament, AMS hat sowohl ein schwarzes ASA- als
  auch ein schwarzes ASA-CF-Fach): waehlt jetzt korrekt das ASA-CF-Fach,
  ueberspringt das ASA-Fach trotz Farb-Uebereinstimmung. Vollstaendiger
  End-to-End-Test ueber `POST /print/prepare` mit derselben Ausgangslage
  bestaetigt die korrekte Vorauswahl (`suggested_tray`) bis in die
  tatsaechliche API-Antwort hinein.
  **Fuer die Weiterarbeit:** Dieselbe Problemklasse (Teilstring-
  Ueberschneidungen) koennte theoretisch auch bei anderen, bisher nicht
  bekannten Bambu-Materialbezeichnungen mit anderer Suffix-Konvention
  auftreten (z. B. falls Bambu je ein Material ohne "-" als Trenner
  einfuehrt, das trotzdem eine Verbundwerkstoff-Variante ist) - aktuell
  nicht bekannt, aber falls in Zukunft ein aehnliches Symptom mit einem
  anderen Materialpaar auftritt, zuerst `_types_compatible()` mit den
  konkreten Typ-Strings durchtesten, bevor eine neue Ursache vermutet
  wird.
- **Zweiter, unabhängiger MQTT-Broker** (`ExtrasMqttManager`) für frei
  definierbare Sensoren/Schalter, die einer Drucker-Karte angehängt
  werden. Aktivierung über `extras_mqtt` in `config.json`, Zuordnung über
  die `extras`-Liste je Drucker. **Nur config-basiert**, kein Formular in
  der Oberfläche dafür (bewusste Scope-Entscheidung).
- **Restdruckzeit** wird über `formatRemaining()` (JS) als „X h Y min
  verbleibend" formatiert (Bambu Lab, OctoPrint, Ultimaker liefern
  `remaining_min`; Formlabs/Creality liefern es nicht).
- **PyInstaller/GitHub Actions:** `pyinstaller --onefile --name
  DruckerDashboard --console app.py`. Der Workflow in
  `.github/workflows/build-exe.yml` baut bei jedem Push automatisch und
  legt das Ergebnis als Artifact ab (bei Tag-Push zusätzlich als Release).

---

## 6. Wichtige Design-Entscheidungen / Lessons Learned (bitte beachten!)

1. **Keine Endpunkte raten.** Der erste Formlabs-Versuch hat mit
   geratenen HTTP-Pfaden gearbeitet – das hat in der Praxis nicht
   funktioniert ("zeigt keine Daten an"). Seitdem gilt: neue Integrationen
   nur auf Basis von tatsächlich recherchierten/dokumentierten APIs
   umsetzen (offizielle Doku, Community-Reverse-Engineering mit
   Quellenbeleg, oder zumindest mehrere unabhängige Quellen). Wenn keine
   verlässliche API bekannt ist (z. B. Creality-Modelle mit reinem
   "Creality OS" ohne Klipper), wird die Integration **bewusst
   weggelassen** statt geraten – siehe README, Abschnitt 3d.
2. **Defensive Parsing als Fallback**, wenn das genaue Antwortformat
   unsicher ist (z. B. `FormlabsLocalApiConnection._find_first()` sucht
   rekursiv nach plausiblen Feldnamen statt starre Keys vorauszusetzen).
   Wird bewusst eingesetzt, wenn eine API zwar real existiert, ihr exaktes
   JSON-Schema aber nicht mit Sicherheit bekannt ist.
3. **Bugfix-Beispiel Bambu Kammertemperatur:** Die `pushall`-MQTT-Anfrage
   brauchte laut Bambu-Protokoll zwingend `"version": 1` und
   `"push_target": 1` - ohne diese Felder hat die Firmware den Request
   komplett ignoriert. Immer bei "Wert kommt nie an"-Bugs zuerst die
   Rohantwort/das exakte Request-Format gegen die Originaldokumentation
   prüfen, bevor Umgehungslösungen gebaut werden.
4. **Jede Änderung wird getestet, bevor sie ausgeliefert wird:**
   - `python3 -m py_compile app.py` (Syntax) - **Achtung:** prueft nur
     gegen die lokal installierte Python-Version (hier 3.12), nicht
     gegen die vom GitHub-Actions-Build genutzte Version (3.11) - siehe
     Punkt 6 unten fuer die daraus resultierende Faustregel.
   - Ein kurzes Python-Testskript, das `app.dash.add_printer(...)` für
     jeden betroffenen Typ aufruft und `app.app.test_client()` für die
     relevanten Routen nutzt (siehe vorherige Chat-Historie für konkrete
     Beispiele).
   - Vor jedem Ausliefern `rm -f config.json`, damit keine Test-Drucker
     versehentlich in der ausgelieferten Beispiel-Config landen.
5. **"Ohne den bisherigen Aufbau zu ändern" ernst nehmen:** Neue
   Druckertypen/Features werden **additiv** eingebaut (neue Klassen, neue
   `elif`-Zweige, neue Konstanten) statt bestehende Funktionen
   umzuschreiben. Rückwärtskompatibilität von `config.json` wird über
   `setdefault()` in `load_config()` sichergestellt.
6. **`py_compile` beim Entwickeln reicht nicht - Ziel-Python-Version
   beachten.** `.github/workflows/build-exe.yml` baut explizit mit
   **Python 3.11** (bewusste, stabile Wahl). Lokales Testen/Entwickeln
   lief bislang mit Python 3.12, das mehr Syntax erlaubt als 3.11 (z. B.
   Backslashes im Ausdrucksteil eines f-strings, seit PEP 701/Python
   3.12). Ein `python3 -m py_compile app.py` unter 3.12 erkennt solche
   Faelle NICHT als Fehler, obwohl der GitHub-Actions-Build (Python
   3.11) dann mit `SyntaxError: f-string expression part cannot include
   a backslash` fehlschlaegt (siehe v1.4.8-Bugfix in Abschnitt 5).
   **Faustregel fuer neue f-strings:** keine Backslashes (`\n`, `\t`,
   escaped quotes etc.) direkt im `{...}`-Ausdrucksteil verwenden -
   den entsprechenden String-Teil immer vorher in eine eigene Variable
   auslagern und nur diese Variable im f-string referenzieren. Bei
   Unsicherheit: der Container hier hat keinen Zugriff auf einen
   Python-3.11-Interpreter (nur 3.12 vorinstalliert, `apt install
   python3.11` nicht in den erlaubten Paketquellen) - ein manueller
   Regex-Scan auf "Backslash innerhalb von geschweiften Klammern in
   einem f-string" ist ein brauchbarer Ersatz-Check, falls unsicher.

---

## 7. Bekannte Unsicherheiten / offene Punkte für die Weiterarbeit

- **Formlabs-Feldnamen** (`FL_PROGRESS_KEYS`, `FL_FILE_KEYS`,
  `FL_MATERIAL_KEYS`, `FL_STATE_KEYS` in `app.py`) sind nicht an einem
  echten Gerät verifiziert, nur aus Doku-Fragmenten plausibel abgeleitet.
  Falls ein Nutzer meldet, dass Formlabs-Karten leer bleiben (obwohl
  PreFormServer läuft), zuerst die rohe JSON-Antwort von
  `GET http://localhost:44388/devices/` bzw. `/devices/{id}/` einsehen
  lassen und die Konstanten entsprechend ergänzen.
- **Ultimaker-Feldnamen** (`/api/v1/print_job`: `name`, `progress`,
  `time_elapsed`, `time_total`) sind mit mittlerer Sicherheit aus
  Community-Quellen/Cloud-API-Doku abgeleitet, aber nicht an echter
  Hardware getestet. Gleiches Vorgehen wie bei Formlabs, falls Daten
  fehlen: rohe Antwort von `http://<IP>/api/v1/print_job` prüfen.
- **Kein UI-Editor für Extras (Sensoren/Schalter).** Aktuell nur über
  manuelles Bearbeiten von `config.json` möglich. Ein Formular dafür wäre
  ein sinnvoller nächster Ausbauschritt, wurde aber aus Aufwandsgründen
  bisher nicht umgesetzt.
- **Keine Authentifizierung am Dashboard selbst.** Es ist für den Betrieb
  im vertrauenswürdigen LAN gedacht, nicht für den Betrieb im offenen
  Internet.
- **Bambu-Kamera-Protokoll** ist reverse-engineert (Community-Wissen,
  nicht offiziell von Bambu Lab dokumentiert) und könnte durch ein
  Firmware-Update brechen.
- **Druckauftrag-Feature (Abschnitt 5) ist ebenfalls reverse-engineert**
  (FTPS-Upload + MQTT `project_file`, keine offizielle Bambu-API) und
  könnte durch ein Firmware-Update brechen — gleiche Kategorie wie das
  Kamera-Protokoll. Das gilt auch für die AMS-Zuordnung: die
  Umrechnungsregel "4 Fächer pro AMS-Einheit" (`_slot_to_flat_index()`)
  ist Community-Konvention, nicht offiziell dokumentiert.
- **AMS-Zuordnungsvorschlag matcht nur auf exakte Farbe (+ groben
  Typ-Abgleich).** Kein Abgleich auf `filament_id`/Hersteller-SKU, kein
  RFID-Abgleich wie bei manchen kommerziellen Tools. Bei zwei optisch
  identischen Farben in unterschiedlichen Fächern wird das erste
  unbenutzte Fach vorgeschlagen (Reihenfolge in `self.status["ams"]`) —
  der Nutzer sieht und bestätigt das aber im Dialog vor dem Drucken, es
  ist also (seit v1.2.0) kein automatischer Blindgriff mehr.
- **Kein Fortschritts-/Upload-Balken beim Bestätigen.** ~~Der Button
  zeigt nur "Wird gesendet ..." ohne Prozentfortschritt des
  FTPS-Uploads selbst.~~ **Seit v1.4.0 erledigt** (Fortschrittsbalken +
  Prozent + Byte-Anzeige, siehe Abschnitt 5).
- **FTPS-Upload: X1-Serie (X1C, X1E) - GELOEST UND VOM NUTZER BESTAETIGT
  (Stand v1.5.5).** Vollstaendige Chronologie siehe Abschnitt 5 "Bambu
  Lab: Druckauftrag...". Kurzfassung: nach VIER aufeinanderfolgenden
  Fehldiagnosen (vsftpd/`unwrap()` unvollstaendig getestet, Thread-/GIL-
  Theorie, gleichzeitige MQTT-Verbindung, Selbstaufruf-Muster/Windows
  Defender) fand ein direkter Code-Vergleich zwischen der scheiternden
  `ftps_upload_helper.py` und dem nachweislich funktionierenden
  Referenz-Testskript des Nutzers die tatsaechliche Ursache: `ftplib.
  FTP_TLS.ntransfercmd()` uebergibt entgegen einer nie verifizierten
  Annahme KEIN `session=self.sock.session` fuer die Datenverbindung -
  ohne dieses Override (in `ImplicitFtpTls` seit v1.4.5 versehentlich
  entfernt) lehnt vsftpd auf der X1-Serie (Option `require_ssl_reuse`)
  die Datenverbindung nach kurzer Zeit ab. **Der Nutzer hat v1.5.4 in
  der Praxis erfolgreich getestet** (mehrere PLA-Drucke auf X1C und
  X1E ohne Probleme) - dieses Kapitel gilt damit als abgeschlossen.
- **AMS-Zuordnung bei Verbundwerkstoffen (PLA-CF, PETG-CF, ASA-CF, PA-CF,
  ABS-GF usw.) - behoben in v1.5.5.** Nach dem geloesten FTPS-Problem
  meldete der Nutzer ein neues, unabhaengiges Symptom: ASA-CF-Drucke
  wurden korrekt uebertragen und gestartet, blieben aber beim
  Materialladen haengen (PLA war nicht betroffen). Ursache: die
  Typ-Pruefung in `_find_matching_tray()` nutzte einen reinen
  Teilstring-Vergleich, der "ASA" faelschlich als Teilmenge von
  "ASA-CF" akzeptierte (und analog fuer alle anderen Verbundwerkstoffe)
  - bei uebereinstimmender Fach-Farbe konnte so ein Fach mit dem
  falschen (unverstaerkten) Grundmaterial automatisch vorgeschlagen und
  bei Uebernahme durch den Nutzer an den Drucker gesendet werden. Der
  Drucker erkennt dann per RFID ein anderes Material als erwartet und
  wartet vermutlich auf eine Bestaetigung am Display, was remote wie
  ein Haengenbleiben wirkt. Fix: neue Funktion `_types_compatible()`
  vergleicht Materialtyp-Suffixe (Teil nach "-") auf exakte
  Uebereinstimmung, bevor der Basis-Name weiterhin locker verglichen
  werden darf (siehe Abschnitt 5 fuer Details und Testabdeckung). Wie
  bei FTPS: **noch keine Bestaetigung durch einen echten ASA-CF-Druck
  nach dem Fix** - sollte das Problem trotzdem weiterhin auftreten,
  zuerst pruefen, welches AMS-Fach im Dialog tatsaechlich vorausgewaehlt
  war (Screenshot/Notiz vor dem Bestaetigen) und mit dem tatsaechlichen
  Fachinhalt vergleichen.
  Separat, unabhaengig von diesem Thema: Auf dem A1 Mini hat die
  Datei-Uebertragung (mit Python-`ftplib`, v1.4.5) funktioniert, der
  Druck selbst startete aber nicht - laut Nutzer wahrscheinlich, weil
  kein passendes Material ausgewaehlt/geladen war. Das ist vermutlich
  kein Bug, sondern normales Verhalten (siehe `send_print()`/
  `_request_print()`: ohne AMS-Daten fallen alle Filamente auf
  "Extern/manuell" zurueck, `use_ams` wird `False` - der Druck muesste
  dann eigentlich trotzdem starten, ggf. mit Nachfrage am Display
  welches Material eingelegt ist). Falls das beim naechsten Test
  weiterhin auftritt, lohnt sich ein genauerer Blick auf die genaue
  Fehlermeldung/das Verhalten am Drucker-Display.
- **Kein Live-Neuladen der AMS-Vorschau, falls sich der AMS-Inhalt
  während der Dialog offen ist ändert.** Die Vorschläge basieren auf dem
  Status zum Zeitpunkt des Drag & Drop (`preview_print()`); wechselt der
  Nutzer waehrenddessen z. B. eine Spule, muss die Datei erneut
  gezogen werden, um einen aktualisierten Vorschlag zu bekommen (die
  manuelle Korrektur im Dropdown funktioniert davon unabhängig trotzdem).
- **Farbwort-Zuordnung ist eine Näherung** (`NAMED_COLORS` in `app.py`,
  ca. 30 Einträge, nächster RGB-Abstand). Bei changierenden/gemischten
  Filamenten (z. B. "Silk Dual Color") kann das angezeigte Wort nur
  ungefähr passen — der exakte Hex-Wert bleibt per Tooltip abrufbar.
- **macOS-Build ist unsigniert/nicht notarisiert** (GitHub Actions,
  `.github/workflows/build-exe.yml`, Runner `macos-14` = natives Apple
  Silicon). Nutzer müssen die Gatekeeper-Warnung beim ersten Start
  einmalig bestätigen (siehe README, Abschnitt 0).

---

## 9. Versionierung & Commit-Konvention

Ab dieser Übergabe wird das Projekt versioniert. **Regel für die
Weiterarbeit: bei jeder ausgelieferten Änderung `APP_VERSION` in
`app.py` erhöhen (semantisch: MAJOR.MINOR.PATCH — siehe README,
Abschnitt 0a) und einen passenden Commit-Text mitliefern.**

- Aktuelle Version: **v1.5.5** (v1.1.0: Drag-&-Drop-Druckfeature,
  macOS-Build, Versionierung selbst. v1.2.0: AMS-Zuordnung als
  bestätigbarer Dialog statt Sofort-Druck. v1.3.0: Dialog zeigt nur noch
  die für den jeweiligen Druck tatsächlich benötigten Filamente
  [`slice_info.config`-Auswertung statt kompletter Projekt-Filamentliste],
  klare "Vorschlag vs. anderes Material"-Auswahl pro Filament. v1.3.1:
  FTPS-Datenverbindung ohne TLS-Session-Resumption + stufenspezifische
  Fehlermeldungen, da der `EOF occurred in violation of protocol`-Fehler
  trotz der Massnahmen aus v1.2.0 weiterhin reproduzierbar beim
  eigentlichen Datei-Upload auftrat. v1.3.2: Hex-Farbcode zusätzlich als
  Text im AMS-Dialog; großzügigerer Timeout + Byte-Fortschritt in der
  Fehlermeldung für die FTPS-Datenverbindung, nachdem Nutzer-Feedback
  bestätigte, dass der Fehler konkret während der laufenden Übertragung
  auftritt, nicht beim Verbindungsaufbau. v1.4.0: Farbe wird als Wort
  statt Hex-Code angezeigt; echter Fortschrittsbalken mit Prozent- und
  Byte-Anzeige beim Senden [asynchroner Confirm-Ablauf mit Polling];
  FTPS-Upload gedrosselt [Pause pro Block], nachdem zwei Versuche exakt
  beim selben Byte-Stand abbrachen — deutliches Indiz für einen
  druckerseitigen Pufferüberlauf statt Netzwerk-Flakiness. v1.4.1:
  Diese Theorie widerlegt (ein Versuch nach Drosselung brach noch
  früher/anders ab) - Fehlermeldung zeigt jetzt alle 3 Retry-Versuche
  einzeln statt nur den letzten, für bessere Diagnose. v1.4.2: Diese
  Diagnose zeigte perfekt reproduzierbare Abbrüche bei exakt 8192 Bytes
  über alle 3 Versuche — Review ergab einen konkreten fehlenden
  FTP-Protokollschritt (`TYPE I`, Binärmodus-Umschaltung), der beim
  Umbau in v1.3.1 versehentlich verlorengegangen war; jetzt ergänzt.
  v1.4.3: `TYPE I` hat den Fehler NICHT behoben (weiterhin exakt 8192
  Bytes) — Rückfrage beim Nutzer ergab TLS-inspizierende Antivirus-
  Software + verwaltetes Firmennetzwerk, Vermutung TLS-Inspektion als
  Ursache (spaeter widerlegt). v1.4.4: FileZilla-Vergleichstest bewies
  das Gegenteil — FileZilla überträgt dieselbe Datei im selben Netzwerk
  problemlos. Upload auf `ftp.storbinary()` zurückgebaut (statt der
  seit v1.3.1 manuellen Sende-Schleife) — reichte allein aber nicht,
  Fehler trat weiterhin bei festem Blockgrößen-Vielfachen auf. v1.4.5:
  zweite übersehene Abweichung gefunden — `ImplicitFtpTls` hatte
  weiterhin eine `ntransfercmd()`-Überschreibung ohne TLS-Session-
  Wiederverwendung (seit v1.3.1) und `_ftps_upload_once()` erzwang
  weiterhin TLS-Version 1.2 (seit v1.2.0), beides unbestätigte
  Altlasten. Beides entfernt — Upload-Pfad besteht jetzt nur noch aus
  dem für implizites TLS zwingend nötigen Minimum + Standard-`ftplib`-
  Verhalten, keine weiteren spekulativen Anpassungen mehr vorhanden.
  Bestätigung durch Nutzer stand zum Zeitpunkt dieser Übergabe noch aus.
  v1.4.6: Fehler trat auf dem X1C danach erneut exakt beim selben
  Byte-Wert auf wie vor v1.4.5 — zeigt, dass die dort entfernten
  Overrides gar nicht die Ursache waren. FTPS-Upload komplett auf einen
  `curl`-Unterprozess umgestellt statt Pythons `ftplib`/`ssl` zu nutzen
  [curl = von Python unabhängige TLS-Bibliothek, vorinstalliert auf
  Windows/macOS/Linux]. **Wichtige Präzisierung nach Auslieferung:**
  Nutzer-Feedback ergab, dass v1.4.5 [reiner Python-Code] auf einem A1
  Mini erfolgreich übertragen hatte, während derselbe Code auf dem X1C
  weiterhin scheiterte — das Problem ist also vermutlich X1C-spezifisch
  [Firmware-Eigenheit], nicht ein grundsätzliches Python/TLS-Problem.
  Ob curl [v1.4.6] das X1C-Problem tatsächlich löst, stand zum
  Zeitpunkt dieser Übergabe noch aus — siehe Abschnitt 7 für die
  offenen Fragen und nächsten Schritte. v1.4.7: Antwort kam — curl
  scheiterte ebenfalls, UND zwar auf X1C UND X1E [nicht nur X1C]. Damit
  eindeutig bestätigt: X1-Serie-spezifisches Problem, betrifft sowohl
  Python `ssl` als auch curl. Verbose-Logging [`--verbose`] ergänzt für
  echte TLS-Diagnose statt nacktem Exit-Code; `--tlsv1.2 --tls-max 1.2`
  als neues Experiment über curls Schannel-Backend [anderer Code-Pfad
  als der bereits gescheiterte Python/OpenSSL-Versuch]. Ergebnis stand
  zum Zeitpunkt dieser Übergabe noch aus. v1.4.8: reiner Build-Fix, kein
  Verhaltensunterschied — GitHub-Actions-macOS-Build schlug fehl
  [`SyntaxError: f-string expression part cannot include a backslash`],
  da der Runner mit Python 3.11 baut, während lokal mit 3.12 entwickelt
  wurde [dort seit PEP 701 erlaubt]. Betroffene Stelle in der
  curl-Fehlermeldungs-Konstruktion behoben, String-Teil vor dem
  f-string separat zusammengebaut. v1.4.9: curl-Ansatz wieder verworfen
  — curl (Schannel) scheiterte auf X1C/X1E identisch zu Python zuvor,
  brachte also keinen Vorteil bei zusätzlicher externer Abhängigkeit.
  Rückbau auf den einfacheren Python-`ftplib`-Ansatz [Stand v1.4.5],
  der nachweislich für einen Teil der Druckerflotte [A1 Mini]
  funktioniert. v1.5.0: Grundursache per Recherche vermutet — X1-Serie
  läuft auf vsftpd mit `require_ssl_reuse`, Pythons `storbinary()`
  verletze das durch ein abschließendes `unwrap()` [`_storbinary_no_unwrap()`
  als Fix] — **stellte sich als falsche Fährte heraus, änderte beim
  echten Test nichts**. v1.5.1: Prozess-Isolation des Uploads
  [Sentinel-Parameter `--ftps-upload-worker`] als Fix für eine vermutete
  Thread-/GIL-Konkurrenz mit dem MQTT-Hintergrundthread — **ebenfalls
  eine falsche Fährte**, scheiterte beim echten Test auf X1C identisch
  erneut. v1.5.2: vermutete gleichzeitige MQTT+FTPS-Verbindung als
  Ursache [Fix: `pause_mqtt()`/`resume_mqtt()`/`wait_for_mqtt_reconnect()`]
  — **ebenfalls eine falsche Fährte**, scheiterte beim echten Test
  erneut identisch (73728 Bytes). v1.5.3: vermutete Ursache — das
  Dashboard rief sich bis dahin selbst mit einem versteckten
  Kommandozeilen-Argument neu auf, was Windows Defender ähnlich wie
  manche Schadsoftware-Lademechanismen behandeln könnte. Fix: separate,
  eigenständig mitgelieferte Hilfsanwendung
  [`FtpsUploadHelper.exe`/`ftps_upload_helper.py`] statt Selbstaufruf —
  **ebenfalls eine falsche Fährte**: scheiterte beim Test sogar bei
  komplett eigenständigem Aufruf (Dashboard geschlossen) identisch.
  v1.5.4: tatsächliche, per direktem Code-Vergleich mit dem
  funktionierenden Referenzskript bestätigte Ursache — `ntransfercmd()`
  in `ImplicitFtpTls` übergab seit v1.4.5 kein `session=self.sock.session`
  mehr, basierend auf einer nie verifizierten Annahme über Pythons
  Standardverhalten (per `inspect.getsource()` widerlegt: die eingebaute
  Methode macht das nicht automatisch). Die ursprüngliche vsftpd-Diagnose
  aus v1.5.0 war die ganze Zeit inhaltlich richtig, nur unvollständig
  umgesetzt. Fix: `ntransfercmd()`-Override mit `session=self.sock.session`
  in `app.py` UND `ftps_upload_helper.py` wieder ergänzt — entspricht
  jetzt Zeile für Zeile dem vom Nutzer verifizierten Referenzcode. **Vom
  Nutzer bestätigt: mehrere PLA-Drucke auf X1C und X1E ohne Probleme.**
  v1.5.5: neues, unabhängiges Problem gemeldet — ASA-CF-Drucke blieben
  beim Materialladen hängen (PLA nicht betroffen). Ursache: zu lockere
  Teilstring-Typprüfung in `_find_matching_tray()` akzeptierte "ASA" als
  Teilmenge von "ASA-CF" (analog für alle Verbundwerkstoffe), konnte bei
  Farbübereinstimmung ein Fach mit falschem Grundmaterial vorschlagen.
  Fix: neue Funktion `_types_compatible()` vergleicht Materialtyp-
  Suffixe [Teil nach "-"] auf exakte Übereinstimmung, bevor der
  Basis-Name weiterhin locker verglichen werden darf. Bestätigung durch
  Nutzer stand zum Zeitpunkt dieser Übergabe noch aus).
- `APP_VERSION` ist die einzige Quelle der Wahrheit; der GitHub-Actions-
  Workflow liest sie automatisch per Regex aus `app.py` aus.
- Empfohlener Ablauf beim Ausliefern einer neuen Version: `APP_VERSION`
  anpassen → committen mit dem mitgelieferten Commit-Text → taggen
  (`git tag vX.Y.Z && git push origin vX.Y.Z`) → GitHub Actions baut
  automatisch Windows-exe + macOS-arm64-Build und hängt beide ans
  Release an.

---

## 10. Wie man weiterarbeitet

1. `app.py`, `README.md` und dieses Dokument in den neuen Chat geben.
2. Bei neuen Druckertypen: erst kurz recherchieren, ob/wie eine lokale
   API existiert (siehe Abschnitt 6, Punkt 1), dann analog zu den
   bestehenden `*Connection`-Klassen eine neue Klasse anlegen (oder eine
   bestehende um einen neuen `type`-Wert erweitern, falls die Anbindung
   technisch identisch ist wie bei Creality/Formlabs).
3. Neue Typen müssen an folgenden Stellen ergänzt werden (siehe Tabelle
   in Abschnitt 3 "Wichtigste Codestellen"):
   - Typ-Konstante (`KNOWN_TYPES` bzw. eigenes `*_TYPES`-Tuple)
   - `DashboardApp._start_printer()` und `add_printer()`
   - `api_add_printer()` (Validierung der Pflichtfelder)
   - `camera_stream()` (falls Kamera unterstützt wird)
   - Frontend: `<select id="f_type">`-Option, ein neues `<div
     id="...Fields">` im Modal, `toggleTypeFields()`, `openAddModal()`
     (Felder zum Zurücksetzen ergänzen), `submitAdd()`, `refresh()`-
     Dispatch, eine neue `render...Card()`-Funktion
   - `config.example.json` um ein Beispiel ergänzen
   - `README.md` um einen neuen Abschnitt ergänzen (Nummerierung beachten)
4. Nach jeder Änderung: kompilieren + Smoke-Test (siehe Abschnitt 6,
   Punkt 4), bevor die Datei ausgeliefert wird.
