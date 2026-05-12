#!/usr/bin/env python3
import os
import sys
import time
import shutil
import logging
import subprocess
import json
from datetime import datetime
from urllib.request import urlopen

# ---------------- CONFIG ----------------

APP_NAME = "Sprit Dashboard"
DASHBOARD_FILE = "sprit.py"
BACKUP_DIR = "/home/pi/sprit_backups"
LOG_FILE = "/home/pi/updater.log"
UPDATE_STATE_FILE = "/home/pi/sprit_update_state.json"

# Basis-URL des GitHub-Repos (Raw Files)
BASE_RAW_URL = "https://raw.githubusercontent.com/antaril/Universal-Spritcomputer-Dashboard/main"

# Dateien, die aktualisiert werden sollen (relativ zum Skriptverzeichnis)
FILES_TO_UPDATE = [
    "sprit.py",
    "updater.py",
    "version.txt",
    "schl-geschlossen.png",
    "schl-offen.png",
]

# Verzeichnis, in dem sprit.py / updater.py liegen
APP_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------

# Logging
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


def log(msg: str) -> None:
    print(msg)
    logging.info(msg)


def run(cmd: str) -> subprocess.CompletedProcess:
    logging.debug(f"CMD: {cmd}")
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


# ---------------- FUNCTIONS ----------------

def check_root() -> None:
    if os.geteuid() != 0:
        log("Updater sollte als root laufen (sudo).")
        sys.exit(1)


def check_internet() -> None:
    log("Prüfe Internetverbindung …")
    result = run("ping -c 1 8.8.8.8")
    if result.returncode != 0:
        log("❌ Keine Internetverbindung")
        sys.exit(1)
    log("✔ Internet OK")


def backup_dashboard() -> None:
    """Legt ein Backup der lokalen Dateien im BACKUP_DIR an."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    for fname in FILES_TO_UPDATE:
        src = os.path.join(APP_DIR, fname)
        if not os.path.exists(src):
            continue
        backup_name = f"{fname}.{timestamp}.bak"
        dst = os.path.join(BACKUP_DIR, backup_name)
        shutil.copy2(src, dst)
        log(f"Backup erstellt: {dst}")


def stop_dashboard() -> None:
    log("Stoppe laufendes Dashboard …")
    run("pkill -f sprit.py")
    time.sleep(1)


def download_file(filename: str) -> None:
    """Lädt eine einzelne Datei von GitHub Raw herunter und speichert sie im APP_DIR."""
    url = f"{BASE_RAW_URL}/{filename}"
    dest = os.path.join(APP_DIR, filename)
    log(f"Lade {filename} von {url} …")
    try:
        with urlopen(url, timeout=10) as resp:
            content = resp.read()
    except Exception as e:
        log(f"❌ Download fehlgeschlagen für {filename}: {e}")
        sys.exit(1)

    # Inhalt (Textdateien) schreiben
    try:
        with open(dest, "wb") as f:
            f.write(content)
        log(f"✔ {filename} aktualisiert")
    except Exception as e:
        log(f"❌ Schreiben von {dest} fehlgeschlagen: {e}")
        sys.exit(1)


def update_from_github() -> None:
    log("Starte Dateibasiertes Update von GitHub (Raw Files) …")
    for fname in FILES_TO_UPDATE:
        download_file(fname)
    log("✔ Alle Dateien wurden von GitHub aktualisiert")


def write_update_state() -> None:
    """Schreibt eine kleine Statusdatei, damit das Dashboard nach Neustart
    weiß, dass gerade ein Update durchgeführt wurde.
    """
    try:
        version_path = os.path.join(APP_DIR, "version.txt")
        new_version = None
        if os.path.exists(version_path):
            with open(version_path, "r", encoding="utf-8") as f:
                new_version = (f.read().strip() or None)

        state = {
            "status": "updated",
            "new_version": new_version,
            "timestamp": datetime.now().isoformat(),
        }
        with open(UPDATE_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
        log(f"Update-State geschrieben: {UPDATE_STATE_FILE} (Version: {new_version})")
    except Exception as e:
        log(f"Update-State konnte nicht geschrieben werden: {e}")


def start_dashboard() -> None:
    log("Starte Dashboard neu …")
    script_path = os.path.join(APP_DIR, DASHBOARD_FILE)
    run(f"python3 {script_path} &")


# ---------------- MAIN ----------------

def main() -> None:
    log("===================================")
    log(f"{APP_NAME} – Updater gestartet")
    log("===================================")

    check_root()
    check_internet()
    backup_dashboard()
    update_from_github()
    write_update_state()

    log("✔ Update abgeschlossen")
    log("===================================")


if __name__ == "__main__":
    main()

