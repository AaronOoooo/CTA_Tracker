# CTA_Tracker# PyPortal CTA Bus Tracker (Chicago)

This project runs on an **Adafruit PyPortal** and displays live **CTA Bus Tracker** arrival predictions for a nearby stop (example: *Indiana & 31st Street, Stop ID 1563*). It refreshes automatically and also prints useful debugging output to the **Serial console** (Mu).

## What it does
- Connects to your Wi-Fi using the PyPortal’s ESP32 co-processor (ESP32SPI)
- Calls the CTA Bus Tracker API and pulls prediction data
- Displays the next arrivals on the PyPortal screen
- Logs status + arrivals + errors to Serial
- If Wi-Fi / ESP32SPI errors occur, it resets the Wi-Fi connection and retries

## Hardware
- Adafruit **PyPortal** (original / Titano should work as long as ESP32SPI networking is available)
- USB cable that supports data

## CircuitPython + Libraries
This project assumes you’re running CircuitPython on the PyPortal and have the required Adafruit libraries copied to `CIRCUITPY/lib`.

Required libraries:
- `adafruit_requests.mpy`
- `adafruit_connection_manager.mpy`
- `adafruit_display_text/`
- `adafruit_bus_device/`
- `adafruit_esp32spi/`

## Setup
1. Copy `code.py` to the root of your `CIRCUITPY` drive.
2. Create a `secrets.py` file on the root of `CIRCUITPY` (same folder as `code.py`).
3. Add your Wi-Fi and CTA API key to `secrets.py` (template below).
4. Open Mu → **Serial** to view logs while it runs.

## secrets.py
Create a file named `secrets.py` with the following content:

```python
secrets = {
    "ssid": "YOUR_WIFI_NAME",
    "password": "YOUR_WIFI_PASSWORD",
    "cta_api_key": "YOUR_CTA_BUS_TRACKER_API_KEY"
}

Important: Do not commit secrets.py to GitHub. See “Keeping secrets out of Git” below.

Configuration (in code.py)

Edit these values near the top of code.py if needed:

STOP_ID – CTA stop ID (example: 1563)

ROUTES – routes to filter (example: ["1", "4", "X4"])

REFRESH_SECONDS – refresh interval

MAX_RESULTS – number of arrivals displayed

Keeping secrets out of Git

Add secrets.py to your .gitignore so it never gets committed.

Notes

CTA data comes from the official CTA Bus Tracker API. Predictions depend on service availability and may be empty at certain times.

Version

v0.0.1 — initial working version (PyPortal display + Serial logging + auto-reconnect)

