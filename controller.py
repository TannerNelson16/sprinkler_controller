from microdot import Microdot, send_file
import network
from machine import Pin, RTC, reset
import machine
import ujson
import time
from time import sleep
from _thread import start_new_thread
import uasyncio as asyncio
from umqtt.simple import MQTTClient
import ntptime
import utime
import uos as os
import gc

machine.sleep(0)       # Disable light sleep

# Constants
SSID = "Mosby2"
PASSWORD = "Tf2241994!"
RELAY_PINS = [13, 21, 14, 27, 26, 25, 33, 32, 19, 18]  # Update your GPIO pin numbers here
relays = [Pin(pin, Pin.OUT) for pin in RELAY_PINS]
wifi = network.WLAN(network.STA_IF)
for relay in relays:
    relay.value(1)

global MQTT
MQTT = 1
MQTT_BROKER = "192.168.1.74"
MQTT_USER = "Tanner23456"
MQTT_PASSWORD = "Tn7281994!"

CLIENT_ID = "intellidwell_SC"
TOPIC_BASE = "home/sprinklers/"

client = MQTTClient(CLIENT_ID, MQTT_BROKER, user=MQTT_USER, password=MQTT_PASSWORD, keepalive=60)
client.set_last_will(f"{TOPIC_BASE}status", "Offline", retain=True)

LOG_FILE = 'logs.txt'
log_buffer = []  # Buffer for in-memory logging
LOG_BUFFER_LIMIT = 5  # Lower limit to reduce memory usage

def log_message(message):
    global log_buffer
    current_time = time.localtime()
    timestamp = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(*current_time[:6])
    log_buffer.append(f"{timestamp}: {message}\n")
    print(f"LOG: {timestamp}: {message}")  # Immediate console feedback
    if len(log_buffer) >= LOG_BUFFER_LIMIT:  # Flush the buffer to file when it reaches the limit
        flush_log_buffer()

def flush_log_buffer():
    global log_buffer
    try:
        with open(LOG_FILE, 'a') as f:
            for line in log_buffer:
                f.write(line)
        log_buffer = []
    except Exception as e:
        print(f"Failed to flush log buffer: {e}")

def command_callback(topic, msg):
    topic = topic.decode()
    msg = msg.decode()
    log_message(f"Command received: Topic: {topic}, Message: {msg}")
    parts = topic.split('/')
    
    if len(parts) == 4 and parts[0] == "cmnd" and parts[1] == "zone" and parts[3] == "power":
        pin = int(parts[2])
        if msg == "ON":
            relays[pin].value(0)
            log_message(f"Relay {pin} turned ON via MQTT")
        elif msg == "OFF":
            relays[pin].value(1)
            log_message(f"Relay {pin} turned OFF via MQTT")
        publish_relay_status(client, pin, relays[pin].value())
    elif len(parts) == 4 and parts[1] == "zone" and parts[3] == "schedule":
        pin = int(parts[2])
        status = msg.lower() == "true"
        if update_schedule_status(pin, status):
            publish_schedule_status(client, pin, status)
        else:
            log_message("Invalid schedule operation")

client.set_callback(command_callback)

async def connect_mqtt():
    try:
        client.connect()
        log_message("MQTT connected.")
        await subscribe_to_topics()
    except Exception as e:
        log_message(f"Failed to connect to MQTT: {e}")
        await asyncio.sleep(5)
        await connect_mqtt()

async def subscribe_to_topics():
    try:
        for i in range(len(relays)):
            client.subscribe(f"cmnd/zone/{i}/power")
            client.subscribe(f"cmnd/zone/{i}/schedule")
            log_message(f"Subscribed to: cmnd/zone/{i}/power and cmnd/zone/{i}/schedule")
        publish_discovery(client)
        publish_schedule_discovery(client)
    except Exception as e:
        log_message(f"Failed to subscribe to topics: {e}")

async def check_messages():
    while True:
        try:
            client.check_msg()
        except OSError as e:
            log_message(f"Failed to check MQTT messages: {e}")
            await reconnect()
        await asyncio.sleep(1)
        gc.collect()  # Run garbage collection to free up memory

async def reconnect():
    log_message("Reconnecting to MQTT broker...")
    try:
        client.disconnect()
        await asyncio.sleep(5)  # Short delay before reconnecting
        client.connect()
        log_message("Reconnected to MQTT broker.")
        await subscribe_to_topics()  # Ensure all subscriptions are re-established
    except OSError as e:
        log_message(f"Failed to reconnect: {e}")
        await asyncio.sleep(10)  # Increase delay after failure
        await reconnect()  # Continue attempting to reconnect

# Web server app
app = Microdot()

@app.route('/logs-page')
def logs_page(request):
    return send_file('logs.html')

@app.route('/get-logs')
def get_logs(request):
    try:
        flush_log_buffer()  # Ensure all logs are written to the file
        response_text = ''
        with open(LOG_FILE, 'r') as f:
            for line in f:
                response_text += line
                if len(response_text) > 1024:  # Send in smaller chunks
                    return response_text, {'Content-Type': 'text/plain'}
        return response_text, {'Content-Type': 'text/plain'}
    except MemoryError:
        log_message("MemoryError while reading logs.")
        gc.collect()  # Run garbage collection
        return 'Error reading logs', 500
    except Exception as e:
        log_message(f"Error reading logs: {e}")
        return 'Error reading logs', 500

@app.route('/clear-logs', methods=['POST'])
def clear_logs(request):
    try:
        with open(LOG_FILE, 'w') as f:
            f.write("")
        log_message("Logs cleared.")
        return 'Logs cleared', 200
    except Exception as e:
        log_message(f"Error clearing logs: {e}")
        return 'Error clearing logs', 500

@app.route('/restart', methods=['POST'])
def restart(request):
    log_message("Restarting device...")
    flush_log_buffer()
    gc.collect()  # Run garbage collection
    reset()
    return 'Restarting...', 200

@app.route('/relay/<pin>/<state>')
def toggle_relay(request, pin, state):
    pin = int(pin)
    if 0 <= pin < len(relays):
        if state == 'on':
            relays[pin].value(0)
            log_message(f"Relay {pin+1} turned on manually.")
            if MQTT == 1:
                publish_relay_status(client, pin, relays[pin].value())
            return 'Relay turned on!'
        elif state == 'off':
            relays[pin].value(1)
            log_message(f"Relay {pin+1} turned off manually.")
            if MQTT == 1:
                publish_relay_status(client, pin, relays[pin].value())
            return 'Relay turned off!'
    return 'Invalid relay or command', 400

@app.route('/')
def index(request):
    return send_file('index.html')

@app.route('/get-relay-states')
def get_relay_states(request):
    states = [relay.value() for relay in relays]
    return ujson.dumps(states), {'Content-Type': 'application/json'}

@app.route('/scheduler')
def scheduler(request):
    return send_file('scheduler.html')

@app.route('/set-schedule/<pin>', methods=['POST'])
def set_schedule(request, pin):
    pin = int(pin)
    days = request.form.getlist('day')
    onTime = request.form.get('onTime')
    offTime = request.form.get('offTime')

    schedule = {
        'days': days,
        'onTime': onTime,
        'offTime': offTime
    }
    schedules[pin] = schedule
    with open('schedules.json', 'w') as f:
        ujson.dump(schedules, f)
    log_message(f"Schedule set for Relay {pin + 1}")
    return f'Schedule set for Relay {pin + 1}!'

@app.route('/toggle-schedule/<pin>/<status>', methods=['GET'])
def toggle_schedule(request, pin, status):
    pin = int(pin)
    status = status.lower() == 'true'
    if 0 <= pin < len(schedules):
        schedules[pin]['enabled'] = status
        with open('schedules.json', 'w') as f:
            ujson.dump(schedules, f)
        publish_schedule_status(client, pin, status)
        log_message(f"Schedule for Relay {pin+1} {'enabled' if status else 'disabled'}!")
        return f"Schedule for Relay {pin+1} {'enabled' if status else 'disabled'}!"
    return 'Invalid pin or status', 400

@app.route('/get-schedules')
def get_schedules(request):
    return ujson.dumps(schedules), {'Content-Type': 'application/json'}

# Load or create a schedule store
try:
    with open('schedules.json', 'r') as f:
        schedules = ujson.load(f)
except (OSError, ValueError):
    schedules = [{} for _ in RELAY_PINS]
    with open('schedules.json', 'w') as f:
        ujson.dump(schedules, f)
    log_message("Created new schedules.json file")

async def connect_to_wifi():
    try:
        ap = network.WLAN(network.AP_IF)
        ap.active(False)
        wifi = network.WLAN(network.STA_IF)
        wifi.active(True)
        wifi.config(pm=0)
        wifi.connect(SSID, PASSWORD)
        count = 0
        while not wifi.isconnected() and count < 30:  # Increase the timeout
            count += 1
            log_message(f"Connecting to WiFi... Attempt {count}")
            await asyncio.sleep(1)
        if wifi.isconnected():
            log_message('Connected to WiFi')
        else:
            log_message('Failed to connect to WiFi. Entering AP mode.')
            enter_AP_mode()
    except Exception as e:
        log_message(f"Exception during WiFi connection: {e}")
        enter_AP_mode()

async def sync_time():
    timezone_offset = -6  # Adjust this to your timezone offset from UTC
    while True:
        try:
            ntptime.settime()
            log_message("Time synchronized with NTP server.")
            tm = utime.localtime(utime.mktime(utime.localtime()) + timezone_offset * 3600)
            machine.RTC().datetime((tm[0], tm[1], tm[2], tm[6] + 1, tm[3], tm[4], tm[5], 0))
            log_message(f"Time adjusted to local timezone: {tm}")
        except Exception as e:
            log_message(f"Failed to sync time: {e}")
        await asyncio.sleep(3600)  # Sync time every hour
        gc.collect()  # Run garbage collection

async def check_schedules():
    while True:
        try:
            now = time.localtime()
            current_time = f"{now[3]:02}:{now[4]:02}"
            current_day = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][now[6]]

            for pin, schedule in enumerate(schedules):
                if schedule.get('enabled', False):
                    if current_day in schedule.get('days', []) and schedule.get('onTime') == current_time:
                        relays[pin].value(0)
                        if MQTT == 1:
                            publish_relay_status(client, pin, relays[pin].value())
                        log_message(f"Relay {pin+1} turned on at {current_time}")
                    elif schedule.get('offTime') == current_time:
                        relays[pin].value(1)
                        if MQTT == 1:
                            publish_relay_status(client, pin, relays[pin].value())
                        log_message(f"Relay {pin+1} turned off at {current_time}")
            await asyncio.sleep(60)  # Check every minute instead of every 10 seconds
            gc.collect()  # Run garbage collection
        except Exception as e:
            log_message(f"Exception in check_schedules: {e}")
            await asyncio.sleep(60)
            gc.collect()  # Run garbage collection

def publish_discovery(client):
    try:
        base_topic = "homeassistant/switch/SC{}"
        for i in range(len(relays)):
            topic = base_topic.format(i) + "/config"
            payload = {
                "name": f"Zone {i + 1}",
                "command_topic": f"cmnd/zone/{i}/power",
                "state_topic": f"stat/zone/{i}/state",
                "payload_on": "ON",
                "payload_off": "OFF",
                "unique_id": f"intellidwellSC{i}",
                "device": {
                    "identifiers": [f"intellidwellSC"],
                    "name": f"Sprinkler Controller",
                    "manufacturer": "Intellidwell",
                    "model": "Sprinkler Controller V1.0",
                    "sw_version": "1.0"
                },
                "platform": "mqtt"
            }
            client.publish(topic, ujson.dumps(payload), retain=True)
        log_message("Published MQTT discovery messages")
    except Exception as e:
        log_message(f"Failed to publish discovery: {e}")

def publish_relay_status(client, relay, status):
    try:
        topic = f"stat/zone/{relay}/state"
        payload = "OFF" if status else "ON"
        client.publish(topic, payload)
        log_message(f"Published relay status for relay {relay}: {payload}")
    except Exception as e:
        log_message(f"Failed to publish relay status: {e}")

def update_schedule_status(pin, status):
    try:
        if 0 <= pin < len(schedules):
            schedules[pin]['enabled'] = status
            with open('schedules.json', 'w') as f:
                ujson.dump(schedules, f)
            log_message(f"Schedule for Relay {pin+1} {'enabled' if status else 'disabled'}!")
            return True
        return False
    except Exception as e:
        log_message(f"Failed to update schedule status: {e}")
        return False

def disconnect_from_wifi():
    wlan = network.WLAN(network.STA_IF)
    if wlan.isconnected():
        log_message("Disconnecting from WiFi...")
        wlan.disconnect()
        time.sleep(1)
    log_message("Disconnected from WiFi")

def enter_AP_mode():
    try:
        disconnect_from_wifi()
        wifi = network.WLAN(network.STA_IF)
        wifi.active(False)
        ap_ssid = "intelidwellSC"
        ap_password = "Sprinkler12345"
        
        ap = network.WLAN(network.AP_IF)
        ap.active(True)
        ap.config(essid=ap_ssid, password=ap_password)

        log_message(f"Configuration mode activated. Connect to AP: {ap_ssid} with password:{ap_password}")
        log_message("Visit http://192.168.4.1 in your web browser to configure.")

        app.run(host='0.0.0.0', port=80)
        while True:
            time.sleep(300)
            reset()
    except Exception as e:
        log_message(f"Failed to enter AP mode: {e}")

def publish_schedule_discovery(client):
    try:
        base_topic = "homeassistant/switch/SS{}"
        for i in range(len(relays)):
            topic = base_topic.format(i) + "/config"
            payload = {
                "name": f"Zone {i+1} Scheduler",
                "command_topic": f"cmnd/zone/{i}/schedule",
                "state_topic": f"stat/zone/{i}/schedule",
                "payload_on": "true",
                "payload_off": "false",
                "unique_id": f"intellidwellSS{i}",
                "device": {
                    "identifiers": [f"intellidwellSS"],
                    "name": f"Sprinkler Scheduler",
                    "manufacturer": "Intellidwell",
                    "model": "Sprinkler Controller V1.0",
                    "sw_version": "1.0"
                },
                "platform": "mqtt"
            }
            client.subscribe(f"cmnd/zone/{i}/schedule")
            client.publish(topic, ujson.dumps(payload), retain=True)
        log_message("Published schedule MQTT discovery messages")
    except Exception as e:
        log_message(f"Failed to publish schedule discovery: {e}")
    
def publish_schedule_status(client, pin, enabled):
    try:
        topic = f"stat/zone/{pin}/schedule"
        payload = "true" if enabled else "false"
        client.publish(topic, payload)
        log_message(f"Published schedule status for relay {pin}: {payload}")
    except Exception as e:
        log_message(f"Failed to publish schedule status: {e}")

def run_server():
    log_message("Starting Microdot server...")
    app.run(host='0.0.0.0', port=80)

async def main():
    try:
        await connect_to_wifi()
        await connect_mqtt()
        start_new_thread(run_server, ())
        await asyncio.gather(
            sync_time(),
            check_schedules(),
            check_messages()
        )
    except Exception as e:
        log_message(f"Error in main loop: {e}")
        await main_without_mqtt()

async def main_without_mqtt():
    global MQTT
    MQTT = 0
    try:
        await connect_to_wifi()
        await asyncio.gather(
            check_schedules(),
            run_server()
        )
    except Exception as e:
        log_message(f"Error found again. Restarting without MQTT or WIFI: {e}")
        await main_without_mqtt_or_wifi()

async def main_without_mqtt_or_wifi():
    global MQTT
    MQTT = 0
    try:
        await asyncio.gather(
            check_schedules(),
            run_ap_mode()
        )
    except Exception as e:
        log_message(f"Serious error: {e}")

async def run_ap_mode():
    log_message("Entering AP mode...")
    enter_AP_mode()

try:
    asyncio.run(main())
except Exception as e:
    log_message(f"Error in startup: {e}")
    try:
        asyncio.run(main_without_mqtt())
    except Exception as e:
        log_message(f"Error in main_without_mqtt: {e}")
        try:
            asyncio.run(main_without_mqtt_or_wifi())
        except Exception as e:
            log_message(f"Serious error in final recovery: {e}")

