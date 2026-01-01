import time
import board
import busio
import digitalio
import displayio
import terminalio
from adafruit_display_text import label
import adafruit_connection_manager
import adafruit_requests
import adafruit_esp32spi.adafruit_esp32spi as esp32
from secrets import secrets

# =========================
# CONFIG & HARDWARE
# =========================
STOP_ID = "1563"
ROUTES = ["1", "4", "X4"]
MAX_RESULTS = 5
REFRESH_SECONDS = 30
HOST = "www.ctabustracker.com"

# =========================
# SIMPLE SERIAL LOGGER
# =========================
def log(msg):
    print(msg)

# =========================
# SETUP ESP32
# =========================
esp32_cs = digitalio.DigitalInOut(board.ESP_CS)
esp32_ready = digitalio.DigitalInOut(board.ESP_BUSY)
esp32_reset = digitalio.DigitalInOut(board.ESP_RESET)
spi = busio.SPI(board.SCK, board.MOSI, board.MISO)

esp = esp32.ESP_SPIcontrol(spi, esp32_cs, esp32_ready, esp32_reset)

pool = adafruit_connection_manager.get_radio_socketpool(esp)
ssl_context = adafruit_connection_manager.get_radio_ssl_context(esp)
requests = adafruit_requests.Session(pool, ssl_context)

# =========================
# DISPLAY SETUP
# =========================
display = board.DISPLAY
group = displayio.Group()
display.root_group = group

title = label.Label(terminalio.FONT, text="CTA – 31st & Indiana", x=10, y=15)
status = label.Label(terminalio.FONT, text="Starting...", x=10, y=35)
group.append(title)
group.append(status)

rows = []
for i in range(MAX_RESULTS):
    row = label.Label(terminalio.FONT, text="", x=10, y=60 + (i * 20))
    rows.append(row)
    group.append(row)


def update_display(header, lines):
    status.text = header
    for i in range(MAX_RESULTS):
        rows[i].text = lines[i] if i < len(lines) else ""

    # SERIAL MIRROR
    log(f"[DISPLAY] {header}")
    for line in lines:
        log("  " + line)


# =========================
# NETWORK FUNCTIONS
# =========================
def connect_wifi():
    update_display("Resetting WiFi...", [])
    log("Resetting ESP32")

    esp.reset()
    time.sleep(1)

    while not esp.is_connected:
        try:
            log("Connecting to WiFi...")
            esp.connect_AP(secrets["ssid"], secrets["password"])
        except Exception as e:
            log(f"WiFi error: {e}")
            update_display("Retry WiFi...", [])
            time.sleep(2)

    update_display("WiFi Connected", [])
    log("WiFi connected")


def fetch_data():
    url = (
        f"http://{HOST}/bustime/api/v3/getpredictions"
        f"?key={secrets['cta_api_key']}"
        f"&stpid={STOP_ID}"
        f"&rt={','.join(ROUTES)}"
        f"&top={MAX_RESULTS}"
        f"&format=json"
    )

    log(f"Requesting: {url}")

    with requests.get(url) as r:
        data = r.json()

    res = data.get("bustime-response", {})
    preds = res.get("prd", [])

    if isinstance(preds, dict):
        preds = [preds]

    return preds


# =========================
# MAIN LOOP
# =========================
connect_wifi()

while True:
    try:
        predictions = fetch_data()

        if not predictions:
            update_display("No arrivals", [])
        else:
            lines = []
            for p in predictions[:MAX_RESULTS]:
                route = p.get("rt", "??")
                dest = p.get("des", "")[:15]
                mins = p.get("prdctdn", "")
                lines.append(f"{route:<3} {dest:<15} {mins}")

            update_display("Next buses:", lines)

    except (TimeoutError, RuntimeError, OSError) as e:
        log(f"ERROR: {e}")
        update_display("Recovering...", [])
        connect_wifi()

    time.sleep(REFRESH_SECONDS)
