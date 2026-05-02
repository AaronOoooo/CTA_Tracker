import time
import random
import board
import busio
import digitalio
import displayio
import terminalio
from adafruit_display_text import label
import rtc

import adafruit_connection_manager
import adafruit_requests
import adafruit_esp32spi.adafruit_esp32spi as esp32
from adafruit_requests import OutOfRetries

from secrets import secrets

import gc
import microcontroller

# =========================
# CONFIG
# =========================
STOP_ID = "1563"
ROUTES = ["1", "4", "X4"]
MAX_RESULTS = 5

REFRESH_SECONDS = 30          # CTA API refresh interval
UI_TICK_SECONDS = 1           # countdown/spinner tick
HOST = "www.ctabustracker.com"

WEATHER_REFRESH_SECONDS = 30 * 60  # OpenWeatherMap refresh interval
WEATHER_DETAIL_ROTATE_SECONDS = 30    # rotate extra weather details
FORECAST_DAYS = 4                     # number of forecast days to show

# Home weather always stays visible on line 1.
# The cities below are used as occasional "world weather" panels on line 2.
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


DEST_WIDTH = 16             # width for destination column

SPINNER_FRAMES = ["|", "/", "-", "\\"]  # safe in terminal font

DEBUG_SCREEN = False  # True only if you want to mirror the screen to Serial

TIME_SYNC_RETRY_INTERVAL = 15 * 60  # retry every 15 minutes until it works
_last_time_sync_attempt = 0

DEBUG_ROWS = False  # True only if you want to print arrival rows to Serial

MAX_REBUILDS_BEFORE_RESET = 3
rebuild_count = 0

# =========================
# DISPLAY SETUP (Option A)
# =========================
display = board.DISPLAY
group = displayio.Group()
display.root_group = group

# Line 1: Stop name + (NB/SB)
line_stop = label.Label(terminalio.FONT, text="(starting...)", x=8, y=14)
group.append(line_stop)

# Line 2: Updated time + Next in + spinner
line_status = label.Label(terminalio.FONT, text="Updated --:--  Next in --s  |", x=8, y=32)
group.append(line_status)

# Divider
divider = label.Label(terminalio.FONT, text="-" * 30, x=8, y=44)
group.append(divider)

# Arrivals
rows = []
y0 = 60
for i in range(MAX_RESULTS):
    r = label.Label(terminalio.FONT, text="", x=8, y=y0 + (i * 18))
    rows.append(r)
    group.append(r)

# Weather area below CTA arrivals
# Line 1 always shows home/current conditions.
# Line 2 rotates home weather details plus one world city panel.
line_weather_1 = label.Label(terminalio.FONT, text="Weather: --", x=8, y=158)
group.append(line_weather_1)

line_weather_2 = label.Label(terminalio.FONT, text="Loading weather...", x=8, y=176)
group.append(line_weather_2)

def set_rows(lines):
    for i in range(MAX_RESULTS):
        rows[i].text = lines[i] if i < len(lines) else ""

    # SERIAL MIRROR
    if DEBUG_ROWS:
        print("[SCREEN] Rows:")
        for line in lines:
            print("  " + line)

def gc_sweep(tag=""):
    gc.collect()

# =========================
# ESP32 NETWORK SETUP
# =========================
esp32_cs = digitalio.DigitalInOut(board.ESP_CS)
esp32_ready = digitalio.DigitalInOut(board.ESP_BUSY)
esp32_reset = digitalio.DigitalInOut(board.ESP_RESET)
spi = busio.SPI(board.SCK, board.MOSI, board.MISO)

esp = esp32.ESP_SPIcontrol(spi, esp32_cs, esp32_ready, esp32_reset)
pool = adafruit_connection_manager.get_radio_socketpool(esp)
ssl_context = adafruit_connection_manager.get_radio_ssl_context(esp)
requests = adafruit_requests.Session(pool, ssl_context)

def rebuild_network():
    global requests, pool, ssl_context, esp, time_ready, rebuild_count

    print("Rebuilding network stack...")

    rebuild_count += 1
    print(f"Rebuild count: {rebuild_count}/{MAX_REBUILDS_BEFORE_RESET}")

    if rebuild_count >= MAX_REBUILDS_BEFORE_RESET:
        print("Too many rebuilds; forcing device reset.")
        time.sleep(1)
        microcontroller.reset()

    try:
        esp.reset()
        time.sleep(1)
    except Exception as e:
        print("ESP reset error:", repr(e))

    # Reconnect WiFi
    connect_wifi()

    # Free old network objects to reduce heap fragmentation before rebuilding
    drop_network_objects()
    
    # Recreate socket pool + requests session
    pool = adafruit_connection_manager.get_radio_socketpool(esp)
    ssl_context = adafruit_connection_manager.get_radio_ssl_context(esp)
    requests = adafruit_requests.Session(pool, ssl_context)

    print("Network rebuild complete.")

# =========================
# TIME HELPERS
# =========================
def try_set_time_via_http():
    """
    Sets RTC using WorldTimeAPI over HTTP.
    Includes DST automatically for America/Chicago.
    Returns True if time was set, False otherwise.
    """
    url = "http://worldtimeapi.org/api/timezone/America/Chicago"
    try:
        # Don't print the whole URL every time; keep serial clean
        print("Time sync (HTTP): requesting WorldTimeAPI...")
        with requests.get(url) as r:
            data = r.json()

        unixtime = int(data["unixtime"])
        raw_offset = int(data.get("raw_offset", 0))
        dst_offset = int(data.get("dst_offset", 0))

        local_epoch = unixtime + raw_offset + dst_offset
        t = time.localtime(local_epoch)

        rtc.RTC().datetime = t
        print("Time sync OK:", t)
        return True

    except Exception as e:
        print("Time sync failed:", repr(e))
        return False

def try_set_time_via_ntp():
    """
    Try to set the board RTC from NTP.
    Returns True if time was set, False otherwise.
    """
    try:
        import rtc
        import adafruit_ntp
    except ImportError as e:
        print("NTP: missing library:", e)
        return False

    # Try a couple of known servers. (Some networks block one but not another.)
    servers = [
        "0.adafruit.pool.ntp.org",
        "1.adafruit.pool.ntp.org",
        "time.cloudflare.com",
    ]

    for server in servers:
        try:
            print("NTP: trying", server)
            ntp = adafruit_ntp.NTP(
                pool,
                server=server,
                tz_offset=-6,        # CST standard; note DST caveat below
                socket_timeout=2,    # shorter timeout can be more reliable on some setups
                cache_seconds=3600
            )
            rtc.RTC().datetime = ntp.datetime
            print("NTP: time set OK:", time.localtime())
            return True
        except OSError as e:
            print("NTP failed on", server, ":", repr(e))
        except Exception as e:
            print("NTP unexpected error on", server, ":", repr(e))

    return False


def format_time_12h(tstruct):
    # tstruct = time.localtime()
    hour = tstruct.tm_hour
    minute = tstruct.tm_min
    ampm = "AM"
    if hour >= 12:
        ampm = "PM"
    hour12 = hour % 12
    if hour12 == 0:
        hour12 = 12
    return "{}:{:02d} {}".format(hour12, minute, ampm)


# =========================
# CTA HELPERS
# =========================
def connect_wifi():
    print("Resetting ESP / connecting WiFi...")
    esp.reset()
    time.sleep(1)

    while not esp.is_connected:
        try:
            esp.connect_AP(secrets["ssid"], secrets["password"])
        except (RuntimeError, ConnectionError, TimeoutError, OSError) as e:
            print("WiFi retry:", repr(e))
            time.sleep(2)

    print("WiFi connected.")


def build_url():
    # Use HTTP (less load on ESP32SPI than HTTPS for this endpoint)
    return (
        "http://{}/bustime/api/v3/getpredictions"
        "?key={}&stpid={}&rt={}&top={}&format=json"
    ).format(
        HOST,
        secrets["cta_api_key"],
        STOP_ID,
        ",".join(ROUTES),
        str(MAX_RESULTS),
    )

def weather_city_for_url(city):
    # Basic URL-safe city text for simple names like "Chicago" or "New York"
    return str(city).replace(" ", "%20")

def weather_city_display_name(city):
    # Turn "Lexington,KY,US" into "Lexington" for the small screen.
    return str(city).split(",")[0]

def fetch_world_weather_panel(api_key):
    """
    Fetches current weather for one random world city.
    Returns a compact panel string like 'Tokyo: 74F Clear'.
    If anything fails, returns None so home weather is not affected.
    """
    if not WORLD_WEATHER_CITIES:
        return None

    city = random.choice(WORLD_WEATHER_CITIES)
    url = (
        "http://api.openweathermap.org/data/2.5/weather"
        "?q={}&appid={}&units=imperial"
    ).format(weather_city_for_url(city), api_key)

    try:
        print("GET world weather current (url hidden)")
        with requests.get(url) as r:
            data = r.json()

        main = data.get("main", {})
        weather_list = data.get("weather", [])

        temp = temp_to_int(main.get("temp", 0))

        desc = "Weather"
        if weather_list and isinstance(weather_list, list):
            desc = str(weather_list[0].get("description", "Weather"))
            if desc:
                desc = desc[0].upper() + desc[1:]

        panel = "{}: {}F {}".format(weather_city_display_name(city), temp, desc[:12])

        try:
            del data
            del main
            del weather_list
        except Exception:
            pass

        return panel

    except Exception as e:
        print("World weather fetch error:", repr(e))
        return None


def temp_to_int(value):
    try:
        return int(float(value) + 0.5)
    except Exception:
        return 0

def set_weather_unavailable():
    city = secrets.get("weather_city", "Chicago")
    line_weather_1.text = "Weather: " + str(city)[:20]
    line_weather_2.text = "Weather unavailable"

def shorten_text(s, max_len):
    s = str(s)
    if len(s) <= max_len:
        return s
    if max_len <= 3:
        return s[:max_len]
    return s[:max_len - 3] + "..."


def deg_to_compass(deg):
    try:
        deg = float(deg)
    except Exception:
        return ""
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    ix = int((deg + 22.5) / 45) % 8
    return dirs[ix]


def format_epoch_time_12h(epoch_value):
    try:
        t = time.localtime(int(epoch_value))
        return format_clock_12h_lower(t.tm_hour, t.tm_min)
    except Exception:
        return "--:--"


def day_name_from_epoch(epoch_value):
    try:
        t = time.localtime(int(epoch_value))
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        return names[t.tm_wday]
    except Exception:
        return "Day"


def make_forecast_panels(items):
    """
    Builds two compact 4-day forecast panels from OpenWeatherMap 3-hour forecast data.
    Chooses one daytime-ish sample per date when possible.
    """
    daily = []
    seen_dates = []
    preferred_hours = [12, 15, 9, 18]

    for target_hour in preferred_hours:
        for item in items:
            try:
                dt_txt = str(item.get("dt_txt", ""))
                date_part = dt_txt[:10]
                hour_part = int(dt_txt[11:13]) if len(dt_txt) >= 13 else -1
                if not date_part or date_part in seen_dates or hour_part != target_hour:
                    continue

                main = item.get("main", {})
                temp_hi = temp_to_int(main.get("temp_max", main.get("temp", 0)))
                temp_lo = temp_to_int(main.get("temp_min", main.get("temp", 0)))
                name = day_name_from_epoch(item.get("dt", 0))
                daily.append((date_part, name, temp_hi, temp_lo))
                seen_dates.append(date_part)

                if len(daily) >= FORECAST_DAYS:
                    break
            except Exception:
                pass

        if len(daily) >= FORECAST_DAYS:
            break

    panels = []
    if len(daily) >= 2:
        panels.append("{} {}/{} {} {}/{}".format(daily[0][1], daily[0][2], daily[0][3], daily[1][1], daily[1][2], daily[1][3]))
    if len(daily) >= 4:
        panels.append("{} {}/{} {} {}/{}".format(daily[2][1], daily[2][2], daily[2][3], daily[3][1], daily[3][2], daily[3][3]))
    elif len(daily) >= 3:
        panels.append("{} {}/{}".format(daily[2][1], daily[2][2], daily[2][3]))

    return panels


def fetch_weather():
    """
    Fetches current weather and 4-day forecast from OpenWeatherMap.
    Keeps current conditions always visible and rebuilds the rotating detail panels.
    Returns True if current weather succeeds, False if not.
    """
    global weather_detail_panels, weather_detail_index, last_weather_detail_rotate

    city = secrets.get("weather_city", "Chicago")
    api_key = secrets.get("openweather_api_key", "")

    if not api_key:
        print("Weather: missing openweather_api_key in secrets.py")
        set_weather_unavailable()
        weather_detail_panels = ["Weather unavailable"]
        return False

    current_url = (
        "http://api.openweathermap.org/data/2.5/weather"
        "?q={}&appid={}&units=imperial"
    ).format(weather_city_for_url(city), api_key)

    forecast_url = (
        "http://api.openweathermap.org/data/2.5/forecast"
        "?q={}&appid={}&units=imperial"
    ).format(weather_city_for_url(city), api_key)

    print("GET weather current (url hidden)")

    try:
        with requests.get(current_url) as r:
            data = r.json()

        main = data.get("main", {})
        weather_list = data.get("weather", [])
        wind = data.get("wind", {})
        sys_data = data.get("sys", {})

        temp = temp_to_int(main.get("temp", 0))
        hi = temp_to_int(main.get("temp_max", 0))
        lo = temp_to_int(main.get("temp_min", 0))
        feels = temp_to_int(main.get("feels_like", temp))
        humidity = int(main.get("humidity", 0))

        desc = "Weather"
        if weather_list and isinstance(weather_list, list):
            desc = str(weather_list[0].get("description", "Weather"))
            if desc:
                desc = desc[0].upper() + desc[1:]

        wind_speed = temp_to_int(wind.get("speed", 0))
        wind_dir = deg_to_compass(wind.get("deg", ""))

        sunrise = format_epoch_time_12h(sys_data.get("sunrise", 0))
        sunset = format_epoch_time_12h(sys_data.get("sunset", 0))

        # Always-visible weather line.
        line_weather_1.text = "{}F {} / Hi {} - Lo {}".format(temp, desc[:9], hi, lo)

        new_panels = []
        new_panels.append("Rain --% / Wind {}mph {}".format(wind_speed, wind_dir))
        new_panels.append("Feels {}F / Hum {}%".format(feels, humidity))

        world_panel = fetch_world_weather_panel(api_key)
        if world_panel:
            new_panels.append(world_panel)

        new_panels.append("Rise {} Set {}".format(sunrise, sunset))

        try:
            del data
            del main
            del weather_list
            del wind
            del sys_data
        except Exception:
            pass

        print("GET weather forecast (url hidden)")

        try:
            with requests.get(forecast_url) as r:
                fdata = r.json()

            items = fdata.get("list", [])

            if isinstance(items, list) and items:
                # OpenWeatherMap forecast POP is 0.0 to 1.0.
                # Use the highest chance in the next 24 hours.
                max_pop = 0
                for item in items[:8]:
                    try:
                        pop = int(float(item.get("pop", 0)) * 100)
                        if pop > max_pop:
                            max_pop = pop
                    except Exception:
                        pass

                new_panels[0] = "Rain {}% / Wind {}mph {}".format(max_pop, wind_speed, wind_dir)

                forecast_panels = make_forecast_panels(items)
                if forecast_panels:
                    # Keep rain + feels first, then forecast panels, then any remaining panels
                    # such as world weather and sunrise/sunset.
                    new_panels = [new_panels[0], new_panels[1]] + forecast_panels + new_panels[2:]

            try:
                del fdata
                del items
            except Exception:
                pass

        except Exception as e:
            print("Weather forecast fetch error:", repr(e))
            # Current weather still displays even if the forecast request fails.

        weather_detail_panels = new_panels
        weather_detail_index = 0
        last_weather_detail_rotate = time.monotonic()
        line_weather_2.text = shorten_text(weather_detail_panels[0], 30)

        gc_sweep("after weather")
        return True

    except MemoryError as e:
        print("MemoryError during weather fetch:", repr(e))
        print("Forcing device reset to recover memory.")
        time.sleep(1)
        microcontroller.reset()

    except Exception as e:
        print("Weather fetch error:", repr(e))
        set_weather_unavailable()
        weather_detail_panels = ["Weather unavailable"]
        weather_detail_index = 0
        return False


def rotate_weather_detail_if_needed():
    global weather_detail_index, last_weather_detail_rotate

    if not weather_detail_panels:
        return

    if (time.monotonic() - last_weather_detail_rotate) >= WEATHER_DETAIL_ROTATE_SECONDS:
        weather_detail_index += 1
        if weather_detail_index >= len(weather_detail_panels):
            weather_detail_index = 0

        line_weather_2.text = shorten_text(weather_detail_panels[weather_detail_index], 30)
        last_weather_detail_rotate = time.monotonic()


def fetch_predictions():
    global rebuild_count

    url = build_url()
    print("GET CTA predictions (url hidden)")

    for attempt in range(2):  # try once, then rebuild + retry once
        try:
            with requests.get(url) as r:
                data = r.json()

            res = data.get("bustime-response", {})
            preds = res.get("prd", [])
            if isinstance(preds, dict):
                preds = [preds]
            if not isinstance(preds, list):
                preds = []

            try:
                del data
                del res
            except Exception:
                pass

            gc_sweep("after fetch")
            rebuild_count = 0
            return preds

        except MemoryError as e:
            print("MemoryError during fetch:", repr(e))
            print("Forcing device reset to recover memory.")
            time.sleep(1)
            microcontroller.reset()

        except (OutOfRetries, OSError, TimeoutError, RuntimeError, ValueError) as e:
            print("Fetch error:", repr(e))
            if attempt == 0:
                set_rows(["Network hiccup...", "Rebuilding..."])
                rebuild_network()
                time.sleep(2)
                continue
            raise

def drop_network_objects():
    global requests, pool, ssl_context
    try:
        requests = None
        pool = None
        ssl_context = None
    except Exception:
        pass
    gc.collect()

def direction_abbrev(preds):
    # Pull NB/SB from first prediction's rtdir if present
    if preds and isinstance(preds[0], dict):
        d = str(preds[0].get("rtdir", "")).upper()
        if "NORTH" in d:
            return "NB"
        if "SOUTH" in d:
            return "SB"
        if "EAST" in d:
            return "EB"
        if "WEST" in d:
            return "WB"
    return "??"


def stop_name_from_preds(preds):
    if preds and isinstance(preds[0], dict):
        name = preds[0].get("stpnm", "")
        if name:
            return str(name)
    return "CTA Stop"


def minutes_key(p):
    # Sort DUE first
    if not isinstance(p, dict):
        return 9999
    v = str(p.get("prdctdn", "999")).strip()
    if v.upper() == "DUE":
        return -1
    try:
        return int(v)
    except ValueError:
        return 9999


def pad_right(s, width):
    s = str(s)
    if len(s) >= width:
        return s[:width]
    return s + (" " * (width - len(s)))


def format_arrival_line(p):
    rt = str(p.get("rt", "??"))
    des = str(p.get("des", ""))
    cdn = str(p.get("prdctdn", "")).strip()

    # Route padded to 3 chars (no rjust/ljust)
    if len(rt) == 1:
        rt = rt + "  "
    elif len(rt) == 2:
        rt = rt + " "
    else:
        rt = rt[:3]

    # Destination padded
    des = pad_right(des, DEST_WIDTH)

    # Keep DUE as DUE
    if cdn.upper() == "DUE":
        cdn = "DUE"

    # New: add clock time
    clk = arrival_clock_text(p)

    return rt + " " + des + " " + cdn + "  " + clk

# =========================
# UI STATE
# =========================
spinner_i = 0
seconds_to_refresh = REFRESH_SECONDS
last_updated_text = "--:--"
time_ready = False
last_weather_fetch = 0
last_weather_detail_rotate = 0
weather_detail_index = 0
weather_detail_panels = ["Loading weather..."]


def update_header(stop_name, dir_abbrev, updated_text, seconds_left, spinner_char):
    # Line 1: stop + (NB)
    # Keep it short enough to fit
    suffix = " ({})".format(dir_abbrev)
    max_stop_len = 28 - len(suffix)
    s = stop_name[:max_stop_len]
    line_stop.text = s + suffix

    # Line 2: Updated + countdown + spinner
    line_status.text = "Updated {}  Next in {:>2d}s  {}".format(updated_text, seconds_left, spinner_char)

    # SERIAL MIRROR
    # SERIAL MIRROR (optional)
    if DEBUG_SCREEN:
        print("[SCREEN] " + line_stop.text)
        print("[SCREEN] " + line_status.text)

def tick_ui(stop_name, dir_abbrev):
    global spinner_i, seconds_to_refresh
    spinner_char = SPINNER_FRAMES[spinner_i % len(SPINNER_FRAMES)]
    spinner_i += 1

    # Decrement countdown
    if seconds_to_refresh > 0:
        seconds_to_refresh -= UI_TICK_SECONDS
        if seconds_to_refresh < 0:
            seconds_to_refresh = 0

    update_header(stop_name, dir_abbrev, last_updated_text, seconds_to_refresh, spinner_char)

def mark_updated_now():
    global last_updated_text, time_ready, _last_time_sync_attempt

    # If we don't have real time yet, retry periodically (non-spammy)
    if not time_ready:
        now = time.monotonic()
        if (now - _last_time_sync_attempt) > TIME_SYNC_RETRY_INTERVAL:
            _last_time_sync_attempt = now
            try:
                time_ready = try_set_time_via_http()
            except Exception:
                time_ready = False

    # Now set the updated text
    if time_ready:
        last_updated_text = format_time_12h(time.localtime())
    else:
        last_updated_text = "--:--"

def format_clock_12h_lower(hour24, minute):
    ampm = "am"
    if hour24 >= 12:
        ampm = "pm"
    hour12 = hour24 % 12
    if hour12 == 0:
        hour12 = 12
    return "{}:{:02d}{}".format(hour12, minute, ampm)


def arrival_clock_text(p):
    """
    Returns 'h:mmam/pm' for the bus arrival time.
    Uses CTA 'prdtm' first; falls back to now + minutes when possible.
    """
    # 1) Use CTA provided prediction timestamp
    prdtm = p.get("prdtm", "")
    if prdtm and isinstance(prdtm, str) and len(prdtm) >= 14:
        # CTA format: "YYYYMMDD HH:MM"
        try:
            hour24 = int(prdtm[9:11])
            minute = int(prdtm[12:14])
            return format_clock_12h_lower(hour24, minute)
        except Exception:
            pass

    # 2) Fallback: now + prdctdn minutes (only if we have real time)
    cdn = str(p.get("prdctdn", "")).strip().upper()
    if time_ready and cdn and cdn != "DUE":
        try:
            mins = int(cdn)
            now = time.time()
            t = time.localtime(now + (mins * 60))
            return format_clock_12h_lower(t.tm_hour, t.tm_min)
        except Exception:
            pass

    return "--:--"

# =========================
# MAIN
# =========================
connect_wifi()
time_ready = try_set_time_via_http()
# time_ready = try_set_time_via_ntp()

# Prime first weather fetch
fetch_weather()
last_weather_fetch = time.monotonic()


# Prime first fetch so we can show stop name + direction immediately
stop_name = "CTA Stop"
dir_abbrev = "??"

try:
    update_header("Fetching...", "??", "--:--", seconds_to_refresh, SPINNER_FRAMES[0])
    preds = fetch_predictions()
    preds.sort(key=minutes_key)

    stop_name = stop_name_from_preds(preds)
    dir_abbrev = direction_abbrev(preds)

    lines = [format_arrival_line(p) for p in preds[:MAX_RESULTS]]
    set_rows(lines)

    mark_updated_now()
    seconds_to_refresh = REFRESH_SECONDS

except Exception as e:
    print("Startup fetch error:", repr(e))
    set_rows(["Error: " + str(e)[:20]])
    # keep stop_name/dir as defaults

while True:
    try:
        # UI ticks every second; only fetch when countdown hits 0
        tick_ui(stop_name, dir_abbrev)

        rotate_weather_detail_if_needed()

        # Weather refreshes separately every 30 minutes.
        # This keeps CTA predictions on their normal 30-second rhythm.
        if (time.monotonic() - last_weather_fetch) >= WEATHER_REFRESH_SECONDS:
            fetch_weather()
            last_weather_fetch = time.monotonic()

        if seconds_to_refresh == 0:
            update_header(stop_name, dir_abbrev, last_updated_text, 0, "*")  # fetching indicator
            preds = fetch_predictions()
            preds.sort(key=minutes_key)

            stop_name = stop_name_from_preds(preds)
            dir_abbrev = direction_abbrev(preds)

            lines = [format_arrival_line(p) for p in preds[:MAX_RESULTS]]
            set_rows(lines)

            mark_updated_now()
            seconds_to_refresh = REFRESH_SECONDS

        time.sleep(UI_TICK_SECONDS)

    except (TimeoutError, RuntimeError, OSError, OutOfRetries, ValueError) as e:
        print("Network error:", repr(e))
        set_rows(["Network issue...", "Reconnecting..."])
        rebuild_network()
        # Optionally force immediate refresh after recovery:
        seconds_to_refresh = 1
