import time
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

REFRESH_SECONDS = 30          # API refresh interval
UI_TICK_SECONDS = 1           # countdown/spinner tick
HOST = "www.ctabustracker.com"

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
