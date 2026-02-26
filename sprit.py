
#!/usr/bin/env python3
import tkinter as tk
from tkinter import messagebox
from gpiozero import Button, PWMOutputDevice
import threading
import time
import gps
import json
import os
import subprocess
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo
import glob
import logging
import math

# --- Logging Setup ---
logging.basicConfig(
    filename="/home/pi/sprit_dashboard.log",
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logging.info("Dashboard gestartet")

# --- Konstanten ---
FLOW_PIN = 12
K_FACTOR = 40000
fuel_capacity = 20.0
reserve_liters = 5.0

# --- Version & Update ---
VERSION = "1.24"
# URL zu einer Textdatei mit einer Zeile Versionsnummer (z.B. "1.05"). Leer = keine Prüfung.
VERSION_URL = "https://raw.githubusercontent.com/antaril/Universal-Spritcomputer-Dashboard/main/version.txt"
UPDATER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "updater.py")

# --- Pfade ---
TRIP_FILE = "/home/pi/trip_data.json"
CONFIG_FILE = "/home/pi/dashboard_config.json"
TRIP_BACKUP_FILE = "/home/pi/trip_data.bak.json"
TRIP_TMP_FILE = "/home/pi/trip_data.tmp"

# --- Bildschirmhelligkeit (Raspberry Pi DSI / SPI-Display) ---
BACKLIGHT_PATH = None
BACKLIGHT_MAX = 255
BACKLIGHT_PWM = None
BACKLIGHT_PWM_PIN = 18  # GPIO 18 (PWM0) - häufig für SPI-Displays. Falls anders, hier ändern.

def _init_backlight():
    """Findet Backlight: zuerst /sys/class/backlight/, sonst GPIO-PWM (für SPI-Displays)."""
    global BACKLIGHT_PATH, BACKLIGHT_MAX, BACKLIGHT_PWM
    
    # 1. Standard Linux Backlight-Interface (offizielles Pi-Display)
    base = "/sys/class/backlight"
    if os.path.isdir(base):
        for name in os.listdir(base):
            path = os.path.join(base, name)
            max_file = os.path.join(path, "max_brightness")
            bright_file = os.path.join(path, "brightness")
            if os.path.isfile(max_file) and os.path.isfile(bright_file):
                try:
                    with open(max_file, "r") as f:
                        BACKLIGHT_MAX = max(1, int(f.read().strip()))
                    BACKLIGHT_PATH = bright_file
                    logging.info(f"Backlight gefunden (sysfs): {bright_file}, max={BACKLIGHT_MAX}")
                    return
                except (ValueError, OSError):
                    continue
    
    # 2. GPIO-PWM für SPI-Displays (z.B. 3.5" TFT)
    try:
        BACKLIGHT_PWM = PWMOutputDevice(BACKLIGHT_PWM_PIN, frequency=1000, initial_value=0.8)
        BACKLIGHT_MAX = 100  # PWM verwendet 0.0-1.0, wir mappen auf 0-100%
        logging.info(f"Backlight gefunden (GPIO-PWM Pin {BACKLIGHT_PWM_PIN})")
    except Exception as e:
        logging.warning(f"GPIO-PWM Backlight nicht verfügbar (Pin {BACKLIGHT_PWM_PIN}): {e}")

def set_brightness(percent):
    """Helligkeit setzen (0–100). Unterstützt sysfs und GPIO-PWM."""
    percent = max(0, min(100, int(percent)))
    
    if BACKLIGHT_PATH is not None:
        # Sysfs-Interface (offizielles Pi-Display)
        value = int(BACKLIGHT_MAX * percent / 100)
        try:
            with open(BACKLIGHT_PATH, "w") as f:
                f.write(str(value))
        except OSError as e:
            logging.error(f"Helligkeit setzen fehlgeschlagen: {e}")
    elif BACKLIGHT_PWM is not None:
        # GPIO-PWM (SPI-Display)
        try:
            BACKLIGHT_PWM.value = percent / 100.0  # PWM: 0.0-1.0
        except Exception as e:
            logging.error(f"PWM-Helligkeit setzen fehlgeschlagen: {e}")

def get_brightness():
    """Aktuelle Helligkeit in Prozent (0–100) lesen. Bei Fehler None."""
    if BACKLIGHT_PATH is not None:
        try:
            with open(BACKLIGHT_PATH, "r") as f:
                val = int(f.read().strip())
            return max(0, min(100, int(100 * val / BACKLIGHT_MAX)))
        except (ValueError, OSError):
            return None
    elif BACKLIGHT_PWM is not None:
        try:
            return int(BACKLIGHT_PWM.value * 100)
        except Exception:
            return None
    return None


# --- Globale Variablen ---
pulse_count = 0
flow_rate = 0.0
speed = 0.0
sat_seen = 0
sat_used = 0
gps_fix = False
temp_celsius = 0.0
temp_oil_celsius = None
volt_value = None
current_l100 = 0.0

last_temp_celsius_value = None
last_temp_celsius_time = None
last_temp_oil_value = None
last_temp_oil_time = None
last_display_oil_temp = None
last_display_oil_trend_symbol = ""

trip_liters = 0.0
trip_distance = 0.0
avg_consumption = 0.0
fuel_liters = fuel_capacity + reserve_liters
avg_speed = 0.0

trip_liters_tag = 0.0
trip_distance_tag = 0.0
avg_consumption_tag = 0.0
avg_speed_tag = 0.0
trip_time = 0.0        # Sekunden (Trip)
trip_time_tag = 0.0    # Sekunden (Tag)

consumption_values = []
consumption_values_tag = []
speed_values = []
speed_values_tag = []

date_tag = datetime.now(ZoneInfo("Europe/Berlin")).strftime("%Y-%m-%d")

lock = threading.Lock()
toggle_display_counter = 0
last_toggle_time = 0


last_gps_lat = None
last_gps_lon = None
current_lat = None
current_lon = None
gps_was_lost = False

# --- Config Setup ---
default_config = {
    "show_speed": True,
    "show_avg_speed": True,
    "show_l100": True,
    "show_avg": True,
    "show_lh": True,
    "show_trip": True,
    "show_temp": True,
    "show_volt": True,
    "show_sat": True,
    "show_trip_time": True,
    "show_day_time": True,
    "temp_swapped": False,
    "night_mode": False,
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            cfg = json.load(open(CONFIG_FILE, "r", encoding="utf-8"))
            for k, v in default_config.items():
                if k not in cfg:
                    cfg[k] = v
            return cfg
        except Exception:
            return default_config.copy()
    return default_config.copy()

def save_config():
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f)

def check_latest_version():
    """Liest die neueste Version von VERSION_URL. Rückgabe: Versionsstring oder None."""
    if not (VERSION_URL and VERSION_URL.strip()):
        return None
    try:
        req = urllib.request.Request(VERSION_URL, headers={"User-Agent": "SpritDashboard/1.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            version = r.read().decode("utf-8", errors="ignore").strip()
            return version if version else None
    except Exception as e:
        logging.debug(f"Versionsprüfung fehlgeschlagen: {e}")
        return None

def run_update():
    """Startet den Updater (sudo) und bietet danach einen Neustart-Button an."""
    if not os.path.isfile(UPDATER_SCRIPT):
        messagebox.showerror("Update", f"Updater nicht gefunden:\n{UPDATER_SCRIPT}")
        return
    try:
        proc = subprocess.Popen(
            ["sudo", "python3", UPDATER_SCRIPT],
            cwd=os.path.dirname(UPDATER_SCRIPT),
            start_new_session=True,
        )

        messagebox.showinfo("Update", "Update wurde gestartet.\nBitte warte, bis es abgeschlossen ist.")

        def wait_for_update():
            ret = proc.wait()

            def show_result():
                if ret == 0:
                    # Eigenes kleines Fenster mit „Restart Skript“-Button
                    theme = get_theme_colors()
                    win = tk.Toplevel(root)
                    win.title("Update")
                    win.configure(bg=theme["bg_panel"])

                    msg = tk.Label(
                        win,
                        text="Update erfolgreich abgeschlossen.\nDu kannst das Skript jetzt neu starten.",
                        bg=theme["bg_panel"],
                        fg=theme["fg_text"],
                        font=("Arial", 11),
                        padx=10,
                        pady=10,
                        justify="left",
                    )
                    msg.pack(fill="both", expand=True)

                    btn_frame = tk.Frame(win, bg=theme["bg_panel"])
                    btn_frame.pack(pady=(0, 10))

                    def do_restart():
                        save_data()
                        save_config()
                        root.destroy()
                        os._exit(0)

                    restart_btn = tk.Button(
                        btn_frame,
                        text="Restart Skript",
                        font=("Arial", 11, "bold"),
                        bg="green",
                        fg="white",
                        activebackground="green",
                        activeforeground="white",
                        command=do_restart,
                    )
                    restart_btn.pack(side="left", padx=5)

                    close_btn = tk.Button(
                        btn_frame,
                        text="Später",
                        font=("Arial", 10),
                        command=win.destroy,
                    )
                    close_btn.pack(side="left", padx=5)
                else:
                    messagebox.showerror(
                        "Update",
                        "Update ist fehlgeschlagen.\nBitte Logdatei auf dem Raspberry prüfen.",
                    )

            try:
                root.after(0, show_result)
            except tk.TclError:
                # Fenster evtl. schon geschlossen
                pass

        threading.Thread(target=wait_for_update, daemon=True).start()
    except Exception as e:
        messagebox.showerror("Update", f"Updater starten fehlgeschlagen:\n{e}")
        logging.error(f"Update starten: {e}")

config = load_config()

# --- Sensor Setup ---
def pulse_callback():
    global pulse_count
    with lock:
        pulse_count += 1

flow_sensor = Button(FLOW_PIN, pull_up=True)
flow_sensor.when_pressed = pulse_callback

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0  # km
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# --- GPS Thread ---
def read_gps():
    global speed, sat_seen, sat_used, gps_fix
    try:
        session = gps.gps(mode=gps.WATCH_ENABLE | gps.WATCH_NEWSTYLE)
    except Exception as e:
        logging.error(f"GPS Modul nicht gefunden: {e}")
        return

    while True:
        lat, lon = None, None
        try:
            report = session.next()
            cls = report.get("class", None) if isinstance(report, dict) else getattr(report, "class", None)
            if cls == "TPV":
                mode = report.get("mode", 1)
                gps_fix = mode >= 2
                lat = report.get("lat", None)
                lon = report.get("lon", None)

            if gps_fix and lat is not None and lon is not None:
                global current_lat, current_lon, speed
                spd = report.get("speed", None)
                if spd is not None:
                    speed = spd * 3.6
                current_lat = lat
                current_lon = lon

            if cls == "SKY":
                sats = report.get("satellites", [])
                sat_seen = len(sats)
                sat_used = len([s for s in sats if s.get("used", False)])
        except StopIteration:
            continue
        except Exception as e:
            logging.error(f"GPS-Fehler: {e}")
            time.sleep(1)

# --- Dateioperationen ---
def save_data():
    data = {
        "trip_liters": trip_liters,
        "trip_distance": trip_distance,
        "fuel_liters": fuel_liters,
        "consumption_values": consumption_values,
        "speed_values": speed_values,
        "trip_liters_tag": trip_liters_tag,
        "trip_distance_tag": trip_distance_tag,
        "consumption_values_tag": consumption_values_tag,
        "speed_values_tag": speed_values_tag,
        "date": date_tag,
        "trip_time": trip_time,
        "trip_time_tag": trip_time_tag
    }

    try:
        # 1. in temp-Datei schreiben
        with open(TRIP_TMP_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)

        # 2. alte Hauptdatei als Backup sichern
        if os.path.exists(TRIP_FILE):
            os.replace(TRIP_FILE, TRIP_BACKUP_FILE)

        # 3. temp -> Hauptdatei (atomar)
        os.replace(TRIP_TMP_FILE, TRIP_FILE)

    except Exception as e:
        logging.error(f"Fehler beim sicheren Speichern: {e}")


def load_data():
    global trip_liters, trip_distance, fuel_liters
    global trip_liters_tag, trip_distance_tag
    global consumption_values, consumption_values_tag
    global speed_values, speed_values_tag, date_tag
    global trip_time, trip_time_tag

    def load_from(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def is_empty(data):
        """True, wenn die gelesenen Daten leer/ungültig sind."""
        if not isinstance(data, dict):
            return True
        return len(data) == 0

    main_data = None
    backup_data = None

    # Hauptdatei einlesen
    try:
        if os.path.exists(TRIP_FILE):
            main_data = load_from(TRIP_FILE)
    except Exception as e:
        logging.error(f"Hauptdatei defekt: {e}")

    # Backup einlesen
    try:
        if os.path.exists(TRIP_BACKUP_FILE):
            backup_data = load_from(TRIP_BACKUP_FILE)
    except Exception as e:
        logging.error(f"Backup defekt: {e}")

    chosen_data = None
    loaded_from_backup = False

    # Fall 1: gar keine gültigen Daten
    if main_data is None and backup_data is None:
        logging.error("Keine gueltigen Daten gefunden, Reset")
        reset_trip()
        return

    # Fall 2: Hauptdatei und Backup vorhanden
    if main_data is not None and backup_data is not None:
        problem = is_empty(main_data) or main_data != backup_data
        if problem:
            try:
                use_backup = messagebox.askyesno(
                    "Datenfehler erkannt",
                    "Datenfehler erkannt, Backup laden?\nJa = Backup verwenden, Nein = aktuelle Daten behalten/zurücksetzen.",
                )
            except Exception as e:
                logging.error(f"Fehler beim Anzeigen des Datenfehler-Dialogs: {e}")
                use_backup = True

            if use_backup:
                chosen_data = backup_data
                loaded_from_backup = True
            else:
                # Wenn Hauptdatei leer ist und der Nutzer 'Nein' sagt, lieber komplett zurücksetzen
                if is_empty(main_data):
                    reset_trip()
                    return
                chosen_data = main_data
        else:
            chosen_data = main_data

    # Fall 3: nur Hauptdatei vorhanden
    elif main_data is not None:
        if is_empty(main_data):
            if backup_data is not None and not is_empty(backup_data):
                try:
                    use_backup = messagebox.askyesno(
                        "Datenfehler erkannt",
                        "Datenfehler erkannt, Backup laden?\nJa = Backup verwenden, Nein = Daten zurücksetzen.",
                    )
                except Exception as e:
                    logging.error(f"Fehler beim Anzeigen des Datenfehler-Dialogs: {e}")
                    use_backup = True

                if use_backup:
                    chosen_data = backup_data
                    loaded_from_backup = True
                else:
                    reset_trip()
                    return
            else:
                logging.error("Hauptdatei leer und kein gueltiges Backup vorhanden, Reset")
                reset_trip()
                return
        else:
            chosen_data = main_data

    # Fall 4: nur Backup vorhanden
    else:
        try:
            use_backup = messagebox.askyesno(
                "Datenfehler erkannt",
                "Datenfehler erkannt, Backup laden?\nJa = Backup verwenden, Nein = Daten zurücksetzen.",
            )
        except Exception as e:
            logging.error(f"Fehler beim Anzeigen des Datenfehler-Dialogs: {e}")
            use_backup = True

        if use_backup:
            chosen_data = backup_data
            loaded_from_backup = True
        else:
            reset_trip()
            return

    if chosen_data is None:
        logging.error("Kein Datensatz ausgewaehlt, Reset")
        reset_trip()
        return

    data = chosen_data

    # Daten übernehmen
    trip_liters = data.get("trip_liters", 0.0)
    trip_distance = data.get("trip_distance", 0.0)
    fuel_liters = data.get("fuel_liters", fuel_capacity + reserve_liters)
    consumption_values = data.get("consumption_values", [])
    speed_values = data.get("speed_values", [])
    trip_liters_tag = data.get("trip_liters_tag", 0.0)
    trip_distance_tag = data.get("trip_distance_tag", 0.0)
    consumption_values_tag = data.get("consumption_values_tag", [])
    speed_values_tag = data.get("speed_values_tag", [])
    date_tag = data.get("date", date_tag)
    trip_time = data.get("trip_time", 0.0)
    trip_time_tag = data.get("trip_time_tag", 0.0)

    # Wenn aus Backup geladen: Hauptdatei sofort reparieren (gültige Daten zurückschreiben)
    if loaded_from_backup:
        try:
            save_data()
            logging.info("Hauptdatei aus Backup wiederhergestellt")
        except Exception as e:
            logging.error(f"Reparatur der Hauptdatei fehlgeschlagen: {e}")


# --- Temperatur DS18B20 (Aussen + Öl) ---
def _read_one_temp(device_file):
    """Liest einen DS18B20-Sensor, gibt Temperatur oder None zurück."""
    try:
        with open(device_file, 'r') as f:
            lines = f.readlines()
        if lines[0].strip()[-3:] == 'YES':
            equals_pos = lines[1].find('t=')
            if equals_pos != -1:
                return float(lines[1][equals_pos+2:]) / 1000.0
    except Exception:
        pass
    return None

def read_temp():
    global temp_celsius, temp_oil_celsius
    global last_temp_celsius_value, last_temp_celsius_time
    global last_temp_oil_value, last_temp_oil_time
    base_dir = '/sys/bus/w1/devices/'
    try:
        device_folders = sorted(glob.glob(base_dir + '28-*'))
        if not device_folders:
            temp_celsius = None
            temp_oil_celsius = None
            return
        # Erster Sensor = Aussen
        val1 = _read_one_temp(device_folders[0] + '/w1_slave')
        if val1 is not None:
            temp_celsius = val1
            last_temp_celsius_value = val1
            last_temp_celsius_time = time.time()
        else:
            temp_celsius = None
        # Zweiter Sensor = Öl (falls vorhanden)
        if len(device_folders) >= 2:
            val2 = _read_one_temp(device_folders[1] + '/w1_slave')
            if val2 is not None:
                temp_oil_celsius = val2
                last_temp_oil_value = val2
                last_temp_oil_time = time.time()
            else:
                temp_oil_celsius = None
        else:
            temp_oil_celsius = None
    except Exception as e:
        temp_celsius = None
        temp_oil_celsius = None
        logging.error(f"Fehler beim Lesen der Temperatur: {e}")

# --- INA219 Setup ---
try:
    import board
    import busio
    from adafruit_ina219 import INA219
    i2c = busio.I2C(board.SCL, board.SDA)
    ina = INA219(i2c)
except Exception:
    ina = None

# --- GUI Setup ---
root = tk.Tk()
root.title("Sprit Dashboard")
root.attributes("-fullscreen", True)
root.bind("<Escape>", lambda e: on_exit())
root.config(cursor="none")

def get_theme_colors():
    """Liefert die aktuell aktiven Farbcodes für Tag-/Nachtmodus."""
    if config.get("night_mode", False):
        return {
            # Hintergrund bleibt immer schwarz (blendarm)
            "bg_root": "black",
            "bg_panel": "black",
            "bg_side": "black",
            "fg_text": "#E0E6FF",
            "fg_accent": "#7FC8FF",
            "fg_warning": "#FFC857",
            "fg_danger": "#FF6B6B",
            "fg_muted": "#A0A4B8",
            # Spezielle Farben für Anzeigen im Nachtmodus
            "fg_speed": "#7FC8FF",
            "fg_consumption": "#9BE36C",
            "fg_volt": "#FFC857",
            # Tankanzeige
            "fuel_main_high": "#2E8B57",   # gedämpftes Grün
            "fuel_main_mid": "#D9A800",    # dunkleres Gelb
            "fuel_main_low": "#8B0000",    # dunkles Rot
            "fuel_reserve": "#8B4513",     # braun für Reserve
        }
    else:
        return {
            # Hintergrund bleibt immer schwarz (blendarm)
            "bg_root": "black",
            "bg_panel": "black",
            "bg_side": "black",
            "fg_text": "white",
            "fg_accent": "deepskyblue",
            "fg_warning": "yellow",
            "fg_danger": "red",
            "fg_muted": "white",
            # Spezielle Farben für Anzeigen im Tagmodus
            "fg_speed": "cyan",
            "fg_consumption": "lime",
            "fg_volt": "orange",
            # Tankanzeige
            "fuel_main_high": "green",
            "fuel_main_mid": "yellow",
            "fuel_main_low": "red",
            "fuel_reserve": "orange",
        }

theme = get_theme_colors()

root.configure(bg=theme["bg_root"])

frame_dashboard = tk.Frame(root, bg=theme["bg_panel"])
frame_dashboard.pack(expand=True, fill="both")

version_label = tk.Label(frame_dashboard, text=f"V{VERSION}", font=("Arial", 10), fg=theme["fg_text"], bg=theme["bg_panel"])
version_label.place(x=5, y=5)

# --- Reset Funktionen ---
def reset_trip():
    global trip_liters, trip_distance, avg_consumption, fuel_liters
    global consumption_values, speed_values
    global trip_time

    trip_liters = 0.0
    trip_distance = 0.0
    avg_consumption = 0.0
    fuel_liters = fuel_capacity + reserve_liters
    consumption_values = []
    speed_values = []
    trip_time = 0.0

    save_data()
    draw_fuel_bar(fuel_liters, avg_consumption)

def day_reset():
    global trip_liters_tag, trip_distance_tag, avg_consumption_tag, avg_speed_tag
    global consumption_values_tag, speed_values_tag, date_tag
    global trip_time_tag

    trip_liters_tag = 0.0
    trip_distance_tag = 0.0
    avg_consumption_tag = 0.0
    avg_speed_tag = 0.0
    consumption_values_tag = []
    speed_values_tag = []
    trip_time_tag = 0.0
    date_tag = datetime.now(ZoneInfo("Europe/Berlin")).strftime("%Y-%m-%d")

    save_data()
    draw_fuel_bar(fuel_liters, avg_consumption)

def on_exit():
    save_data()
    save_config()
    root.destroy()

# --- Frames ---
left_frame = tk.Frame(frame_dashboard, bg=theme["bg_side"])
left_frame.pack(side="left", fill="y")
center_frame = tk.Frame(frame_dashboard, bg=theme["bg_panel"])
center_frame.pack(side="left", expand=True)
center_frame.grid_columnconfigure(0, weight=1)
right_frame = tk.Frame(frame_dashboard, bg=theme["bg_root"])
right_frame.pack(side="right", fill="y")

# --- Buttons ---
top_frame = tk.Frame(left_frame, bg=theme["bg_side"])
top_frame.pack(side="top", fill="x", pady=5)
middle_frame = tk.Frame(left_frame, bg=theme["bg_side"])
middle_frame.pack(expand=True)
bottom_frame = tk.Frame(left_frame, bg=theme["bg_side"])
bottom_frame.pack(side="bottom", fill="x", pady=5)

btn_day_reset = tk.Button(
    top_frame,
    text="Dayreset",
    font=("Arial", 12, "bold"),
    bg="orange",
    fg="black",
    activebackground="orange",
    activeforeground="black",
    highlightthickness=0,
    command=day_reset,
)
btn_day_reset.pack(pady=5, padx=5)

def toggle_block(key, var):
    config[key] = var.get()
    save_config()
    update_visibility()

def open_config():
    cfg_win = tk.Toplevel(root)
    cfg_win.title("Dashboard Config")

    theme = get_theme_colors()

    cfg_win.configure(bg=theme["bg_panel"])

    # Zwei Spalten nebeneinander
    left_col = tk.Frame(cfg_win, padx=10, pady=5, bg=theme["bg_panel"])
    left_col.pack(side="left", fill="y")
    right_col = tk.Frame(cfg_win, padx=10, pady=5, bg=theme["bg_panel"])
    right_col.pack(side="left", fill="y")

    # --- Links: Checkboxen ---
    for key in default_config.keys():
        if not isinstance(default_config[key], bool):
            continue
        var = tk.BooleanVar(value=config.get(key, default_config[key]))
        cb = tk.Checkbutton(
            left_col,
            text=key.replace("show_", "").replace("_", " ").title(),
            variable=var,
            command=lambda k=key, v=var: toggle_block(k, v),
            bg=theme["bg_panel"],
            fg=theme["fg_text"],
            selectcolor=theme["bg_side"],
            activebackground=theme["bg_panel"],
            activeforeground=theme["fg_text"],
        )
        cb.pack(anchor="w")

    # --- Rechts: Version, Update, Helligkeit, Nachtmodus ---
    version_info_label = tk.Label(
        right_col,
        text=f"Aktuell: V{VERSION}  |  Neueste: …",
        font=("Arial", 10),
        bg=theme["bg_panel"],
        fg=theme["fg_text"],
    )
    version_info_label.pack(anchor="w")

    def fetch_version_in_thread():
        latest = check_latest_version()
        def update():
            if latest is None:
                version_info_label.config(text=f"Aktuell: V{VERSION}  |  Neueste: —")
            else:
                version_info_label.config(text=f"Aktuell: V{VERSION}  |  Neueste: {latest}")
        try:
            cfg_win.after(0, update)
        except tk.TclError:
            pass

    threading.Thread(target=fetch_version_in_thread, daemon=True).start()

    btn_update = tk.Button(
        right_col,
        text="Jetzt aktualisieren",
        font=("Arial", 11, "bold"),
        bg="green",
        fg="white",
        activebackground="green",
        activeforeground="white",
        command=run_update,
    )
    btn_update.pack(pady=5)

    # Nachtmodus Toggle
    night_var = tk.BooleanVar(value=config.get("night_mode", False))

    def on_night_mode_toggle():
        config["night_mode"] = night_var.get()
        save_config()
        apply_theme()

    night_cb = tk.Checkbutton(
        right_col,
        text="Nachtmodus",
        variable=night_var,
        command=on_night_mode_toggle,
        font=("Arial", 10, "bold"),
        bg=theme["bg_panel"],
        fg=theme["fg_text"],
        selectcolor=theme["bg_side"],
        activebackground=theme["bg_panel"],
        activeforeground=theme["fg_text"],
    )
    night_cb.pack(anchor="w", pady=(8, 2))

    # Helligkeitseinstellung wurde entfernt, da sie hardwareseitig nicht zuverlässig funktioniert.

btn_config = tk.Button(
    middle_frame,
    text="Config",
    font=("Arial", 12, "bold"),
    bg="blue",
    fg="white",
    activebackground="blue",
    activeforeground="white",
    highlightthickness=0,
    command=open_config,
)
btn_config.pack(pady=5, padx=5)

btn_reset_trip = tk.Button(
    bottom_frame,
    text="Reset",
    font=("Arial", 12, "bold"),
    bg="red",
    fg="white",
    activebackground="red",
    activeforeground="white",
    highlightthickness=0,
    command=reset_trip,
)
btn_reset_trip.pack(pady=5, padx=5)

sat_label = tk.Label(bottom_frame, text="Sat : 0/0", font=("Arial", 14), fg=theme["fg_warning"], bg=theme["bg_side"])
sat_label.pack(pady=5, padx=5)

# --- Labels im center_frame ---
speed_label = tk.Label(center_frame, text="Speed: -- km/h", font=("Arial", 22), fg=theme["fg_speed"], bg=theme["bg_panel"])
speed_label.grid(row=0, column=0, pady=2)

avg_speed_label = tk.Label(center_frame, text="Ø km/h: -- / --", font=("Arial", 16), fg=theme["fg_text"], bg=theme["bg_panel"])
avg_speed_label.grid(row=1, column=0, pady=2)

l100_label = tk.Label(center_frame, text="Verbrauch: -- l/100km", font=("Arial", 16), fg=theme["fg_consumption"], bg=theme["bg_panel"])
l100_label.grid(row=2, column=0, pady=2)

avg_label = tk.Label(center_frame, text="Ø: -- / -- l/100km", font=("Arial", 16), fg=theme["fg_consumption"], bg=theme["bg_panel"])
avg_label.grid(row=3, column=0, pady=2)

# --- l/h + Volt ---
lh_volt_frame = tk.Frame(center_frame, bg=theme["bg_panel"])
lh_volt_frame.grid(row=4, column=0, pady=2)

lh_label = tk.Label(lh_volt_frame, text="l/h: 0.00", font=("Arial", 16), fg=theme["fg_consumption"], bg=theme["bg_panel"])
lh_label.pack(side="left", padx=10)

volt_label = tk.Label(lh_volt_frame, text="Volt: --", font=("Arial", 16), fg=theme["fg_volt"], bg=theme["bg_panel"])
volt_label.pack(side="left", padx=10)

# --- Trip-Zeit ---
trip_time_frame = tk.Frame(center_frame, bg=theme["bg_panel"])
trip_time_frame.grid(row=5, column=0, pady=2)

trip_time_label = tk.Label(trip_time_frame, text="Trip Time: 00:00 | 00:00", font=("Arial", 16), fg=theme["fg_text"], bg=theme["bg_panel"])
trip_time_label.pack(side="left", padx=10)

# --- Weitere Labels ---
distance_label = tk.Label(center_frame, text="Trip km: 0.00 / 0.00", font=("Arial", 16), fg=theme["fg_text"], bg=theme["bg_panel"])
distance_label.grid(row=6, column=0, pady=2)

def on_temp_click():
    """Klick auf Temperaturzeile: Anzeige Aussen ↔ Öl tauschen (falls Sensoren vertauscht erkannt)."""
    config["temp_swapped"] = not config.get("temp_swapped", False)
    save_config()

temp_label = tk.Button(
    center_frame, text="Aussen: --  / Öl: --",
    font=("Arial", 15), fg=theme["fg_accent"], bg=theme["bg_panel"],
    relief="flat", bd=0, highlightthickness=0,
    activebackground=theme["bg_panel"], activeforeground=theme["fg_accent"],
    command=on_temp_click
)
temp_label.grid(row=7, column=0, pady=2)

# --- Fuel Canvas ---
date_label = tk.Label(right_frame, text="", font=("Arial", 14), fg=theme["fg_text"], bg=theme["bg_root"])
date_label.pack(anchor="ne", pady=(5, 0), padx=5)
time_label = tk.Label(right_frame, text="", font=("Arial", 14), fg=theme["fg_text"], bg=theme["bg_root"])
time_label.pack(anchor="ne", pady=(0, 5), padx=5)
fuel_canvas = tk.Canvas(right_frame, width=60, bg=theme["bg_root"], highlightthickness=0)
fuel_canvas.pack(fill="y", expand=True)

def draw_fuel_bar(level, avg_consumption):
    global toggle_display_counter, last_toggle_time
    theme = get_theme_colors()

    now = time.time()
    if now - last_toggle_time >= 5:   # alle 5 Sekunden umschalten
        toggle_display_counter += 1
        last_toggle_time = now

    fuel_canvas.delete("all")

    width = fuel_canvas.winfo_width()
    height = fuel_canvas.winfo_height()
    if width < 2 or height < 2:
        fuel_canvas.after(100, lambda: draw_fuel_bar(level, avg_consumption))
        return

    total_capacity = fuel_capacity + reserve_liters
    level = max(0, min(level, total_capacity))
    total_height = int(height * (level / total_capacity))
    reserve_height = int(height * (reserve_liters / total_capacity))
    main_height = max(0, total_height - reserve_height)
    y0_main = height - total_height
    y0_reserve = height - reserve_height

    color_reserve = theme.get("fuel_reserve", "orange")
    if level > reserve_liters:
        percent_main = (level - reserve_liters) / fuel_capacity * 100
        if percent_main > 60:
            color_main = theme.get("fuel_main_high", "green")
        elif percent_main > 10:
            color_main = theme.get("fuel_main_mid", "yellow")
        else:
            color_main = theme.get("fuel_main_low", "red")
    else:
        color_main = theme.get("fuel_reserve", "orange")

    # Textfarbe: im Nachtmodus immer weiches Textweiß, im Tagmodus auf gelbem Bereich schwarz
    if config.get("night_mode", False):
        text_color = theme["fg_text"]
    else:
        # Tagmodus: bei mittlerem Füllstand (gelb) besser schwarzer Text
        text_color = "white" if color_main != "yellow" else "black"

    if level > reserve_liters:
        fuel_canvas.create_rectangle(0, y0_main, width, y0_reserve, fill=color_main, outline="white")
    fuel_canvas.create_rectangle(0, y0_reserve, width, height, fill=color_reserve, outline="white")


    if toggle_display_counter % 2 == 0:
        text_main = f"{max(level - reserve_liters, 0):.1f} l"
        text_reserve = f"{min(reserve_liters, level):.1f} l"
    else:
        usable = max(level - reserve_liters, 0)
        range_main = (usable / avg_consumption * 100) if avg_consumption > 0 else 0
        range_reserve = (min(reserve_liters, level) / avg_consumption * 100) if avg_consumption > 0 else 0
        text_main = f"{int(range_main)} km"
        text_reserve = f"{int(range_reserve)} km"

    fuel_canvas.create_text(width // 2, y0_main + main_height // 2,
                            text=text_main, fill=text_color, font=("Arial", 12, "bold"))
    fuel_canvas.create_text(width // 2, y0_reserve + reserve_height // 2,
                            text=text_reserve, fill=text_color, font=("Arial", 12, "bold"))

def on_fuel_click(event=None):
    global fuel_liters
    fuel_liters -= 2.5
    if fuel_liters <= 0:
        fuel_liters = fuel_capacity + reserve_liters
    draw_fuel_bar(fuel_liters, avg_consumption)
    save_data()

fuel_canvas.bind("<Button-1>", on_fuel_click)
fuel_canvas.bind("<Configure>", lambda e: draw_fuel_bar(fuel_liters, avg_consumption))

# --- Uhrzeit ---
def update_time():
    now = datetime.now(ZoneInfo("Europe/Berlin"))
    date_label.config(text=now.strftime("%d.%m.%y"))
    time_label.config(text=now.strftime("%H:%M:%S"))
    root.after(1000, update_time)

# --- Dashboard Update ---
def update_visibility():
    grid_widgets = [
        ("show_speed", speed_label),
        ("show_avg_speed", avg_speed_label),
        ("show_l100", l100_label),
        ("show_avg", avg_label),
        ("show_trip", distance_label),
        ("show_temp", temp_label),
    ]

    pack_widgets = [
        ("show_lh", lh_volt_frame),
        ("show_trip_time", trip_time_frame),
    ]

    # grid-Widgets
    for key, widget in grid_widgets:
        if config.get(key, True):
            widget.grid()
        else:
            widget.grid_remove()

    # grid-Frames (Combo-Frames!)
    for key, widget in pack_widgets:
        if config.get(key, True):
            widget.grid()
        else:
            widget.grid_remove()

    # Sat-Anzeige links (pack)
    if config.get("show_sat", True):
        sat_label.pack(pady=5, padx=5)
    else:
        sat_label.pack_forget()


def format_time(seconds):
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h:02d}:{m:02d}"

def update_gui():
    try:
        _update_gui_impl()
    except tk.TclError:
        pass  # Fenster bereits geschlossen

def _update_gui_impl():
    speed_label.config(text=f"Speed: {speed:.1f} km/h" if gps_fix else "Speed: -- km/h")
    avg_speed_label.config(text=f"Ø km/h: {avg_speed:.1f} / {avg_speed_tag:.1f}")
    l100_label.config(text=f"Verbrauch: {current_l100:.2f} l/100km")
    avg_label.config(text=f"Ø: {avg_consumption:.2f} / {avg_consumption_tag:.2f} l/100km")

    lh_label.config(text=f"l/h: {flow_rate:.2f}")

    trip_time_label.config(
        text=f"Trip Time: {format_time(trip_time)} | {format_time(trip_time_tag)}"
    )

    global last_temp_celsius_value, last_temp_celsius_time
    global last_temp_oil_value, last_temp_oil_time
    global last_display_oil_temp, last_display_oil_trend_symbol

    distance_label.config(text=f"Trip km: {trip_distance:.2f} / {trip_distance_tag:.2f}")

    now_ts = time.time()
    max_age = 15.0

    # Letzte gültige Temperaturen verwenden, solange sie nicht älter als max_age sind
    disp_temp1 = None
    if last_temp_celsius_value is not None and last_temp_celsius_time is not None:
        if now_ts - last_temp_celsius_time <= max_age:
            disp_temp1 = last_temp_celsius_value

    disp_temp2 = None
    if last_temp_oil_value is not None and last_temp_oil_time is not None:
        if now_ts - last_temp_oil_time <= max_age:
            disp_temp2 = last_temp_oil_value

    # Sensor 1 = erster 28-*, Sensor 2 = zweiter 28-*; bei temp_swapped Anzeige tauschen
    if config.get("temp_swapped", False):
        aussen_val, oil_val = disp_temp2, disp_temp1
    else:
        aussen_val, oil_val = disp_temp1, disp_temp2

    aussen_str = f"{aussen_val:.1f}°C" if aussen_val is not None else "--"

    # Trend und Anzeige für Öltemperatur
    oil_str = "--"
    if oil_val is not None:
        oil_str = f"{oil_val:.1f}°C"
        if last_display_oil_temp is not None:
            delta = oil_val - last_display_oil_temp
            if abs(delta) < 0.2:
                last_display_oil_trend_symbol = ""
            elif delta > 0:
                last_display_oil_trend_symbol = " ↑"
            else:
                last_display_oil_trend_symbol = " ↓"
        last_display_oil_temp = oil_val
    else:
        last_display_oil_temp = None
        last_display_oil_trend_symbol = ""

    oil_display = oil_str + last_display_oil_trend_symbol

    # Farbgebung je nach Öltemperatur
    theme = get_theme_colors()
    oil_color = theme["fg_accent"]
    if oil_val is not None:
        if oil_val >= 110:
            oil_color = "red"
        elif oil_val >= 100:
            oil_color = "yellow"

    temp_label.config(
        text=f"Aussen: {aussen_str}  / Öl: {oil_display}",
        fg=oil_color,
        activeforeground=oil_color,
        bg=theme["bg_panel"],
        activebackground=theme["bg_panel"],
    )

    volt_label.config(text=f"Volt: {volt_value:.2f} V" if volt_value is not None else "Volt: --")
    sat_label.config(text=f"Sat : {sat_used}/{sat_seen}")
    draw_fuel_bar(fuel_liters, avg_consumption)

def update_dashboard():
    global pulse_count, flow_rate, speed, gps_fix
    global trip_liters, trip_distance, avg_consumption, fuel_liters
    global avg_speed, consumption_values, speed_values
    global trip_liters_tag, trip_distance_tag, avg_consumption_tag, avg_speed_tag
    global consumption_values_tag, speed_values_tag
    global trip_time, trip_time_tag
    global volt_value
    global current_l100
    global last_gps_lat, last_gps_lon, current_lat, current_lon, gps_was_lost

    last_time = time.time()
    last_pulse = 0
    save_counter = 0

    while True:
        time.sleep(1)
        read_temp()
        now = time.time()
        dt = now - last_time
        if dt <= 0:
            dt = 1.0
        last_time = now

        with lock:
            pulses = pulse_count - last_pulse
            last_pulse = pulse_count

        liters = pulses / K_FACTOR
        flow_rate = liters * 3600 / dt

        # --- GPS Distanzberechnung und Tripzeit ---
        if gps_fix:
            trip_time += dt
            trip_time_tag += dt

            if gps_was_lost:
                # Strecke nachholen: von letzter bekannter Position vor Ausfall
                if last_gps_lat is not None and last_gps_lon is not None and current_lat is not None and current_lon is not None:
                    dist = haversine(last_gps_lat, last_gps_lon, current_lat, current_lon)
                    dist = min(dist, 5.0)  # Ausreißer verhindern
                    trip_distance += dist
                    trip_distance_tag += dist
                gps_was_lost = False

            # Normale Distanz über Geschwindigkeit
            if speed > 1.0:
                trip_distance += speed * dt / 3600
                trip_distance_tag += speed * dt / 3600

            # Letzte GPS-Position speichern
            last_gps_lat = current_lat
            last_gps_lon = current_lon

        else:
            # GPS fehlt → markieren, dass GPS verloren ging
            gps_was_lost = True

        # --- Verbrauch pro 100 km (nur bei sinnvoller Fahrt) ---
        if speed >= 8 and trip_distance >= 0.1:
            current_l100 = (flow_rate / speed) * 100

            # harte Ausreißer ignorieren
            if 0 < current_l100 < 50:
                consumption_values.append(current_l100)
                if len(consumption_values) > 300:
                    consumption_values.pop(0)

                consumption_values_tag.append(current_l100)
                if len(consumption_values_tag) > 300:
                    consumption_values_tag.pop(0)
        else:
            current_l100 = 0

        # --- Trip-Liter erhöhen ---
        trip_liters += liters
        trip_liters_tag += liters
        fuel_liters -= liters
        fuel_liters = max(0, fuel_liters)


        # --- Durchschnitt aus realem Verbrauch berechnen ---
        if trip_distance >= 1.0:
            avg_consumption = (trip_liters / trip_distance) * 100
        else:
            avg_consumption = 0

        if trip_distance_tag >= 1.0:
            avg_consumption_tag = (trip_liters_tag / trip_distance_tag) * 100
        else:
            avg_consumption_tag = 0

        # Geschwindigkeit für Durchschnitt (nur bei Fahrt)
        if speed >= 8:
            speed_values.append(speed)
            if len(speed_values) > 300:
                speed_values.pop(0)

            speed_values_tag.append(speed)
            if len(speed_values_tag) > 300:
                speed_values_tag.pop(0)

        # Durchschnitt berechnen
        avg_speed = sum(speed_values) / len(speed_values) if speed_values else 0
        avg_speed_tag = sum(speed_values_tag) / len(speed_values_tag) if speed_values_tag else 0





        # --- INA219 Spannung ---
        if ina is not None:
            try:
                volt_value = ina.bus_voltage
            except Exception as e:
                volt_value = None
                logging.error(f"INA219 Fehler: {e}")
        else:
            volt_value = None

        root.after(0, update_gui)

        save_counter += 1
        if save_counter >= 10:
            save_data()
            save_counter = 0

# --- Start ---
_init_backlight()
load_data()
draw_fuel_bar(fuel_liters, avg_consumption)
update_visibility()
update_time()

def apply_theme():
    """Aktuelles Farbschema auf Hauptfenster anwenden."""
    theme = get_theme_colors()
    root.configure(bg=theme["bg_root"])
    frame_dashboard.configure(bg=theme["bg_panel"])
    left_frame.configure(bg=theme["bg_side"])
    center_frame.configure(bg=theme["bg_panel"])
    right_frame.configure(bg=theme["bg_root"])
    version_label.configure(bg=theme["bg_panel"], fg=theme["fg_text"])
    date_label.configure(bg=theme["bg_root"], fg=theme["fg_text"])
    time_label.configure(bg=theme["bg_root"], fg=theme["fg_text"])
    fuel_canvas.configure(bg=theme["bg_root"])
    # Haupt-Labels anpassen
    sat_label.configure(bg=theme["bg_side"], fg=theme["fg_warning"])
    speed_label.configure(bg=theme["bg_panel"], fg=theme["fg_speed"])
    avg_speed_label.configure(bg=theme["bg_panel"], fg=theme["fg_text"])
    l100_label.configure(bg=theme["bg_panel"], fg=theme["fg_consumption"])
    avg_label.configure(bg=theme["bg_panel"], fg=theme["fg_consumption"])
    lh_volt_frame.configure(bg=theme["bg_panel"])
    lh_label.configure(bg=theme["bg_panel"], fg=theme["fg_consumption"])
    volt_label.configure(bg=theme["bg_panel"], fg=theme["fg_volt"])
    trip_time_frame.configure(bg=theme["bg_panel"])
    trip_time_label.configure(bg=theme["bg_panel"], fg=theme["fg_text"])
    distance_label.configure(bg=theme["bg_panel"], fg=theme["fg_text"])
    temp_label.configure(bg=theme["bg_panel"], activebackground=theme["bg_panel"])

    # Buttons je nach Modus etwas abdunkeln, ohne Hintergrund zu ändern
    if config.get("night_mode", False):
        btn_day_reset.configure(
            bg="#8B5A00",
            fg="white",
            activebackground="#8B5A00",
            activeforeground="white",
        )
        btn_config.configure(
            bg="#1E3A5F",
            fg="white",
            activebackground="#1E3A5F",
            activeforeground="white",
        )
        btn_reset_trip.configure(
            bg="#7A1E1E",
            fg="white",
            activebackground="#7A1E1E",
            activeforeground="white",
        )
    else:
        btn_day_reset.configure(
            bg="orange",
            fg="black",
            activebackground="orange",
            activeforeground="black",
        )
        btn_config.configure(
            bg="blue",
            fg="white",
            activebackground="blue",
            activeforeground="white",
        )
        btn_reset_trip.configure(
            bg="red",
            fg="white",
            activebackground="red",
            activeforeground="white",
        )

apply_theme()

threading.Thread(target=read_gps, daemon=True).start()
threading.Thread(target=update_dashboard, daemon=True).start()
root.mainloop()
