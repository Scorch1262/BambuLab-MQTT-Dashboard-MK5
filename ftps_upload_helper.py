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

    WICHTIG (v1.5.4 - Korrektur): Frueher wurde hier auf ein
    `ntransfercmd()`-Override verzichtet, in der irrigen Annahme, Pythons
    eingebautes `ftplib.FTP_TLS.ntransfercmd()` wuerde die TLS-Sitzung
    der Kontrollverbindung automatisch fuer die Datenverbindung
    wiederverwenden. Das stimmt nicht (verifiziert per
    `inspect.getsource(ftplib.FTP_TLS.ntransfercmd)`: die eingebaute
    Methode uebergibt nur `server_hostname`, kein `session=...`). Die
    X1-Serie laeuft auf vsftpd mit `require_ssl_reuse` und verlangt
    genau diese Sitzungs-Wiederverwendung. Ohne das Override bricht die
    Datenverbindung reproduzierbar bei ca. 11% ab - das Override wird
    deshalb wieder ergaenzt (siehe UEBERGABE.md fuer die volle
    Chronologie)."""

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
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(
                conn, server_hostname=self.host, session=self.sock.session
            )
        return conn, size


def main():
    if len(sys.argv) != 5:
        print(json.dumps({
            "type": "error",
            "message": "Falsche Anzahl Argumente (erwartet: IP ACCESS_CODE DATEI ZIELNAME)",
            "sent": 0, "total": 0,
        }))
        sys.exit(1)

    ip, access_code, local_path, remote_name = sys.argv[1:5]

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


if __name__ == "__main__":
    main()
