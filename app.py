"""
3D-Drucker Dashboard
=====================
Ein lokal laufendes Web-Dashboard fuer 3D-Drucker im eigenen Netzwerk.

Unterstuetzte Geraetetypen:

- Bambu Lab (P1/X1/A1 Serie, LAN-/Developer-Modus): Verbindung per MQTT.
  Fortschritt, Datei, Kamera, AMS-Filamente, Temperaturen.
- Formlabs Drucker (Form 3/4/Fuse Serie): Verbindung ueber die offizielle
  "Formlabs Local API" (siehe Klasse FormlabsLocalApiConnection unten fuer
  Details/Voraussetzungen). Fortschritt, Druckauftrag, Material.
- Formlabs Wash L / Formlabs Cure L: gleiche Anbindung wie Formlabs
  Drucker, Fortschritt + aktueller Zyklus. (Die "kleinen" Form Wash/Form
  Cure OHNE "L" haben keinerlei Netzwerkfunktion und koennen technisch
  nicht eingebunden werden.)
- OctoPrint: Verbindung per REST-API (X-Api-Key). Fortschritt, Datei,
  Temperaturen, Kamera - wird optisch/funktional wie ein Bambu Lab
  Drucker dargestellt.

Zusaetzlich: ueber einen zweiten, unabhaengigen MQTT-Broker koennen frei
definierte Sensoren (Anzeige eines Werts) und Schaltflaechen (senden
fester An-/Aus-Nachrichten) konfiguriert und einer beliebigen Drucker-
karte angehaengt werden (siehe ExtrasMqttManager unten sowie die
"extras_mqtt"/"extras"-Abschnitte in config.json).

Start (Entwicklung):   python app.py
Erreichbar unter:      http://<IP-DES-PCS>:8000
Konfiguration:         config.json (liegt im selben Ordner wie das Skript
                        bzw. wie die von PyInstaller erzeugte .exe)
"""

# Versionsnummer (semantische Versionierung, siehe UEBERGABE.md
# Abschnitt "Versionierung"). Einzige Quelle der Wahrheit fuer die
# Version - wird vom GitHub-Actions-Workflow per Regex ausgelesen, um
# Release-Tag und Datei-Namen zu erzeugen. Bei jeder ausgelieferten
# Aenderung hier erhoehen (siehe Abschnitt in UEBERGABE.md fuer die
# Regeln, was Major/Minor/Patch bedeutet).
APP_VERSION = "1.5.6"

import os
import sys
import json
import ssl
import ipaddress
import socket
import struct
import threading
import time
import uuid
import tempfile
import zipfile
import ftplib
import subprocess
import json
import xml.etree.ElementTree as ET
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime

from flask import Flask, jsonify, request, Response, render_template_string, redirect
from werkzeug.utils import secure_filename
import paho.mqtt.client as mqtt


# ----------------------------------------------------------------------
# Pfade: Config liegt neben der EXE (bzw. neben app.py im Dev-Betrieb)
# ----------------------------------------------------------------------
def base_dir() -> str:
    if getattr(sys, "frozen", False):          # laeuft als PyInstaller-EXE
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _find_ftps_upload_helper():
    """Sucht nach einer separat mitgelieferten FtpsUploadHelper-exe im
    selben Ordner wie das Hauptprogramm. Siehe ausfuehrliche Begruendung
    bei PrinterConnection._ftps_upload_once() (v1.5.3): eine separate
    Helfer-exe statt eines Selbstaufrufs mit verstecktem Kommandozeilen-
    Flag, um Sicherheitssoftware-Heuristiken nicht zu triggern. Gibt
    None zurueck, wenn keine gefunden wird - der Aufrufer faellt dann
    auf den Selbstaufruf-Mechanismus zurueck (z. B. im Entwicklungs-
    betrieb ohne vorherigen Build der Helfer-exe)."""
    name = "FtpsUploadHelper.exe" if os.name == "nt" else "FtpsUploadHelper"
    candidate = os.path.join(base_dir(), name)
    return candidate if os.path.isfile(candidate) else None


CONFIG_PATH = os.path.join(base_dir(), "config.json")
LOCK = threading.Lock()

DEFAULT_CONFIG = {
    "server": {
        "host": "0.0.0.0",
        "port": 8000
    },
    "preform_server": "http://localhost:44388",
    "extras_mqtt": {
        "enabled": False,
        "host": "",
        "port": 1883,
        "username": "",
        "password": "",
        "tls": False
    },
    "printers": []
}

FORMLABS_TYPES = ("formlabs", "formlabs_wash", "formlabs_cure")
# Alle Creality-"Versionen" nutzen technisch dieselbe Anbindung (Moonraker-
# API, siehe CrealityConnection) - die verschiedenen Typwerte dienen nur
# der Beschriftung/Auswahl im Formular, nicht einer unterschiedlichen
# technischen Anbindung.
CREALITY_TYPES = ("creality_k1", "creality_k1c", "creality_k1max", "creality_k1se", "creality_other")
KNOWN_TYPES = ("bambu",) + FORMLABS_TYPES + ("octoprint",) + CREALITY_TYPES + ("ultimaker",)


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("server", DEFAULT_CONFIG["server"])
    cfg.setdefault("preform_server", DEFAULT_CONFIG["preform_server"])
    cfg.setdefault("extras_mqtt", json.loads(json.dumps(DEFAULT_CONFIG["extras_mqtt"])))
    cfg.setdefault("printers", [])
    for p in cfg["printers"]:
        p.setdefault("extras", [])
    return cfg


def save_config(cfg: dict) -> None:
    with LOCK:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4, ensure_ascii=False)


# ----------------------------------------------------------------------
# MQTT-Client fuer einen einzelnen Bambu Lab Drucker
# ----------------------------------------------------------------------
class PrinterConnection:
    """Haelt die MQTT-Verbindung zu einem Bambu Lab Drucker und den
    zuletzt empfangenen, aufbereiteten Status."""

    PUSHALL_INTERVAL_SEC = 300

    def __init__(self, printer_cfg: dict):
        self.cfg = printer_cfg
        self.id = printer_cfg["id"]
        self.status = {
            "connected": False,
            "last_update": None,
            "gcode_state": "UNKNOWN",
            "progress": 0,
            "file_name": "-",
            "chamber_temp": None,
            "nozzle_temp": None,
            "bed_temp": None,
            "remaining_min": None,
            "ams": []
        }
        self._client = None
        self._stop = False
        self._paused = False
        self._last_pushall = 0.0

    def start(self):
        self._stop = False
        client_id = f"dashboard-{self.id}-{uuid.uuid4().hex[:6]}"
        self._client = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)
        self._client.username_pw_set("bblp", self.cfg["access_code"])
        self._client.tls_set_context(ssl._create_unverified_context())
        self._client.tls_insecure_set(True)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect
        self._client.reconnect_delay_set(min_delay=1, max_delay=15)
        threading.Thread(target=self._connect_loop, daemon=True).start()

    def _connect_loop(self):
        while not self._stop:
            if self._paused:
                # Waehrend eines FTPS-Uploads bewusst pausiert (siehe
                # pause_mqtt()/resume_mqtt()) - kein Reconnect-Versuch,
                # bis explizit fortgesetzt wird.
                time.sleep(0.5)
                continue
            try:
                self._client.connect(self.cfg["ip"], int(self.cfg.get("mqtt_port", 8883)), keepalive=30)
                self._client.loop_forever(retry_first_connection=True)
            except Exception:
                self.status["connected"] = False
                time.sleep(5)
            if self._stop:
                break
            time.sleep(3)

    def stop(self):
        self._stop = True
        try:
            if self._client:
                self._client.disconnect()
        except Exception:
            pass

    def pause_mqtt(self):
        """Trennt die MQTT-Verbindung voruebergehend und unterdrueckt
        automatisches Reconnect, bis resume_mqtt() aufgerufen wird.

        Hintergrund (v1.5.2): Selbst mit vollstaendiger Prozess-Isolation
        des FTPS-Uploads (siehe _run_ftps_upload_worker()) trat der
        Verbindungsabbruch auf der X1-Serie weiterhin identisch auf -
        das schliesst Python-Thread-/GIL-Konkurrenz als Ursache aus.
        Was in beiden Faellen unveraendert blieb: die dauerhaft aktive
        MQTT-Verbindung im Hauptprozess laeuft parallel zur FTPS-
        Datenverbindung weiter. Vermutung: der eingebettete Netzwerk-
        Stack des Druckers kommt mit einer gleichzeitig aktiven MQTT-
        Verbindung UND einer neuen, datenintensiven FTPS-Verbindung
        nicht zuverlaessig zurecht. Deshalb wird die MQTT-Verbindung
        jetzt waehrend des Uploads bewusst kurz getrennt."""
        self._paused = True
        try:
            if self._client:
                self._client.disconnect()
        except Exception:
            pass

    def resume_mqtt(self):
        """Hebt eine mit pause_mqtt() gesetzte Pause wieder auf - der
        Hintergrund-Thread (_connect_loop) verbindet sich danach
        automatisch neu."""
        self._paused = False

    def wait_for_mqtt_reconnect(self, timeout: float = 15.0) -> bool:
        """Wartet bis zu `timeout` Sekunden darauf, dass die MQTT-
        Verbindung nach resume_mqtt() wiederhergestellt ist (fuer
        _request_print(), das eine aktive Verbindung braucht)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.status.get("connected"):
                return True
            time.sleep(0.3)
        return self.status.get("connected", False)

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.status["connected"] = True
            topic = f"device/{self.cfg['serial']}/report"
            client.subscribe(topic)
            self._request_pushall(client)
        else:
            self.status["connected"] = False

    def _request_pushall(self, client):
        req_topic = f"device/{self.cfg['serial']}/request"
        payload = {"pushing": {"sequence_id": "0", "command": "pushall", "version": 1, "push_target": 1}}
        try:
            client.publish(req_topic, json.dumps(payload))
            self._last_pushall = time.time()
        except Exception:
            pass

    def _on_disconnect(self, client, userdata, rc):
        self.status["connected"] = False

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8", errors="ignore"))
        except Exception:
            return
        p = payload.get("print")
        if p:
            self._apply_print_report(p)
        if time.time() - self._last_pushall > self.PUSHALL_INTERVAL_SEC:
            self._request_pushall(client)

    def _apply_print_report(self, p: dict):
        s = self.status
        s["connected"] = True
        s["last_update"] = datetime.now().strftime("%H:%M:%S")

        if "gcode_state" in p:
            s["gcode_state"] = p["gcode_state"]
        if "mc_percent" in p:
            s["progress"] = p["mc_percent"]
        if "subtask_name" in p and p["subtask_name"]:
            s["file_name"] = p["subtask_name"]
        elif "gcode_file" in p and p["gcode_file"]:
            s["file_name"] = p["gcode_file"]
        if "chamber_temper" in p:
            s["chamber_temp"] = p["chamber_temper"]
        if "nozzle_temper" in p:
            s["nozzle_temp"] = p["nozzle_temper"]
        if "bed_temper" in p:
            s["bed_temp"] = p["bed_temper"]
        if "mc_remaining_time" in p:
            s["remaining_min"] = p["mc_remaining_time"]

        ams_root = p.get("ams", {}).get("ams")
        if isinstance(ams_root, list):
            slots = []
            for unit in ams_root:
                for tray in unit.get("tray", []):
                    slots.append({
                        "slot": f'{unit.get("id", "0")}-{tray.get("id", "0")}',
                        "type": tray.get("tray_type") or "-",
                        "color": _argb_to_css(tray.get("tray_color")),
                        "remain": tray.get("remain", -1)
                    })
            s["ams"] = slots

    # ------------------------------------------------------------------
    # Druckauftrag per Drag & Drop senden
    #
    # Bambu-Drucker haben keine REST-Upload-API. Der Ablauf ist:
    #   1. Datei per FTPS (Port 990, IMPLIZITES TLS - nicht das normale
    #      "AUTH TLS", siehe ImplicitFtpTls unten) auf den Drucker
    #      hochladen. Login: Benutzer "bblp", Passwort = Access Code.
    #   2. Ueber den bereits bestehenden MQTT-Kanal ein "project_file"-
    #      Kommando senden, das auf die hochgeladene Datei zeigt.
    #
    # Quelle/Recherche (siehe UEBERGABE.md Abschnitt 6, Punkt 1 - keine
    # geratenen Endpunkte): community-dokumentiertes MQTT-Kommando
    # "project_file" (Bambu-Forum "MQTT for A1"), FTPS Port 990 mit
    # implizitem TLS (OpenBambuAPI-Projekt, diverse Community-Tools).
    #
    # WICHTIGE EINSCHRAENKUNG: Es werden bewusst nur bereits fertig
    # gesclicte .gcode.3mf-Dateien (Export aus Bambu Studio/OrcaSlicer)
    # unterstuetzt. Rohe .gcode-Dateien lassen sich laut mehreren
    # Community-Quellen nicht zuverlaessig per MQTT starten.
    #
    # WICHTIGE VORAUSSETZUNG: Auf dem Drucker muss "Developer Mode" /
    # LAN-Modus aktiviert sein (Bambu Handy App -> Drucker -> Einstellungen
    # -> "Developer Mode" bzw. "LAN Only Mode"). Ohne das lehnt neuere
    # Firmware den project_file-Befehl ab. Siehe README, Abschnitt "Bambu
    # Lab: Druckauftrag per Drag & Drop senden".
    #
    # AUTOMATISCHE AMS-ZUORDNUNG (siehe UEBERGABE.md Abschnitt 7 fuer
    # Quellenlage): Die .gcode.3mf-Datei enthaelt in
    # "Metadata/project_settings.config" (JSON) die Arrays
    # "filament_colour" und "filament_type" (0-basiert, ein Eintrag pro
    # im Slicer verwendetem Filament). Diese werden gegen die *aktuell*
    # vom Drucker gemeldeten AMS-Faecher (self.status["ams"], live per
    # MQTT-Report) nach Farbe+Typ gematcht und dem Nutzer als Vorschlag
    # angezeigt (siehe preview_print), BEVOR tatsaechlich gedruckt wird -
    # der Nutzer kann jede Zuordnung im Browser wie in Bambu Studio von
    # Hand korrigieren, bevor send_print() sie final an den Drucker
    # sendet. Es wird also nirgends mehr "blind" gedruckt.
    # ------------------------------------------------------------------
    def preview_print(self, local_path: str):
        """Liest Filament-Infos aus der .gcode.3mf und schlaegt eine
        AMS-Zuordnung anhand der aktuell bekannten AMS-Faecher vor. Wirft
        keine Exception bei nicht auswertbarer Datei - liefert dann leere
        Listen, das Frontend zeigt dann "keine AMS-Zuordnung moeglich"."""
        filaments = _parse_3mf_filaments(local_path)
        ams_trays_raw = list(self.status.get("ams") or [])
        ams_trays = [{
            "flat_index": _slot_to_flat_index(t.get("slot")),
            "slot": t.get("slot"),
            "type": t.get("type"),
            "color": _normalize_hex(t.get("color")) or "666666",
            "remain": t.get("remain"),
        } for t in ams_trays_raw]

        used_slots = set()
        suggestion = []
        for fil in filaments:
            best = _find_matching_tray(fil, ams_trays_raw, used_slots)
            if best is None:
                suggestion.append(-1)
            else:
                used_slots.add(best["slot"])
                suggestion.append(_slot_to_flat_index(best["slot"]))

        return {
            "filaments": [
                {"index": f["index"], "color": f["color"] or "666666", "type": f["type"],
                 "suggested_tray": suggestion[i]}
                for i, f in enumerate(filaments)
            ],
            "ams_trays": ams_trays,
        }

    def send_print(self, local_path: str, remote_name: str, mapping, on_progress=None):
        """Laedt local_path per FTPS auf den Drucker hoch und startet den
        Druck mit der vom Nutzer (im Browser) bestaetigten/korrigierten
        AMS-Zuordnung. mapping ist eine Liste von AMS-Fach-Indizes (-1 =
        kein AMS / externe Spule fuer dieses Filament) oder None/leer,
        wenn ganz ohne AMS gedruckt werden soll. on_progress(sent, total)
        wird waehrend des Uploads wiederholt aufgerufen (fuer die
        Fortschrittsanzeige im Browser). Wirft eine Exception mit
        Klartext-Fehlermeldung bei Problemen."""
        use_ams = bool(mapping) and any(m is not None and int(m) >= 0 for m in mapping)
        ams_summary = {
            "use_ams": use_ams,
            "mapping": [int(m) for m in mapping] if mapping else None,
            "total": len(mapping) if mapping else 0,
            "matched": sum(1 for m in (mapping or []) if m is not None and int(m) >= 0),
        }
        # WICHTIG (v1.5.2): MQTT-Verbindung waehrend des Uploads bewusst
        # kurz trennen - siehe ausfuehrliche Begruendung bei
        # pause_mqtt(). Bei einem Fehlschlag wird die Verbindung zwar
        # ebenfalls wieder freigegeben, aber NICHT blockierend auf den
        # Reconnect gewartet (der Fehler soll dem Nutzer sofort
        # angezeigt werden, nicht erst nach bis zu 15s Wartezeit) - das
        # Reconnect passiert dann einfach im Hintergrund. Nur bei Erfolg
        # wird gewartet, weil der nachfolgende MQTT-Druckbefehl
        # (_request_print) eine aktive Verbindung braucht.
        self.pause_mqtt()
        try:
            self._ftps_upload(local_path, remote_name, on_progress=on_progress)
        except Exception:
            self.resume_mqtt()
            raise
        self.resume_mqtt()
        self.wait_for_mqtt_reconnect(timeout=15)
        self._request_print(remote_name, ams_summary)
        return ams_summary

    def _ftps_upload(self, local_path: str, remote_name: str, on_progress=None):
        # Bis zu 3 Versuche mit jeweils komplett neuer Verbindung, statt
        # beim ersten TLS-Ausrutscher aufzugeben - defensive Robustheit
        # (analog zu Punkt 2 in UEBERGABE.md, "defensives Parsen als
        # Fallback"). Alle 3 Versuche werden gesammelt und am Ende
        # gemeinsam gezeigt, nicht nur der letzte - das war entscheidend
        # fuer die Diagnose in v1.4.1-v1.4.3 (siehe UEBERGABE.md,
        # Chronologie) und bleibt aus Diagnosegruenden bestehen.
        attempts = []
        for attempt in range(1, 4):
            try:
                self._ftps_upload_once(local_path, remote_name, on_progress=on_progress)
                return
            except (OSError, RuntimeError, subprocess.SubprocessError) as e:
                attempts.append(f"Versuch {attempt}: {e}")
                if attempt < 3:
                    time.sleep(1.5 * attempt)
        attempts_text = "\n".join(attempts)
        raise RuntimeError(
            f"FTPS-Upload nach 3 Versuchen fehlgeschlagen:\n{attempts_text}\n"
            f"Falls das auf eine PASV-Datenverbindung hinweist: bitte pruefen, "
            f"ob Dashboard-Rechner und Drucker im selben Netzwerksegment ohne "
            f"Client-/AP-Isolation und ohne dazwischenliegende Firewall/VPN/"
            f"Docker-NAT sind. Ansonsten: Developer Mode am Drucker pruefen "
            f"und ob am Display selbst ein Fehler angezeigt wird."
        )

    def _ftps_upload_once(self, local_path: str, remote_name: str, on_progress=None):
        # ------------------------------------------------------------
        # WICHTIG (v1.5.3) - Der Upload laeuft ueber eine SEPARATE,
        # eigenstaendig mitgelieferte Helfer-exe (FtpsUploadHelper),
        # NICHT mehr per Selbstaufruf der Haupt-exe mit einem
        # versteckten Sentinel-Flag (wie in v1.5.1/v1.5.2).
        #
        # Vorgeschichte: Trotz Prozess-Isolation (v1.5.1) und MQTT-Pause
        # waehrend des Uploads (v1.5.2) scheiterte der Upload im
        # Dashboard weiterhin reproduzierbar - selbst auf einem
        # komplett isolierten Testnetzwerk mit nur Windows Defender,
        # mit identischer Datei, bei der ein eigenstaendiges Diagnose-
        # Tool (ftps_test_minimal.py, praktisch identische FTPS-Logik)
        # im direkten Vergleich zuverlaessig funktionierte. Der einzige
        # verbleibende strukturelle Unterschied: das Dashboard rief
        # sich SELBST mit einem versteckten Kommandozeilen-Argument neu
        # auf ("--ftps-upload-worker"). Ein Programm, das eine Kopie
        # von sich selbst mit einem versteckten Flag startet, ist ein
        # Verhaltensmuster, das Sicherheitssoftware (u. a. Windows
        # Defender) aehnlich wie manche Schadsoftware-Lademechanismen
        # behandeln kann - mit moeglichen Auswirkungen auf dessen
        # Netzwerkverkehr, auch ohne dass etwas sichtbar blockiert wird.
        #
        # Loesung: eine separate, eindeutig benannte Helfer-exe
        # (FtpsUploadHelper.exe, aus ftps_upload_helper.py gebaut, liegt
        # neben der Haupt-exe) wird stattdessen aufgerufen. "Programm A
        # startet Programm B" ist ein voellig normaler, unauffaelliger
        # Vorgang fuer Sicherheitssoftware. Der Selbstaufruf-Mechanismus
        # (_run_ftps_upload_worker() weiter unten) bleibt als Fallback
        # bestehen, falls die Helfer-exe (noch) nicht gefunden wird
        # (z. B. im Entwicklungsbetrieb ohne vorherigen Build).
        # ------------------------------------------------------------
        helper_path = _find_ftps_upload_helper()
        if helper_path:
            cmd = [helper_path, self.cfg["ip"], self.cfg["access_code"], local_path, remote_name]
        else:
            exe = sys.executable
            if getattr(sys, "frozen", False):
                cmd = [exe, "--ftps-upload-worker", self.cfg["ip"], self.cfg["access_code"], local_path, remote_name]
            else:
                cmd = [exe, os.path.abspath(__file__), "--ftps-upload-worker",
                       self.cfg["ip"], self.cfg["access_code"], local_path, remote_name]

        total_size = os.path.getsize(local_path)
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        result = None
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue  # unerwartete Ausgabe ignorieren, kein Grund zum Abbruch
                if msg.get("type") == "progress":
                    if on_progress:
                        try:
                            on_progress(msg.get("sent", 0), msg.get("total", total_size))
                        except Exception:
                            pass
                elif msg.get("type") in ("done", "error"):
                    result = msg
        finally:
            try:
                proc.wait(timeout=30)
            except Exception:
                proc.kill()
                proc.wait()

        if result is None:
            stderr_text = ""
            try:
                stderr_text = (proc.stderr.read() or "").strip()
            except Exception:
                pass
            raise RuntimeError(
                f"Upload-Prozess wurde beendet, ohne ein Ergebnis zu melden "
                f"(Exit-Code {proc.returncode}).{(' ' + stderr_text) if stderr_text else ''}"
            )

        if result.get("type") == "error":
            sent = result.get("sent", 0)
            total = result.get("total", total_size)
            percent = int(sent * 100 / total) if total else 0
            raise RuntimeError(
                f"Verbindung ist waehrend der Dateiuebertragung abgebrochen "
                f"(bei {sent}/{total} Bytes, {percent}%): {result.get('message')}. "
                f"Die Datei ist damit unvollstaendig auf dem Drucker "
                f"gelandet (falls ueberhaupt)."
            )

        if on_progress:
            on_progress(total_size, total_size)

    def _request_print(self, remote_name: str, ams_summary: dict = None):
        if not self._client or not self.status.get("connected"):
            raise RuntimeError(
                "Keine aktive MQTT-Verbindung zum Drucker - Druckauftrag "
                "kann nicht gestartet werden."
            )
        ams_summary = ams_summary or {}
        # WICHTIG (v1.5.6): Der Befehl enthielt bisher nur eine Teilmenge
        # der von Bambu Studio selbst gesendeten Felder. Recherche nach
        # einem gemeldeten Problem ("Druck startet, haengt dann aber beim
        # Materialladen - nur bei bestimmten Materialien wie ASA-CF, PLA
        # unbetroffen") ergab per Vergleich mit mehreren unabhaengigen,
        # dokumentierten Referenz-Payloads (Cinder's Blog "Bambu AMS
        # Filament Mapping", als "funktioniert zuverlaessig" bestaetigt;
        # OpenBambuAPI-Projekt, Doridian/OpenBambuAPI auf GitHub): es
        # fehlten mehrere Felder, allen voran `bed_type` - ohne dieses
        # Feld ist unklar, welchen Druckbett-Typ die Firmware annimmt,
        # was insbesondere bei anspruchsvolleren Materialien (hohe
        # Bett-/Duesentemperatur, wie ASA-CF) zu Problemen im weiteren
        # Startablauf fuehren kann. Ergaenzt um den vollstaendigen,
        # dokumentierten Feldsatz - alle zusaetzlichen Felder sind laut
        # beiden Quellen fuer lokale (nicht Cloud-)Drucke unkritisch mit
        # "0" bzw. "auto" zu befuellen.
        job_name = os.path.splitext(os.path.splitext(remote_name)[0])[0] or remote_name
        payload = {
            "print": {
                "sequence_id": "0",
                "command": "project_file",
                "param": "Metadata/plate_1.gcode",
                "url": f"file:///sdcard/{remote_name}",
                "bed_type": "auto",
                "project_id": "0",
                "profile_id": "0",
                "task_id": "0",
                "subtask_id": "0",
                "subtask_name": job_name,
                "use_ams": bool(ams_summary.get("use_ams")),
                "timelapse": False,
                "flow_cali": False,
                "bed_leveling": True,
                "layer_inspect": True,
                "vibration_cali": True,
            }
        }
        if ams_summary.get("mapping") is not None:
            payload["print"]["ams_mapping"] = ams_summary["mapping"]
        req_topic = f"device/{self.cfg['serial']}/request"
        result = self._client.publish(req_topic, json.dumps(payload))
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError("MQTT-Befehl zum Druckstart konnte nicht gesendet werden.")


def _parse_3mf_filaments(local_path: str):
    """Liest Filamentfarbe/-typ aus einer .gcode.3mf (ZIP-Container) und
    schraenkt sie auf die fuer DIESEN Druck (Plate 1 - das Dashboard
    druckt immer "Metadata/plate_1.gcode", siehe _request_print)
    tatsaechlich benoetigten Filamente ein.

    Quelle/Struktur (siehe UEBERGABE.md fuer Details, keine geratenen
    Felder): "Metadata/project_settings.config" (JSON) enthaelt die
    Arrays "filament_colour"/"filament_type" fuer ALLE im Projekt
    konfigurierten Filamente (0-basiert, parallel) - das koennen mehr
    sein, als auf einer einzelnen Platte tatsaechlich verwendet werden.
    "Metadata/slice_info.config" (XML) enthaelt pro Plate die WIRKLICH
    verbrauchten Filamente als <filament id="1" .../>-Elemente
    (1-basiert!). Nur diese Schnittmenge wird angezeigt/zugeordnet.

    Rein defensiv: Ist project_settings.config nicht auswertbar, wird
    eine leere Liste zurueckgegeben. Ist slice_info.config nicht
    vorhanden/auswertbar oder liefert keine Treffer fuer Plate 1, wird
    NICHT geraten, sondern auf die vollstaendige Filamentliste aus
    project_settings.config zurueckgefallen (sicherer, nur etwas
    weniger praezise als die Plate-gefilterte Liste)."""
    try:
        with zipfile.ZipFile(local_path) as zf:
            with zf.open("Metadata/project_settings.config") as f:
                cfg = json.load(f)
    except Exception:
        return []

    colours = cfg.get("filament_colour")
    types = cfg.get("filament_type")
    if not isinstance(colours, list) or not colours:
        return []
    if not isinstance(types, list):
        types = []

    all_filaments = []
    for i, colour in enumerate(colours):
        ftype = types[i] if i < len(types) else ""
        all_filaments.append({
            "index": i,
            "color": _normalize_hex(colour),
            "type": (ftype or "").strip().upper(),
        })

    used_indices = _parse_plate1_used_filament_indices(local_path)
    if used_indices:
        filtered = [f for f in all_filaments if f["index"] in used_indices]
        if filtered:
            return filtered
    return all_filaments


def _parse_plate1_used_filament_indices(local_path: str):
    """Liest "Metadata/slice_info.config" (XML) und liefert die Menge
    der auf Plate 1 tatsaechlich verbrauchten Filament-Indizes
    (0-basiert), oder None, wenn die Datei fehlt/nicht auswertbar ist
    bzw. keine Plate mit index=1 gefunden wird - der Aufrufer faellt
    dann bewusst auf die volle Filamentliste zurueck statt zu raten."""
    try:
        with zipfile.ZipFile(local_path) as zf:
            with zf.open("Metadata/slice_info.config") as f:
                root = ET.parse(f).getroot()
    except Exception:
        return None

    for plate in root.findall("plate"):
        index_val = None
        for meta in plate.findall("metadata"):
            if meta.get("key") == "index":
                index_val = meta.get("value")
                break
        if index_val != "1":
            continue
        indices = set()
        for fil in plate.findall("filament"):
            fid = fil.get("id")
            if fid is None:
                continue
            try:
                indices.add(int(fid) - 1)  # 1-basiert (slice_info) -> 0-basiert
            except ValueError:
                continue
        return indices or None

    return None


def _normalize_hex(value):
    if not isinstance(value, str):
        return ""
    v = value.strip().lstrip("#").upper()
    return v[0:6] if len(v) >= 6 else ""


def _slot_to_flat_index(slot: str) -> int:
    """Wandelt den Slot-Bezeichner "<ams_unit>-<tray>" (wie ihn
    _apply_print_report weiter oben erzeugt) in den flachen AMS-Index
    um, den "ams_mapping" erwartet (Community-Konvention: 4 Faecher pro
    AMS-Einheit, siehe UEBERGABE.md Abschnitt 7 - nicht offiziell von
    Bambu dokumentiert)."""
    try:
        unit_str, tray_str = slot.split("-", 1)
        return int(unit_str) * 4 + int(tray_str)
    except Exception:
        return -1


def _types_compatible(want_type: str, tray_type: str) -> bool:
    """Prueft, ob zwei Materialtyp-Bezeichnungen (z. B. "PLA", "PLA
    BASIC", "ASA-CF") als kompatibel gelten duerfen fuer die
    automatische AMS-Zuordnung.

    WICHTIG (Bugfix): Ein reiner Teilstring-Vergleich ("want_type in
    tray_type or tray_type in want_type") hat einen gefaehrlichen
    Nebeneffekt: "ASA" ist ein Teilstring von "ASA-CF" - ein Fach mit
    normalem ASA wuerde damit faelschlich als passend fuer ein
    ASA-CF-Filament gelten (und umgekehrt), obwohl das voellig
    unterschiedliche Materialien mit unterschiedlichen Druck-
    eigenschaften sind (das gilt analog fuer alle Verbundwerkstoff-
    Varianten: PLA-CF, PETG-CF, PA-CF, PPS-CF, ABS-GF usw.). Traf das
    zufaellig auf ein farblich uebereinstimmendes Fach mit dem falschen
    Grundmaterial, wurde es automatisch vorgeschlagen und (wenn vom
    Nutzer uebernommen) an den Drucker gesendet - der RFID-Chip im Fach
    meldet dann ein anderes Material als im Slicer hinterlegt, was am
    Drucker typischerweise eine Bestaetigungs-Abfrage auf dem Display
    ausloest, die ohne jemanden vor Ort wie ein "Haengenbleiben beim
    Materialladen" wirkt.

    Deshalb: der Teil nach einem "-" (Verbundwerkstoff-Suffix wie "CF",
    "GF") muss auf beiden Seiten identisch sein - "ASA" (kein Suffix)
    und "ASA-CF" (Suffix "CF") gelten damit NICHT mehr als kompatibel.
    Der Teil vor dem "-" darf weiterhin locker verglichen werden (z. B.
    "PLA" vs. "PLA BASIC", beide ohne Suffix)."""
    if not want_type or not tray_type:
        return True  # nichts zum Vergleichen - nicht blockieren
    if want_type == tray_type:
        return True
    want_base, _, want_suffix = want_type.partition("-")
    tray_base, _, tray_suffix = tray_type.partition("-")
    if want_suffix != tray_suffix:
        return False  # z. B. "ASA" vs. "ASA-CF" - unterschiedliche Materialien
    return want_base in tray_base or tray_base in want_base


def _find_matching_tray(filament: dict, ams_trays: list, used_slots: set):
    """Sucht das erste noch nicht verwendete AMS-Fach, dessen Farbe exakt
    passt und dessen Typ plausibel uebereinstimmt (z. B. angeforderes
    "PLA" passt zu Fach-Typ "PLA Basic", aber "ASA" passt NICHT zu
    "ASA-CF" - siehe _types_compatible()). Liefert None statt zu raten,
    wenn nichts eindeutig passt."""
    want_color = filament.get("color") or ""
    want_type = filament.get("type") or ""
    for tray in ams_trays:
        slot = tray.get("slot")
        if not slot or slot in used_slots:
            continue
        tray_color = _normalize_hex(tray.get("color"))
        if not want_color or tray_color != want_color:
            continue
        tray_type = (tray.get("type") or "").strip().upper()
        if not _types_compatible(want_type, tray_type):
            continue
        return tray
    return None


def _run_ftps_upload_worker(argv):
    """Wird ausgefuehrt, wenn die exe/das Skript mit dem Sentinel-
    Argument "--ftps-upload-worker" gestartet wird (siehe Abzweig ganz
    oben im Programm, VOR jeglicher Flask-/MQTT-/DashboardApp-
    Initialisierung). Fuehrt AUSSCHLIESSLICH den FTPS-Upload durch -
    keine Drucker-Verbindungen, kein Flask, kein MQTT - und meldet
    Fortschritt/Ergebnis als einzelne JSON-Zeilen auf stdout, dann
    beendet sich der Prozess. Aufgerufen von
    PrinterConnection._ftps_upload_once() im Hauptprozess ueber
    subprocess.Popen() - siehe dortiger Kommentar fuer die Begruendung
    (Ressourcen-/Scheduling-Konkurrenz mit dem MQTT-Thread im
    Hauptprozess, wenn der Upload dort als Thread liefe)."""
    if len(argv) != 4:
        print(json.dumps({"type": "error", "message": "Falsche Anzahl Argumente fuer --ftps-upload-worker"}))
        return
    ip, access_code, local_path, remote_name = argv

    ctx = ssl._create_unverified_context()
    ctx.options |= getattr(ssl, "OP_IGNORE_UNEXPECTED_EOF", 0)
    try:
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    except (AttributeError, ValueError):
        pass

    try:
        total_size = os.path.getsize(local_path)
    except OSError as e:
        print(json.dumps({"type": "error", "message": f"Datei nicht lesbar: {e}", "sent": 0, "total": 0}), flush=True)
        return

    sent = 0
    last_pct = -1

    def _progress_cb(block):
        nonlocal sent, last_pct
        sent += len(block)
        pct = int(sent * 100 / total_size) if total_size else 100
        if pct != last_pct:
            last_pct = pct
            print(json.dumps({"type": "progress", "sent": sent, "total": total_size}), flush=True)

    try:
        ftp = ImplicitFtpTls(context=ctx)
        ftp.connect(ip, 990, timeout=25)
        ftp.login("bblp", access_code)
        ftp.prot_p()
        ftp.set_pasv(True)
        with open(local_path, "rb") as f:
            ftp.storbinary(f"STOR {remote_name}", f, blocksize=8192, callback=_progress_cb)
        try:
            ftp.quit()
        except Exception:
            try:
                ftp.close()
            except Exception:
                pass
        print(json.dumps({"type": "done", "sent": sent, "total": total_size}), flush=True)
    except Exception as e:
        print(json.dumps({"type": "error", "message": str(e), "sent": sent, "total": total_size}), flush=True)


class ImplicitFtpTls(ftplib.FTP_TLS):
    """ftplib.FTP_TLS kann von Haus aus nur explizites TLS (AUTH TLS).
    Bambu-Drucker verlangen auf Port 990 IMPLIZITES TLS (die Verbindung
    ist von Anfang an TLS-verschluesselt, kein AUTH-Kommando). Diese
    Subklasse wrappt den Socket direkt beim Verbindungsaufbau - dafuer
    zwingend noetig.

    WICHTIG (v1.5.4 - KORREKTUR eines eigenen Fehlers): Vorherige
    Versionen (ab v1.4.5) verzichteten hier bewusst auf ein
    `ntransfercmd()`-Override, in der irrigen Annahme, Pythons
    eingebautes `ftplib.FTP_TLS.ntransfercmd()` wuerde die TLS-Sitzung
    der Kontrollverbindung bereits automatisch fuer die Datenverbindung
    wiederverwenden (`session=self.sock.session`). **Das stimmt nicht -**
    per `inspect.getsource(ftplib.FTP_TLS.ntransfercmd)` direkt
    ueberprueft (Python 3.11/3.12): die eingebaute Methode uebergibt nur
    `server_hostname=self.host`, OHNE `session=...`. Es findet also
    OHNE dieses Override GAR KEINE TLS-Session-Wiederverwendung fuer die
    Datenverbindung statt.
    Das ist bedeutsam, weil die X1-Serie (X1C/X1E) intern auf vsftpd mit
    `require_ssl_reuse` laeuft (siehe UEBERGABE.md, v1.5.0-Recherche) -
    diese urspruengliche Diagnose war korrekt, wurde aber in v1.5.0
    falsch umgesetzt/getestet (No-Unwrap OHNE Session-Reuse, siehe
    UEBERGABE.md) und deshalb faelschlich als widerlegt verworfen. Der
    entscheidende Beleg kam erst durch einen direkten Vergleich mit dem
    Diagnose-Testskript des Nutzers, dessen "Test A" GENAU dieses
    Override (session=self.sock.session) enthielt und zuverlaessig
    funktionierte, waehrend diese Klasse ohne das Override reproduzierbar
    bei ca. 11% abbrach. Das Override wird deshalb wieder ergaenzt -
    diesmal ueberprueft gegen den tatsaechlichen Python-Quellcode statt
    gegen eine ungeprüfte Annahme."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sock = None

    @property
    def sock(self):
        return self._sock

    @sock.setter
    def sock(self, value):
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value)
        self._sock = value

    def ntransfercmd(self, cmd, rest=None):
        # Siehe Klassen-Docstring: Pythons Standardverhalten wrappt die
        # Datenverbindung OHNE die TLS-Sitzung der Kontrollverbindung
        # wiederzuverwenden. Fuer FTPS-Server, die (wie vsftpd mit
        # `require_ssl_reuse`, was die X1-Serie nachweislich einsetzt)
        # eine gemeinsame Sitzung zwischen Kontroll- und Datenverbindung
        # verlangen, muss das explizit ergaenzt werden.
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(
                conn, server_hostname=self.host, session=self.sock.session
            )
        return conn, size


# ----------------------------------------------------------------------
# WICHTIG: Sentinel-Abzweig fuer den isolierten FTPS-Upload-Kindprozess.
# Muss VOR jeglicher Flask-/MQTT-/DashboardApp-Initialisierung geprueft
# werden (siehe unten "dash = DashboardApp()") - sonst wuerde der
# Kindprozess unnoetig auch alle Drucker-Verbindungen aufbauen und
# einen zweiten Flask-Server starten. Wird von
# PrinterConnection._ftps_upload_once() ausgeloest, das die exe/das
# Skript mit "--ftps-upload-worker <ip> <access_code> <datei> <ziel>"
# neu aufruft (siehe dortiger Kommentar fuer die Begruendung).
# ----------------------------------------------------------------------
if len(sys.argv) > 1 and sys.argv[1] == "--ftps-upload-worker":
    _run_ftps_upload_worker(sys.argv[2:])
    sys.exit(0)


def _argb_to_css(hexval):
    if not hexval or len(hexval) < 6:
        return "#666666"
    return "#" + hexval[0:6]


# ----------------------------------------------------------------------
# Kamera-Stream (Bambu Lab lokaler Kamera-Feed, Port 6000)
# ----------------------------------------------------------------------
def bambu_mjpeg_generator(ip: str, access_code: str, port: int = 6000):
    ctx = ssl._create_unverified_context()
    raw_sock = socket.create_connection((ip, port), timeout=5)
    sock = ctx.wrap_socket(raw_sock)

    auth = bytearray()
    auth += struct.pack("<I", 0x40)
    auth += struct.pack("<I", 0x3000)
    auth += struct.pack("<I", 0)
    auth += struct.pack("<I", 0)
    user_bytes = b"bblp".ljust(32, b"\x00")
    code_bytes = access_code.encode("ascii").ljust(32, b"\x00")
    auth += user_bytes
    auth += code_bytes
    sock.write(auth)

    boundary = b"--frame"
    try:
        while True:
            header = _recv_exact(sock, 16)
            if header is None:
                break
            img_len = struct.unpack("<I", header[0:4])[0]
            payload_len = struct.unpack("<I", header[4:8])[0]
            if img_len == 0 or img_len > 5_000_000:
                break
            jpeg = _recv_exact(sock, img_len)
            if jpeg is None:
                break
            if payload_len > img_len:
                _recv_exact(sock, payload_len - img_len)
            yield (boundary + b"\r\n"
                   b"Content-Type: image/jpeg\r\n"
                   b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n" +
                   jpeg + b"\r\n")
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except Exception:
            return None
        if not chunk:
            return None
        buf += chunk
    return buf


# ----------------------------------------------------------------------
# Formlabs (Drucker, Wash L, Cure L) ueber die offizielle Local API
# ----------------------------------------------------------------------
def _walk_json(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
            yield from _walk_json(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_json(item)


def _find_first(obj, candidate_keys):
    lowered = {c.lower() for c in candidate_keys}
    for k, v in _walk_json(obj):
        if isinstance(k, str) and k.lower() in lowered and v not in (None, ""):
            return v
    return None


FL_PROGRESS_KEYS = ["progress_percentage", "percent_complete", "progress",
                     "print_progress", "completion_percentage", "percentage"]
FL_FILE_KEYS = ["job_name", "print_file_name", "current_job_name",
                "file_name", "job", "print_name", "current_job"]
FL_MATERIAL_KEYS = ["material_name", "material", "cartridge_material",
                     "resin_type", "material_code", "tank_material"]
FL_STATE_KEYS = ["status", "state", "print_status", "device_status", "machine_state"]


class FormlabsLocalApiConnection:
    """Fragt den Status eines Formlabs-Geraets (Drucker, Form Wash L oder
    Form Cure L) ueber die offizielle "Formlabs Local API" ab.

    WICHTIG - Voraussetzung: Formlabs bietet KEINE direkt auf dem Geraet
    unter seiner eigenen IP erreichbare Status-API an. Die einzige von
    Formlabs offiziell dokumentierte Moeglichkeit, lokal per IP an
    Geraetestatus zu kommen, ist die "Formlabs Local API": dafuer muss
    zusaetzlich auf einem PC im selben Netzwerk (typischerweise dem PC,
    auf dem auch dieses Dashboard laeuft) das kostenlose Programm
    "PreFormServer" (Teil der normalen PreForm-Installation) im
    Hintergrund laufen, z. B. gestartet mit:

        PreFormServer.exe --port 44388

    Dieses Dashboard verbindet sich dann zu http://localhost:44388
    (konfigurierbar ueber "preform_server" in config.json) und fragt dort
    den Status des Geraets mit der hinterlegten IP-Adresse ab. Laeuft kein
    PreFormServer, bleibt die Karte zwangslaeufig ohne Daten - das ist eine
    Einschraenkung von Formlabs selbst, nicht dieses Programms.

    Die Original-Geraete "Form Wash" und "Form Cure" (ohne "L") haben laut
    Formlabs KEINE Netzwerkfunktion und koennen technisch nicht
    eingebunden werden - nur die "L"-Varianten (Form Wash L / Form Cure L).

    Das genaue JSON-Format der Geraeteantwort kann sich je nach
    Geraetetyp/Firmware unterscheiden. Diese Klasse durchsucht die Antwort
    daher defensiv nach den wichtigsten Feldern (siehe FL_*_KEYS oben),
    statt starre Schluessel vorauszusetzen.
    """

    POLL_INTERVAL_SEC = 5

    def __init__(self, printer_cfg: dict, preform_server_url: str):
        self.cfg = printer_cfg
        self.id = printer_cfg["id"]
        self.preform_url = (preform_server_url or DEFAULT_CONFIG["preform_server"]).rstrip("/")
        self.status = {
            "connected": False,
            "last_update": None,
            "progress": 0,
            "file_name": "-",
            "material": "-",
            "device_status": "UNKNOWN",
            "error": None
        }
        self._stop = False
        self._device_id = None

    def start(self):
        self._stop = False
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def stop(self):
        self._stop = True

    def _poll_loop(self):
        self._discover()
        while not self._stop:
            try:
                self._refresh()
            except urllib.error.URLError:
                self.status["connected"] = False
                self.status["error"] = (
                    f"PreFormServer unter {self.preform_url} nicht erreichbar. "
                    f"Laeuft PreFormServer.exe im Hintergrund?"
                )
            except Exception as e:
                self.status["connected"] = False
                self.status["error"] = str(e)
            time.sleep(self.POLL_INTERVAL_SEC)

    def _http_json(self, method, path, payload=None):
        url = f"{self.preform_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                      headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            raw = resp.read()
        return json.loads(raw.decode("utf-8", errors="ignore")) if raw else {}

    def _discover(self):
        try:
            self._http_json("POST", "/discover-devices/",
                             {"ip_address": self.cfg["ip"], "timeout_seconds": 8})
        except Exception:
            pass

    def _refresh(self):
        data = self._http_json("GET", "/devices/")
        devices = data.get("devices", [])
        match = next((d for d in devices if d.get("ip_address") == self.cfg["ip"]), None)

        if not match:
            self.status["connected"] = False
            self.status["error"] = (
                "Geraet mit dieser IP wurde vom PreFormServer (noch) nicht gefunden. "
                "Pruefen: PreFormServer laeuft, Geraet ist eingeschaltet und im "
                "gleichen Netzwerk erreichbar."
            )
            return

        self.status["error"] = None
        self._device_id = match.get("id")
        detail = match
        if self._device_id:
            try:
                detail = self._http_json("GET", f"/devices/{self._device_id}/")
            except Exception:
                pass

        self.status["connected"] = bool(match.get("is_connected", True))

        state = _find_first(detail, FL_STATE_KEYS)
        if state:
            self.status["device_status"] = str(state).upper()

        progress_raw = _find_first(detail, FL_PROGRESS_KEYS)
        if progress_raw is not None:
            try:
                pct = float(progress_raw)
                if pct <= 1:
                    pct *= 100
                self.status["progress"] = round(pct, 1)
            except (TypeError, ValueError):
                pass

        file_name = _find_first(detail, FL_FILE_KEYS)
        if file_name:
            self.status["file_name"] = str(file_name)

        if self.cfg.get("type") == "formlabs":
            material = _find_first(detail, FL_MATERIAL_KEYS)
            if material:
                self.status["material"] = str(material)

        self.status["last_update"] = datetime.now().strftime("%H:%M:%S")


# ----------------------------------------------------------------------
# OctoPrint (offiziell dokumentierte REST-API, https://docs.octoprint.org)
# ----------------------------------------------------------------------
class OctoPrintConnection:
    """Bindet einen 3D-Drucker mit angeschlossenem OctoPrint (z. B. an
    einem Raspberry Pi) genauso ein wie einen Bambu Lab Drucker: gleiche
    Kartenansicht mit Fortschritt, Dateiname, Temperaturen und Kamera.

    Benoetigt einen OctoPrint API-Key (OctoPrint -> Einstellungen ->
    API -> "API Key").

    Hinweis: OctoPrint liefert i. d. R. keine Kammertemperatur (nur
    Duesen- und Betttemperatur) und kein AMS-Aequivalent - diese Felder
    bleiben daher bei OctoPrint-Druckern leer, das ist kein Fehler.
    """

    POLL_INTERVAL_SEC = 3

    def __init__(self, printer_cfg: dict):
        self.cfg = printer_cfg
        self.id = printer_cfg["id"]
        self.status = {
            "connected": False,
            "last_update": None,
            "gcode_state": "UNKNOWN",
            "progress": 0,
            "file_name": "-",
            "chamber_temp": None,
            "nozzle_temp": None,
            "bed_temp": None,
            "remaining_min": None,
            "ams": [],
            "error": None
        }
        self._stop = False

    def start(self):
        self._stop = False
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def stop(self):
        self._stop = True

    def _poll_loop(self):
        while not self._stop:
            try:
                self._refresh()
            except urllib.error.URLError:
                self.status["connected"] = False
                self.status["error"] = "OctoPrint nicht erreichbar (IP/Port pruefen)."
            except urllib.error.HTTPError as e:
                self.status["connected"] = False
                if e.code == 403:
                    self.status["error"] = "OctoPrint hat den API-Key abgelehnt (403)."
                else:
                    self.status["error"] = f"OctoPrint HTTP-Fehler {e.code}."
            except Exception as e:
                self.status["connected"] = False
                self.status["error"] = str(e)
            time.sleep(self.POLL_INTERVAL_SEC)

    def _base_url(self):
        scheme = "https" if self.cfg.get("https") else "http"
        port = int(self.cfg.get("port", 80))
        return f"{scheme}://{self.cfg['ip']}:{port}"

    def _get(self, path):
        url = self._base_url() + path
        req = urllib.request.Request(url, headers={"X-Api-Key": self.cfg.get("api_key", "")})
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read()
        return json.loads(raw.decode("utf-8", errors="ignore")) if raw else {}

    def _refresh(self):
        printer = self._get("/api/printer")
        job = self._get("/api/job")

        self.status["connected"] = True
        self.status["error"] = None

        state_text = (printer.get("state") or {}).get("text", "UNKNOWN")
        self.status["gcode_state"] = str(state_text).upper()

        temps = printer.get("temperature") or {}
        tool0 = temps.get("tool0") or {}
        bed = temps.get("bed") or {}
        if tool0.get("actual") is not None:
            self.status["nozzle_temp"] = tool0["actual"]
        if bed.get("actual") is not None:
            self.status["bed_temp"] = bed["actual"]

        job_info = job.get("job") or {}
        file_info = job_info.get("file") or {}
        if file_info.get("name"):
            self.status["file_name"] = file_info["name"]

        progress = job.get("progress") or {}
        completion = progress.get("completion")
        if completion is not None:
            self.status["progress"] = round(completion, 1)

        remaining = progress.get("printTimeLeft")
        if remaining is not None:
            self.status["remaining_min"] = round(remaining / 60)

        self.status["last_update"] = datetime.now().strftime("%H:%M:%S")


# ----------------------------------------------------------------------
# Creality (Klipper-basierte Modelle: K1 / K1C / K1 Max / K1 SE sowie
# jeder andere Klipper-faehige Creality-Drucker), ueber die offizielle
# Moonraker-API (https://moonraker.readthedocs.io)
# ----------------------------------------------------------------------
class CrealityConnection:
    """Bindet einen Creality-Drucker mit Klipper-Firmware ueber die
    offizielle Moonraker-API ein (das Backend hinter Fluidd/Mainsail).

    Die Moonraker-API selbst ist bei allen Klipper-faehigen Creality-
    Druckern identisch (K1, K1C, K1 Max, K1 SE, oder ein Ender/CR-Drucker
    mit Klipper-Umbau z. B. per Sonic Pad) - das in config.json hinterlegte
    "type"-Feld (creality_k1 / creality_k1c / creality_k1max /
    creality_k1se / creality_other) dient daher NUR der Beschriftung auf
    der Karte, nicht einer unterschiedlichen technischen Anbindung.

    WICHTIGE Voraussetzung: Auf den werkseitigen K1/K1C/K1 Max/K1 SE ist
    Moonraker NICHT vorinstalliert. Der Drucker muss zuerst per SSH
    "gerootet" und Moonraker manuell nachinstalliert werden (z. B. ueber
    das verbreitete Creality-Helper-Script) - siehe README fuer Details.
    Ist Moonraker nicht erreichbar, bleibt die Karte ohne Daten und zeigt
    einen entsprechenden Hinweis an.

    Neuere Modelle mit reinem "Creality OS" ohne Klipper (z. B. Ender-3 V3
    SE) werden bewusst NICHT unterstuetzt, da es dafuer keine offiziell
    dokumentierte lokale Status-API gibt - eine Anbindung waere reines
    Rätselraten wie zuvor beim ersten (fehlgeschlagenen) Formlabs-Versuch.
    """

    POLL_INTERVAL_SEC = 3

    def __init__(self, printer_cfg: dict):
        self.cfg = printer_cfg
        self.id = printer_cfg["id"]
        self.status = {
            "connected": False,
            "last_update": None,
            "gcode_state": "UNKNOWN",
            "progress": 0,
            "file_name": "-",
            "chamber_temp": None,
            "nozzle_temp": None,
            "bed_temp": None,
            "remaining_min": None,
            "ams": [],
            "error": None
        }
        self._stop = False
        self._chamber_object_name = None   # per Discovery ermittelt, falls vorhanden

    def start(self):
        self._stop = False
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def stop(self):
        self._stop = True

    def _poll_loop(self):
        self._discover_objects()
        while not self._stop:
            try:
                self._refresh()
            except urllib.error.URLError:
                self.status["connected"] = False
                self.status["error"] = "Moonraker nicht erreichbar (IP/Port pruefen, laeuft Moonraker auf dem Drucker?)."
            except urllib.error.HTTPError as e:
                self.status["connected"] = False
                self.status["error"] = f"Moonraker HTTP-Fehler {e.code}."
            except Exception as e:
                self.status["connected"] = False
                self.status["error"] = str(e)
            time.sleep(self.POLL_INTERVAL_SEC)

    def _base_url(self):
        return f"http://{self.cfg['ip']}:{int(self.cfg.get('port', 7125))}"

    def _get(self, path):
        url = self._base_url() + path
        headers = {}
        if self.cfg.get("api_key"):
            headers["X-Api-Key"] = self.cfg["api_key"]
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read()
        return json.loads(raw.decode("utf-8", errors="ignore")) if raw else {}

    def _discover_objects(self):
        """Fragt einmalig ab, welche Klipper-Objekte auf diesem Drucker
        ueberhaupt existieren, um z. B. einen optionalen Kammer-
        Temperatursensor (falls per Klipper-Config vorhanden, z. B. beim
        K1 Max) nur dann mit abzufragen, wenn er wirklich da ist - fehlt
        er und wird trotzdem abgefragt, lehnt Moonraker die ganze Anfrage ab."""
        try:
            data = self._get("/printer/objects/list")
            objects = data.get("result", {}).get("objects", [])
            chamber = next(
                (o for o in objects if o.lower().startswith("temperature_sensor") and "chamber" in o.lower()),
                None
            )
            self._chamber_object_name = chamber
        except Exception:
            self._chamber_object_name = None

    def _refresh(self):
        objs = ["print_stats", "display_status", "extruder", "heater_bed"]
        if self._chamber_object_name:
            objs.append(self._chamber_object_name)
        query = "&".join(urllib.parse.quote(o) for o in objs)

        data = self._get(f"/printer/objects/query?{query}")
        status = (data.get("result") or {}).get("status", {})

        self.status["connected"] = True
        self.status["error"] = None

        ps = status.get("print_stats") or {}
        if ps.get("state"):
            self.status["gcode_state"] = str(ps["state"]).upper()
        if ps.get("filename"):
            self.status["file_name"] = ps["filename"]

        ds = status.get("display_status") or {}
        progress = ds.get("progress")
        if progress is not None:
            self.status["progress"] = round(float(progress) * 100, 1)

        extruder = status.get("extruder") or {}
        if extruder.get("temperature") is not None:
            self.status["nozzle_temp"] = round(extruder["temperature"], 1)

        bed = status.get("heater_bed") or {}
        if bed.get("temperature") is not None:
            self.status["bed_temp"] = round(bed["temperature"], 1)

        if self._chamber_object_name and self._chamber_object_name in status:
            chamber_obj = status[self._chamber_object_name] or {}
            if chamber_obj.get("temperature") is not None:
                self.status["chamber_temp"] = round(chamber_obj["temperature"], 1)

        self.status["last_update"] = datetime.now().strftime("%H:%M:%S")


# ----------------------------------------------------------------------
# Ultimaker (S-Serie, UM3) ueber die offizielle lokale Drucker-API
# ----------------------------------------------------------------------
class UltimakerConnection:
    """Bindet einen netzwerkfaehigen Ultimaker-Drucker (UM3, S3, S5, S7,
    Factor 4, ...) ueber dessen offizielle, direkt auf dem Drucker
    laufende lokale REST-API ein: http://<Drucker-IP>/api/v1/ (Swagger-
    Dokumentation dazu ist direkt am Drucker unter
    http://<Drucker-IP>/docs/api/ abrufbar).

    Diese API ist fuer reine Status-Abfragen OHNE Authentifizierung
    nutzbar - ein Login/API-Key wird laut Ultimaker-Dokumentation nur
    fuer schreibende Aktionen (z. B. Druckauftrag starten) benoetigt.
    Dieses Dashboard fragt ausschliesslich lesend ab, es ist daher kein
    API-Key noetig.

    Verwendete Endpunkte:
      GET /api/v1/printer/status                                -> Status-Text (z. B. "printing", "idle")
      GET /api/v1/print_job                                     -> aktueller Druckauftrag (404 wenn keiner laeuft)
      GET /api/v1/printer/bed/temperature                       -> {"current":.., "target":..}
      GET /api/v1/printer/heads/0/extruders/0/hotend/temperature -> {"current":.., "target":..}

    Hinweis: Ultimaker-Desktopdrucker (UM3/S-Serie) haben keinen
    Kammertemperatursensor - dieses Feld bleibt daher immer leer, das
    ist normal und kein Fehler.
    """

    POLL_INTERVAL_SEC = 3

    def __init__(self, printer_cfg: dict):
        self.cfg = printer_cfg
        self.id = printer_cfg["id"]
        self.status = {
            "connected": False,
            "last_update": None,
            "gcode_state": "UNKNOWN",
            "progress": 0,
            "file_name": "-",
            "chamber_temp": None,
            "nozzle_temp": None,
            "bed_temp": None,
            "remaining_min": None,
            "error": None
        }
        self._stop = False

    def start(self):
        self._stop = False
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def stop(self):
        self._stop = True

    def _poll_loop(self):
        while not self._stop:
            try:
                self._refresh()
            except urllib.error.URLError:
                self.status["connected"] = False
                self.status["error"] = "Drucker nicht erreichbar (IP/Port pruefen)."
            except Exception as e:
                self.status["connected"] = False
                self.status["error"] = str(e)
            time.sleep(self.POLL_INTERVAL_SEC)

    def _base_url(self):
        return f"http://{self.cfg['ip']}:{int(self.cfg.get('port', 80))}"

    def _get(self, path):
        url = self._base_url() + path
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read()
        return json.loads(raw.decode("utf-8", errors="ignore")) if raw else None

    def _refresh(self):
        state = self._get("/api/v1/printer/status")
        self.status["connected"] = True
        self.status["error"] = None
        if isinstance(state, str) and state:
            self.status["gcode_state"] = state.upper()

        # Kein aktiver Druckauftrag -> Ultimaker antwortet hier mit 404,
        # das ist der Normalfall im Leerlauf, keine Fehlermeldung wert.
        try:
            job = self._get("/api/v1/print_job")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                job = None
            else:
                raise

        if job:
            if job.get("name"):
                self.status["file_name"] = job["name"]
            progress = job.get("progress")
            if progress is not None:
                self.status["progress"] = round(float(progress) * 100, 1)
            elapsed = job.get("time_elapsed")
            total = job.get("time_total")
            if elapsed is not None and total is not None and total > elapsed:
                self.status["remaining_min"] = round((total - elapsed) / 60)
        else:
            self.status["file_name"] = "-"
            self.status["progress"] = 0
            self.status["remaining_min"] = None

        try:
            bed = self._get("/api/v1/printer/bed/temperature")
            if bed and bed.get("current") is not None:
                self.status["bed_temp"] = round(bed["current"], 1)
        except Exception:
            pass

        try:
            hotend = self._get("/api/v1/printer/heads/0/extruders/0/hotend/temperature")
            if hotend and hotend.get("current") is not None:
                self.status["nozzle_temp"] = round(hotend["current"], 1)
        except Exception:
            pass

        self.status["last_update"] = datetime.now().strftime("%H:%M:%S")


# ----------------------------------------------------------------------
# Zweiter, unabhaengiger MQTT-Broker fuer frei definierbare Sensoren
# und Schaltflaechen, die einer Drucker-Karte angehaengt werden
# ----------------------------------------------------------------------
class ExtrasMqttManager:
    """Verbindet sich (falls in config.json unter "extras_mqtt" aktiviert)
    zu einem ZWEITEN, von den Druckern unabhaengigen MQTT-Broker (z. B.
    einem Heimautomatisierungs-Broker wie Mosquitto/Home Assistant) und
    haelt die zuletzt empfangenen Werte aller relevanten Topics vor.

    Sensoren: ein an einem Drucker hinterlegter "extras"-Eintrag mit
    kind="sensor" abonniert "topic" und zeigt den zuletzt empfangenen
    Rohwert (als Text) auf der Drucker-Karte an.

    Schalter: ein Eintrag mit kind="switch" zeigt zwei Buttons ("Ein"/
    "Aus") auf der Karte. Ein Klick veroeffentlicht den konfigurierten
    payload_on/payload_off auf "command_topic" - es wird kein Zustand
    vom Broker zurueckgelesen (einfache "Fire-and-forget"-Schaltflaeche,
    wie angefragt).
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg.get("extras_mqtt") or {}
        self.values = {}
        self._client = None
        self._stop = False

    def start(self):
        if not self.cfg.get("enabled") or not self.cfg.get("host"):
            return
        self._stop = False
        threading.Thread(target=self._connect_loop, daemon=True).start()

    def stop(self):
        self._stop = True
        try:
            if self._client:
                self._client.disconnect()
        except Exception:
            pass

    def _connect_loop(self):
        client_id = f"dashboard-extras-{uuid.uuid4().hex[:6]}"
        self._client = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)
        if self.cfg.get("username"):
            self._client.username_pw_set(self.cfg.get("username"), self.cfg.get("password", ""))
        if self.cfg.get("tls"):
            self._client.tls_set_context(ssl._create_unverified_context())
            self._client.tls_insecure_set(True)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.reconnect_delay_set(min_delay=1, max_delay=15)

        while not self._stop:
            try:
                self._client.connect(self.cfg["host"], int(self.cfg.get("port", 1883)), keepalive=30)
                self._client.loop_forever(retry_first_connection=True)
            except Exception:
                time.sleep(5)
            if self._stop:
                break
            time.sleep(3)

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            client.subscribe("#")

    def _on_message(self, client, userdata, msg):
        try:
            self.values[msg.topic] = msg.payload.decode("utf-8", errors="ignore")
        except Exception:
            pass

    def get_value(self, topic):
        if not topic:
            return None
        return self.values.get(topic)

    def publish(self, topic, payload):
        if not self._client:
            return False
        try:
            self._client.publish(topic, payload)
            return True
        except Exception:
            return False


# ----------------------------------------------------------------------
# Zentrale Verwaltung aller Drucker-Verbindungen
# ----------------------------------------------------------------------
class DashboardApp:
    PRINT_JOB_MAX_AGE_SEC = 20 * 60  # Aufraeumen liegen gebliebener Temp-Dateien

    def __init__(self):
        self.cfg = load_config()
        self.connections = {}
        self.extras = ExtrasMqttManager(self.cfg)
        self.extras.start()
        for p in self.cfg["printers"]:
            self._start_printer(p)
        self._print_jobs = {}          # job_id -> {local_path, remote_name, printer_id, created}
        self._print_jobs_lock = threading.Lock()
        self._print_progress = {}      # job_id -> {phase, sent, total, percent, error, ams}
        self._print_progress_lock = threading.Lock()

    def _start_printer(self, printer_cfg: dict):
        ptype = printer_cfg.get("type", "bambu")
        if ptype in FORMLABS_TYPES:
            conn = FormlabsLocalApiConnection(printer_cfg, self.cfg.get("preform_server"))
        elif ptype == "octoprint":
            conn = OctoPrintConnection(printer_cfg)
        elif ptype in CREALITY_TYPES:
            conn = CrealityConnection(printer_cfg)
        elif ptype == "ultimaker":
            conn = UltimakerConnection(printer_cfg)
        else:
            conn = PrinterConnection(printer_cfg)
        self.connections[printer_cfg["id"]] = conn
        conn.start()

    def add_printer(self, name, ip, ptype="bambu", access_code=None, serial=None,
                     camera_port=6000, mqtt_port=8883,
                     api_key=None, port=None, https=False, webcam_url=None):
        new_printer = {
            "id": uuid.uuid4().hex[:10],
            "name": name,
            "type": ptype,
            "ip": ip,
            "extras": []
        }
        if ptype in FORMLABS_TYPES:
            pass
        elif ptype == "octoprint":
            new_printer.update({
                "api_key": api_key or "",
                "port": port or 80,
                "https": https,
                "webcam_url": webcam_url or ""
            })
        elif ptype in CREALITY_TYPES:
            new_printer.update({
                "api_key": api_key or "",
                "port": port or 7125,     # Moonraker-Standardport
                "webcam_url": webcam_url or ""
            })
        elif ptype == "ultimaker":
            new_printer.update({
                "port": port or 80,
                "webcam_url": webcam_url or ""
            })
        else:
            new_printer.update({
                "access_code": access_code,
                "serial": serial,
                "mqtt_port": mqtt_port,
                "camera_port": camera_port
            })

        self.cfg["printers"].append(new_printer)
        save_config(self.cfg)
        self._start_printer(new_printer)
        return new_printer

    def remove_printer(self, printer_id):
        self.cfg["printers"] = [p for p in self.cfg["printers"] if p["id"] != printer_id]
        save_config(self.cfg)
        conn = self.connections.pop(printer_id, None)
        if conn:
            conn.stop()

    def get_printer_cfg(self, printer_id):
        for p in self.cfg["printers"]:
            if p["id"] == printer_id:
                return p
        return None

    def _resolve_extras(self, printer_cfg):
        out = []
        for ex in printer_cfg.get("extras", []):
            item = dict(ex)
            if ex.get("kind") == "sensor":
                item["value"] = self.extras.get_value(ex.get("topic"))
            out.append(item)
        return out

    def all_status(self):
        out = []
        for p in self.cfg["printers"]:
            conn = self.connections.get(p["id"])
            item = {
                "id": p["id"],
                "name": p["name"],
                "ip": p["ip"],
                "type": p.get("type", "bambu"),
            }
            item.update(conn.status if conn else {})
            item["extras"] = self._resolve_extras(p)
            out.append(item)
        return out

    def send_extra_command(self, printer_id, extra_id, action):
        p = self.get_printer_cfg(printer_id)
        if not p:
            return False, "Drucker nicht gefunden."
        extra = next((e for e in p.get("extras", []) if e.get("id") == extra_id), None)
        if not extra or extra.get("kind") != "switch":
            return False, "Schalter nicht gefunden."
        topic = extra.get("command_topic")
        payload = extra.get("payload_on") if action == "on" else extra.get("payload_off")
        if not topic or payload is None:
            return False, "Schalter ist in config.json nicht vollstaendig konfiguriert."
        ok = self.extras.publish(topic, payload)
        return ok, (None if ok else "MQTT-Verbindung fuer Sensoren/Schalter nicht verfuegbar.")

    def send_print_job(self, printer_id, local_path, remote_name):
        p = self.get_printer_cfg(printer_id)
        if not p:
            return False, "Drucker nicht gefunden.", None
        if p.get("type", "bambu") != "bambu":
            return False, "Druckauftrag per Drag & Drop wird aktuell nur fuer Bambu Lab Drucker unterstuetzt.", None
        conn = self.connections.get(printer_id)
        if not conn:
            return False, "Keine Verbindung zu diesem Drucker.", None
        try:
            ams_summary = conn.send_print(local_path, remote_name, None)
            return True, None, ams_summary
        except Exception as e:
            return False, str(e), None

    # ------------------------------------------------------------------
    # Zweistufiger Ablauf fuer die AMS-Zuordnungspruefung im Browser
    # (wie in Bambu Studio): erst "prepare" (Datei parsen, Vorschlag
    # anzeigen), dann "confirm" (mit ggf. vom Nutzer korrigierter
    # Zuordnung tatsaechlich hochladen + drucken), oder "cancel".
    # ------------------------------------------------------------------
    def _purge_stale_print_jobs(self):
        cutoff = time.time() - self.PRINT_JOB_MAX_AGE_SEC
        stale = [jid for jid, j in self._print_jobs.items() if j["created"] < cutoff]
        for jid in stale:
            job = self._print_jobs.pop(jid, None)
            if job:
                self._cleanup_job_file(job)
            with self._print_progress_lock:
                self._print_progress.pop(jid, None)

    def prepare_print_job(self, printer_id, local_path, remote_name):
        p = self.get_printer_cfg(printer_id)
        if not p:
            return False, "Drucker nicht gefunden.", None, None
        if p.get("type", "bambu") != "bambu":
            return False, "Druckauftrag per Drag & Drop wird aktuell nur fuer Bambu Lab Drucker unterstuetzt.", None, None
        conn = self.connections.get(printer_id)
        if not conn:
            return False, "Keine Verbindung zu diesem Drucker.", None, None

        preview = conn.preview_print(local_path)

        with self._print_jobs_lock:
            self._purge_stale_print_jobs()
            job_id = uuid.uuid4().hex
            self._print_jobs[job_id] = {
                "local_path": local_path,
                "remote_name": remote_name,
                "printer_id": printer_id,
                "created": time.time(),
            }
        return True, None, job_id, preview

    def _set_progress(self, job_id, **kwargs):
        with self._print_progress_lock:
            p = self._print_progress.setdefault(job_id, {
                "phase": "idle", "sent": 0, "total": 0, "percent": 0,
                "error": None, "ams": None,
            })
            p.update(kwargs)

    def get_print_progress(self, job_id):
        with self._print_progress_lock:
            p = self._print_progress.get(job_id)
            return dict(p) if p else None

    def start_confirm_print_job(self, job_id, mapping):
        """Startet FTPS-Upload + Druckstart in einem Hintergrund-Thread
        und kehrt SOFORT zurueck - der Fortschritt wird ueber
        get_print_progress(job_id) abgefragt (Polling vom Browser).
        Bei einem Fehler bleiben Job und Temp-Datei bewusst erhalten
        (kein Cleanup), damit der Nutzer erneut "Drucken starten"
        klicken kann, ohne die Datei nochmal hochladen und die
        AMS-Zuordnung neu waehlen zu muessen. Nur bei Erfolg oder durch
        cancel_print_job()/die 20-Minuten-Aufraeumroutine wird die
        Temp-Datei geloescht."""
        with self._print_jobs_lock:
            job = self._print_jobs.get(job_id)
        if not job:
            return False, "Druckauftrag nicht gefunden oder abgelaufen - bitte Datei erneut hochladen."
        conn = self.connections.get(job["printer_id"])
        if not conn:
            return False, "Keine Verbindung mehr zu diesem Drucker."

        try:
            total_size = os.path.getsize(job["local_path"])
        except OSError:
            with self._print_jobs_lock:
                self._print_jobs.pop(job_id, None)
            return False, "Zwischengespeicherte Datei nicht mehr vorhanden - bitte erneut hochladen."

        self._set_progress(job_id, phase="uploading", sent=0, total=total_size, percent=0, error=None, ams=None)

        def worker():
            def on_progress(sent, total):
                percent = int(sent * 100 / total) if total else 0
                self._set_progress(job_id, phase="uploading", sent=sent, total=total, percent=percent)
            try:
                ams_summary = conn.send_print(job["local_path"], job["remote_name"], mapping, on_progress=on_progress)
                self._set_progress(job_id, phase="done", sent=total_size, total=total_size, percent=100, ams=ams_summary)
                with self._print_jobs_lock:
                    self._print_jobs.pop(job_id, None)
                self._cleanup_job_file(job)
            except Exception as e:
                self._set_progress(job_id, phase="error", error=str(e))

        threading.Thread(target=worker, daemon=True).start()
        return True, None

    def cancel_print_job(self, job_id):
        with self._print_jobs_lock:
            job = self._print_jobs.pop(job_id, None)
        if job:
            self._cleanup_job_file(job)
        with self._print_progress_lock:
            self._print_progress.pop(job_id, None)
        return True

    @staticmethod
    def _cleanup_job_file(job):
        try:
            os.remove(job["local_path"])
            os.rmdir(os.path.dirname(job["local_path"]))
        except Exception:
            pass


dash = DashboardApp()
app = Flask(__name__)


# ----------------------------------------------------------------------
# REST-API
# ----------------------------------------------------------------------
@app.route("/api/printers", methods=["GET"])
def api_list_printers():
    return jsonify(dash.all_status())


@app.route("/api/printers", methods=["POST"])
def api_add_printer():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    ip = (data.get("ip") or "").strip()
    ptype = (data.get("type") or "bambu").strip().lower()

    if ptype not in KNOWN_TYPES:
        return jsonify({"error": "Unbekannter Druckertyp."}), 400
    if not name or not ip:
        return jsonify({"error": "Name und IP sind Pflichtfelder."}), 400
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return jsonify({"error": "Ungueltige IP-Adresse."}), 400

    if ptype in FORMLABS_TYPES:
        printer = dash.add_printer(name, ip, ptype=ptype)

    elif ptype == "octoprint":
        api_key = (data.get("api_key") or "").strip()
        if not api_key:
            return jsonify({"error": "Fuer OctoPrint ist der API-Key ein Pflichtfeld."}), 400
        try:
            port = int(data.get("port") or 80)
        except ValueError:
            return jsonify({"error": "Ungueltiger Port."}), 400
        https = bool(data.get("https"))
        webcam_url = (data.get("webcam_url") or "").strip()
        printer = dash.add_printer(name, ip, ptype="octoprint", api_key=api_key,
                                    port=port, https=https, webcam_url=webcam_url)

    elif ptype in CREALITY_TYPES:
        # Moonraker erlaubt vertrauenswuerdige LAN-IPs standardmaessig ohne
        # API-Key (siehe [authorization] trusted_clients in moonraker.conf)
        # - der Key ist hier deshalb optional, anders als bei OctoPrint.
        api_key = (data.get("api_key") or "").strip()
        try:
            port = int(data.get("port") or 7125)
        except ValueError:
            return jsonify({"error": "Ungueltiger Port."}), 400
        webcam_url = (data.get("webcam_url") or "").strip()
        printer = dash.add_printer(name, ip, ptype=ptype, api_key=api_key,
                                    port=port, webcam_url=webcam_url)

    elif ptype == "ultimaker":
        # Die Ultimaker-API benoetigt fuer reine Status-Abfragen (die
        # dieses Dashboard ausschliesslich macht) keinerlei Login/Key.
        try:
            port = int(data.get("port") or 80)
        except ValueError:
            return jsonify({"error": "Ungueltiger Port."}), 400
        webcam_url = (data.get("webcam_url") or "").strip()
        printer = dash.add_printer(name, ip, ptype="ultimaker", port=port, webcam_url=webcam_url)

    else:
        access_code = (data.get("access_code") or "").strip()
        serial = (data.get("serial") or "").strip()
        if not access_code or not serial:
            return jsonify({"error": "Fuer Bambu Lab Drucker sind Access Code und Seriennummer Pflichtfelder."}), 400
        printer = dash.add_printer(name, ip, ptype="bambu", access_code=access_code, serial=serial)

    return jsonify(printer), 201


@app.route("/api/printers/<printer_id>", methods=["DELETE"])
def api_delete_printer(printer_id):
    dash.remove_printer(printer_id)
    return jsonify({"ok": True})


@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify(dash.all_status())


@app.route("/api/printers/<printer_id>/extras/<extra_id>/command", methods=["POST"])
def api_extra_command(printer_id, extra_id):
    data = request.get_json(force=True) or {}
    action = (data.get("action") or "").strip().lower()
    if action not in ("on", "off"):
        return jsonify({"error": "action muss 'on' oder 'off' sein."}), 400
    ok, err = dash.send_extra_command(printer_id, extra_id, action)
    if not ok:
        return jsonify({"error": err}), 400
    return jsonify({"ok": True})


@app.route("/api/printers/<printer_id>/print/prepare", methods=["POST"])
def api_print_prepare(printer_id):
    """Schritt 1: Nimmt eine per Drag & Drop hochgeladene, bereits
    gesclicte .gcode.3mf-Datei entgegen, liest die Filament-Infos aus
    und schlaegt eine AMS-Zuordnung vor - laedt aber noch NICHTS auf den
    Drucker hoch und startet noch keinen Druck. Der Nutzer bestaetigt
    oder korrigiert die Zuordnung im Browser, danach folgt /print/confirm."""
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "Keine Datei erhalten."}), 400

    filename = secure_filename(f.filename)
    if not filename.lower().endswith(".gcode.3mf"):
        return jsonify({
            "error": "Nur fertig gesclicte .gcode.3mf-Dateien werden unterstuetzt "
                     "(Export aus Bambu Studio/OrcaSlicer)."
        }), 400

    tmp_dir = tempfile.mkdtemp(prefix="dashboard-print-")
    tmp_path = os.path.join(tmp_dir, filename)
    f.save(tmp_path)

    ok, err, job_id, preview = dash.prepare_print_job(printer_id, tmp_path, filename)
    if not ok:
        try:
            os.remove(tmp_path)
            os.rmdir(tmp_dir)
        except Exception:
            pass
        return jsonify({"error": err}), 400

    return jsonify({
        "ok": True,
        "job_id": job_id,
        "filename": filename,
        "filaments": preview["filaments"],
        "ams_trays": preview["ams_trays"],
    })


@app.route("/api/printers/<printer_id>/print/confirm", methods=["POST"])
def api_print_confirm(printer_id):
    """Schritt 2: Startet FTPS-Upload + Druckstart der in /print/prepare
    zwischengespeicherten Datei mit der vom Nutzer bestaetigten bzw. im
    Browser korrigierten AMS-Zuordnung IM HINTERGRUND und antwortet
    sofort. Fortschritt wird ueber GET .../print/progress/<job_id>
    abgefragt (Polling)."""
    data = request.get_json(force=True) or {}
    job_id = data.get("job_id")
    mapping = data.get("mapping")
    if not job_id:
        return jsonify({"error": "job_id fehlt."}), 400
    if mapping is not None and not isinstance(mapping, list):
        return jsonify({"error": "mapping muss eine Liste sein."}), 400

    ok, err = dash.start_confirm_print_job(job_id, mapping)
    if not ok:
        return jsonify({"error": err}), 400
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/printers/<printer_id>/print/progress/<job_id>", methods=["GET"])
def api_print_progress(printer_id, job_id):
    """Fortschritt eines per /print/confirm gestarteten Uploads.
    phase: "uploading" | "done" | "error". Wird vom Browser alle paar
    hundert Millisekunden abgefragt, um Prozent-Anzeige + Fortschritts-
    balken zu aktualisieren."""
    progress = dash.get_print_progress(job_id)
    if not progress:
        return jsonify({"error": "Kein laufender Vorgang mit dieser job_id gefunden."}), 404
    return jsonify(progress)


@app.route("/api/printers/<printer_id>/print/cancel", methods=["POST"])
def api_print_cancel(printer_id):
    """Bricht einen vorbereiteten (aber noch nicht bestaetigten)
    Druckauftrag ab und raeumt die zwischengespeicherte Datei auf."""
    data = request.get_json(force=True) or {}
    job_id = data.get("job_id")
    if not job_id:
        return jsonify({"error": "job_id fehlt."}), 400
    dash.cancel_print_job(job_id)
    return jsonify({"ok": True})


@app.route("/api/version", methods=["GET"])
def api_version():
    return jsonify({"version": APP_VERSION})


@app.route("/camera/<printer_id>")
def camera_stream(printer_id):
    pcfg = dash.get_printer_cfg(printer_id)
    if not pcfg:
        return "Drucker nicht gefunden", 404
    ptype = pcfg.get("type", "bambu")

    if ptype == "bambu":
        try:
            gen = bambu_mjpeg_generator(pcfg["ip"], pcfg["access_code"], int(pcfg.get("camera_port", 6000)))
            return Response(gen, mimetype="multipart/x-mixed-replace; boundary=frame")
        except Exception as e:
            return f"Kamera nicht erreichbar: {e}", 502

    elif ptype == "octoprint":
        webcam_url = pcfg.get("webcam_url") or f"http://{pcfg['ip']}:8080/webcam/?action=stream"
        return redirect(webcam_url)

    elif ptype in CREALITY_TYPES:
        # Crowsnest (der bei Klipper/Moonraker-Setups uebliche Webcam-Dienst)
        # stellt seinen MJPEG-Stream typischerweise unter diesem Pfad bereit.
        webcam_url = pcfg.get("webcam_url") or f"http://{pcfg['ip']}/webcam/?action=stream"
        return redirect(webcam_url)

    elif ptype == "ultimaker":
        # Bei allen netzwerkfaehigen Ultimaker-Modellen mit eingebauter
        # Kamera (UM3, S-Serie) liefert der integrierte mjpg-streamer den
        # Stream ueblicherweise unter diesem Pfad.
        webcam_url = pcfg.get("webcam_url") or f"http://{pcfg['ip']}:8080/?action=stream"
        return redirect(webcam_url)

    else:
        return "Kamera-Funktion ist fuer diesen Druckertyp nicht verfuegbar.", 400


@app.route("/")
def index():
    return render_template_string(INDEX_HTML)


# ----------------------------------------------------------------------
# Frontend (dunkles, technisches Dashboard-Design)
# ----------------------------------------------------------------------
INDEX_HTML = r"""
<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Drucker Dashboard</title>
<style>
  :root{
    --bg:#0a0c0f;
    --panel:#12151a;
    --panel-2:#171b21;
    --border:#242a33;
    --text:#e5e8ec;
    --text-dim:#8891a0;
    --accent:#ff9142;
    --accent-2:#3ddc97;
    --danger:#ff5d5d;
    --mono: 'JetBrains Mono', 'Consolas', 'SFMono-Regular', monospace;
    --sans: 'Inter', 'Segoe UI', system-ui, sans-serif;
  }
  *{box-sizing:border-box;}
  body{
    margin:0; background:var(--bg); color:var(--text);
    font-family:var(--sans); letter-spacing:0.1px;
  }
  header{
    display:flex; align-items:center; justify-content:space-between;
    padding:22px 32px; border-bottom:1px solid var(--border);
    background:linear-gradient(180deg,#0d1014,#0a0c0f);
  }
  header h1{
    font-size:18px; font-weight:600; margin:0; letter-spacing:0.5px;
    text-transform:uppercase; color:var(--text);
  }
  header h1 span{ color:var(--accent); }
  .btn{
    background:var(--accent); color:#12100c; border:none; border-radius:6px;
    padding:10px 18px; font-weight:600; font-size:13px; cursor:pointer;
    letter-spacing:0.3px; transition:filter .15s ease;
  }
  .btn:hover{ filter:brightness(1.1); }
  .btn-ghost{
    background:transparent; color:var(--text-dim); border:1px solid var(--border);
  }
  .btn-ghost:hover{ color:var(--text); border-color:#3a4250; }
  .btn-mini{
    background:#1b2027; color:var(--text); border:1px solid var(--border);
    border-radius:5px; padding:5px 10px; font-size:11px; cursor:pointer;
    font-family:var(--mono);
  }
  .btn-mini:hover{ border-color:var(--accent-2); }
  .btn-mini.off:hover{ border-color:var(--danger); }
  main{ padding:28px 32px; max-width:1100px; margin:0 auto; }
  .printer-card{
    background:var(--panel); border:1px solid var(--border); border-radius:10px;
    margin-bottom:22px; overflow:hidden;
  }
  .card-head{
    display:flex; align-items:center; justify-content:space-between;
    padding:16px 20px; border-bottom:1px solid var(--border);
    background:var(--panel-2);
  }
  .card-head .name{ font-size:15px; font-weight:600; }
  .card-head .ip{ font-family:var(--mono); font-size:12px; color:var(--text-dim); margin-left:10px;}
  .type-badge{
    font-family:var(--mono); font-size:10px; padding:2px 8px; border-radius:20px;
    border:1px solid var(--border); color:var(--text-dim); text-transform:uppercase; margin-left:10px;
  }
  .status-dot{
    width:9px; height:9px; border-radius:50%; display:inline-block; margin-right:8px;
    background:var(--danger);
  }
  .status-dot.online{ background:var(--accent-2); box-shadow:0 0 6px var(--accent-2); }
  .head-right{ display:flex; align-items:center; gap:14px; }
  .cam-icon{
    cursor:pointer; width:30px; height:30px; border-radius:6px;
    display:flex; align-items:center; justify-content:center;
    border:1px solid var(--border); background:#1b2027;
  }
  .cam-icon:hover{ border-color:var(--accent); }
  .cam-icon svg{ width:16px; height:16px; fill:var(--text-dim); }
  .cam-icon:hover svg{ fill:var(--accent); }
  .del-icon{ cursor:pointer; color:var(--text-dim); font-size:18px; padding:0 4px;}
  .del-icon:hover{ color:var(--danger); }

  .card-body{ padding:20px; display:grid; grid-template-columns:1.3fr 1fr; gap:24px; }
  .card-body.single-col{ grid-template-columns:1fr; }
  @media(max-width:760px){ .card-body{ grid-template-columns:1fr; } }

  .field-label{ font-size:11px; text-transform:uppercase; letter-spacing:0.6px; color:var(--text-dim); margin-bottom:6px; }
  .file-name{ font-family:var(--mono); font-size:13px; margin-bottom:14px; word-break:break-all; }
  .error-hint{
    font-family:var(--mono); font-size:11.5px; color:var(--danger);
    background:#2a1414; border:1px solid #4a1f1f; border-radius:6px;
    padding:8px 10px; margin-top:4px;
  }

  .progress-row{ display:flex; align-items:center; gap:12px; margin-bottom:16px; }
  .progress-track{
    flex:1; height:10px; border-radius:5px; background:#20252c; overflow:hidden;
    border:1px solid var(--border);
  }
  .progress-fill{
    height:100%; background:linear-gradient(90deg,var(--accent-2),#2bb987);
    width:0%; transition:width .4s ease;
  }
  .progress-pct{ font-family:var(--mono); font-size:14px; min-width:46px; text-align:right;}

  .temps{ display:flex; gap:18px; margin-top:6px; flex-wrap:wrap; }
  .temp-chip{
    background:#1b2027; border:1px solid var(--border); border-radius:6px;
    padding:8px 12px; font-family:var(--mono); font-size:12.5px; color:var(--text-dim);
  }
  .temp-chip b{ color:var(--text); font-size:13px; }

  .ams-title{ font-size:11px; text-transform:uppercase; letter-spacing:0.6px; color:var(--text-dim); margin-bottom:10px;}
  .ams-slot{ display:flex; align-items:center; gap:10px; margin-bottom:9px; }
  .ams-swatch{ width:14px; height:14px; border-radius:3px; border:1px solid #000a; flex-shrink:0;}
  .ams-meta{ font-family:var(--mono); font-size:11.5px; color:var(--text-dim); width:110px; flex-shrink:0;}
  .ams-track{ flex:1; height:8px; border-radius:4px; background:#20252c; overflow:hidden; border:1px solid var(--border);}
  .ams-fill{ height:100%; }
  .ams-remain{ font-family:var(--mono); font-size:11.5px; width:36px; text-align:right; color:var(--text-dim);}
  .empty-ams{ font-size:12px; color:var(--text-dim); font-style:italic; }

  .drop-zone{
    margin-top:16px; border:1px dashed var(--border); border-radius:8px;
    padding:14px; text-align:center; font-size:12px; color:var(--text-dim);
    transition:border-color .15s ease, background .15s ease;
  }
  .drop-zone.dragover{ border-color:var(--accent-2); background:#132018; color:var(--text); }
  .drop-zone.uploading{ border-color:var(--accent); color:var(--text); }
  .drop-zone .dz-hint{ font-size:10.5px; margin-top:4px; color:var(--text-dim); }
  .drop-zone .dz-status{
    font-family:var(--mono); font-size:11.5px; margin-top:8px;
  }
  .drop-zone .dz-status.err{ color:var(--danger); }
  .drop-zone .dz-status.ok{ color:var(--accent-2); }

  .ver-badge{
    font-family:var(--mono); font-size:11px; color:var(--text-dim);
    font-weight:400; vertical-align:middle; margin-left:4px;
  }

  .ams-modal{ width:480px; }
  .ams-row{
    display:flex; align-items:flex-start; gap:12px; padding:12px 0;
    border-bottom:1px solid var(--border);
  }
  .ams-row:last-child{ border-bottom:none; }
  .ams-row-body{ flex:1; min-width:0; }
  .ams-row-label{ font-size:13px; font-weight:600; margin-bottom:8px; }
  .ams-row-type{ font-family:var(--mono); font-size:11px; font-weight:400; color:var(--text-dim); }
  .ams-swatch{ margin-top:3px; }
  .ams-radio{
    display:flex; align-items:center; gap:8px; font-size:12.5px; color:var(--text-dim);
    padding:5px 0; cursor:pointer;
  }
  .ams-radio input[type="radio"]{ width:auto; margin:0; flex-shrink:0; accent-color:var(--accent-2); }
  .ams-radio b{ color:var(--text); font-weight:600; }
  .ams-row-select{
    background:#0d1014; border:1px solid var(--border); color:var(--text);
    padding:5px 7px; border-radius:6px; font-family:var(--mono); font-size:11.5px;
    margin-left:4px;
  }
  .ams-row-select:disabled{ opacity:0.4; cursor:not-allowed; }

  .ams-progress-wrap{ margin-top:16px; }
  .ams-progress-track{
    height:8px; background:#0d1014; border:1px solid var(--border);
    border-radius:5px; overflow:hidden;
  }
  .ams-progress-bar{
    height:100%; background:var(--accent-2); width:0%;
    transition:width .25s ease;
  }
  .ams-progress-label{
    font-family:var(--mono); font-size:11.5px; color:var(--text-dim);
    margin-top:6px;
  }

  .toast-container{
    position:fixed; top:18px; right:18px; z-index:80;
    display:flex; flex-direction:column; gap:10px; max-width:340px;
  }
  .toast{
    background:var(--panel); border:1px solid var(--border); border-radius:8px;
    padding:12px 14px; font-size:13px; box-shadow:0 6px 18px rgba(0,0,0,.45);
  }
  .toast.ok{ border-color:#2bb98755; color:var(--accent-2); }
  .toast.err{ border-color:#c0392b55; color:var(--danger); }

  .state-badge{
    font-family:var(--mono); font-size:11px; padding:3px 9px; border-radius:20px;
    border:1px solid var(--border); color:var(--text-dim); text-transform:uppercase;
  }
  .state-badge.running{ color:var(--accent-2); border-color:#2bb98755; }
  .state-badge.paused{ color:var(--accent); border-color:#ff914255; }

  .empty-state{
    text-align:center; padding:70px 20px; color:var(--text-dim);
  }
  .empty-state .btn{ margin-top:16px; }

  .extras-section{ margin-top:18px; padding-top:14px; border-top:1px solid var(--border); }
  .extras-row{ display:flex; flex-wrap:wrap; gap:10px; }
  .extra-sensor{
    background:#1b2027; border:1px solid var(--border); border-radius:6px;
    padding:7px 11px; font-family:var(--mono); font-size:12px; color:var(--text-dim);
    display:flex; gap:8px; align-items:center;
  }
  .extra-sensor b{ color:var(--text); }
  .extra-switch{
    background:#1b2027; border:1px solid var(--border); border-radius:6px;
    padding:6px 10px; display:flex; gap:8px; align-items:center; font-size:12px;
  }

  /* Modal */
  .modal-backdrop{
    display:none; position:fixed; inset:0; background:rgba(0,0,0,.6);
    align-items:center; justify-content:center; z-index:50;
  }
  .modal-backdrop.show{ display:flex; }
  .modal{
    background:var(--panel); border:1px solid var(--border); border-radius:10px;
    width:400px; padding:24px; max-height:85vh; overflow-y:auto;
  }
  .modal h2{ font-size:15px; margin:0 0 18px 0; text-transform:uppercase; letter-spacing:0.5px;}
  .modal label{ font-size:11px; color:var(--text-dim); text-transform:uppercase; letter-spacing:0.5px;}
  .modal input, .modal select{
    width:100%; background:#0d1014; border:1px solid var(--border); color:var(--text);
    padding:9px 10px; border-radius:6px; margin:6px 0 14px 0; font-family:var(--mono); font-size:13px;
  }
  .modal input:focus, .modal select:focus{ outline:none; border-color:var(--accent); }
  .checkbox-row{ display:flex; align-items:center; gap:8px; margin:6px 0 14px 0; }
  .checkbox-row input{ width:auto; margin:0; }
  .modal-actions{ display:flex; justify-content:flex-end; gap:10px; margin-top:6px;}
  .error-msg{ color:var(--danger); font-size:12px; margin-bottom:10px; display:none;}
  .hint-text{ font-size:11px; color:var(--text-dim); margin:-8px 0 14px 0; line-height:1.4;}

  .cam-modal .modal{ width:auto; padding:0; overflow:hidden; }
  .cam-modal img{ display:block; max-width:90vw; max-height:80vh; background:#000; }
  .cam-modal .cam-close{
    position:absolute; top:14px; right:20px; color:#fff; font-size:26px; cursor:pointer; z-index:60;
  }
</style>
</head>
<body>

<header>
  <h1>Drucker<span>Dashboard</span> <span class="ver-badge" id="verBadge"></span></h1>
  <button class="btn" onclick="openAddModal()">+ Drucker hinzufuegen</button>
</header>

<main id="printerList"></main>

<!-- Modal: Drucker hinzufuegen -->
<div class="modal-backdrop" id="addModal">
  <div class="modal">
    <h2>Neuen Drucker hinzufuegen</h2>
    <div class="error-msg" id="addError"></div>

    <label>Druckertyp</label>
    <select id="f_type" onchange="toggleTypeFields()">
      <option value="bambu">Bambu Lab</option>
      <option value="formlabs">Formlabs (Drucker)</option>
      <option value="formlabs_wash">Formlabs Wash L</option>
      <option value="formlabs_cure">Formlabs Cure L</option>
      <option value="octoprint">OctoPrint</option>
      <option value="creality_k1">Creality K1</option>
      <option value="creality_k1c">Creality K1C</option>
      <option value="creality_k1max">Creality K1 Max</option>
      <option value="creality_k1se">Creality K1 SE</option>
      <option value="creality_other">Creality (sonstiger Klipper-Drucker)</option>
      <option value="ultimaker">Ultimaker</option>
    </select>

    <label>Name</label>
    <input id="f_name" placeholder="z. B. X1C Werkstatt">
    <label>IP-Adresse des Geraets</label>
    <input id="f_ip" placeholder="192.168.1.50">

    <div id="bambuFields">
      <label>Access Code (LAN-Modus, Drucker-Display &rarr; Einstellungen)</label>
      <input id="f_code" placeholder="8-stelliger Code">
      <label>Seriennummer</label>
      <input id="f_serial" placeholder="z. B. 01P00A123456789">
    </div>

    <div id="formlabsHint" class="hint-text">
      Benoetigt den lokal laufenden "PreFormServer" (Formlabs Local API,
      Teil der PreForm-Installation) - siehe README.
    </div>

    <div id="octoprintFields">
      <label>API-Key</label>
      <input id="f_apikey" placeholder="OctoPrint-Einstellungen &rarr; API">
      <label>Port</label>
      <input id="f_port" placeholder="80">
      <div class="checkbox-row">
        <input type="checkbox" id="f_https">
        <label style="margin:0;">HTTPS verwenden</label>
      </div>
      <label>Webcam-URL (optional)</label>
      <input id="f_webcam" placeholder="http://IP:8080/webcam/?action=stream">
    </div>

    <div id="crealityFields">
      <div class="hint-text">
        Benoetigt Moonraker auf dem Drucker (bei werkseitigen K1/K1C/K1 Max/
        K1 SE muss dafuer erst per SSH "gerootet" werden) - siehe README.
      </div>
      <label>API-Key (meist nicht noetig, siehe README)</label>
      <input id="f_creality_apikey" placeholder="optional">
      <label>Moonraker-Port</label>
      <input id="f_creality_port" placeholder="7125">
      <label>Webcam-URL (optional)</label>
      <input id="f_creality_webcam" placeholder="http://IP/webcam/?action=stream">
    </div>

    <div id="ultimakerFields">
      <div class="hint-text">
        Nutzt die offizielle, unauthentifizierte lokale Ultimaker-API -
        kein Login/API-Key noetig, siehe README.
      </div>
      <label>Port (optional)</label>
      <input id="f_ultimaker_port" placeholder="80">
      <label>Webcam-URL (optional)</label>
      <input id="f_ultimaker_webcam" placeholder="http://IP:8080/?action=stream">
    </div>

    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeAddModal()">Abbrechen</button>
      <button class="btn" onclick="submitAdd()">Hinzufuegen</button>
    </div>
  </div>
</div>

<!-- Modal: Kamera -->
<div class="modal-backdrop cam-modal" id="camModal">
  <span class="cam-close" onclick="closeCam()">&times;</span>
  <div class="modal">
    <img id="camImg" src="">
  </div>
</div>

<!-- Modal: AMS-Zuordnung pruefen/korrigieren vor dem Drucken -->
<div class="modal-backdrop" id="amsModal">
  <div class="modal ams-modal">
    <h2>AMS-Zuordnung pruefen</h2>
    <div class="file-name" id="amsModalFilename" style="margin-bottom:16px;"></div>
    <div id="amsModalRows"></div>
    <div class="ams-progress-wrap" id="amsProgressWrap" style="display:none;">
      <div class="ams-progress-track"><div class="ams-progress-bar" id="amsProgressBar" style="width:0%"></div></div>
      <div class="ams-progress-label" id="amsProgressLabel"></div>
    </div>
    <div class="modal-actions">
      <button class="btn btn-ghost" id="amsModalCancelBtn" onclick="cancelAmsModal()">Abbrechen</button>
      <button class="btn" id="amsModalConfirmBtn" onclick="confirmAmsModal()">Drucken starten</button>
    </div>
  </div>
</div>

<!-- Toasts (unabhaengig vom Drucker-Grid, ueberleben refresh()) -->
<div class="toast-container" id="toastContainer"></div>

<script>
const CAM_ICON = `<svg viewBox="0 0 24 24"><path d="M4 7h3l1.5-2h7L17 7h3a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2zm8 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8z"/></svg>`;

const FL_LABELS = {
  formlabs:      { badge: 'Formlabs Drucker', file: 'Aktueller Druckauftrag', showMaterial: true  },
  formlabs_wash: { badge: 'Formlabs Wash L',  file: 'Aktueller Waschzyklus',  showMaterial: false },
  formlabs_cure: { badge: 'Formlabs Cure L',  file: 'Aktueller Haertezyklus', showMaterial: false }
};

const CREALITY_TYPES = ['creality_k1', 'creality_k1c', 'creality_k1max', 'creality_k1se', 'creality_other'];
const CREALITY_LABELS = {
  creality_k1:    'Creality K1',
  creality_k1c:   'Creality K1C',
  creality_k1max: 'Creality K1 Max',
  creality_k1se:  'Creality K1 SE',
  creality_other: 'Creality (Klipper)'
};

function toggleTypeFields(){
  const type = document.getElementById('f_type').value;
  document.getElementById('bambuFields').style.display = (type === 'bambu') ? 'block' : 'none';
  document.getElementById('octoprintFields').style.display = (type === 'octoprint') ? 'block' : 'none';
  document.getElementById('crealityFields').style.display = CREALITY_TYPES.includes(type) ? 'block' : 'none';
  document.getElementById('ultimakerFields').style.display = (type === 'ultimaker') ? 'block' : 'none';
  document.getElementById('formlabsHint').style.display =
    (type === 'formlabs' || type === 'formlabs_wash' || type === 'formlabs_cure') ? 'block' : 'none';
}

function openAddModal(){
  document.getElementById('addError').style.display='none';
  ['f_name','f_ip','f_code','f_serial','f_apikey','f_port','f_webcam',
   'f_creality_apikey','f_creality_port','f_creality_webcam',
   'f_ultimaker_port','f_ultimaker_webcam'].forEach(id => document.getElementById(id).value='');
  document.getElementById('f_https').checked = false;
  document.getElementById('f_type').value = 'bambu';
  toggleTypeFields();
  document.getElementById('addModal').classList.add('show');
}
function closeAddModal(){ document.getElementById('addModal').classList.remove('show'); }

async function submitAdd(){
  const type = document.getElementById('f_type').value;
  const body = {
    type: type,
    name: document.getElementById('f_name').value.trim(),
    ip: document.getElementById('f_ip').value.trim(),
  };
  if(type === 'bambu'){
    body.access_code = document.getElementById('f_code').value.trim();
    body.serial = document.getElementById('f_serial').value.trim();
  } else if(type === 'octoprint'){
    body.api_key = document.getElementById('f_apikey').value.trim();
    body.port = document.getElementById('f_port').value.trim() || 80;
    body.https = document.getElementById('f_https').checked;
    body.webcam_url = document.getElementById('f_webcam').value.trim();
  } else if(CREALITY_TYPES.includes(type)){
    body.api_key = document.getElementById('f_creality_apikey').value.trim();
    body.port = document.getElementById('f_creality_port').value.trim() || 7125;
    body.webcam_url = document.getElementById('f_creality_webcam').value.trim();
  } else if(type === 'ultimaker'){
    body.port = document.getElementById('f_ultimaker_port').value.trim() || 80;
    body.webcam_url = document.getElementById('f_ultimaker_webcam').value.trim();
  }

  const res = await fetch('/api/printers', {
    method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)
  });
  const data = await res.json();
  if(!res.ok){
    const err = document.getElementById('addError');
    err.textContent = data.error || 'Fehler beim Hinzufuegen.';
    err.style.display='block';
    return;
  }
  closeAddModal();
  refresh();
}

async function deletePrinter(id){
  if(!confirm('Diesen Drucker wirklich entfernen?')) return;
  await fetch('/api/printers/' + id, { method:'DELETE' });
  refresh();
}

function openCam(id){
  document.getElementById('camImg').src = '/camera/' + id + '?_=' + Date.now();
  document.getElementById('camModal').classList.add('show');
}
function closeCam(){
  document.getElementById('camModal').classList.remove('show');
  document.getElementById('camImg').src = '';
}

async function extraCommand(printerId, extraId, action){
  await fetch(`/api/printers/${printerId}/extras/${extraId}/command`, {
    method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({action})
  });
}

function stateClass(state){
  if(!state) return '';
  const s = String(state).toUpperCase();
  if(['RUNNING','PRINTING','WASHING','CURING','BUSY','OPERATIONAL'].includes(s)) return 'running';
  if(['PAUSE','PAUSED'].includes(s)) return 'paused';
  return '';
}

function formatRemaining(totalMinutes){
  if(totalMinutes === undefined || totalMinutes === null) return '';
  const mins = Math.max(0, Math.round(totalMinutes));
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  if(h > 0){
    return `${h} h ${m} min verbleibend`;
  }
  return `${m} min verbleibend`;
}

function renderAms(ams){
  if(!ams || ams.length === 0){
    return '<div class="empty-ams">Kein AMS erkannt / keine Fach-Daten.</div>';
  }
  return ams.map(t => {
    const remain = (t.remain === undefined || t.remain === null || t.remain < 0) ? '–' : t.remain + '%';
    const width = (t.remain && t.remain > 0) ? t.remain : 0;
    return `<div class="ams-slot">
      <div class="ams-swatch" style="background:${t.color}"></div>
      <div class="ams-meta">${t.type}</div>
      <div class="ams-track"><div class="ams-fill" style="width:${width}%; background:${t.color}"></div></div>
      <div class="ams-remain">${remain}</div>
    </div>`;
  }).join('');
}

function renderExtras(printerId, extras){
  if(!extras || extras.length === 0) return '';
  return `<div class="extras-section">
    <div class="field-label">Sensoren &amp; Schalter</div>
    <div class="extras-row">
      ${extras.map(e => {
        if(e.kind === 'switch'){
          return `<div class="extra-switch">
            <span>${e.label}</span>
            <button class="btn-mini" onclick="extraCommand('${printerId}','${e.id}','on')">Ein</button>
            <button class="btn-mini off" onclick="extraCommand('${printerId}','${e.id}','off')">Aus</button>
          </div>`;
        }
        const val = (e.value === undefined || e.value === null || e.value === '') ? '–' : e.value;
        return `<div class="extra-sensor"><span>${e.label}</span><b>${val}${e.unit ? ' ' + e.unit : ''}</b></div>`;
      }).join('')}
    </div>
  </div>`;
}

async function refresh(){
  const res = await fetch('/api/status');
  const printers = await res.json();
  const list = document.getElementById('printerList');

  if(printers.length === 0){
    list.innerHTML = `<div class="empty-state">
      Noch keine Drucker hinterlegt.
      <div><button class="btn" onclick="openAddModal()">+ Drucker hinzufuegen</button></div>
    </div>`;
    return;
  }

  list.innerHTML = printers.map(p => {
    if(p.type === 'octoprint') return renderOctoPrintCard(p);
    if(p.type === 'formlabs' || p.type === 'formlabs_wash' || p.type === 'formlabs_cure') return renderFormlabsCard(p);
    if(CREALITY_TYPES.includes(p.type)) return renderCrealityCard(p);
    if(p.type === 'ultimaker') return renderUltimakerCard(p);
    return renderBambuCard(p);
  }).join('');
}

function renderBambuCard(p){
  const online = p.connected;
  const pct = p.progress || 0;
  const remMin = formatRemaining(p.remaining_min);
  return `
    <div class="printer-card">
      <div class="card-head">
        <div>
          <span class="status-dot ${online ? 'online' : ''}"></span>
          <span class="name">${p.name}</span>
          <span class="ip">${p.ip}</span>
          <span class="type-badge">Bambu Lab</span>
        </div>
        <div class="head-right">
          <span class="state-badge ${stateClass(p.gcode_state)}">${p.gcode_state || 'UNKNOWN'}</span>
          <div class="cam-icon" title="Kamera anzeigen" onclick="openCam('${p.id}')">${CAM_ICON}</div>
          <div class="del-icon" title="Entfernen" onclick="deletePrinter('${p.id}')">&times;</div>
        </div>
      </div>
      <div class="card-body">
        <div>
          <div class="field-label">Aktuelle Datei</div>
          <div class="file-name">${p.file_name || '-'} ${remMin ? ' &middot; ' + remMin : ''}</div>

          <div class="field-label">Fortschritt</div>
          <div class="progress-row">
            <div class="progress-track"><div class="progress-fill" style="width:${pct}%"></div></div>
            <div class="progress-pct">${pct}%</div>
          </div>

          <div class="field-label">Temperaturen</div>
          <div class="temps">
            <div class="temp-chip">Kammer <b>${p.chamber_temp ?? '–'}&deg;C</b></div>
            <div class="temp-chip">Duese <b>${p.nozzle_temp ?? '–'}&deg;C</b></div>
            <div class="temp-chip">Bett <b>${p.bed_temp ?? '–'}&deg;C</b></div>
          </div>
        </div>
        <div>
          <div class="ams-title">AMS / Filament</div>
          ${renderAms(p.ams)}
          ${renderDropZone(p.id)}
        </div>
      </div>
      ${renderExtras(p.id, p.extras)}
    </div>`;
}

function renderDropZone(printerId){
  return `
    <div class="drop-zone" id="dz-${printerId}"
         ondragover="dzDragOver(event)"
         ondragleave="dzDragLeave(event)"
         ondrop="dzDrop(event,'${printerId}')">
      Fertig gesclicte .gcode.3mf-Datei hier ablegen zum Drucken
      <div class="dz-hint">Erfordert Developer Mode / LAN-Modus am Drucker</div>
    </div>`;
}

function dzDragOver(ev){
  ev.preventDefault();
  ev.currentTarget.classList.add('dragover');
}
function dzDragLeave(ev){
  ev.currentTarget.classList.remove('dragover');
}

// Merkt sich Zuordnungs-Modal-Zustand fuer den aktuell offenen Vorgang
let amsModalJobId = null;
let amsModalPrinterId = null;

async function dzDrop(ev, printerId){
  ev.preventDefault();
  const zone = ev.currentTarget;
  zone.classList.remove('dragover');
  const files = ev.dataTransfer.files;
  if(!files || files.length === 0) return;
  const file = files[0];

  if(!file.name.toLowerCase().endsWith('.gcode.3mf')){
    showToast('Nur .gcode.3mf-Dateien werden unterstuetzt.', 'err');
    return;
  }

  zone.classList.add('uploading');
  const form = new FormData();
  form.append('file', file);
  try{
    const res = await fetch('/api/printers/' + printerId + '/print/prepare', { method:'POST', body: form });
    const data = await res.json();
    if(!res.ok){
      showToast(data.error || 'Fehler beim Vorbereiten des Druckauftrags.', 'err');
      return;
    }
    openAmsModal(printerId, data);
  } catch(e){
    showToast('Netzwerkfehler beim Hochladen.', 'err');
  } finally {
    zone.classList.remove('uploading');
  }
}

// Grobe Zuordnung Hex-Farbe -> deutscher Farbname (naechster Treffer per
// RGB-Abstand). Eine exakte Farbe laesst sich nicht immer 1:1 in ein Wort
// uebersetzen - das ist eine bewusste, für die Anzeige ausreichende
// Näherung, keine Farbmanagement-Software. Der exakte Hex-Wert bleibt bei
// Bedarf als Tooltip (title-Attribut) abrufbar.
const NAMED_COLORS = [
  ['Schwarz', '000000'], ['Weiss', 'FFFFFF'], ['Grau', '808080'],
  ['Hellgrau', 'D3D3D3'], ['Dunkelgrau', '404040'],
  ['Rot', 'FF0000'], ['Dunkelrot', '8B0000'], ['Rosa', 'FFC0CB'],
  ['Pink', 'FF1493'], ['Magenta', 'FF00FF'],
  ['Orange', 'FFA500'], ['Gelb', 'FFFF00'], ['Hellgelb', 'FFFFE0'],
  ['Braun', '8B4513'], ['Beige', 'F5F5DC'],
  ['Gruen', '008000'], ['Hellgruen', '90EE90'], ['Dunkelgruen', '006400'],
  ['Olivgruen', '808000'],
  ['Tuerkis', '40E0D0'], ['Cyan', '00FFFF'],
  ['Blau', '0000FF'], ['Hellblau', 'ADD8E6'], ['Dunkelblau', '00008B'],
  ['Marineblau', '000080'],
  ['Lila', '800080'], ['Violett', '8A2BE2'],
  ['Gold', 'FFD700'], ['Silber', 'C0C0C0'], ['Kupfer', 'B87333'],
];

function hexToRgb(hex){
  const h = (hex || '').replace('#', '');
  return {
    r: parseInt(h.substring(0, 2), 16) || 0,
    g: parseInt(h.substring(2, 4), 16) || 0,
    b: parseInt(h.substring(4, 6), 16) || 0,
  };
}

function colorNameFor(hex){
  const target = hexToRgb(hex);
  let best = 'unbekannt', bestDist = Infinity;
  for(const [name, h] of NAMED_COLORS){
    const c = hexToRgb(h);
    const dist = (c.r - target.r) ** 2 + (c.g - target.g) ** 2 + (c.b - target.b) ** 2;
    if(dist < bestDist){ bestDist = dist; best = name; }
  }
  return best;
}

function openAmsModal(printerId, data){
  amsModalJobId = data.job_id;
  amsModalPrinterId = printerId;

  document.getElementById('amsModalFilename').textContent = data.filename;
  resetAmsProgress();

  const rows = document.getElementById('amsModalRows');
  if(!data.filaments || data.filaments.length === 0){
    rows.innerHTML = `<div class="hint-text">Konnte keine Filament-Infos aus der Datei lesen - der Druck kann ohne AMS-Zuordnung (externe Spule) gestartet werden.</div>`;
  } else if(!data.ams_trays || data.ams_trays.length === 0){
    rows.innerHTML = `<div class="hint-text">Keine aktuellen AMS-Fach-Daten vom Drucker verfuegbar - der Druck kann ohne automatische Zuordnung gestartet werden (am Display manuell waehlen).</div>`
      + data.filaments.map((f, i) => amsRowHtml(f, i, [])).join('');
  } else {
    rows.innerHTML = data.filaments.map((f, i) => amsRowHtml(f, i, data.ams_trays)).join('');
  }

  document.getElementById('amsModal').classList.add('show');
}

function amsRowHtml(filament, i, amsTrays){
  const suggested = (filament.suggested_tray === undefined) ? -1 : filament.suggested_tray;
  const suggestedTray = amsTrays.find(t => t.flat_index === suggested) || null;
  const hasSuggestion = suggested !== -1 && suggestedTray !== null;
  const filamentColorName = colorNameFor(filament.color);
  const filamentHexTitle = '#' + (filament.color || '').toUpperCase();

  const suggestionLabel = hasSuggestion
    ? `AMS-Fach ${suggestedTray.flat_index} &middot; ${suggestedTray.type || '-'} &middot; <span title="${'#' + (suggestedTray.color || '').toUpperCase()}">${colorNameFor(suggestedTray.color)}</span>`
    : `Extern / manuell am Display (keine passende Farbe im AMS gefunden)`;

  // "Andere Wahl"-Dropdown: alle AMS-Faecher AUSSER dem bereits vorgeschlagenen,
  // plus immer die Option "Extern / manuell".
  const otherTrays = amsTrays.filter(t => t.flat_index !== suggested);
  const otherOptions = [`<option value="-1">Extern / manuell am Display</option>`]
    .concat(otherTrays.map(t => {
      const remain = (t.remain === undefined || t.remain === null || t.remain < 0) ? '?' : t.remain + '%';
      const trayColorName = colorNameFor(t.color);
      return `<option value="${t.flat_index}">AMS-Fach ${t.flat_index} &middot; ${t.type || '-'} &middot; ${trayColorName} &middot; ${remain}</option>`;
    })).join('');

  const groupName = 'ams-choice-' + i;
  return `
    <div class="ams-row" data-filament-index="${i}" data-suggested="${suggested}">
      <div class="ams-swatch" style="background:#${filament.color}" title="${filamentHexTitle}"></div>
      <div class="ams-row-body">
        <div class="ams-row-label" title="${filamentHexTitle}">Filament ${i + 1} <span class="ams-row-type">(${filament.type || '-'} &middot; ${filamentColorName})</span></div>
        <label class="ams-radio">
          <input type="radio" name="${groupName}" value="suggested" checked onchange="amsRowToggle(${i})">
          <span>Vorschlag aus Datei verwenden: <b>${suggestionLabel}</b></span>
        </label>
        <label class="ams-radio">
          <input type="radio" name="${groupName}" value="other" onchange="amsRowToggle(${i})">
          <span>Anderes Material aus dem AMS waehlen:</span>
          <select class="ams-row-select" id="ams-other-${i}" disabled>${otherOptions}</select>
        </label>
      </div>
    </div>`;
}

function amsRowToggle(i){
  const select = document.getElementById('ams-other-' + i);
  const row = select.closest('.ams-row');
  const chosen = row.querySelector(`input[name="ams-choice-${i}"]:checked`).value;
  select.disabled = chosen !== 'other';
}

function closeAmsModal(){
  document.getElementById('amsModal').classList.remove('show');
  document.getElementById('amsModalRows').innerHTML = '';
  resetAmsProgress();
}

function resetAmsProgress(){
  document.getElementById('amsProgressWrap').style.display = 'none';
  document.getElementById('amsProgressBar').style.width = '0%';
  document.getElementById('amsProgressLabel').textContent = '';
  document.getElementById('amsModalConfirmBtn').disabled = false;
  document.getElementById('amsModalConfirmBtn').textContent = 'Drucken starten';
  document.getElementById('amsModalCancelBtn').disabled = false;
}

function setAmsProgress(percent, label){
  document.getElementById('amsProgressWrap').style.display = 'block';
  document.getElementById('amsProgressBar').style.width = Math.max(0, Math.min(100, percent)) + '%';
  document.getElementById('amsProgressLabel').textContent = label;
}

function formatBytes(n){
  if(n >= 1024 * 1024) return (n / (1024 * 1024)).toFixed(1) + ' MB';
  if(n >= 1024) return (n / 1024).toFixed(0) + ' KB';
  return n + ' B';
}

async function cancelAmsModal(){
  const jobId = amsModalJobId;
  const printerId = amsModalPrinterId;
  closeAmsModal();
  amsModalJobId = null;
  amsModalPrinterId = null;
  if(jobId){
    try{ await fetch('/api/printers/' + printerId + '/print/cancel', {
      method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({job_id: jobId})
    }); } catch(e){ /* Aufraeumen ist best-effort */ }
  }
}

async function confirmAmsModal(){
  const jobId = amsModalJobId;
  const printerId = amsModalPrinterId;
  if(!jobId) return;

  const rows = document.querySelectorAll('#amsModalRows .ams-row');
  const mapping = Array.from(rows).map(row => {
    const i = row.dataset.filamentIndex;
    const suggested = parseInt(row.dataset.suggested, 10);
    const chosen = row.querySelector(`input[name="ams-choice-${i}"]:checked`).value;
    if(chosen === 'suggested') return suggested;
    return parseInt(document.getElementById('ams-other-' + i).value, 10);
  });

  const btn = document.getElementById('amsModalConfirmBtn');
  btn.disabled = true;
  btn.textContent = 'Wird gesendet ...';
  document.getElementById('amsModalCancelBtn').disabled = true;
  setAmsProgress(0, 'Wird gestartet ...');

  try{
    const res = await fetch('/api/printers/' + printerId + '/print/confirm', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({job_id: jobId, mapping: mapping})
    });
    const data = await res.json();
    if(!res.ok){
      resetAmsProgress();
      showToast(data.error || 'Fehler beim Starten des Druckauftrags.', 'err');
      return;
    }
    await pollAmsProgress(printerId, jobId);
  } catch(e){
    resetAmsProgress();
    showToast('Netzwerkfehler beim Senden.', 'err');
  }
}

async function pollAmsProgress(printerId, jobId){
  while(amsModalJobId === jobId){
    await new Promise(r => setTimeout(r, 400));
    let data;
    try{
      const res = await fetch('/api/printers/' + printerId + '/print/progress/' + jobId);
      if(!res.ok){
        resetAmsProgress();
        showToast('Fortschritt konnte nicht abgefragt werden.', 'err');
        return;
      }
      data = await res.json();
    } catch(e){
      resetAmsProgress();
      showToast('Netzwerkfehler beim Abfragen des Fortschritts.', 'err');
      return;
    }

    if(data.phase === 'uploading'){
      setAmsProgress(data.percent, `Wird auf den Drucker geladen ... ${data.percent}% (${formatBytes(data.sent)} / ${formatBytes(data.total)})`);
    } else if(data.phase === 'done'){
      setAmsProgress(100, 'Fertig.');
      const ams = data.ams;
      if(ams && ams.total > 0){
        showToast(`Druckauftrag gesendet (${ams.matched}/${ams.total} Filamente AMS-zugeordnet).`, 'ok');
      } else {
        showToast('Druckauftrag gesendet (ohne AMS-Zuordnung).', 'ok');
      }
      amsModalJobId = null;
      amsModalPrinterId = null;
      closeAmsModal();
      return;
    } else if(data.phase === 'error'){
      resetAmsProgress();
      showToast(data.error || 'Fehler beim Senden des Druckauftrags.', 'err');
      // Modal + Job bleiben erhalten - "Drucken starten" erneut moeglich,
      // ohne die Datei nochmal hochladen oder die Zuordnung neu waehlen
      // zu muessen (siehe DashboardApp.start_confirm_print_job()).
      return;
    }
  }
}

// Toasts: unabhaengig vom alle 2,5s neu gerenderten Drucker-Grid, damit
// Erfolgs-/Fehlermeldungen nicht durch refresh() sofort wieder
// verschwinden.
function showToast(message, kind){
  const container = document.getElementById('toastContainer');
  const el = document.createElement('div');
  el.className = 'toast ' + (kind === 'err' ? 'err' : 'ok');
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => el.remove(), 7000);
}

function renderOctoPrintCard(p){
  const online = p.connected;
  const pct = p.progress || 0;
  const remMin = formatRemaining(p.remaining_min);
  return `
    <div class="printer-card">
      <div class="card-head">
        <div>
          <span class="status-dot ${online ? 'online' : ''}"></span>
          <span class="name">${p.name}</span>
          <span class="ip">${p.ip}</span>
          <span class="type-badge">OctoPrint</span>
        </div>
        <div class="head-right">
          <span class="state-badge ${stateClass(p.gcode_state)}">${p.gcode_state || 'UNKNOWN'}</span>
          <div class="cam-icon" title="Kamera anzeigen" onclick="openCam('${p.id}')">${CAM_ICON}</div>
          <div class="del-icon" title="Entfernen" onclick="deletePrinter('${p.id}')">&times;</div>
        </div>
      </div>
      <div class="card-body">
        <div>
          <div class="field-label">Aktuelle Datei</div>
          <div class="file-name">${p.file_name || '-'} ${remMin ? ' &middot; ' + remMin : ''}</div>

          <div class="field-label">Fortschritt</div>
          <div class="progress-row">
            <div class="progress-track"><div class="progress-fill" style="width:${pct}%"></div></div>
            <div class="progress-pct">${pct}%</div>
          </div>

          <div class="field-label">Temperaturen</div>
          <div class="temps">
            <div class="temp-chip">Duese <b>${p.nozzle_temp ?? '–'}&deg;C</b></div>
            <div class="temp-chip">Bett <b>${p.bed_temp ?? '–'}&deg;C</b></div>
          </div>
          ${p.error ? `<div class="error-hint">${p.error}</div>` : ''}
        </div>
        <div>
          <div class="field-label">Hinweis</div>
          <div class="hint-text" style="margin:0;">OctoPrint liefert keine Kammertemperatur / kein AMS-Aequivalent.</div>
        </div>
      </div>
      ${renderExtras(p.id, p.extras)}
    </div>`;
}

function renderCrealityCard(p){
  const online = p.connected;
  const pct = p.progress || 0;
  const label = CREALITY_LABELS[p.type] || 'Creality (Klipper)';
  const hasChamber = (p.chamber_temp !== undefined && p.chamber_temp !== null);
  return `
    <div class="printer-card">
      <div class="card-head">
        <div>
          <span class="status-dot ${online ? 'online' : ''}"></span>
          <span class="name">${p.name}</span>
          <span class="ip">${p.ip}</span>
          <span class="type-badge">${label}</span>
        </div>
        <div class="head-right">
          <span class="state-badge ${stateClass(p.gcode_state)}">${p.gcode_state || 'UNKNOWN'}</span>
          <div class="cam-icon" title="Kamera anzeigen" onclick="openCam('${p.id}')">${CAM_ICON}</div>
          <div class="del-icon" title="Entfernen" onclick="deletePrinter('${p.id}')">&times;</div>
        </div>
      </div>
      <div class="card-body">
        <div>
          <div class="field-label">Aktuelle Datei</div>
          <div class="file-name">${p.file_name || '-'}</div>

          <div class="field-label">Fortschritt</div>
          <div class="progress-row">
            <div class="progress-track"><div class="progress-fill" style="width:${pct}%"></div></div>
            <div class="progress-pct">${pct}%</div>
          </div>

          <div class="field-label">Temperaturen</div>
          <div class="temps">
            ${hasChamber ? `<div class="temp-chip">Kammer <b>${p.chamber_temp}&deg;C</b></div>` : ''}
            <div class="temp-chip">Duese <b>${p.nozzle_temp ?? '–'}&deg;C</b></div>
            <div class="temp-chip">Bett <b>${p.bed_temp ?? '–'}&deg;C</b></div>
          </div>
          ${p.error ? `<div class="error-hint">${p.error}</div>` : ''}
        </div>
        <div>
          <div class="field-label">Hinweis</div>
          <div class="hint-text" style="margin:0;">Ueber Moonraker angebunden. Kammertemperatur nur sichtbar, falls im Klipper-Setup ein entsprechender Sensor konfiguriert ist.</div>
        </div>
      </div>
      ${renderExtras(p.id, p.extras)}
    </div>`;
}

function renderUltimakerCard(p){
  const online = p.connected;
  const pct = p.progress || 0;
  const remMin = formatRemaining(p.remaining_min);
  return `
    <div class="printer-card">
      <div class="card-head">
        <div>
          <span class="status-dot ${online ? 'online' : ''}"></span>
          <span class="name">${p.name}</span>
          <span class="ip">${p.ip}</span>
          <span class="type-badge">Ultimaker</span>
        </div>
        <div class="head-right">
          <span class="state-badge ${stateClass(p.gcode_state)}">${p.gcode_state || 'UNKNOWN'}</span>
          <div class="cam-icon" title="Kamera anzeigen" onclick="openCam('${p.id}')">${CAM_ICON}</div>
          <div class="del-icon" title="Entfernen" onclick="deletePrinter('${p.id}')">&times;</div>
        </div>
      </div>
      <div class="card-body">
        <div>
          <div class="field-label">Aktuelle Datei</div>
          <div class="file-name">${p.file_name || '-'} ${remMin ? ' &middot; ' + remMin : ''}</div>

          <div class="field-label">Fortschritt</div>
          <div class="progress-row">
            <div class="progress-track"><div class="progress-fill" style="width:${pct}%"></div></div>
            <div class="progress-pct">${pct}%</div>
          </div>

          <div class="field-label">Temperaturen</div>
          <div class="temps">
            <div class="temp-chip">Duese <b>${p.nozzle_temp ?? '–'}&deg;C</b></div>
            <div class="temp-chip">Bett <b>${p.bed_temp ?? '–'}&deg;C</b></div>
          </div>
          ${p.error ? `<div class="error-hint">${p.error}</div>` : ''}
        </div>
        <div>
          <div class="field-label">Hinweis</div>
          <div class="hint-text" style="margin:0;">Ultimaker-Desktopdrucker haben keinen Kammertemperatursensor.</div>
        </div>
      </div>
      ${renderExtras(p.id, p.extras)}
    </div>`;
}

function renderFormlabsCard(p){
  const online = p.connected;
  const pct = p.progress || 0;
  const labels = FL_LABELS[p.type] || FL_LABELS.formlabs;
  return `
    <div class="printer-card">
      <div class="card-head">
        <div>
          <span class="status-dot ${online ? 'online' : ''}"></span>
          <span class="name">${p.name}</span>
          <span class="ip">${p.ip}</span>
          <span class="type-badge">${labels.badge}</span>
        </div>
        <div class="head-right">
          <span class="state-badge ${stateClass(p.device_status)}">${p.device_status || 'UNKNOWN'}</span>
          <div class="del-icon" title="Entfernen" onclick="deletePrinter('${p.id}')">&times;</div>
        </div>
      </div>
      <div class="card-body single-col">
        <div>
          <div class="field-label">${labels.file}</div>
          <div class="file-name">${p.file_name || '-'}</div>

          <div class="field-label">Fortschritt</div>
          <div class="progress-row">
            <div class="progress-track"><div class="progress-fill" style="width:${pct}%"></div></div>
            <div class="progress-pct">${pct}%</div>
          </div>

          ${labels.showMaterial ? `
          <div class="field-label">Geladenes Harz / Material</div>
          <div class="file-name">${p.material || '-'}</div>
          ` : ''}

          ${p.error ? `<div class="error-hint">${p.error}</div>` : ''}
        </div>
      </div>
      ${renderExtras(p.id, p.extras)}
    </div>`;
}

async function loadVersion(){
  try{
    const res = await fetch('/api/version');
    const data = await res.json();
    document.getElementById('verBadge').textContent = 'v' + data.version;
  } catch(e){ /* Version ist rein informativ - Fehler hier ignorieren */ }
}

loadVersion();
refresh();
setInterval(refresh, 2500);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    host = dash.cfg["server"].get("host", "0.0.0.0")
    port = int(dash.cfg["server"].get("port", 8000))
    print(f"Dashboard laeuft auf http://{host}:{port}  (im lokalen Netz erreichbar ueber die IP dieses PCs)")
    app.run(host=host, port=port, debug=False, threaded=True)
