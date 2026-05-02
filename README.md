# PyPortal CTA Bus Tracker + Weather Rotation

A CircuitPython project for the **Adafruit PyPortal** that displays live **Chicago CTA bus arrival predictions** with a compact weather dashboard. The tracker is designed as an always-on glanceable screen: CTA arrivals stay visible at the top, while local Chicago weather and rotating world weather details appear below.

The current version is moving into **v0.3.0**, which adds a rotating world weather panel while preserving Chicago as the always-visible home weather location.

---

## What this application does

- Connects the PyPortal to Wi-Fi using the ESP32SPI co-processor.
- Calls the **CTA Bus Tracker API** for live bus arrival predictions.
- Displays the next arrivals for a configured CTA stop and selected routes.
- Shows route number, destination, minutes until arrival, and local clock arrival time.
- Keeps CTA arrivals as the main display priority.
- Shows Chicago/home weather beneath the CTA arrivals.
- Rotates extra weather details such as feels-like temperature, humidity, rain chance, wind, sunrise/sunset, and forecast panels.
- Adds a rotating **world weather** panel that shows one random city from a configured list.
- Automatically refreshes CTA data every 30 seconds.
- Refreshes weather less frequently to reduce API calls and protect device stability.
- Includes Wi-Fi/network recovery logic for ESP32SPI hiccups.
- Prints helpful debugging information to the Serial console in Mu.

---

## Display layout

The screen is organized to keep the bus tracker useful at a glance:

```text
Stop Name (NB/SB)
Updated 8:42 AM  Next in 23s  /
------------------------------
1   Destination       3   8:45am
4   Destination       7   8:49am
X4  Destination       9   8:51am
...
Chicago: 61F Cloudy / Hi 67 - Lo 49
Rain 20% / Wind 8mph W
```

The second weather line rotates through Chicago details, forecast panels, sunrise/sunset, and one random world city.

Example rotating weather details:

```text
Feels 59F / Hum 72%
Rain 20% / Wind 8mph W
Sat 63/48 Sun 65/51
Tokyo: 74F Clear
Rise 6:02am Set 7:42pm
```

---

## Hardware

- Adafruit **PyPortal**
- USB data cable
- Wi-Fi network
- Computer with Mu, Thonny, or another serial-capable editor

The project was built around the PyPortal using CircuitPython and ESP32SPI networking.

---

## Software requirements

- CircuitPython installed on the PyPortal
- Required Adafruit CircuitPython libraries copied to `CIRCUITPY/lib`
- CTA Bus Tracker API key
- OpenWeatherMap API key

Required libraries include:

- `adafruit_requests.mpy`
- `adafruit_connection_manager.mpy`
- `adafruit_display_text/`
- `adafruit_bus_device/`
- `adafruit_esp32spi/`
- `adafruit_ntp.mpy` optional, if using the NTP time-sync helper

---

## Setup

1. Copy `code.py` to the root of the `CIRCUITPY` drive.
2. Create a `secrets.py` file on the root of `CIRCUITPY`.
3. Add your Wi-Fi, CTA API key, OpenWeatherMap API key, and home weather city.
4. Open Mu and use the **Serial** console to watch startup logs.
5. Confirm the PyPortal connects to Wi-Fi and begins showing CTA arrivals.

---

## `secrets.py` example

Create a file named `secrets.py` in the same folder as `code.py`:

```python
secrets = {
    "ssid": "YOUR_WIFI_NAME",
    "password": "YOUR_WIFI_PASSWORD",
    "cta_api_key": "YOUR_CTA_BUS_TRACKER_API_KEY",
    "openweather_api_key": "YOUR_OPENWEATHERMAP_API_KEY",
    "weather_city": "Chicago"
}
```

Do **not** commit `secrets.py` to GitHub.

---

## Main configuration values

These values can be edited near the top of `code.py`:

| Setting | Purpose |
| --- | --- |
| `STOP_ID` | CTA stop ID to monitor |
| `ROUTES` | CTA routes to display, such as `['1', '4', 'X4']` |
| `MAX_RESULTS` | Maximum number of arrival rows shown |
| `REFRESH_SECONDS` | CTA refresh interval |
| `WEATHER_REFRESH_SECONDS` | Weather API refresh interval |
| `WEATHER_DETAIL_ROTATE_SECONDS` | How often the second weather line rotates |
| `WORLD_WEATHER_CITIES` | List of global cities used for random weather rotation |

Example world weather city list:

```python
WORLD_WEATHER_CITIES = [
    "Lexington,KY,US",
    "New York,US",
    "Los Angeles,US",
    "Miami,US",
    "Rio de Janeiro,BR",
    "London,GB",
    "Tokyo,JP",
    "Toronto,CA",
    "Atlanta,US",
    "Juneau,AK,US",
    "Paris,FR",
]
```

---

## Keeping secrets out of Git

Add this to `.gitignore`:

```text
secrets.py
```

This prevents Wi-Fi passwords and API keys from being uploaded to GitHub.

---

## Network and reliability notes

The PyPortal uses an ESP32 co-processor for Wi-Fi. Long-running CircuitPython network projects can occasionally hit socket, memory, or ESP32SPI issues. This project includes recovery logic that:

- Retries Wi-Fi connection.
- Rebuilds the network stack after repeated request failures.
- Resets the device after too many rebuild attempts.
- Handles common `OutOfRetries`, `OSError`, `TimeoutError`, `RuntimeError`, and `MemoryError` situations.

Weather calls are intentionally less frequent than CTA calls to reduce API load and lower memory pressure.

---

## Notes

CTA predictions come from the CTA Bus Tracker API. Arrival data depends on CTA service availability and may occasionally be empty or delayed.

Weather data comes from OpenWeatherMap. City names should be formatted clearly, especially for world cities or cities with shared names.

---

## Version history

### v0.3.0

- Added rotating world weather city panel.
- Preserved Chicago/home weather as the always-visible weather line.
- Added one randomly selected world city to the weather detail rotation.
- Kept CTA arrivals as the primary screen feature.
- Continued using slower weather refreshes to protect device stability.

### v0.2.x

- Added robust weather display.
- Added current weather details below CTA arrivals.
- Added rotating weather detail line.
- Added forecast-style panels.
- Added weather refresh timing separate from CTA refresh timing.

### v0.0.5

- Added local clock arrival time next to bus arrival minutes.

### v0.0.4

- Improved crash handling and network stability.

### v0.0.2

- Added on-screen refresh countdown and spinner.
- Added last refresh timestamp in 12-hour format.
- Added route direction such as NB/SB.
- Improved Wi-Fi recovery and stability.
- Added Serial logging for debugging.
- Added HTTP-based time sync with DST support.

### v0.0.1

- Initial working PyPortal CTA display.
- Added Serial logging.
- Added basic auto-reconnect behavior.
