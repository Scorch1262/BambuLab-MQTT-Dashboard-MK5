#!/usr/bin/env python3
"""
Eigenstaendiger FTPS-Upload-Helfer fuer das Bambu-Drucker-Dashboard.

WICHTIG (v1.5.3): Wird vom Hauptprogramm (app.py) als SEPARATE exe
aufgerufen - NICHT per Selbstaufruf mit einem versteckten Sentinel-
Kommandozeilen-Flag, wie es bis v1.5.2 der Fall war. Grund: trotz
identischer Datei und identischem, sauberem Netzwerk (isoliert, nur
Windows Defender) scheiterte der Upload im Dashboard weiterhin
reproduzierbar, waehrend ein eigenstaendiges Diagnose-Tool
(ftps_test_minimal.py) mit praktisch identischer FTPS-Logik zuverlaessig
funktionierte. Der einzige verbleibende strukturelle Unterschied: das
Dashboard rief sich selbst mit einem versteckten Argument
("--ftps-upload-worker") erneut auf - ein Verhaltensmuster (Programm
startet eine Kopie von sich selbst mit einem verstecktem Flag), das
manche Sicherheitssoftware inkl. Windows Defender aehnlich wie manche
Schadsoftware-Lademechanismen behandelt und dessen Netzwerkverkehr
davon negativ beeinflusst werden kann, auch ohne dass etwas sichtbar
blockiert wird. Diese separate, eindeutig benannte Helfer-exe (wird
als eigenstaendige Datei neben der Haupt-exe mitgeliefert, siehe
GitHub-Actions-Workflow) vermeidet dieses Muster komplett - fuer eine
Sicherheitssoftware ist "Programm A startet Programm B" ein voellig
normaler, unauffaelliger Vorgang.

Verwendung (wird vom Dashboard automatisch aufgerufen, nicht fuer den
manuellen Gebrauch gedacht - fuer manuelles Testen siehe stattdessen
ftps_test_minimal.py):
    FtpsUploadHelper.exe <DRUCKER-IP> <ACCESS_CODE> <LOKALE-DATEI> <ZIEL-DATEINAME>

Gibt Fortschritt/Ergebnis als einzelne JSON-Zeilen auf stdout aus:
    {"type": "progress", "sent": N, "total": M}
    {"type": "done", "sent": N, "total": M}
    {"type": "error", "message": "...", "sent": N, "total": M}
"""
import sys
import os
import ssl
import ftplib
import json


class ImplicitFtpTls(ftplib.FTP_TLS):
    """ftplib.FTP_TLS kann von Haus aus nur explizites TLS (AUTH TLS).
    Bambu-Drucker verlangen auf Port 990 IMPLIZITES TLS (die Verbindung
    ist von Anfang an TLS-verschluesselt, kein AUTH-Kommando). Diese
    Subklasse wrappt den Socket direkt beim Verbindungsaufbau.

    WICHTIG (v1.5.8 - Profile statt Einzelschalter): Die X1-Serie
    (X1C/X1E, bestaetigt funktionsfaehig) und der A1 Mini (bestaetigt
    NICHT funktionsfaehig mit den bisherigen Einstellungen) brauchen
    unterschiedliche TLS-Konfigurationen. Ein isolierter Test von nur
    `reuse_session` (v1.5.7) hat gezeigt: Der A1-Mini-Timeout nach 100%
    uebertragener Bytes tritt UNABHAENGIG von `reuse_session` auf (mit
    UND ohne identisch) - die eigentliche Ursache lag also woanders.
    Beim Vergleich mit der zuletzt beim Nutzer bestaetigt funktionierenden
    Version (v1.4.5) fiel auf: dort war KEIN TLS-Versions-Deckel aktiv
    (`ctx.maximum_version` wurde erst spaeter, in v1.5.0 fuer die
    X1-Serie, wieder eingefuehrt) - TLS durfte sich frei aushandeln
    (typischerweise TLS 1.3). Deshalb jetzt zwei vollstaendige,
    benannte Profile statt einzelner Schalter:
      - "x1": TLS gedeckelt auf Version 1.2 + Sitzungs-Wiederverwendung
        fuer die Datenverbindung (X1-Serie/vsftpd braucht beides)
      - "a1": KEIN TLS-Versions-Deckel (freie Aushandlung) + KEINE
        Sitzungs-Wiederverwendung (entspricht dem zuletzt beim A1 Mini
        bestaetigt funktionierenden Verhalten aus v1.4.5)
    Siehe `main()` fuer die Alternierung zwischen beiden Profilen ueber
    die 3 automatischen Upload-Versuche."""

    def __init__(self, *args, reuse_session: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self._sock = None
        self._reuse_session = reuse_session

    @property
    def sock(self):
        return self._sock

    @sock.setter
    def sock(self, value):
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value)
        self._sock = value

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            if self._reuse_session:
                conn = self.context.wrap_socket(
                    conn, server_hostname=self.host, session=self.sock.session
                )
            else:
                conn = self.context.wrap_socket(conn, server_hostname=self.host)
        return conn, size


# Vollstaendige, benannte Verbindungsprofile - siehe Klassen-Docstring
# von ImplicitFtpTls oben fuer die Begruendung. "x1" ist der Standard
# (Default), falls kein Profil explizit angegeben wird.
#
# WICHTIG (v1.6.0): "a1" bekommt zusaetzlich "skip_unwrap": True. Grund:
# der Nutzer bestaetigte, dass eine Uebertragung DERSELBEN Datei per
# Bambu Studio ca. 1-2 Sekunden NACH Erreichen von 100% erfolgreich
# abschliesst - der A1 Mini ist also NICHT langsam (widerlegt die
# Timeout-Theorie aus v1.5.9 endgueltig). Das lenkt den Verdacht auf den
# Schritt zwischen "Datei komplett gesendet" und "Antwort gelesen":
# Pythons `ftplib.FTP.storbinary()` ruft nach der Uebertragung
# automatisch `conn.unwrap()` auf - ein sauberer TLS-Verbindungsabschluss
# der Datenverbindung, bei dem auf ein TLS-close_notify vom Server
# gewartet wird. Vermutung: der A1 Mini sendet die eigentliche "226
# Transfer complete"-Antwort zwar prompt, blockiert aber (oder antwortet
# nicht sauber) auf das formale TLS-close_notify, das unwrap() erwartet -
# waehrend Bambu Studio vermutlich keinen sauberen TLS-Shutdown auf der
# Datenverbindung abwartet, sondern die Verbindung nach der Uebertragung
# einfach zumacht. "skip_unwrap" testet genau das: die Datenverbindung
# wird nach der Uebertragung OHNE formalen TLS-Abschluss geschlossen
# (siehe _storbinary_no_unwrap() unten).
PROFILES = {
    "x1": {"cap_tls12": True, "reuse_session": True, "skip_unwrap": False},
    "a1": {"cap_tls12": False, "reuse_session": False, "skip_unwrap": True},
}


def _build_context(profile: dict):
    ctx = ssl._create_unverified_context()
    ctx.options |= getattr(ssl, "OP_IGNORE_UNEXPECTED_EOF", 0)
    if profile["cap_tls12"]:
        try:
            ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        except (AttributeError, ValueError):
            pass
    return ctx


def _storbinary_no_unwrap(ftp, cmd, fp, blocksize=8192, callback=None):
    """Wie ftplib.FTP.storbinary(), aber bewusst OHNE den abschliessenden
    Aufruf von conn.unwrap() bei TLS-Datenverbindungen - siehe
    ausfuehrliche Begruendung beim PROFILES-Dict oben. Fuer eine normale
    (nicht-TLS) Datenverbindung verhaelt sich diese Funktion identisch
    zu ftplib.FTP.storbinary()."""
    ftp.voidcmd("TYPE I")
    with ftp.transfercmd(cmd) as conn:
        while True:
            buf = fp.read(blocksize)
            if not buf:
                break
            conn.sendall(buf)
            if callback:
                callback(buf)
        # WICHTIG: bewusst KEIN conn.unwrap() hier - das ist der
        # entscheidende Unterschied zu ftplib.FTP.storbinary().
    return ftp.voidresp()


def main():
    if len(sys.argv) not in (5, 6):
        print(json.dumps({
            "type": "error",
            "message": "Falsche Anzahl Argumente (erwartet: IP ACCESS_CODE DATEI ZIELNAME [PROFIL])",
            "sent": 0, "total": 0,
        }))
        sys.exit(1)

    ip, access_code, local_path, remote_name = sys.argv[1:5]
    # 5. (optionales) Argument: Profilname ("x1" oder "a1", siehe
    # PROFILES oben). Standard "x1", falls nicht angegeben (z. B. bei
    # manuellem Aufruf zu Testzwecken).
    profile_name = sys.argv[5] if len(sys.argv) >= 6 and sys.argv[5] in PROFILES else "x1"
    profile = PROFILES[profile_name]

    ctx = _build_context(profile)

    try:
        total_size = os.path.getsize(local_path)
    except OSError as e:
        print(json.dumps({"type": "error", "message": f"Datei nicht lesbar: {e}", "sent": 0, "total": 0}), flush=True)
        sys.exit(1)

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
        ftp = ImplicitFtpTls(context=ctx, reuse_session=profile["reuse_session"])
        # WICHTIG (v1.5.9, seither PRAeZISIERT durch v1.6.0): Timeout auf
        # 120s belassen als defensive Absicherung - der eigentliche Fix
        # fuer den A1-Mini-Fall ist aber "skip_unwrap" oben, NICHT das
        # Zeitlimit selbst (der Nutzer bestaetigte, dass Bambu Studio
        # binnen 1-2s nach 100% fertig ist - der Drucker ist nicht
        # langsam, die v1.5.9-Timeout-Theorie war unvollstaendig).
        ftp.connect(ip, 990, timeout=120)
        ftp.login("bblp", access_code)
        ftp.prot_p()
        ftp.set_pasv(True)
        with open(local_path, "rb") as f:
            if profile["skip_unwrap"]:
                _storbinary_no_unwrap(ftp, f"STOR {remote_name}", f, blocksize=8192, callback=_progress_cb)
            else:
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


if __name__ == "__main__":
    main()
