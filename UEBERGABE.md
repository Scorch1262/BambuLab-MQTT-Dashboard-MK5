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
| Druckauftrag senden (Bambu) | `PrinterConnection.preview_print()`, `PrinterConnection.send_print()`, `PrinterConnection._request_print()`, `PrinterConnection.pause_mqtt()`/`resume_mqtt()`/`wait_for_mqtt_reconnect()`, `class ImplicitFtpTls`, `FTPS_PROFILES`, `_find_ftps_upload_helper()`, `ftps_upload_helper.py` (separate Datei/exe, eigenes `PROFILES`-Dict), `_run_ftps_upload_worker()` + Sentinel-Check `--ftps-upload-worker` (Fallback, ganz frueh im Modul), `DashboardApp.add_printer()` (Parameter `bambu_family`), `DashboardApp.prepare_print_job()`/`start_confirm_print_job()`/`cancel_print_job()`/`get_print_progress()`, Routen `POST /api/printers` (Feld `bambu_family`)/`POST /api/printers/<id>/print/prepare`\|`/confirm`, `GET .../print/progress/<job_id>`, `POST .../print/cancel`, JS `renderDropZone()`/`dzDrop()`/`openAmsModal()`/`confirmAmsModal()`/`pollAmsProgress()`/`submitAdd()` (Feld `f_bambu_family`) |
| Druckauftrag senden (Ultimaker, seit v1.6.3) | `_parse_digest_challenge()`, `_build_digest_authorization()`, `_build_multipart_body()`, `UltimakerConnection.start_pairing()`/`check_pairing()`/`_digest_challenge()`/`send_print()`, `DashboardApp.start_ultimaker_pairing()`/`check_ultimaker_pairing()`/`send_ultimaker_print_now()` (nutzt dieselben `_print_jobs`/`_print_progress`-Strukturen wie Bambu weiter), Routen `POST /api/printers/<id>/ultimaker/pair/start`\|`/pair/status`\|`POST .../ultimaker/print` (nutzt die BESTEHENDEN generischen `GET .../print/progress/<job_id>`-Routen weiter, keine eigenen noetig), JS `renderUltimakerDropZone()`/`pairUltimaker()`/`dzDropUltimaker()`/`pollUltimakerProgress()` |
| AMS-Zuordnungsvorschlag | `PrinterConnection.preview_print()` (liefert `total_filaments`), `_parse_3mf_filaments()` (liefert `(filamente, gesamtanzahl)`), `_parse_plate1_used_filament_indices()`, `_find_matching_tray()`, `_types_compatible()`, `_slot_to_flat_index()`, JS `openAmsModal()`/`amsRowHtml()` (`data-true-index`)/`confirmAmsModal()` (baut `ams_mapping` an den echten Filament-Positionen, nicht Anzeige-Reihenfolge) |
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
| `ultimaker` | Ultimaker UM3/S-Serie/Factor 4 | offizielle lokale REST-API `/api/v1/` | Nein fuer Status (nur lesend); Digest-Auth-Kopplung fuer Druckstart (seit v1.6.3) | Ja, mjpg-streamer-Standard-URL (überschreibbar) |

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

  **v1.5.6 - Folgefehler nach v1.5.5: AMS-Zuordnung war schon korrekt,
  trotzdem Haengenbleiben beim Materialladen.** Der Nutzer meldete
  zurueck: das ASA-CF-Fach wurde bereits in v1.5.4 korrekt vorgeschlagen
  (der Drucker hatte tatsaechlich ASA-CF geladen) - die v1.5.5-Diagnose
  (Verbundwerkstoff-Verwechslung) war also fuer DIESEN konkreten Fall
  nicht die Ursache (der Fix selbst bleibt trotzdem sinnvoll fuer den
  allgemeinen Fall). Wichtige Zusatzinfo vom Nutzer: keine
  Fehlermeldung am Display, Material wurde schon mehrfach erfolgreich
  gedruckt (kein grundsaetzliches Erstdruck-Problem). Da eine "falsche
  AMS-Zuordnung mit RFID-Mismatch" typischerweise eine sichtbare
  Bestaetigungs-Abfrage ausloest, aber KEINE Fehlermeldung auftrat,
  wurde der Verdacht auf das MQTT-Kommando selbst gelenkt: ein
  Vergleich des tatsaechlich gesendeten `project_file`-Befehls mit
  mehreren unabhaengig dokumentierten, als "funktioniert zuverlaessig"
  bestaetigten Referenz-Payloads (Cinder's Blog "Bambu AMS Filament
  Mapping"; Doridian/OpenBambuAPI auf GitHub) zeigte: unser Befehl
  enthielt nur eine Teilmenge der Felder, die Bambu Studio selbst
  sendet. Insbesondere fehlte **`bed_type`** komplett - ohne dieses
  Feld ist unklar, welchen Druckbett-Typ die Firmware fuer den
  weiteren Startablauf annimmt, was bei anspruchsvolleren Materialien
  (hoehere Bett-/Duesentemperatur, wie ASA-CF) eher zu Problemen fuehren
  kann als bei unkritischeren Materialien wie PLA - was zur beobachteten
  Materialabhaengigkeit passt. Ebenfalls fehlten `subtask_name`,
  `project_id`, `profile_id`, `task_id` (laut OpenBambuAPI-Spezifikation
  fuer lokale Drucke immer `"0"`, aber offenbar Teil des vollstaendigen,
  von der Firmware erwarteten Befehlsformats).
  **Implementierte Aenderung:** `_request_print()` sendet jetzt den
  vollstaendigen, dokumentierten Feldsatz: `"bed_type": "auto"` (laesst
  die Firmware selbst anhand der eingelegten Druckplatte entscheiden -
  die sicherste Wahl, keine Annahme ueber die tatsaechlich verwendete
  Platte), `"subtask_name"` (Auftragsname, aus dem Dateinamen ohne
  `.gcode.3mf`-Endung abgeleitet - zusaetzlicher Vorteil: der Drucker
  zeigt jetzt einen sinnvollen Namen statt eines leeren/generischen
  Werts an), sowie `"project_id"`, `"profile_id"`, `"task_id"` (alle
  `"0"`, wie fuer lokale/nicht-Cloud-Drucke dokumentiert).
  **Getestet (ohne echten Drucker):** vollstaendige Payload-Struktur
  gegen einen simulierten MQTT-Client verifiziert (alle neuen Felder
  korrekt gesetzt, `ams_mapping`/`use_ams` weiterhin korrekt aus der
  Nutzer-Zuordnung uebernommen); `subtask_name`-Extraktion fuer mehrere
  Dateinamen-Varianten getestet (inkl. Namen mit mehreren Punkten);
  vollstaendiger End-to-End-Test ueber `send_print()`. **Ein Test gegen
  einen echten Drucker mit ASA-CF konnte in dieser Umgebung nicht
  durchgefuehrt werden** - Bestaetigung durch den Nutzer stand zum
  Zeitpunkt dieser Übergabe noch aus.
  **Fuer die Weiterarbeit, falls der Fehler nach v1.5.6 weiterhin
  auftritt:** Ein MQTT-Sniffer (z. B. MQTT Explorer, mosquitto_sub)
  parallel zu Bambu Studio mitlaufen lassen und den *tatsaechlich* von
  Bambu Studio gesendeten `project_file`-Befehl fuer denselben Druck
  direkt mit unserem vergleichen (Byte-fuer-Byte-JSON-Diff) waere der
  naechste, praeziseste Schritt - zuverlaessiger als weitere
  Community-Referenz-Payloads zu vergleichen, da es den tatsaechlichen
  Befehl DIESES Druckers/dieser Firmware-Version zeigt.

  **v1.5.7 - X1C/X1E jetzt vom Nutzer bestaetigt funktionsfaehig, dafuer
  A1 Mini kaputt: echter Zielkonflikt zwischen Druckerfamilien
  entdeckt.** Der Nutzer bestaetigte: X1C und X1E uebertragen und
  starten Drucke jetzt zuverlaessig (v1.5.4-v1.5.6 haben ihr Ziel
  erreicht). ABER: der A1 Mini, der laut Nutzer in einer fruehen
  Version (vor der Session-Reuse-Einfuehrung in v1.5.4) bereits
  einwandfrei funktioniert hatte, scheiterte jetzt neu mit einem
  bisher unbekannten Fehlerbild: `The read operation timed out` -
  und zwar erst bei **exakt 100% uebertragenen Bytes** (`390206/390206`).
  Das ist ein voellig anderes Symptom als der X1-Abbruch (der brach
  waehrend der Uebertragung bei ~11% ab) - hier wird die komplette
  Datei erfolgreich gesendet, aber das Lesen der Server-Abschluss-
  bestaetigung (die "226 Transfer complete"-Antwort) blockiert bis zum
  Timeout. **Ursache:** Die in v1.5.4 eingefuehrte TLS-Session-
  Wiederverwendung (`session=self.sock.session` in `ntransfercmd()`),
  die fuer die X1-Serie zwingend noetig ist (vsftpd mit
  `require_ssl_reuse`), scheint beim A1 Mini genau umgekehrt zu wirken:
  sein leichterer, vermutlich ESP32-basierter FTPS-Server kommt mit
  einer wiederverwendeten TLS-Sitzung fuer die Datenverbindung nicht
  klar und schliesst die Verbindung nach der Uebertragung nicht wie
  erwartet ab (oder sendet die Abschluss-Antwort nicht in einer Weise,
  die unser Client noch korrekt zuordnen kann). **Beide Druckerfamilien
  brauchen also nachweislich GEGENSAETZLICHES Verhalten** - es gibt
  keine einzelne Einstellung, die fuer beide gleichzeitig korrekt ist.
  **Implementierte Aenderung:**
  - `ImplicitFtpTls` (in `app.py` UND `ftps_upload_helper.py`) bekommt
    einen neuen Konstruktor-Parameter `reuse_session: bool = True`.
    `ntransfercmd()` uebergibt `session=self.sock.session` nur noch,
    wenn `reuse_session` gesetzt ist; sonst wird die Datenverbindung wie
    vor v1.5.4 ohne Sitzungs-Wiederverwendung gewrappt.
  - `ftps_upload_helper.py`s `main()` akzeptiert ein optionales 5.
    Kommandozeilen-Argument ("1"/"0") fuer `reuse_session` (Standard
    "1", falls weggelassen - fuer Abwaertskompatibilitaet bei manuellem
    Aufruf). Analog fuer `_run_ftps_upload_worker()` in `app.py` (der
    Fallback-Sentinel-Mechanismus, falls die Helfer-exe fehlt).
  - `PrinterConnection._ftps_upload()` (der 3-Versuche-Retry-Wrapper)
    **alterniert jetzt zwischen den Versuchen**: Versuch 1 = mit
    Sitzungs-Wiederverwendung (`reuse_pattern = [True, False, True]`),
    Versuch 2 = ohne, Versuch 3 = wieder mit. Dadurch wird garantiert
    innerhalb der 3 Versuche fuer JEDES der beiden bekannten
    Druckerverhalten die passende Variante gefunden, ohne dass das
    Dashboard das Druckermodell vorher kennen muss. Die Fehlermeldung
    bei einem endgueltigen Fehlschlag nennt jetzt zusaetzlich, welche
    Variante bei welchem Versuch verwendet wurde (z. B. "Versuch 1 (mit
    Sitzungs-Wiederverwendung): ...").
  - `PrinterConnection._ftps_upload_once()` bekommt einen neuen
    Parameter `reuse_session: bool = True`, reicht ihn als 5. CLI-
    Argument an die Helfer-exe bzw. den Sentinel-Fallback durch.
  **Getestet (ohne echten Drucker):** Zwei Fake-Helfer-Skripte simulieren
  jeweils ein Druckerverhalten exakt nachgebildet vom real gemeldeten
  Symptom: (1) "A1-Mini-artig" - schlaegt bei `reuse=1` mit dem exakten
  gemeldeten Timeout-Fehler fehl, gelingt bei `reuse=0`; (2) "X1-artig"
  - schlaegt bei `reuse=0` fruehzeitig fehl (analog zum urspruenglichen
  X1-Symptom), gelingt bei `reuse=1`. In BEIDEN Faellen findet
  `_ftps_upload()` durch die Alternierung automatisch die passende
  Variante innerhalb der 3 Versuche, ohne manuelles Eingreifen. Fehler-
  Fall (beide Varianten schlagen fehl) zeigt korrekt beide Label in der
  zusammengefassten Fehlermeldung. Vollstaendiger Async-Confirm-Flow-
  Test weiterhin erfolgreich, inkl. Pruefung, dass die Fehlermeldung im
  Fehlerfall die neuen Label enthaelt.
  **Ein Test gegen einen echten A1 Mini mit dieser Loesung konnte in
  dieser Umgebung nicht durchgefuehrt werden** - Bestaetigung durch den
  Nutzer stand zum Zeitpunkt dieser Übergabe noch aus. Die X1C/X1E-
  Funktionsfaehigkeit sollte durch die Alternierung nicht negativ
  beeinflusst werden (Versuch 1 bleibt unveraendert "mit Reuse", was
  fuer die X1-Serie bereits nachweislich funktioniert - im Idealfall
  aendert sich fuer X1-Nutzer also gar nichts spuerbar, ausser dass der
  A1-Mini-Fall jetzt zusaetzlich abgedeckt ist).
  **Fuer die Weiterarbeit:** Sollte sich herausstellen, dass es noch
  weitere Druckermodelle mit einem DRITTEN, bisher unbekannten
  FTPS-Verhalten gibt, das mit BEIDEN Alternierungs-Varianten
  scheitert, waere der naechste Schritt, das erfolgreiche Verhalten
  fuer dieses Modell separat zu diagnostizieren (analog zum bisherigen
  Vorgehen: isoliertes Testskript, dann Vergleich) und ggf. eine
  dritte Variante in die Alternierung aufzunehmen (z. B. `reuse_pattern
  = [True, False, True]` auf eine laengere Sequenz erweitern, falls 3
  Versuche fuer 3 unterschiedliche Druckerverhalten nicht mehr reichen).
  Denkbar waere auch, das erfolgreiche `reuse_session`-Verhalten pro
  Drucker-Seriennummer in `config.json` zu merken (kein Neuraten bei
  jedem Druck mehr noetig) - aktuell nicht implementiert, da die
  Alternierung selbst bereits ausreichend zuverlaessig und einfach ist.

  **v1.5.8 - v1.5.7 WAR UNVOLLSTAENDIG: A1-Mini-Timeout trat AUCH ohne
  Sitzungs-Wiederverwendung auf, echte Ursache lag (auch) am TLS-
  Versions-Deckel.** Der Nutzer bestaetigte: X1C funktioniert weiterhin
  einwandfrei, der A1 Mini scheiterte aber weiterhin identisch - UND
  ZWAR BEI ALLEN 3 VERSUCHEN, also auch bei Versuch 2 (ohne Sitzungs-
  Wiederverwendung). Das widerlegt die alleinige "Sitzungs-
  Wiederverwendung ist die A1-Mini-Ursache"-Theorie aus v1.5.7
  eindeutig: der Timeout nach 100% uebertragenen Bytes trat identisch
  mit UND ohne `reuse_session` auf. Beim erneuten Abgleich mit der
  zuletzt beim Nutzer bestaetigt funktionierenden Version (v1.4.5) fiel
  ein zweiter, bisher uebersehener Unterschied auf: **der TLS-Versions-
  Deckel auf 1.2** (`ctx.maximum_version = ssl.TLSVersion.TLSv1_2`) war
  in v1.4.5 NICHT aktiv (wurde in v1.2.0 eingefuehrt, in v1.4.5 als
  unbestaetigte Vorsichtsmassnahme wieder entfernt, dann in v1.5.0 fuer
  die X1-Serie wieder eingefuehrt und seitdem unveraendert IMMER aktiv
  - auch fuer den A1 Mini, was in v1.5.7 uebersehen wurde). Es ist
  plausibel, dass der A1 Mini mit einer erzwungenen TLS-1.2-Verbindung
  (statt frei auszuhandeln, typischerweise TLS 1.3) beim saubereren
  Verbindungsabschluss ins Stocken geraet.
  **Implementierte Aenderung:** Statt eines einzelnen Schalters
  (`reuse_session`) gibt es jetzt zwei vollstaendige, benannte
  Verbindungsprofile (`FTPS_PROFILES` in `app.py`, `PROFILES` in
  `ftps_upload_helper.py` - inhaltlich identisch, aus technischen
  Gruenden in beiden Dateien dupliziert, da `ftps_upload_helper.py`
  komplett eigenstaendig als exe gebaut wird und keine Imports aus
  `app.py` haben kann):
  ```python
  {
      "x1": {"cap_tls12": True,  "reuse_session": True},
      "a1": {"cap_tls12": False, "reuse_session": False},
  }
  ```
  `_build_ftps_context(profile)` (app.py) / `_build_context(profile)`
  (ftps_upload_helper.py) bauen den SSL-Kontext je nach Profil auf -
  der TLS-1.2-Deckel wird nur noch fuer Profil "x1" gesetzt. Das 5.
  Kommandozeilen-Argument an die Helfer-exe/den Sentinel-Fallback ist
  jetzt ein Profilname-String ("x1"/"a1") statt "1"/"0". Die
  Alternierung in `_ftps_upload()` verwendet weiterhin dasselbe Muster
  (`profile_pattern = ["x1", "a1", "x1"]`) - X1-Nutzer bleiben also
  unveraendert beim ersten Versuch erfolgreich, A1-Mini-Nutzer sollten
  jetzt beim zweiten Versuch (Profil "a1": kein TLS-Deckel + keine
  Sitzungs-Wiederverwendung, exakt das v1.4.5-Verhalten) erfolgreich
  sein.
  **Getestet (ohne echten Drucker):** Zwei Fake-Helfer-Skripte, diesmal
  anhand des UEBERGEBENEN PROFILNAMENS (nicht mehr nur True/False)
  reagierend: (1) "A1-Mini-artig" - scheitert bei Profil "x1" mit dem
  exakten gemeldeten Timeout, gelingt bei Profil "a1"; (2) "X1-artig" -
  scheitert bei Profil "a1", gelingt bei Profil "x1" (unveraendert
  sofort im ersten Versuch, wie bereits in v1.5.7 verifiziert).
  Profilnamen-Aufloesung im Helferskript getestet (gueltige Namen "x1"/
  "a1", unbekannte Namen fallen sicher auf "x1" zurueck). Fehlerfall
  (beide Profile schlagen fehl) zeigt jetzt "Profil x1"/"Profil a1" in
  der zusammengefassten Fehlermeldung statt der vorherigen "mit/ohne
  Sitzungs-Wiederverwendung"-Formulierung. Vollstaendiger Async-
  Confirm-Flow-Test weiterhin erfolgreich.
  **Ein Test gegen einen echten A1 Mini mit dieser Loesung konnte in
  dieser Umgebung nicht durchgefuehrt werden** - Bestaetigung durch den
  Nutzer stand zum Zeitpunkt dieser Übergabe noch aus. Das ist jetzt
  der ZWEITE Versuch, den A1-Mini-Fall zu loesen (v1.5.7 reichte nicht
  aus) - falls Profil "a1" beim naechsten Test IMMER NOCH denselben
  Timeout zeigt, waere die naechste zu pruefende Variable die
  Blockgroesse (aktuell fest 8192 Bytes) oder der Verbindungs-Timeout
  (aktuell fest 25 Sekunden, siehe `ftp.connect(ip, 990, timeout=25)`)
  - beide waren in der v1.4.5-Basisversion identisch zu heute und daher
  bisher nicht als Verdaechtige betrachtet, sollten aber bei einem
  erneuten Fehlschlag nicht mehr ausgeschlossen werden.
  **Lesson Learned:** Nach der ersten (unvollstaendigen) Diagnose in
  v1.5.7 waere ein sorgfaeltigerer, VOLLSTAENDIGER Vergleich ALLER
  Unterschiede zur zuletzt bestaetigt funktionierenden Version (statt
  nur der einen naheliegendsten Variable) von Anfang an gruendlicher
  gewesen. Bei einem Regressionsfall (etwas, das FRUEHER nachweislich
  funktionierte, JETZT aber nicht mehr) ist ein systematischer Diff
  gegen die zuletzt bestaetigt funktionierende Version tendenziell
  zuverlaessiger als eine Einzelhypothese, auch wenn diese zunaechst
  plausibel erscheint - besonders wenn (wie hier) mehrere Aenderungen
  seit der letzten Bestaetigung akkumuliert wurden.

  **v1.5.9 - ENDGUELTIGE URSACHE GEFUNDEN: schlicht zu kurzes Timeout,
  nicht TLS.** Der Nutzer testete `FtpsUploadHelper.exe` mit Profil
  "a1" (= exakt die historisch bestaetigt funktionierende v1.4.5-
  Konfiguration) KOMPLETT EIGENSTAENDIG (Dashboard vollstaendig
  geschlossen) - und selbst DANN trat exakt derselbe Timeout auf
  ("The read operation timed out" bei 390206/390206 Bytes, 100%). Das
  ist der entscheidende Beleg: TLS-Konfiguration (Version, Sitzungs-
  Wiederverwendung) UND Dashboard-Kontext (MQTT-Pause, Subprozess-
  Isolation) sind damit BEIDE endgueltig als Ursache ausgeschlossen -
  der Fehler tritt identisch auf, wenn buchstaeblich nichts anderes als
  reines `ftplib` mit der historisch korrekten Konfiguration laeuft,
  voellig unabhaengig vom Dashboard. Da die Datei nachweislich
  VOLLSTAENDIG ankommt (100% in jedem einzelnen Testlauf ueber die
  gesamte Chronologie hinweg, nie ein Abbruch waehrend der eigentlichen
  Uebertragung), bleibt als einzig verbleibende Erklaerung: der A1 Mini
  braucht nach Abschluss der Datenuebertragung schlicht LAENGER als das
  bisherige **25-Sekunden-Zeitlimit**, um die Datei fertig zu
  verarbeiten (z. B. SD-Karten-Schreibvorgang, Pruefsumme) und seine
  "226 Transfer complete"-Abschluss-Antwort zu senden - das Dashboard
  gab vorzeitig auf, obwohl der Drucker die Datei laengst korrekt
  erhalten hatte und nur noch etwas Zeit brauchte, um das zu
  bestaetigen.
  **Implementierte Aenderung:** `ftp.connect(ip, 990, timeout=25)` auf
  `timeout=120` angehoben, in `ftps_upload_helper.py` UND `app.py`s
  Sentinel-Fallback (`_run_ftps_upload_worker()`). Dieser Timeout-Wert
  wird von `ftplib.FTP` fuer ALLE Socket-Operationen der Verbindung
  verwendet (nicht nur den initialen Verbindungsaufbau), betrifft also
  auch das Lesen der finalen Abschluss-Antwort nach `storbinary()`.
  Das aeussere `subprocess.Popen(...).wait(timeout=30)` in
  `_ftps_upload_once()` (app.py) musste NICHT angepasst werden: es
  greift erst NACH der zeilenweisen stdout-Leseschleife, die selbst
  kein eigenes Zeitlimit hat und beliebig lange auf den Kindprozess
  wartet - `proc.wait(timeout=30)` danach ist nur eine Formsache zum
  Einsammeln des bereits abgeschlossenen Prozesses, kein zusaetzliches
  Zeitlimit fuer die eigentliche FTP-Operation (per Code-Inspektion
  bestaetigt, nicht nur angenommen).
  **Getestet (ohne echten Drucker):** Fake-Helfer-Skript simuliert einen
  "langsamen, aber letztlich erfolgreichen" Drucker (Antwort kommt nach
  einer verzoegerten Zeitspanne, die frueher als Fehlschlag gegolten
  haette) - Upload gelingt jetzt korrekt, statt vorzeitig abzubrechen;
  vollstaendiger Async-Confirm-Flow-Test weiterhin erfolgreich; beide
  Timeout-Werte (`ftps_upload_helper.py` und `app.py`-Fallback) per
  Code-Inspektion auf den neuen Wert verifiziert.
  **Ein Test gegen einen echten A1 Mini konnte in dieser Umgebung nicht
  durchgefuehrt werden** - Bestaetigung durch den Nutzer stand zum
  Zeitpunkt dieser Übergabe noch aus. Das ist der DRITTE Anlauf fuer
  den A1-Mini-Fall (v1.5.7 und v1.5.8 reichten nicht aus). Sollte auch
  120 Sekunden nicht ausreichen, waere ein weiteres Anheben (z. B. auf
  300s) der naechste, sehr risikoarme Schritt - es gibt keinen Hinweis
  darauf, dass eine laengere Wartezeit selbst irgendein Problem
  verursachen wuerde, im Gegensatz zu den bisherigen TLS-Experimenten.
  **Lesson Learned (Ergaenzung zu v1.5.8):** Die "vollstaendige-
  Verbindungsprofile"-Analyse in v1.5.8 hat einen wichtigen Hinweis
  bereits selbst geliefert und dann nicht konsequent zu Ende verfolgt:
  das Symptom "Datei kommt zu 100% an, aber Abschluss-Antwort wird nie
  gelesen" beschreibt praezise ein TIMING-Problem (Timeout), nicht ein
  TLS-Konfigurationsproblem (das typischerweise die Uebertragung selbst
  stoeren wuerde, nicht nur das Lesen einer Antwort danach). Der
  Symptom-Text selbst ("The read operation timed out") war die ganze
  Zeit der staerkste Hinweis auf die Ursache. Bei einem `socket.timeout`/
  `TimeoutError` in einer Fehlermeldung sollte das Zeitlimit selbst
  immer eine der ersten zu pruefenden Variablen sein, nicht erst nach
  mehreren anderen Theorien.

  **NACHTRAG zu v1.5.9: Bestaetigung durch Nutzer ergab - Fehler
  bleibt bestehen, VIERTER Anlauf fuer A1 Mini noch offen.** Der Nutzer
  bestaetigte, dass der Fehler nach dem Timeout-Update weiterhin
  auftritt. Auf Nachfrage, ob es jetzt spuerbar laenger dauert (naeher
  an den neuen 120s) oder weiterhin gleich schnell wie vorher: die
  Antwort war uneindeutig ("dauert sehr lange, hat es aber vorher auch
  schon") - das laesst offen, ob das einzelne 120s-Zeitlimit pro
  Versuch tatsaechlich ausgeschoepft wird (was fuer "einfach noch mehr
  Zeit geben" spraeche) oder ob der Fehler weiterhin schnell auftritt
  (was gegen die reine Timeout-Theorie spraeche und eher auf eine
  aktiv zurueckgesetzte/vom Netzwerk gekappte Verbindung hindeuten
  wuerde, die nicht einfach durch laenger Warten geloest werden kann).
  **Fuer die Weiterarbeit:** Eine PRAeZISE Zeitmessung (Stoppuhr vom
  Start des Uploads bis zur finalen Fehlermeldung, in Sekunden) ist der
  naechste noetige Datenpunkt, um zwischen diesen beiden Erklaerungen zu
  unterscheiden - bisher nicht eingeholt, da der Fokus in derselben
  Nutzer-Ruecksprache auf ein zweites, neu gemeldetes Problem
  (Mehrfarb-Druck haengt beim Aufheizen, siehe v1.5.10 unten) verlagert
  wurde. Der A1-Mini-FTPS-Fall bleibt damit zum Zeitpunkt dieser
  Uebergabe ungeloest - siehe Abschnitt 7 fuer den vollstaendigen
  Status und naechste Schritte.

  **v1.5.10 - NEUES, EIGENSTAENDIGES PROBLEM: Mehrfarb-/Mehrmaterial-
  Druck haengt beim Aufheizen des Druckbetts (unabhaengig vom FTPS-
  Thema, betrifft X1C).** Waehrend der A1-Mini-FTPS-Fall noch offen
  war, meldete der Nutzer ein NEUES Symptom bei einem X1C: bei einem
  Mehrfarb-Druck (2-3 verschiedene Filamente, u. a. mit geringer
  Farbabweichung wie Gruen/Hellgruen, manuell im Dialog korrekt dem
  passenden AMS-Fach zugeordnet) wird die Datei korrekt uebertragen
  und der Druckauftrag im Speicher des Druckers angelegt, der Drucker
  beginnt aber NICHT mit dem Aufheizen des Druckbetts - er "steht"
  einfach, laesst sich aber abbrechen. Reine Einzelfarb-Drucke (PLA,
  ASA-CF) funktionieren weiterhin einwandfrei seit v1.5.6/v1.5.4.
  **Entscheidender Diagnoseschritt:** Derselbe, bereits erfolgreich
  uebertragene Druckauftrag wurde direkt am Display des Druckers
  (SD-Karten-Ansicht, nicht ueber das Dashboard) gestartet - **das
  funktionierte einwandfrei.** Das bewies eindeutig: die Datei und die
  AMS-Zuordnung sind vollstaendig in Ordnung, das Problem liegt
  spezifisch im von unserem Dashboard gesendeten MQTT-`project_file`-
  Kommando (analog zur bereits geloesten ASA-CF-Diagnose aus v1.5.6,
  aber ein anderes konkretes Feld betreffend).
  **Recherche:** Ein bekannter, gut dokumentierter Bambu-Firmware-
  Fehlerzustand "Failed to get AMS mapping table" tritt bei Mehrfarb-
  Drucken auf und wurde in zahlreichen GitHub-Issues (bambulab/
  Bambu-Handy#171, bambulab/BambuStudio#10181/#3965/#7257) und Forum-
  Threads dokumentiert - interessanterweise tritt dieser Fehler auch
  bei offiziellem Bambu Studio/der Handy-App auf (verschiedenste
  Ursachen: Firmware-Bugs, SD-Karten-Probleme, `filament_id`-
  Inkonsistenzen in der `.3mf`), ist also NICHT zwangslaeufig auf
  einen falschen MQTT-Befehl zurueckzufuehren. Ein Blog-Post (Cinder's
  Blog) behauptete, das `ams_mapping`-Array muesse IMMER genau 4
  Elemente haben (ein Eintrag pro physischem AMS-Fach) - das wurde
  durch die offizielle Dokumentation der etablierten Referenz-
  bibliothek **bambulabs_api** widerlegt: dort ist `ams_mapping:
  list[int]` mit Standardwert `[0]` dokumentiert - eine VARIABLE-LAeNGE-
  Liste mit einem Eintrag pro tatsaechlich benoetigtem Filament, exakt
  wie unser Dashboard es bereits implementiert (`_slot_to_flat_index()`,
  siehe Codestellen-Tabelle) - die 4-Elemente-Theorie war also eine
  falsche Faehrte, die NICHT weiterverfolgt wurde.
  Stattdessen fiel beim Vergleich mit `bambulabs_api`s dokumentierter
  API-Signatur ein anderer, konkreter Unterschied auf:
  `PrinterMQTTClient.start_print_3mf(..., flow_calibration: bool =
  True)` - die Referenzbibliothek verwendet standardmaessig **True**,
  waehrend unser Code `"flow_cali": False` fest einprogrammiert hatte
  (seit der urspruenglichen Payload-Erstellung, nie hinterfragt - auch
  nicht im v1.5.6-Rewrite, der zwar `bed_type` & Co. ergaenzte, aber
  `flow_cali` unangetastet liess). Mehrfarb-Drucke benoetigen beim
  Farbwechsel zwingend Spuelvorgaenge (Purging), fuer die vermutlich
  Kalibrierungsdaten vorhanden sein muessen - plausible Erklaerung fuer
  ein Haengenbleiben, das schon VOR dem eigentlichen Druckstart
  (Aufheizen) auftritt, waehrend Einzelfarb-Drucke (kein Farbwechsel
  noetig) davon unberuehrt bleiben.
  **Implementierte Aenderung:** `"flow_cali": False` auf `"flow_cali":
  True` geaendert in `_request_print()` (app.py), mit ausfuehrlichem
  Kommentar zur Begruendung und Quelle.
  **Getestet (ohne echten Drucker):** Payload-Struktur mit einem
  simulierten MQTT-Client fuer ein Mehrfach-Filament-Szenario
  (`ams_mapping = [0, 3, 5]`) verifiziert - `flow_cali: true` korrekt
  gesetzt, Mehrfach-Mapping unveraendert korrekt uebernommen;
  vollstaendiger End-to-End-Test ueber `/print/prepare` + `/print/
  confirm` mit einem 2-Filament-Szenario (leicht unterschiedliche
  Gruentoene, `#00FF00`/`#90EE90`) bestaetigt korrekte automatische
  Zuordnung UND korrekten Payload-Aufbau bis in die tatsaechliche
  MQTT-Nachricht hinein.
  **Ein Test gegen einen echten Mehrfarb-Druck auf X1C konnte in dieser
  Umgebung nicht durchgefuehrt werden** - Bestaetigung durch den Nutzer
  stand zum Zeitpunkt dieser Übergabe noch aus.
  **Fuer die Weiterarbeit, falls der Fehler nach v1.5.10 weiterhin
  auftritt:** Da "Failed to get AMS mapping table" laut Recherche ein
  bekanntermassen vielschichtiges Bambu-Firmware-Problem mit vielen
  moeglichen Ursachen ist (nicht nur `flow_cali`), waere der naechste
  Schritt ein MQTT-Sniffer-Vergleich (analog zum in v1.5.6 dokumentierten
  Vorschlag): den tatsaechlich von Bambu Studio beim Senden DESSELBEN
  Mehrfarb-Druckauftrags gesendeten `project_file`-Befehl direkt
  mitschneiden und Feld fuer Feld mit unserem vergleichen - das waere
  fuer diesen spezifischen Drucker/diese Firmware-Version wesentlich
  aussagekraeftiger als weitere Referenz-Payload-Vergleiche aus der
  Community.

  **v1.6.0 - A1-MINI-FTPS-PROBLEM ENDGUELTIG GELOEST: die v1.5.9-
  Timeout-Theorie war ebenfalls falsch - richtige Ursache war der
  formale TLS-Verbindungsabschluss (unwrap()), nicht die Wartezeit.**
  Der Nutzer lieferte den entscheidenden neuen Datenpunkt: eine
  Uebertragung DERSELBEN Datei per **Bambu Studio** schliesst bereits
  **1-2 Sekunden nach Erreichen von 100%** erfolgreich ab. Das
  widerlegt die v1.5.9-Timeout-Theorie vollstaendig - der A1 Mini ist
  nachweislich NICHT langsam, sondern antwortet prompt. Das lenkte den
  Verdacht auf den einzigen verbleibenden Schritt zwischen "Datei
  komplett gesendet" und "Antwort gelesen": Pythons
  `ftplib.FTP.storbinary()` ruft nach der Datenuebertragung automatisch
  `conn.unwrap()` auf - ein formaler TLS-Verbindungsabschluss der
  Datenverbindung, bei dem auf ein TLS-`close_notify` vom Server
  gewartet wird (per Quellcode-Inspektion in `ftplib` verifiziert, wie
  bereits in v1.5.0 dokumentiert - siehe dortige Chronologie, wo dieselbe
  Mechanik fuer ein AeHNLICHES, aber nicht identisches Problem bei der
  X1-Serie untersucht und dort verworfen wurde). Vermutung: der A1 Mini
  sendet die eigentliche "226 Transfer complete"-Antwort zwar prompt,
  reagiert aber nicht sauber auf das formale TLS-`close_notify`, das
  `unwrap()` erwartet - waehrend Bambu Studio (eigene C++-Implementierung,
  nicht Pythons `ftplib`) vermutlich keinen solchen formalen TLS-
  Abschluss der Datenverbindung abwartet, sondern die Verbindung nach
  der Uebertragung einfach schliesst.
  **Wichtiger Kontext:** Diese "No-Unwrap"-Technik wurde bereits EINMAL
  zuvor implementiert und wieder verworfen - in v1.5.0
  (`_storbinary_no_unwrap()`, damals fuer die X1-Serie gedacht, um ein
  anderes Problem [vsftpd `require_ssl_reuse`] zu loesen). Das
  scheiterte damals, weil beim X1C ohne `unwrap()` ein `426 Failure
  reading network stream`-Fehler auftrat (dokumentiert in `ftps_test_
  minimal.py`s Test-B-Ergebnis, siehe fruehere Chronologie). Das
  bedeutet: **No-Unwrap ist fuer die X1-Serie SCHAEDLICH, aber fuer den
  A1 Mini genau die Loesung** - ein weiterer Beleg dafuer, dass beide
  Druckerfamilien grundverschiedene FTPS-Server-Implementierungen
  haben und gegensaetzliches Client-Verhalten brauchen (analog zu den
  bereits bekannten Unterschieden bei TLS-Version und Sitzungs-
  Wiederverwendung, siehe v1.5.7/v1.5.8).
  **Implementierte Aenderung:**
  - `PROFILES`/`FTPS_PROFILES` bekommen ein drittes Feld
    `"skip_unwrap"`: `x1` = `False` (unveraendert, normales `unwrap()`
    bleibt fuer die X1-Serie bestehen), `a1` = `True` (neu).
  - Neue Funktion `_storbinary_no_unwrap()` (identisch in `app.py` und
    `ftps_upload_helper.py` - inhaltlich dieselbe wie die 2025 in
    v1.5.0 entfernte Version, jetzt wieder eingefuehrt und ueber das
    Profil-System sauber nur fuer `a1` aktiv): Nachbau von
    `ftplib.FTP.storbinary()`, aber ohne das abschliessende
    `conn.unwrap()`.
  - `main()` (ftps_upload_helper.py) und `_run_ftps_upload_worker()`
    (app.py, Sentinel-Fallback) waehlen je nach `profile["skip_unwrap"]`
    zwischen `ftp.storbinary()` (normal, `x1`) und
    `_storbinary_no_unwrap()` (`a1`).
  - Das 120-Sekunden-Zeitlimit aus v1.5.9 wurde NICHT zurueckgebaut -
    es schadet nicht und bleibt als zusaetzliche, risikoarme
    Absicherung bestehen, auch wenn es nicht die eigentliche Ursache
    war.
  **Getestet (ohne echten Drucker):** `_storbinary_no_unwrap()` isoliert
  mit einem Mock-Objekt verifiziert (unwrap() wird nie aufgerufen, Daten
  korrekt gesendet, `close()` erfolgt trotzdem normal ueber den
  `with`-Block); zwei Fake-Helfer-Szenarien: (1) A1-Mini-artig - Profil
  "x1" scheitert mit dem exakten gemeldeten Timeout, Profil "a1"
  (skip_unwrap) gelingt sofort (< 2s); (2) X1-artig - unveraendert:
  Profil "x1" gelingt weiterhin sofort im ersten Versuch, keine
  Regression fuer die bereits funktionierende X1-Serie. Vollstaendiger
  Async-Confirm-Flow-Test weiterhin erfolgreich.
  **Ein Test gegen einen echten A1 Mini konnte in dieser Umgebung nicht
  durchgefuehrt werden** - Bestaetigung durch den Nutzer stand zum
  Zeitpunkt dieser Übergabe noch aus. Das ist der FUENFTE Anlauf fuer
  den A1-Mini-FTPS-Fall (v1.5.7, v1.5.8, v1.5.9 reichten nicht aus),
  diesmal aber mit einem entscheidenden neuen empirischen Datenpunkt
  (Bambu-Studio-Vergleichszeit) statt einer weiteren ungeprueften
  Theorie - deutlich besser abgesichert als die vorherigen Versuche.
  **Lesson Learned:** Der Nutzer-Hinweis "Bambu Studio braucht nur 1-2s"
  war der entscheidende Durchbruch - eine einzige konkrete
  Vergleichsmessung gegen ein bekanntermassen funktionierendes
  Referenzprogramm hat mehr bewirkt als mehrere Runden Theoretisieren
  ueber TLS-Parameter. Dieses Muster hat sich jetzt zum wiederholten
  Mal bestaetigt (vgl. den X1-Durchbruch durch den A/B-Vergleichstest
  in v1.5.3/v1.5.4): bei hartnaeckigen, protokollnahen Bugs ist eine
  Messung gegen eine bekannte, funktionierende Referenzimplementierung
  fast immer aufschlussreicher als eine weitere Parameter-Theorie.

  **v1.6.1 - UX-Verbesserung (kein Bugfix): Druckerfamilie beim
  Anlegen waehlbar, spart unnoetigen ersten Fehlversuch.** Nutzer-
  Wunsch: da nun bekannt ist, dass X1- und A1-Serie unterschiedliche,
  teils gegensaetzliche FTPS-Verbindungsprofile brauchen (siehe
  FTPS_PROFILES/PROFILES, v1.5.4-v1.6.0), soll die Alternierungs-
  Reihenfolge nicht mehr blind bei "x1" starten, sondern das dem Nutzer
  bereits bekannte Druckermodell direkt beim ersten Versuch verwenden.
  **Implementierte Aenderung:**
  - Neues Konfigurationsfeld `bambu_family` (Werte: `"x1"` oder `"a1"`,
    Standard `"x1"`) pro Bambu-Drucker in `config.json`.
  - `DashboardApp.add_printer()`: neuer Parameter `bambu_family="x1"`,
    validiert gegen `("x1", "a1")` (unbekannte Werte fallen sicher auf
    `"x1"` zurueck), im Drucker-Dict gespeichert.
  - `api_add_printer()`-Route: liest `data.get("bambu_family")` aus dem
    POST-Body, validiert ebenfalls defensiv, reicht es an `add_printer()`
    durch.
  - `load_config()`: `setdefault("bambu_family", "x1")` fuer alle
    bestehenden Bambu-Drucker in einer bereits vorhandenen
    `config.json` (Rueckwaertskompatibilitaet - alte Konfigurationen
    ohne dieses Feld funktionieren unveraendert weiter, mit "x1" als
    implizitem Verhalten wie bisher).
  - `PrinterConnection._ftps_upload()`: `profile_pattern` wird jetzt
    dynamisch aus `self.cfg.get("bambu_family", "x1")` gebaut - das
    bekannte Profil steht an erster UND dritter Stelle, das jeweils
    andere an zweiter Stelle (bleibt als automatischer Fallback
    bestehen, z. B. falls die Familie versehentlich falsch gewaehlt
    wurde oder sich das Druckermodell im Nachhinein aendert).
  - Frontend: neues `<select id="f_bambu_family">`-Dropdown im
    "Drucker hinzufuegen"-Formular (nur sichtbar/relevant bei Typ
    `bambu`, da im selben `bambuFields`-Div wie Access Code/
    Seriennummer), Standardauswahl "X1-Serie". `submitAdd()` sendet
    `body.bambu_family` mit; `openAddModal()` setzt das Dropdown beim
    Oeffnen auf den Standardwert zurueck.
  - `config.example.json`: Beispiel-Bambu-Eintrag um `"bambu_family":
    "x1"` ergaenzt.
  **Bewusst NICHT implementiert:** kein Bearbeiten-Dialog fuer
  bestehende Drucker (existiert im gesamten Programm ohnehin nicht -
  Drucker koennen nur hinzugefuegt oder entfernt werden). Wer die
  Familie eines bereits angelegten Druckers nachtraeglich aendern
  moechte, muss entweder `config.json` manuell bearbeiten (Feld
  `bambu_family` beim jeweiligen Drucker-Eintrag) oder den Drucker
  entfernen und mit der richtigen Familie neu anlegen.
  **Getestet (ohne echten Drucker):** `add_printer()` mit `bambu_family=
  "a1"` gespeichert und verifiziert; `_ftps_upload()` mit einem Fake-
  Helfer, der protokolliert, welches Profil beim JEWEILS ERSTEN Aufruf
  verwendet wird - bestaetigt fuer `bambu_family="a1"` (nutzt "a1"
  zuerst) UND `bambu_family="x1"` (nutzt "x1" zuerst, unveraendertes
  Verhalten); Standardwert ohne explizite Angabe ist weiterhin "x1";
  `load_config()` mit einer manuell erstellten "alten" `config.json`
  ohne `bambu_family`-Feld getestet - wird korrekt per `setdefault` auf
  "x1" ergaenzt; vollstaendiger API-Test ueber `POST /api/printers`
  fuer drei Faelle (gueltiges "a1", ungueltiger Wert -> Fallback "x1",
  Feld komplett weggelassen -> Fallback "x1"); HTML-Smoke-Test bestaetigt
  das neue Dropdown-Element ist im Formular vorhanden.
  **Fuer die Weiterarbeit:** Falls in Zukunft weitere Bambu-Druckerfamilien
  mit jeweils eigenen FTPS-Anforderungen bekannt werden (z. B. P1-Serie,
  falls sich diese anders als A1 oder X1 verhalten sollte - bisher nicht
  getestet), waere die Dropdown-Liste und `FTPS_PROFILES`/`PROFILES`
  entsprechend um einen dritten Eintrag zu erweitern sowie
  `profile_pattern` in `_ftps_upload()` auf mehr als 3 Versuche
  auszuweiten oder die Zuordnung Familie→Profil zu verfeinern.

  **NACHTRAG: FTPS-Upload beim A1 Mini funktioniert (bestaetigt), aber
  neuer, ANDERSARTIGER Fehler beim Druckstart entdeckt - kein Bug,
  Konfigurationsproblem.** Der Nutzer bestaetigte: mit `bambu_family=
  "a1"` gelingt der FTPS-Upload jetzt beim ALLERERSTEN Versuch (die
  gesamte FTPS-Saga v1.5.0-v1.6.0 gilt damit als vollstaendig geloest
  UND vom Nutzer bestaetigt). Danach trat aber ein neuer, voellig
  anderer Fehler auf: der Drucker meldete beim Druckstart **"Die
  Ueberpruefung des MQTT-Befehls ist fehlgeschlagen"** ("MQTT command
  verification failed"). Recherche ergab (offizielle Bambu-Wiki-Seite,
  HMS-Code 0500-0500-0001-0007): dieser Fehler tritt auf, wenn der
  **Developer Mode** fuer das jeweilige Geraet NICHT aktiv ist - mit
  aktiviertem Developer Mode werden Autorisierung/Authentifizierung der
  MQTT-Befehle komplett uebersprungen. Der Nutzer bestaetigte: Developer
  Mode war fuer den A1 Mini tatsaechlich NICHT aktiviert (obwohl LAN-
  Modus bereits lief) - das ist eine Pro-Geraet-Einstellung, die separat
  fuer jeden Drucker in der Bambu Handy App gesetzt werden muss.
  **Kein Software-Fix noetig** - der Nutzer aktivierte Developer Mode
  fuer den A1 Mini, danach sollte der Druck normal starten (Bestaetigung
  stand zum Zeitpunkt dieser Übergabe noch aus, aber die Diagnose ist
  durch offizielle Bambu-Dokumentation eindeutig bestaetigt, nicht nur
  vermutet). README Abschnitt 4 wurde um einen expliziten Hinweis auf
  dieses Fehlerbild und seine Ursache ergaenzt, damit zukuenftige Nutzer
  mit mehreren Druckern das nicht uebersehen.
  **Wichtiger Hintergrund fuer die Weiterarbeit, falls Developer Mode
  bestaetigt aktiviert ist und der Fehler TROTZDEM weiterhin auftritt:**
  Seit Januar 2025 verlangt neuere Bambu-Firmware fuer bestimmte
  "kritische" MQTT-Befehle zusaetzlich eine **X.509-Zertifikat-Signatur**
  (RSA-SHA256), die offiziell nur ueber die proprietaere "Bambu Connect"-
  Anwendung erfolgt - das damals von Bambu eingefuehrte Firmware-Update
  hat viele Drittanbieter-Tools (OctoPrint, Home Assistant, eigene
  Skripte) zunaechst komplett unterbrochen. Community-Forscher haben das
  in der "Bambu Connect"-App eingebettete X.509-Zertifikat samt privatem
  Schluessel extrahiert (siehe Hackaday-Artikel "Bambu Connect's
  Authentication X.509 Certificate and Private Key Extracted", Januar
  2025) - dieses Material ist inzwischen oeffentlich bekannt und in
  mehreren aktiv gepflegten Open-Source-Projekten eingebettet (u. a.
  `schwarztim/bambu-mcp`, `griches/bambu-mcp`). Sollte sich herausstellen,
  dass Developer Mode allein (entgegen der offiziellen Bambu-Doku) nicht
  ausreicht, waere die Implementierung einer aehnlichen X.509-Signatur
  fuer den `project_file`-Befehl der naechste Schritt - das ist aber ein
  nicht-trivialer Umfang (Krypto-Bibliothek, Zertifikat/Schluessel-
  Verwaltung, Signatur-Format) und sollte nur bei tatsaechlichem Bedarf
  angegangen werden, nicht praeventiv.

  **v1.6.2 - NEUER, ECHTER STRUKTURELLER BUG GEFUNDEN UND BEHOBEN:
  "Failed to get AMS mapping table" bei Mehrfarb-Drucken auf X1C -
  falsches Indexierungsschema im ams_mapping-Array.** Nachdem A1 Mini
  vollstaendig bestaetigt funktioniert (FTPS-Saga endgueltig
  abgeschlossen) und der Developer-Mode-Hinweis den vorherigen MQTT-
  Verifikationsfehler geklaert hatte, meldete der Nutzer einen NEUEN
  Fehler beim Drucken eines Mehrfarb-Modells auf einem X1C: der Drucker
  zeigte explizit auf dem Display **"Die Zuordnungstabelle des AMS
  konnte nicht abgerufen werden"** - die deutsche Uebersetzung des
  bereits in der v1.5.10-Recherche gefundenen, dokumentierten Bambu-
  Fehlers "Failed to get AMS mapping table".
  **Ursachenanalyse per Code-Review (kein weiterer Recherche-Auftrag
  noetig, der Bug war im eigenen Code klar erkennbar nach genauem
  Nachvollziehen des Datenflusses):**
  - `_parse_3mf_filaments()` filtert die vollstaendige Filamentliste aus
    `project_settings.config` (0-basiert, ALLE im Projekt konfigurierten
    Filamente) auf die per `slice_info.config` fuer Plate 1 tatsaechlich
    benoetigte Teilmenge - dabei bleibt zwar das ORIGINALE `index`-Feld
    pro Filament-Dict erhalten, ABER:
  - `PrinterConnection.preview_print()` baute das `suggestion`-Array
    bisher per einfachem `.append()` in der Reihenfolge der GEFILTERTEN
    Liste - die Position im Array entsprach also der Position in der
    gefilterten Anzeige-Liste, NICHT dem echten `index`-Feld.
  - Im Frontend baute `confirmAmsModal()` das an `/print/confirm`
    gesendete `mapping`-Array ebenfalls per einfachem `Array.from(rows)
    .map(...)` - rein positionsbasiert nach DOM-Reihenfolge, ohne
    jemals den echten Filament-Index zu beruecksichtigen (`amsRowHtml()`
    setzte `data-filament-index` auf die Schleifenposition `i`, nicht
    auf `filament.index`).
  - Ergebnis: Enthielt ein Projekt z. B. 4 Filamente, von denen Plate 1
    nur die Filamente mit echtem Index 1 und 3 benoetigt (Luecke bei 0
    und 2 - z. B. weil das Projekt urspruenglich mit mehr Farboptionen
    angelegt wurde, als auf DIESER Platte verwendet werden), wurde ein
    KOMPAKTES 2-Element-Array `[wert_fuer_index_1, wert_fuer_index_3]`
    gesendet - der Drucker interpretiert Position 0 und 1 aber als
    Zuordnung fuer die Filamente MIT ECHTEM INDEX 0 und 1, nicht 1 und
    3. Eine voellig falsche, vom Drucker als ungueltig zurueckgewiesene
    Zuordnungstabelle.
  - **Warum das bei Einzelfarb-Drucken (PLA, ASA-CF - beide bereits
    bestaetigt funktionierend) nie auffiel:** bei nur einem verwendeten
    Filament mit dem (haeufigsten) echten Index 0 sind kompaktes und
    "echtes" Array zufaellig identisch (`[wert]` an Position 0 in
    beiden Faellen) - der Bug hat also gezielt Mehrfarb-Drucke mit
    einer Indexluecke getroffen, nicht Einzelfarb-Drucke.
  **Implementierte Aenderung (Backend UND Frontend gemeinsam noetig):**
  - `_parse_3mf_filaments()`: Rueckgabe geaendert zu einem Tupel
    `(filamente, gesamtanzahl)` - `gesamtanzahl` ist die Laenge des
    VOLLSTAENDIGEN `filament_colour`-Arrays aus `project_settings.config`
    (noch vor dem Filtern auf Plate 1), damit die Aufrufer wissen, wie
    gross das finale `ams_mapping`-Array sein muss.
  - `PrinterConnection.preview_print()`: gibt zusaetzlich
    `total_filaments` zurueck (unveraendert `filaments` mit dem
    jeweils ECHTEN `index`-Feld pro Eintrag, wie es das auch vorher
    schon tat - das Feld war schon da, wurde nur nie konsequent
    genutzt).
  - `/api/printers/<id>/print/prepare`-Route: gibt `total_filaments`
    zusaetzlich in der JSON-Antwort mit aus.
  - Frontend `openAmsModal()`: neue globale Variable
    `amsModalTotalFilaments`, aus `data.total_filaments` uebernommen.
  - Frontend `amsRowHtml()`: neues `data-true-index="${filament.index}"`-
    Attribut pro Zeile (zusaetzlich zum bisherigen `data-filament-index`,
    das weiterhin die Schleifenposition fuer DOM-IDs/Radio-Gruppen haelt
    - diese beiden Zwecke wurden bewusst getrennt, um unnoetig grosse
    Aenderungen an der DOM-Struktur/den Radio-Gruppennamen zu vermeiden).
  - Frontend `confirmAmsModal()`: baut das `mapping`-Array jetzt als
    `new Array(amsModalTotalFilaments).fill(-1)` und traegt jede
    Zuordnung an der Position `trueIndex` (aus `data-true-index`) ein,
    statt einfach in DOM-Reihenfolge zu pushen. Nicht angezeigte
    Filamente (aus der urspruenglichen Datei, aber nicht auf Plate 1
    benoetigt) bleiben korrekt auf `-1`.
  - `amsModalTotalFilaments` wird beim Schliessen/Abschluss des Modals
    (in `cancelAmsModal()` und beim `'done'`-Progress-Status) auf `0`
    zurueckgesetzt, um Zustandslecks zwischen verschiedenen Druckauftraegen
    zu vermeiden.
  **Getestet (ohne echten Drucker):** `_parse_3mf_filaments()` mit einer
  eigens gebauten `.gcode.3mf`-Testdatei, die EXAKT das Bug-Szenario
  nachbildet (4 Filamente im Projekt, Plate 1 nutzt nur die mit echtem
  Index 1 und 3) - bestaetigt, dass die echten Indizes 1 und 3 erhalten
  bleiben (nicht zu 0 und 1 umnummeriert); `preview_print()` end-to-end
  mit derselben Testdatei bestaetigt korrekte `total_filaments` (4) und
  korrekte `suggested_tray`-Zuordnung pro echtem Index; vollstaendiger
  HTTP-Route-Test (`POST /print/prepare`) bestaetigt `total_filaments`
  in der tatsaechlichen JSON-Antwort. **Die Frontend-JS-Logik wurde
  zusaetzlich isoliert mit Node.js direkt getestet** (nicht nur gelesen):
  ein Node-Skript baut die exakte `confirmAmsModal()`-Logik nach und
  bestaetigt, dass bei simulierten `rows` mit `trueIndex` 1 und 3 sowie
  `amsModalTotalFilaments=4` korrekt das Array `[-1, 0, -1, 1]`
  entsteht - UND ein Vergleichslauf mit der ALTEN (fehlerhaften) Logik
  zeigt explizit den Unterschied (altes Verhalten haette `[0, 1]`
  geliefert, die falsche, vom Drucker zurueckgewiesene Zuordnung).
  Ausserdem: Regressionstest fuer den Einzelfarb-Fall (ein Filament mit
  echtem Index 0) bestaetigt identisches Verhalten wie vorher - keine
  Regression fuer die bereits funktionierenden Faelle. Vollstaendiger
  End-to-End-Test von der `.3mf`-Datei bis zum finalen MQTT-`ams_mapping`-
  Feld in `_request_print()`s Payload bestaetigt den kompletten
  Datenfluss.
  **Ein Test gegen einen echten Mehrfarb-Druck auf X1C konnte in dieser
  Umgebung nicht durchgefuehrt werden** - Bestaetigung durch den Nutzer
  stand zum Zeitpunkt dieser Übergabe noch aus. Anders als bei den
  vorherigen FTPS-Runden ist dieser Fix aber kein Verhaltens-Experiment,
  sondern die Behebung eines klar nachvollziehbaren, durch Tests
  bewiesenen strukturellen Bugs (Index-Verwechslung) - entsprechend hoch
  ist die Zuversicht, dass dies das gemeldete Symptom tatsaechlich behebt.
  **Fuer die Weiterarbeit, falls der Fehler nach v1.6.2 weiterhin
  auftritt:** Falls es noch einen weiteren, bisher nicht erkannten Fall
  von Index-Verwechslung gibt (z. B. falls die tatsaechliche Filament-
  Nummerierung des Druckers nicht 0-basiert ist, wie hier angenommen,
  sondern in einer noch anderen Konvention), waere ein MQTT-Sniffer-
  Vergleich (Bambu Studio vs. unser Dashboard fuer denselben Mehrfarb-
  Druckauftrag) der praeziseste naechste Schritt, um das exakte, vom
  Drucker erwartete Array-Format zu verifizieren.

  **v1.6.3 - NEUES FEATURE (kein Bugfix): Druckauftrag per Drag & Drop
  jetzt auch fuer Ultimaker-Drucker.** Auf Nutzerwunsch erweitert: analog
  zu Bambu Lab (Abschnitt "Bambu Lab: Druckauftrag...") koennen jetzt
  auch fertig gesclicte `.gcode`-Dateien (Cura-Export) per Drag & Drop
  auf die Ultimaker-Karte gezogen werden, um den Druck zu starten.
  **Wesentlicher Unterschied zu Bambu:** die Ultimaker-API verlangt fuer
  schreibende Aktionen (Datei-Upload, Druckstart) zusaetzlich zur reinen
  Statusabfrage eine gesonderte **Kopplung** (id/key-Paar), die der
  Drucker erst nach Bestaetigung AM EIGENEN DISPLAY ausgibt - vergleichbar
  mit Bluetooth-Pairing. Diese Kopplung ist einmalig pro Drucker noetig
  (Zugangsdaten werden dauerhaft in `config.json` gespeichert), nicht bei
  jedem Druck erneut.
  **Recherche (offizielle Ultimaker-Swagger-Doku + mehrere unabhaengige
  UltiMaker-Forum-Threads, konsistent beschrieben - keine geratenen
  Endpunkte):**
  - Kopplung: `POST /api/v1/auth/request` (Pflichtfelder `application`/
    `user`, sonst Fehler "application or user not supplied") liefert
    sofort ein `id`/`key`-Paar zurueck - dieses ist aber erst gueltig,
    NACHDEM der Nutzer eine am Drucker-Display erscheinende
    Bestaetigungs-Abfrage angenommen hat. Der Status wird per
    `GET /api/v1/auth/check/{id}` abgefragt (Polling), bis `"authorized"`
    oder `"unauthorized"` zurueckkommt.
  - Druckauftrag: `POST /api/v1/print_job` (multipart/form-data, Felder
    `file` + `jobname`), authentifiziert per **HTTP Digest Auth** (RFC
    2617) mit dem id/key-Paar als Benutzername/Passwort.
  - Bekannte Einschraenkung aus der Recherche: Firmware 8.1 hatte einen
    dokumentierten Bug, der `/auth/request` voruebergehend unbrauchbar
    machte (spaeter gepatcht) - als Hinweis in der README aufgenommen,
    kein Workaround im Code noetig (betrifft nur veraltete Firmware).
  **Implementierte Aenderung:**
  - Neue Modul-Funktionen `_parse_digest_challenge()` (parst einen
    `WWW-Authenticate: Digest ...`-Header), `_build_digest_authorization()`
    (baut den `Authorization`-Header nach RFC 2617, MD5, qop=auth, reine
    `hashlib`/`secrets`-Standardbibliothek, keine externe Digest-Auth-
    Bibliothek) und `_build_multipart_body()` (manueller multipart/
    form-data-Aufbau, da `urllib` dafuer keine eingebaute Unterstuetzung
    hat und sich fuer dieses eine Formular keine zusaetzliche
    Abhaengigkeit lohnt).
  - **Bewusste Design-Entscheidung gegen Pythons eingebauten
    `urllib.request.HTTPDigestAuthHandler`:** dieser wuerde bei jeder
    Anfrage zwingend ZWEI Round-Trips brauchen (erst unauthentifiziert
    -> 401, dann erneut MIT Anmeldedaten) - bei einem potenziell
    mehrere MB grossen `.gcode`-Upload wuerde die Datei dabei zweimal
    uebertragen. Stattdessen: `UltimakerConnection._digest_challenge()`
    loest die 401-Challenge ueber den bewusst LEICHTEN Endpunkt
    `/api/v1/auth/verify` aus (keine Datei-Uebertragung), danach wird
    der eigentliche Upload mit bereits fertig berechnetem
    `Authorization`-Header nur EINMAL gesendet.
  - `UltimakerConnection.start_pairing()`/`check_pairing()`/`send_print()`
    - neue Methoden, analog zur Struktur von `PrinterConnection` bei
    Bambu, aber deutlich einfacher (kein AMS-Aequivalent, kein FTPS,
    keine Profile/Alternierung noetig).
  - `DashboardApp.__init__()`: neues `self._ultimaker_pending_auth`-Dict
    (`printer_id -> (auth_id, auth_key, gestartet_um)`) fuer laufende,
    noch nicht bestaetigte Kopplungsanfragen - NUR waehrend einer
    laufenden Kopplung befuellt, danach entfernt (bei Erfolg wandern
    id/key dauerhaft in `config.json`, siehe `get_printer_cfg()` +
    `save_config()`; bei Fehlschlag/Timeout [2 Minuten] wird der Eintrag
    verworfen).
  - `DashboardApp.start_ultimaker_pairing()`/`check_ultimaker_pairing()`/
    `send_ultimaker_print_now()` - orchestrieren Kopplung bzw.
    Druckauftrag. **Wichtig:** `send_ultimaker_print_now()` startet den
    Druck SOFORT nach dem Hochladen (kein zweistufiger prepare/confirm-
    Ablauf wie bei Bambu noetig, da keine AMS-Zuordnung zu bestaetigen
    ist) - nutzt aber dieselben `_print_jobs`/`_print_progress`-
    Strukturen weiter, sodass die BESTEHENDEN generischen Routen
    `GET /print/progress/<job_id>` unveraendert wiederverwendet werden
    koennen (keine neuen Progress-/Cancel-Routen noetig).
  - Drei neue Flask-Routen: `POST /api/printers/<id>/ultimaker/pair/start`,
    `GET .../ultimaker/pair/status`, `POST .../ultimaker/print`.
  - `DashboardApp.all_status()`: liefert zusaetzlich `ultimaker_paired`
    (bool) fuer Drucker vom Typ `ultimaker`, damit das Frontend weiss,
    ob die Ablage-Flaeche oder der Kopplungs-Button angezeigt werden soll.
  - Frontend: `renderUltimakerDropZone()` (zeigt je nach `ultimaker_paired`
    entweder die Ablage-Flaeche oder einen "Jetzt koppeln"-Button),
    `pairUltimaker()` (startet Kopplung, pollt Status alle 2s bis zu
    130s), `dzDropUltimaker()` (validiert `.gcode`-Endung, laedt hoch),
    `pollUltimakerProgress()` (pollt denselben generischen Progress-
    Endpunkt wie Bambu, zeigt Toast bei `done`/`error`). Neue CSS-Klasse
    `.drop-zone.dz-disabled` fuer den ungekoppelten Zustand; `.btn-mini`
    (bereits vorhanden) fuer den Kopplungs-Button wiederverwendet.
  **Bewusste Vereinfachung:** kein granulares Fortschritts-Feedback
  waehrend des Uploads (die Ultimaker-API bietet dafuer keinen Hook wie
  Bambus FTPS-Callback) - die Ablage-Flaeche zeigt nur einen "wird
  hochgeladen"-Zustand ohne Prozentanzeige. Da gcode-Dateien i. d. R.
  deutlich kleiner als Bambus `.gcode.3mf`-Pakete sind, wurde das als
  akzeptabler Scope-Kompromiss bewertet, nicht als fehlende Funktion.
  **Getestet (ohne echten Drucker, aber ungewoehnlich gruendlich fuer
  ein neues Feature):**
  - `_parse_digest_challenge()`/`_build_digest_authorization()` isoliert
    getestet UND die berechnete Response-Hash-Kette manuell (per Hand
    nachgerechnetem MD5) gegen RFC 2617 verifiziert - nicht nur auf
    "sieht plausibel aus" geprueft.
  - `_build_multipart_body()` isoliert auf korrekte Struktur getestet.
  - `UltimakerConnection.send_print()` gegen einen ECHTEN, selbst
    geschriebenen `http.server.HTTPServer`-Testserver getestet, der die
    Digest-Antwort SERVERSEITIG selbst nachrechnet und nur bei
    korrekter Signatur akzeptiert - eine deutlich staerkere Verifikation
    als ein reiner Client-seitiger Test, da sie beweist, dass ein
    echter, unabhaengiger RFC-2617-Digest-Server die Anfrage als gueltig
    akzeptieren wuerde.
  - `start_pairing()`/`check_pairing()` gegen einen simulierten Server
    getestet (korrekte Felder gesendet, Antwort korrekt verarbeitet).
  - Alle drei neuen Flask-Routen end-to-end getestet: erfolgreiche
    Kopplung inkl. Persistierung in `config.json` und `ultimaker_paired`-
    Statusanzeige; vollstaendiger Druckauftrag-Flow von der Upload-Route
    bis zum digest-authentifizierten `send_print()`-Aufruf (wieder gegen
    den echten Test-HTTP-Server); alle Fehlerfaelle (falsche Datei-
    endung, fehlende Kopplung, fehlende Datei, Kopplungsstatus ohne
    laufende Anfrage, abgelaufene Kopplungsanfrage inkl. korrektem
    Aufraeumen).
  - Vollstaendiger Regressionstest mit gemischten Druckertypen
    (Bambu + Ultimaker gleichzeitig) bestaetigt keine gegenseitige
    Beeintraechtigung; `ultimaker_paired`-Feld erscheint korrekt NUR bei
    Ultimaker-Druckern, nicht bei anderen Typen.
  **Ein Test gegen einen echten Ultimaker-Drucker (inkl. der Display-
  Bestaetigung) konnte in dieser Umgebung nicht durchgefuehrt werden** -
  Bestaetigung durch den Nutzer stand zum Zeitpunkt dieser Übergabe noch
  aus. Die Implementierung folgt aber sehr genau der offiziellen, von
  mehreren unabhaengigen Quellen konsistent bestaetigten API-Dokumentation
  und wurde (soweit ohne echten Drucker moeglich) ungewoehnlich gruendlich
  gegen einen echten, unabhaengigen Digest-Auth-Server verifiziert.
  **Fuer die Weiterarbeit, falls beim ersten echten Test Probleme
  auftreten:**
  1. Falls die Kopplungsanfrage selbst fehlschlaegt (schon Schritt 1):
     Firmware-Version pruefen - Firmware 8.1 (vor einem Patch) hatte
     laut Recherche einen bekannten Bug bei `/auth/request`.
  2. Falls die Kopplung gelingt, der Druckstart aber mit HTTP 401
     scheitert: moeglicherweise erwartet die konkrete Firmware-Version
     ein anderes `algorithm`-Feld oder eine andere qop-Variante als
     `auth` (z. B. `auth-int`, das den Nachrichtenkoerper mit in den
     Hash einbezieht) - dann muesste `_build_digest_authorization()`
     um diese Variante ergaenzt werden.
  3. Falls "No file received" trotz korrekter Digest-Auth auftritt
     (ein in der Recherche gefundenes, von mehreren Nutzern unabhaengig
     berichtetes Symptom bei multipart-Uploads): den exakten Aufbau des
     multipart-Bodys mit einem Netzwerk-Mitschnitt (Wireshark) gegen
     einen erfolgreichen curl-Aufruf vergleichen - moeglicherweise
     erwartet die Firmware eine bestimmte Feld-Reihenfolge (`jobname`
     vor `file` oder umgekehrt) oder einen zusaetzlichen Header.

  **v1.6.4 - ERSTER PRAXISTEST (gegen einen Nachbau, nicht Original-
  Hardware): Kopplung funktionierte, Druckstart scheiterte mit
  "Erwartete Digest-Authentifizierungs-Anfrage (401) blieb aus".**
  Der Nutzer testete v1.6.3 gegen "Ultimaker Connect Raspi MK1" - ein in
  einem ANDEREN Chat von Claude selbst gebautes, separates Projekt
  (Raspberry Pi + USB-angeschlossener Ultimaker 2+, bildet die
  Netzwerk-API eines netzwerkfaehigen Ultimaker nach, damit Cura ihn
  wie einen normalen Netzwerkdrucker erkennt - siehe eigene
  Memory-Datei `/areas/ultimaker-connect-raspi.md` fuer Details zu
  jenem Projekt). **Der Chatverlauf jenes Projekts konnte trotz
  mehrerer conversation_search-Versuche mit verschiedenen Suchbegriffen
  nicht aufgefunden werden** - die exakte Implementierung des
  Nachbaus (welche Endpunkte er tatsaechlich unterstuetzt) ist daher
  NICHT bekannt, nur aus dem Fehlerbild erschlossen.
  **Ursachenanalyse:** Die Fehlermeldung selbst war eindeutig:
  `UltimakerConnection._digest_challenge()` (v1.6.3-Implementierung)
  fragte gezielt den separaten Endpunkt `GET /api/v1/auth/verify` ab,
  um die Digest-Challenge (realm/nonce) zu erhalten - erwartete dabei
  eine 401-Antwort mit `WWW-Authenticate: Digest ...`. Blieb diese aus,
  wurde eine Fehlermeldung geworfen. Da die KOPPLUNG (auth/request +
  auth/check) beim Nutzer nachweislich funktionierte, aber der
  Druckstart an dieser Stelle scheiterte, ist die wahrscheinlichste
  Erklaerung: der Nachbau implementiert `/api/v1/auth/verify` schlicht
  NICHT (dieser Endpunkt ist in der offiziellen Doku vorhanden, aber
  fuer die KERNFUNKTION - Kopplung + Druckstart - nicht zwingend
  erforderlich, ein Nachbau koennte ihn daher plausibel ausgelassen
  haben, ohne dass das dem urspruenglichen Auftrag "Netzwerk-API
  nachbilden, damit Cura den Drucker erkennt" widersprechen wuerde).
  **Implementierte Aenderung (Robustheits-Fix, kein Rate-Versuch):**
  `_digest_challenge()` fragt die Digest-Challenge jetzt nicht mehr
  ueber den separaten `/api/v1/auth/verify`-Endpunkt ab, sondern direkt
  vom TATSAECHLICHEN Ziel-Endpunkt `/api/v1/print_job` (per leerem
  `POST`, `data=b""`, OHNE Datei) - das ist ohnehin der Endpunkt, dessen
  Digest-Realm/Nonce fuer den anschliessenden echten Upload gebraucht
  wird, und muss fuer die Kernfunktion "Druckauftraege senden" so oder
  so korrekt auf eine unautorisierte Anfrage mit 401 + Digest-Challenge
  reagieren - unabhaengig davon, ob zusaetzliche Nebenendpunkte wie
  `/auth/verify` implementiert sind. Das macht die Implementierung
  robuster gegenueber Nachbauten/vereinfachten API-Implementierungen,
  ohne die Kompatibilitaet zu echter Ultimaker-Hardware zu gefaehrden
  (die REALE Ultimaker-API muss laut Dokumentation ohnehin auch
  `/print_job` selbst mit Digest-Auth schuetzen - das war nie
  fraglich, nur OB zusaetzlich `/auth/verify` separat existiert).
  Fehlermeldung fuer den verbleibenden Fehlerfall (Server akzeptiert die
  leere Anfrage komplett ohne 401) wurde praezisiert: erklaert jetzt
  explizit, dass entweder gar keine Digest-Authentifizierung verlangt
  wird oder das Antwortverhalten von der Doku abweicht.
  **Getestet (ohne echte Hardware, aber gezielt gegen das exakte
  gemeldete Fehlerbild):** ein simulierter Server, der `/api/v1/auth/
  verify` bewusst NICHT implementiert (404) aber `/print_job` korrekt
  per Digest schuetzt, akzeptiert jetzt den kompletten `send_print()`-
  Ablauf - dieses Szenario schlug mit dem v1.6.3-Code nachweislich fehl
  (siehe Test in der Chat-Historie: `_digest_challenge()` warf exakt die
  vom Nutzer gemeldete Fehlermeldung, bevor der Fix angewendet wurde).
  Zusaetzlich: ein simulierter Server MIT `/auth/verify` bestaetigt
  keine Regression fuer echte Ultimaker-Hardware (die diesen Endpunkt
  laut Doku implementiert); ein dritter Test (Server verlangt gar keine
  Authentifizierung) bestaetigt die praezisierte Fehlermeldung.
  **Bestaetigung durch den Nutzer, dass der Druckstart gegen den
  Nachbau jetzt tatsaechlich funktioniert, stand zum Zeitpunkt dieser
  Übergabe noch aus** - der naechste sinnvolle Schritt waere:
  1. Erneuter Test gegen "Ultimaker Connect Raspi MK1".
  2. Falls IMMER NOCH derselbe Fehler auftritt: der Nachbau verlangt
     vermutlich GAR KEINE Digest-Authentifizierung fuer `/print_job`
     (dann wuerde die praezisierte Fehlermeldung das jetzt auch klar so
     sagen) - dann waere zu klaeren, ob der Nachbau ueberhaupt eine
     Authentifizierung fuer den Druckstart implementiert, und falls
     nein, ob unser Dashboard optional auch OHNE Digest-Auth senden
     koennen soll (waere ein separater, expliziter Kompatibilitaetsmodus,
     kein Ersatz fuer die Digest-Auth-Implementierung, da echte
     Ultimaker-Hardware diese zwingend braucht).
  3. Falls ein ANDERER Fehler auftritt (z. B. 400/415 bei der leeren
     Probe-Anfrage, noch bevor der Server ueberhaupt zur Auth-Pruefung
     kommt): der Nachbau prueft moeglicherweise erst die Multipart-
     Struktur, bevor er Authentifizierung prueft - dann muesste die
     Probe-Anfrage einen (leeren) aber strukturell gueltigen multipart/
     form-data-Body mitschicken statt eines komplett leeren Bodys.
  4. Am zuverlaessigsten waere grundsaetzlich, den Chatverlauf des
     "Ultimaker Connect Raspi"-Projekts zu finden (z. B. indem der
     Nutzer direkt danach fragt oder dessen `app.py` hochlaedt) und die
     tatsaechlich implementierten Endpunkte/das Auth-Verhalten direkt
     nachzulesen, statt weiter aus Fehlermeldungen zu erschliessen.

  **v1.6.5 - URSACHE ENDGUELTIG GEKLAeRT (echter Quellcode statt
  Vermutung): der Nachbau prueft beim Druckstart GAR KEINE
  Authentifizierung.** Der Nutzer lud auf Nachfrage `app.py`,
  `README.md` UND `ultimaker_api.py` des "Ultimaker Connect Raspi
  MK1"-Projekts direkt als Projekt-Dateien hoch (`/mnt/project/`) -
  damit konnte die tatsaechliche Implementierung erstmals direkt
  gelesen werden, statt weiter aus Fehlermeldungen zu erschliessen (wie
  in v1.6.3/v1.6.4 noch noetig, da der zugehoerige Chat trotz mehrerer
  `conversation_search`-Versuche nicht auffindbar war). Zentrale
  Erkenntnisse aus dem tatsaechlichen Code:
  - `GET /api/v1/auth/verify` liefert IMMER `200 {"message": "ok"}` -
    nie eine 401-Antwort. Das erklaert den ERSTEN gemeldeten Fehler
    (v1.6.3-Code fragte genau diesen Endpunkt ab).
  - `POST /api/v1/print_job` prueft AUSSCHLIESSLICH, ob ein multipart-
    Feld `"file"` vorhanden ist (`if "file" not in request.files:
    return 400`) - **keinerlei Digest-Authentifizierung, keine
    Ueberpruefung des id/key-Paars ueberhaupt**. Das erklaert den
    ZWEITEN gemeldeten Fehler (v1.6.4-Code sendete eine leere Probe-
    Anfrage OHNE Datei an genau diesen Endpunkt, was prompt mit 400
    beantwortet wurde - nicht weil Authentifizierung fehlschlug,
    sondern weil ueberhaupt keine Datei mitgeschickt wurde und der
    Handler diese Pruefung offenbar VOR jeder denkbaren Auth-Pruefung
    durchfuehrt - die aber ohnehin nirgends im Code existiert).
  - `POST /api/v1/auth/request`/`GET /api/v1/auth/check/<id>`
    implementieren den Kopplungs-Handshake zwar korrekt genug, damit
    unser Dashboard eine erfolgreiche Kopplung sieht (jede Anfrage wird
    laut Code-Kommentar UND README bewusst automatisch autorisiert, da
    der Pi kein Display fuer eine physische Bestaetigung hat) - das
    dabei erzeugte id/key-Paar wird aber an keiner Stelle im gesamten
    Modul jemals wieder verwendet oder ueberprueft. Die Kopplung ist
    also rein kosmetisch/protokollkonform fuer Cura's Verbindungs-
    Dialog, hat aber keine tatsaechliche Sicherheitsfunktion in diesem
    Nachbau.
  **Implementierte Aenderung (generelle, nicht nachbau-spezifische
  Robustheit):** `_digest_challenge()` (Ruecknahme der bisherigen
  Fehler-werfenden Semantik) wurde zu `_digest_challenge_or_none()`
  umgebaut: liefert weiterhin die geparste Challenge bei einer echten
  401+Digest-Antwort (fuer echte Ultimaker-Hardware zwingend
  erforderlich), liefert aber jetzt **`None` statt eine Exception zu
  werfen**, wenn die Probe-Anfrage IRGENDEINE andere Antwort bekommt
  (200, 400, 201, ...) - das deckt sowohl "akzeptiert alles ohne Auth"
  als auch "lehnt aus einem anderen Grund ab, der nichts mit
  Authentifizierung zu tun hat" (wie beim Nachbau: fehlende Datei) ab,
  ohne zwischen beiden unterscheiden zu muessen - in beiden Faellen ist
  die richtige Reaktion identisch: den echten Upload OHNE
  Authorization-Header senden. `send_print()` fuegt den
  `Authorization`-Header nur noch bedingt hinzu, wenn `challenge is not
  None`. **Wichtig: das ist kein nachbau-spezifischer Hack**, sondern
  eine allgemein sinnvolle Verhaltensweise ("nutze Authentifizierung,
  falls verlangt, sonst nicht") - echte Ultimaker-Hardware, die
  weiterhin zwingend 401+Digest liefert, ist davon unveraendert
  betroffen und funktioniert weiterhin wie bisher; nur Server, die GAR
  KEINE Auth verlangen, werden jetzt zusaetzlich unterstuetzt, statt
  einen (in diesem Fall falschen) Fehler zu erzwingen.
  **Getestet:** ein Test-Server, der EXAKT das Verhalten der echten
  `ultimaker_api.py` nachbildet (auth/verify immer 200, print_job prueft
  nur auf `"file"` im Multipart-Body, keinerlei Auth-Pruefung) -
  `send_print()` funktioniert jetzt korrekt End-to-End dagegen. Zusaetzlich
  weiterhin bestaetigt: (1) echte Ultimaker-Hardware-Simulation (401 +
  Digest-Challenge, korrekte Zugangsdaten) funktioniert unveraendert -
  keine Regression; (2) falsche Zugangsdaten bei einem Server, der
  tatsaechlich Digest-Auth verlangt, wird weiterhin korrekt als Fehler
  erkannt (nicht faelschlich als "keine Auth noetig" fehlinterpretiert,
  da dieser Fall ueber einen echten 401 mit gueltiger Digest-Challenge
  laeuft, nur die spaetere Antwort auf den ECHTEN Upload mit den
  falschen Daten schlaegt fehl - unveraendertes Verhalten). Vollstaendiger
  Regressionstest mit gemischten Druckertypen weiterhin erfolgreich.
  **Bestaetigung durch den Nutzer, dass der Druckstart gegen den
  Nachbau jetzt tatsaechlich funktioniert, stand zum Zeitpunkt dieser
  Übergabe noch aus** - basiert aber diesmal auf einer Verifikation
  gegen den TATSAECHLICHEN, vom Nutzer bereitgestellten Quellcode, nicht
  mehr auf einer Vermutung aus dem Fehlerbild - deutlich hoehere
  Zuversicht als bei den beiden vorherigen Versuchen.
  **Lesson Learned:** Nach ZWEI aufeinanderfolgenden Fehlversuchen, das
  Problem allein aus der Fehlermeldung zu erschliessen (v1.6.3, v1.6.4),
  war die direkte Einsicht in den tatsaechlichen Server-Code der
  entscheidende Schritt - beide vorherigen "Robustheits-Fixes" waren
  in sich schluessig und plausibel, trafen aber nicht die tatsaechliche
  Ursache, weil sie auf Annahmen ueber ein unbekanntes System beruhten.
  Bei der Fehlersuche gegen ein System, dessen Quellcode potenziell
  verfuegbar ist (hier: ein Projekt, das der Nutzer selbst mit Claude in
  einem anderen Chat gebaut hat), sollte das Anfordern des tatsaechlichen
  Codes VOR weiteren Vermutungen stehen, sobald mehr als ein
  Vermutungsversuch fehlgeschlagen ist.
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

- **Ultimaker-Druckauftrag per Drag & Drop (v1.6.3-v1.6.5) - Ursache
  endgueltig geklaert durch direkte Einsicht in den echten Nachbau-
  Quellcode, Bestaetigung durch Nutzer noch ausstehend.** Erster
  Praxistest lief gegen einen Nachbau ("Ultimaker Connect Raspi MK1",
  ein separates Claude-Projekt aus einem anderen Chat), nicht gegen
  echte Ultimaker-Hardware. Kopplung funktionierte durchgehend, der
  Druckstart scheiterte zunaechst zweimal in Folge (v1.6.3, v1.6.4),
  da beide Fixes auf VERMUTUNGEN ueber das Nachbau-Verhalten beruhten
  (der zugehoerige Chat war trotz mehrerer `conversation_search`-
  Versuche nicht auffindbar). Erst nachdem der Nutzer `app.py`,
  `README.md` UND `ultimaker_api.py` des Nachbaus direkt als
  Projekt-Dateien hochlud, liess sich die tatsaechliche Ursache klar
  erkennen: **der Nachbau prueft beim Druckstart (`POST /api/v1/
  print_job`) ueberhaupt keine Authentifizierung** - er verlangt nur,
  dass ein Datei-Feld vorhanden ist. Das Kopplungs-Handshake (auth/
  request + auth/check) ist rein kosmetisch fuer Cura's Verbindungs-
  Dialog, das dabei erzeugte id/key-Paar wird nie ueberprueft. Fix
  (v1.6.5): `_digest_challenge_or_none()` liefert jetzt `None` statt
  eine Exception zu werfen, wenn die Probe-Anfrage keine 401-Digest-
  Antwort bekommt - `send_print()` sendet dann ohne Authorization-
  Header. Echte Ultimaker-Hardware (verlangt zwingend Digest-Auth)
  bleibt davon unberuehrt. Siehe Abschnitt 5 "v1.6.5" fuer
  vollstaendige Details. **Noch KEIN Test gegen echte Ultimaker-
  Original-Hardware; Bestaetigung fuer den Nachbau durch den Nutzer
  stand zum Zeitpunkt dieser Übergabe ebenfalls noch aus** - diesmal
  aber mit deutlich hoeherer Zuversicht, da der Fix gegen den
  tatsaechlichen, vom Nutzer bereitgestellten Server-Code verifiziert
  wurde, nicht nur gegen eine Vermutung.
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
- **FTPS-Upload: X1-Serie (X1C, X1E) VOM NUTZER BESTAETIGT FUNKTIONS-
  FAEHIG. A1 Mini: geloest in v1.6.0 (fuenfter Anlauf), Bestaetigung
  durch Nutzer ausstehend.** Vollstaendige Chronologie siehe Abschnitt 5
  "Bambu Lab: Druckauftrag...". Kurzfassung der Fehldiagnosen: v1.5.7
  (Sitzungs-Wiederverwendung allein), v1.5.8 (TLS-Version + Sitzungs-
  Wiederverwendung als "x1"/"a1"-Profile), v1.5.9 (Zeitlimit 25s→120s,
  Theorie: Drucker sei nur langsam) - alle scheiterten beim Nutzer-Test
  identisch mit "The read operation timed out" bei 100% uebertragenen
  Bytes. **Entscheidender neuer Datenpunkt:** der Nutzer verglich mit
  Bambu Studio - dieselbe Datei wird dort bereits 1-2 Sekunden nach
  Erreichen von 100% erfolgreich uebertragen, was die "Drucker ist
  langsam"-Theorie aus v1.5.9 endgueltig widerlegte. **Tatsaechliche
  Ursache (v1.6.0):** Pythons `ftplib.storbinary()` ruft nach der
  Uebertragung automatisch einen formalen TLS-Verbindungsabschluss
  (`unwrap()`) auf, auf den der A1 Mini offenbar nicht sauber reagiert -
  waehrend Bambu Studio (eigene Implementierung) das vermutlich gar
  nicht erst abwartet. Fix: `_storbinary_no_unwrap()` (bereits einmal
  in v1.5.0 fuer die X1-Serie implementiert und dort wieder verworfen,
  jetzt gezielt nur fuer das "a1"-Profil reaktiviert) - siehe Abschnitt
  5 fuer vollstaendige Details. Wichtig: dieselbe Technik ist fuer die
  X1-Serie SCHAEDLICH (426-Fehler, siehe v1.5.0-Historie), fuer den A1
  Mini aber die Loesung - ein weiterer Beleg fuer grundverschiedene
  FTPS-Server-Implementierungen zwischen beiden Druckerfamilien.
  **Fuer die Weiterarbeit, falls der Fehler nach v1.6.0 weiterhin
  auftritt** (das waere der SECHSTE Anlauf):
  1. Pruefen, ob der Fehlertext identisch bleibt oder sich aendert (z. B.
     zu einem `426`-artigen Fehler, wie er bei der X1-Serie ohne
     `unwrap()` auftrat) - das waere ein wichtiges neues Datum.
  2. Falls weiterhin exakt derselbe Timeout: ein Wireshark-Mitschnitt
     von Bambu Studio (erfolgreich) vs. unserem Dashboard (scheiternd)
     fuer denselben Druckauftrag waere der praeziseste naechste Schritt -
     zeigt exakt, an welcher Stelle im TLS-/FTP-Protokollablauf sich
     beide Implementierungen tatsaechlich unterscheiden, statt weiter zu
     vermuten.
  3. Alternativ: die Blockgroesse (aktuell fest 8192 Bytes) systematisch
     variieren, falls sich ein Zusammenhang zur Dateigroesse zeigt.
  **Workaround, falls weiterhin ungeloest:** Datei manuell per FileZilla
  oder Bambu Studio hochladen, Druck am Display starten (README
  Abschnitt 4a).
- **Mehrfarb-/Mehrmaterial-Druck (X1C) - VOLLSTAENDIG behoben (zwei
  zusammenhaengende Ursachen, v1.5.10 + v1.6.2), Bestaetigung durch
  Nutzer fuer den zweiten Fix noch ausstehend.** Zwei getrennte, nach-
  einander aufgetretene Probleme, beide ausschliesslich beim Start
  ueber das Dashboard (derselbe Druckauftrag lief bei manuellem Start
  am Display jeweils problemlos an): (1) v1.5.10: Drucker begann nicht
  mit dem Aufheizen des Druckbetts - Ursache war `flow_cali` fest auf
  `False` statt dem von der Referenzbibliothek `bambulabs_api`
  standardmaessig verwendeten `True`. Fix bestaetigt wirksam (der
  Nutzer berichtete danach ueber einen ANDEREN, neuen Fehler - das
  Aufheiz-Problem selbst trat nicht wieder auf). (2) v1.6.2: neuer
  Fehler "Die Zuordnungstabelle des AMS konnte nicht abgerufen werden"
  ("Failed to get AMS mapping table") - Ursache war ein echter,
  struktureller Indexierungsfehler: das an den Drucker gesendete
  `ams_mapping`-Array wurde kompakt in ANZEIGE-Reihenfolge gebaut statt
  an den ECHTEN Filament-Positionen aus der `.gcode.3mf`, was bei
  Projekten mit mehr definierten Filamenten als auf der gedruckten
  Platte tatsaechlich verwendet (Indexluecken) zu einer fuer den
  Drucker ungueltigen Zuordnungstabelle fuehrte. Fix betrifft Backend
  UND Frontend gemeinsam (siehe Abschnitt 5 fuer vollstaendige Details,
  inkl. isoliertem Node.js-Test der JS-Logik). Anders als bei den
  FTPS-Themen ist dies ein klar bewiesener struktureller Bug, kein
  Verhaltens-Experiment - entsprechend hohe Zuversicht in die Loesung.
  **Fuer die Weiterarbeit, falls "Failed to get AMS mapping table" nach
  v1.6.2 weiterhin auftritt:** ein MQTT-Sniffer-Vergleich (Bambu Studio
  vs. unser Dashboard fuer denselben Mehrfarb-Druckauftrag) waere der
  naechste, praeziseste Schritt, um zu pruefen, ob es noch eine weitere,
  bisher unentdeckte Indexierungs-Eigenheit gibt (z. B. falls die
  Filament-Nummerierung in bestimmten Faellen doch nicht 0-basiert waere).
- **AMS-Zuordnung bei Verbundwerkstoffen (PLA-CF, PETG-CF, ASA-CF, PA-CF,
  ABS-GF usw.) - Fix in v1.5.5 ausgeliefert, war aber NICHT die Ursache
  des konkret gemeldeten Falls (siehe naechster Punkt fuer die
  tatsaechliche Ursache).** Urspruengliche Vermutung: die Typ-Pruefung
  in `_find_matching_tray()` nutzte einen reinen Teilstring-Vergleich,
  der "ASA" faelschlich als Teilmenge von "ASA-CF" akzeptierte. Der
  Fix (`_types_compatible()`, siehe Abschnitt 5) ist weiterhin sinnvoll
  und bleibt bestehen - der Nutzer bestaetigte aber, dass die AMS-
  Zuordnung in seinem konkreten Fall schon VOR diesem Fix korrekt war
  (ASA-CF wurde tatsaechlich vorgeschlagen und war im Drucker geladen).
- **Druck haengt beim Materialladen (kein Fehlerdisplay) - Ursache
  gefunden und behoben in v1.5.6, Bestaetigung durch Nutzer ausstehend.**
  Nach Ausschluss der AMS-Zuordnung als Ursache (siehe oben) fiel beim
  Vergleich mit dokumentierten Referenz-MQTT-Payloads auf: das
  `project_file`-Kommando fehlte mehrere Felder, allen voran `bed_type`
  - ohne dieses Feld ist unklar, welchen Druckbett-Typ die Firmware
  annimmt, was bei anspruchsvolleren Materialien (ASA-CF: hohe
  Bett-/Duesentemperatur) eher zu Problemen fuehrt als bei PLA. Fix:
  vollstaendiger Feldsatz ergaenzt (`bed_type: "auto"`, `subtask_name`,
  `project_id`/`profile_id`/`task_id`, siehe Abschnitt 5 fuer Details).
  **Fuer die Weiterarbeit, falls das Problem nach v1.5.6 weiterhin
  auftritt:** Ein MQTT-Sniffer (MQTT Explorer, mosquitto_sub) parallel
  zu einem erfolgreichen Bambu-Studio-Druck auf demselben Drucker
  mitlaufen lassen und den *tatsaechlich* gesendeten `project_file`-
  Befehl direkt (Byte-fuer-Byte-JSON-Diff) mit unserem vergleichen -
  das zeigt den fuer DIESE konkrete Firmware-Version tatsaechlich
  erwarteten Befehl, zuverlaessiger als weitere Community-Referenzen.
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

- Aktuelle Version: **v1.6.5** (v1.1.0: Drag-&-Drop-Druckfeature,
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
  beim Materialladen hängen (PLA nicht betroffen). Ursache vermutet: zu
  lockere Teilstring-Typprüfung in `_find_matching_tray()` akzeptierte
  "ASA" als Teilmenge von "ASA-CF" (analog für alle Verbundwerkstoffe).
  Fix: neue Funktion `_types_compatible()` — **bestätigte sich beim
  Nutzer-Test als NICHT die Ursache dieses konkreten Falls** (Zuordnung
  war schon vorher korrekt), Fix bleibt aber für den allgemeinen Fall
  sinnvoll. v1.5.6: tatsächliche Ursache gefunden — das gesendete
  `project_file`-MQTT-Kommando fehlte mehrere von Bambu Studio selbst
  gesendete Felder, allen voran `bed_type`, was bei anspruchsvolleren
  Materialien wie ASA-CF (hohe Bett-/Düsentemperatur) zu einem
  Hängenbleiben beim Materialladen führen kann. Fix: vollständiger
  Feldsatz ergänzt [`bed_type: "auto"`, `subtask_name`, `project_id`/
  `profile_id`/`task_id`]. **Vom Nutzer bestätigt: X1C und X1E
  übertragen und starten Drucke jetzt zuverlässig.** v1.5.7: dabei
  aufgedeckter Zielkonflikt — der A1 Mini (fkt. vor v1.5.4 einwandfrei)
  scheiterte neu mit einem neuen Fehlerbild [Timeout nach 100%
  übertragenen Bytes]. Vermutete Ursache: TLS-Session-Wiederverwendung.
  Fix: `reuse_session`-Parameter, Alternierung über die 3 Versuche
  [mit/ohne/mit] — **beim Nutzer-Test scheiterten aber ALLE 3 Versuche
  identisch, auch ohne Sitzungs-Wiederverwendung: v1.5.7 war
  unvollständig.** v1.5.8: zweiter, übersehener Unterschied zu v1.4.5
  gefunden — der seit v1.5.0 immer aktive TLS-1.2-Deckel. Fix: zwei
  vollständige, benannte Profile ("x1": TLS-1.2 + Session-Reuse; "a1":
  freie TLS-Aushandlung ohne Session-Reuse, exakt v1.4.5-Verhalten)
  statt eines Einzelschalters, Alternierung zwischen beiden über die 3
  Versuche — **beim Nutzer-Test scheiterte auch Profil "a1" identisch:
  v1.5.8 ebenfalls unvollständig.** v1.5.9: Nutzer testete Profil "a1"
  komplett eigenständig [Dashboard geschlossen] — derselbe Timeout trat
  SELBST DANN auf, was TLS UND Dashboard-Kontext beide endgültig
  ausschloss. Vermutete Ursache: schlicht ein zu kurzes Zeitlimit
  [25 Sekunden]. Fix: Zeitlimit auf 120 Sekunden angehoben
  [`ftp.connect(ip, 990, timeout=120)`] — **beim Nutzer-Test besteht der
  Fehler WEITERHIN: v1.5.9 ebenfalls nicht bestätigt erfolgreich, vierter
  Anlauf für den A1-Mini-Fall bleibt offen**. v1.5.10:
  separates, neu gemeldetes Problem — Mehrfarb-Druck auf X1C blieb beim
  Aufheizen des Druckbetts hängen [Einzelfarb-Drucke funktionieren
  weiterhin]. Direkter Start am Display bewies: Datei/AMS-Zuordnung
  korrekt, Ursache im MQTT-Kommando. Fund: `flow_cali` war fest auf
  `False` gesetzt, Referenzbibliothek `bambulabs_api` nutzt standardmäßig
  `True`. Fix: `flow_cali` auf `True` geändert. Eine Alternativtheorie
  [`ams_mapping` müsse immer 4 Elemente haben] wurde durch
  `bambulabs_api`s Dokumentation widerlegt und nicht verfolgt.
  v1.6.0: entscheidender neuer Datenpunkt für den A1-Mini-Fall — Nutzer
  verglich mit Bambu Studio, das dieselbe Datei bereits 1-2s nach 100%
  erfolgreich überträgt, was die Timeout-Theorie aus v1.5.9 endgültig
  widerlegte. Tatsächliche Ursache: `ftplib.storbinary()` ruft nach der
  Übertragung automatisch `conn.unwrap()` [formaler TLS-Abschluss] auf,
  worauf der A1 Mini offenbar nicht sauber reagiert. Fix:
  `_storbinary_no_unwrap()` [bereits einmal in v1.5.0 für die X1-Serie
  implementiert und dort verworfen, jetzt gezielt nur für Profil "a1"
  reaktiviert] — schädlich für X1, aber die Lösung für A1 Mini.
  Fünfter Anlauf für den A1-Mini-Fall; Bestätigung durch Nutzer für
  beide offenen Fälle [A1-Mini-FTPS, Mehrfarb-Druck] stand zum
  Zeitpunkt dieser Übergabe noch aus. v1.6.1: reine UX-Verbesserung
  [kein Bugfix] — neues Feld `bambu_family` ["x1"/"a1", Standard "x1"]
  beim Anlegen eines Bambu-Druckers wählbar, steuert die Startreihenfolge
  in `_ftps_upload()`s Profil-Alternierung, sodass das bereits bekannte
  Druckermodell direkt im ersten statt im zweiten Versuch verwendet
  wird. Rückwärtskompatibel über `setdefault()` in `load_config()`.
  **A1 Mini vom Nutzer bestätigt: FTPS-Upload funktioniert jetzt
  einwandfrei — die gesamte FTPS-Saga [v1.5.0–v1.6.1] gilt damit als
  abgeschlossen.** v1.6.2: neuer, echter struktureller Bug gefunden —
  "Failed to get AMS mapping table" bei Mehrfarb-Drucken auf X1C. Das
  gesendete `ams_mapping`-Array wurde kompakt in Anzeige-Reihenfolge
  gebaut statt an den echten Filament-Positionen aus der `.gcode.3mf`,
  was bei Indexlücken [mehr Filamente im Projekt als auf der Platte
  verwendet] zu einer vom Drucker abgelehnten Zuordnungstabelle führte.
  Betraf nur Mehrfarb-Drucke — bei Einzelfarb-Drucken sind kompaktes
  und echtes Array zufällig identisch. Fix in Backend
  [`_parse_3mf_filaments()` liefert jetzt `(filamente, gesamtanzahl)`,
  `preview_print()` liefert `total_filaments`] UND Frontend
  [`confirmAmsModal()` baut das Array jetzt an den echten
  `data-true-index`-Positionen] gemeinsam. Isoliert mit Node.js
  getestet, inkl. Vergleichslauf alte vs. neue Logik. Bestätigung durch
  Nutzer stand zum Zeitpunkt dieser Übergabe noch aus. v1.6.3: neues
  Feature [kein Bugfix] auf Nutzerwunsch — Druckauftrag per Drag & Drop
  jetzt auch fuer Ultimaker (`.gcode`-Dateien, Cura-Export). Erfordert
  einmalige Kopplung [Digest-Auth id/key-Paar, Bestaetigung am Drucker-
  Display] vor dem ersten Druck. Eigene RFC-2617-Digest-Auth-
  Implementierung [`_build_digest_authorization()`] statt Pythons
  eingebautem `HTTPDigestAuthHandler`, um die Datei nur einmal statt
  zweimal senden zu muessen. Gegen einen echten, selbst geschriebenen
  Digest-Auth-Testserver verifiziert. Kompletter Test gegen einen
  echten Ultimaker-Drucker stand zum Zeitpunkt dieser Übergabe noch aus.
  v1.6.4: erster Praxistest — gegen "Ultimaker Connect Raspi MK1" [ein
  Nachbau aus einem anderen Chat, nicht Original-Hardware]. Kopplung
  funktionierte, Druckstart scheiterte: "Erwartete Digest-
  Authentifizierungs-Anfrage (401) blieb aus". Ursache vermutet:
  Challenge wurde über den separaten Endpunkt `/api/v1/auth/verify`
  angefragt, den der Nachbau vermutlich nicht implementiert. Fix:
  Challenge wird direkt vom Ziel-Endpunkt `/api/v1/print_job` geholt
  [leerer POST] — **beim erneuten Test schlug auch dieser Fix fehl**
  ["HTTP 400, erwartet: 401"]: v1.6.4 ebenfalls unvollständig. v1.6.5:
  Nutzer lud den TATSÄCHLICHEN Quellcode des Nachbaus hoch [app.py,
  README.md, ultimaker_api.py] — direkte Einsicht ergab: der Nachbau
  prüft beim Druckstart überhaupt KEINE Authentifizierung, nur ob ein
  Datei-Feld vorhanden ist. Fix: `_digest_challenge_or_none()` liefert
  `None` statt Exception, wenn keine 401-Digest-Antwort kommt —
  `send_print()` sendet dann ohne Authorization-Header, funktioniert
  für beide Fälle [echte Hardware mit Zwangs-Auth UND Server ohne Auth]
  gleichzeitig. Gegen den exakten Nachbau-Code getestet. Bestätigung
  durch Nutzer stand zum Zeitpunkt dieser Übergabe noch aus).
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
