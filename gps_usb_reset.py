#!/usr/bin/env python3
import os
import time

# USB-Hub, an dem der GPS-Stick hÃ¤ngt
USB_HUB_ID = "1-1"

def reset_gps_usb():
    try:
        print("GPS USB Hub wird deaktiviert...")
        os.system(f"sudo sh -c 'echo {USB_HUB_ID} > /sys/bus/usb/drivers/usb/unbind'")
        time.sleep(2)  # 2 Sekunden warten
        print("GPS USB Hub wird wieder aktiviert...")
        os.system(f"sudo sh -c 'echo {USB_HUB_ID} > /sys/bus/usb/drivers/usb/bind'")
        print("USB-Reset abgeschlossen.")
    except Exception as e:
        print("Fehler beim USB-Reset:", e)

if __name__ == "__main__":
    reset_gps_usb()
