from microdot import Microdot, send_file
import network
from machine import Pin, RTC, reset
import machine
import ujson
import time
from _thread import start_new_thread
import uasyncio as asyncio
from umqtt.simple import MQTTClient
import ntptime
import utime
import uos as os

# Constants
SSID = 'Your SSID'
PASSWORD = 'Your Password'
RELAY_PINS = [13, 21, 14, 27, 26, 25, 33, 32, 19, 18]  # Update your GPIO pin numbers here
relays = [Pin(pin, Pin.OUT) for pin in RELAY_PINS]

for i in range(10):
    relays[i].value(1)

global MQTT
MQTT = 1
MQTT_BROKER = "your.mqtt.broker.ip"
# MQTT_BROKER = "192.168.1.74"
MQTT_USER = "MQTT USERNAME"
MQTT_PASSWORD = "MQTT PASSWORD"

CLIENT_ID = "intellidwell_SC"
TOPIC_BASE = "home/sprinklers/"

client = MQTTClient(CLIENT_ID, MQTT_BROKER, user=MQTT_USER, password=MQTT_PASSWORD, keepalive=60)
client.set_last_will(f"{TOPIC_BASE}status", "Offline", retain=True)

LOG_FILE = 'logs.txt'

def log_message(message):
    with open('logs.txt', 'a') as f:
        f.write(f"{time.localtime()}: {message}\n")
        
def command_callback(topic, msg):
    print(f"Command received: Topic: {topic.decode()}, Message: {msg.decode()}")
    # Decode bytes to string for easier handling
    topic = topic.decode()
    msg = msg.decode()
    log_message(f"Command received: Topic: {topic}, Message: {msg}")
    parts = topic.split('/')
    
    # Check for relay control commands
    if len(parts) == 4 and parts[0] == "cmnd" and parts[1] == "zone" and parts[3] == "power":
        pin = int(parts[2])
        if msg == "ON":
            relays[pin].value(0)  # Assuming '0' is ON for your relays
            print(f"Relay {pin} turned ON via MQTT")
            log_message(f"Relay {pin} turned ON via MQTT")
        elif msg == "OFF":
            relays[pin].value(1)  # Assuming '1' is OFF for your relays
            print(f"Relay {pin} turned OFF via MQTT")
            log_message(f"Relay {pin} turned OFF via MQTT")
        publish_relay_status(client, pin, relays[pin].value())

    # Check for schedule control commands
    elif len(parts) == 4 and parts[1] == "zone" and parts[3] == "schedule":
        pin = int(parts[2])
        status = msg.lower() == "true"
        if update_schedule_status(pin, status):
            publish_schedule_status(client, pin, status)
        else:
            print("Invalid schedule operation")
            log_message("Invalid schedule operation")


client.set_callback(command_callback)

async def connect_mqtt():
    try:
        client.connect()
        print("MQTT connected.")
        for i in range(len(relays)):
            client.subscribe(f"cmnd/zone/{i}/power")
            client.subscribe(f"cmnd/zone/{i}/schedule")
            print(f"Subscribed to: cmnd/zone/{i}/power and cmnd/zone/{i}/schedule")
        publish_discovery(client)
        publish_schedule_discovery(client)
    except Exception as e:
        print(f"Failed to connect to MQTT: {e}")
        log_message(f"Failed to connect to MQTT: {e}")
        await asyncio.sleep(5)
        await connect_mqtt()

async def check_messages():
    while True:
        try:
            print("Checking MQTT messages...")
            client.check_msg()
        except OSError as e:
            print("Failed to check MQTT messages:", e)
            log_message("Failed to check MQTT messages:", e)
            reconnect()
        await asyncio.sleep(1)

# Web server app
app = Microdot()

@app.route('/logs-page')
def logs_page(request):
    return send_file('logs.html')

@app.route('/get-logs')
def get_logs(request):
    print("Route /get-logs accessed")  # Debugging print statement

    try:
        with open('logs.txt', 'r') as f:
            logs = f.read()
        return logs, {'Content-Type': 'text/plain'}
    except Exception as e:
        log_message(f"Error reading logs: {e}")
        return 'Error reading logs', 500
    
@app.route('/clear-logs', methods=['POST'])
def clear_logs(request):
    print("Route /clear-logs accessed")  # Debugging print statement

    try:
        with open('logs.txt', 'w') as f:
            f.write("")
        log_message("Logs cleared.")
        return 'Logs cleared', 200
    except Exception as e:
        log_message(f"Error clearing logs: {e}")
        return 'Error clearing logs', 500

@app.route('/restart', methods=['POST'])
def restart(request):
    print("Restarting device...")
    log_message("Restarting device...")
    reset()
    return 'Restarting...', 200

@app.route('/relay/<pin>/<state>')
def toggle_relay(request, pin, state):
    pin = int(pin)
    if pin >= 0 and pin < len(relays):
        if state == 'on':
            relays[pin].value(0)  # Set GPIO high
            log_message(f"Relay {pin+1} turned on manually.")
            if MQTT == 1:
                publish_relay_status(client, pin, relays[pin].value())
            return 'Relay turned on!'
        elif state == 'off':
            relays[pin].value(1)  # Set GPIO low
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

    # Assuming 'day' fields are passed as a list
    schedule = {
        'days': days,
        'onTime': onTime,
        'offTime': offTime
    }
    schedules[pin] = schedule
    with open('schedules.json', 'w') as f:
        ujson.dump(schedules, f)
    return f'Schedule set for Relay {pin + 1}!'

@app.route('/toggle-schedule/<pin>/<status>', methods=['GET'])
def toggle_schedule(request, pin, status):
    pin = int(pin)
    status = status.lower() == 'true'
    if 0 <= pin < len(schedules):
        # Set default if 'enabled' key does not exist
        schedules[pin]['enabled'] = status
        with open('schedules.json', 'w') as f:
            ujson.dump(schedules, f)
        publish_schedule_status(client, pin, status)
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

async def connect_to_wifi():
    ap = network.WLAN(network.AP_IF)
    ap.active(False)
    wifi = network.WLAN(network.STA_IF)
    wifi.active(True)
    #wifi.connect(SSID, PASSWORD)
    wifi.connect(SSID)
    count = 0
    while not wifi.isconnected() and count < 30:  # Increase the timeout
        count += 1
        print(f"Connecting to WiFi... Attempt {count}")
        await asyncio.sleep(1)
    if wifi.isconnected():
        print('Connected to WiFi at', wifi.ifconfig()[0])
    else:
        print('Failed to connect to WiFi. Entering AP mode.')
        enter_AP_mode()

async def sync_time():
    timezone_offset = -6  # Adjust this to your timezone offset from UTC (for example, -5 for EST)
    while True:
        try:
            ntptime.settime()
            print("Time synchronized with NTP server.")
            
            # Adjust for timezone
            tm = utime.localtime(utime.mktime(utime.localtime()) + timezone_offset * 3600)
            machine.RTC().datetime((tm[0], tm[1], tm[2], tm[6] + 1, tm[3], tm[4], tm[5], 0))
            
            print(f"Time adjusted to local timezone: {tm}")
            log_message(f"Time adjusted to local timezone: {tm}")
        except Exception as e:
            print(f"Failed to sync time: {e}")
            log_message(f"Failed to sync time: {e}")
        await asyncio.sleep(3600)  # Sync time every hour

async def check_schedules():
    while True:
        print("Checking Shedules...")
        log_message("Checking Shedules...")
        now = time.localtime()
        current_time = f"{now[3]:02}:{now[4]:02}"
        print("Current Time: ", current_time)
        current_day = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][now[6]]

        for pin, schedule in enumerate(schedules):
            if schedule.get('enabled', False):  # Assume False as default if not specified
                if current_day in schedule.get('days', []) and schedule.get('onTime') == current_time:
                    relays[pin].value(0)
                    if MQTT == 1:
                        publish_relay_status(client, pin, relays[pin].value())
                    print(f"Relay {pin+1} turned on at {current_time}")
                    log_message(f"Relay {pin+1} turned on at {current_time}")
                elif schedule.get('offTime') == current_time:
                    relays[pin].value(1)
                    if MQTT == 1:
                        publish_relay_status(client, pin, relays[pin].value())
                    print(f"Relay {pin+1} turned off at {current_time}")
                    log_message(f"Relay {pin+1} turned off at {current_time}")
        await asyncio.sleep(10)  # Check every minute   

def publish_discovery(client):
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

def publish_relay_status(client, relay, status):
    topic = f"stat/zone/{relay}/state"
    payload = "OFF" if status else "ON"
    client.publish(topic, payload)

def update_schedule_status(pin, status):
    if 0 <= pin < len(schedules):
        schedules[pin]['enabled'] = status
        with open('schedules.json', 'w') as f:
            ujson.dump(schedules, f)
        print(f"Schedule for Relay {pin+1} {'enabled' if status else 'disabled'}!")
        log_message(f"Schedule for Relay {pin+1} {'enabled' if status else 'disabled'}!")
        return True
    return False
    
def reconnect():
    print("Reconnecting to MQTT broker...")
    log_message("Reconnecting to MQTT broker...")
    try:
        client.connect()
        client.subscribe(f"cmnd/zone/#")
        print("Reconnected to MQTT broker and subscribed to topics.")
        log_message("Reconnected to MQTT broker and subscribed to topics.")
    except OSError as e:
        print("Failed to reconnect:", e)
        log_message("Failed to reconnect:", e)
        time.sleep(5)
        reconnect()

def disconnect_from_wifi():
    wlan = network.WLAN(network.STA_IF)
    if wlan.isconnected():
        print("Disconnecting from WiFi...")
        wlan.disconnect()
        time.sleep(1)
    print("Disconnected from WiFi")

def enter_AP_mode():
    # Disconnect from WiFi
    disconnect_from_wifi()
    wifi = network.WLAN(network.STA_IF)
    wifi.active(False)
    # Create an Access Point for configuration
    ap_ssid = "intelidwellSC"
    ap_password = "Sprinkler12345"
    
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid=ap_ssid, password=ap_password)

    print("Configuration mode activated. Connect to AP:", ap_ssid, "with password:", ap_password)
    log_message("Configuration mode activated. Connect to AP: {ap_ssid} with password:{ap_pass}")
    print("Visit http://192.168.4.1 in your web browser to configure.")
    log_message("Visit http://192.168.4.1 in your web browser to configure.")

    # Serve the configuration page
    app.run(host='0.0.0.0', port=80)
    
    while True:
        time.sleep(300)
        reset()

def publish_schedule_discovery(client):
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
    
def publish_schedule_status(client, pin, enabled):
    topic = f"stat/zone/{pin}/schedule"
    payload = "true" if enabled else "false"
    client.publish(topic, payload)

def run_server():
    print("Starting Microdot server...")
    app.run(host='0.0.0.0', port=80)

async def main():
    try:
        await connect_to_wifi()
        await connect_mqtt()
        start_new_thread(run_server, ())
        #start_new_thread(sync_time, ())
        #start_new_thread(check_schedules, ())
        #start_new_thread(check_messages, ())
        await asyncio.gather(
            sync_time(),
            check_schedules(),
            check_messages()#,
            #run_server()  # Run the server in the asyncio event loop
        )
        #start_new_thread(run_server, ())
    except OSError as e:
        print("Error found. Restarting without MQTT:", e)
        log_message("Error found. Restarting without MQTT: {e}")
        await main_without_mqtt()

async def main_without_mqtt():
    global MQTT
    MQTT = 0
    try:
        await connect_to_wifi()
        await asyncio.gather(
            check_schedules(),
            run_server()  # Run the server in the asyncio event loop
        )
    except OSError as e:
        print("Error found again. Restarting without MQTT or WIFI:", e)
        log_message("Error found again. Restarting without MQTT or WIFI: {e}")
        await main_without_mqtt_or_wifi()

async def main_without_mqtt_or_wifi():
    global MQTT
    MQTT = 0
    try:
        await asyncio.gather(
            check_schedules(),
            run_ap_mode()  # Run AP mode in the asyncio event loop
        )
    except OSError as e:
        print("Serious error!", e)
        log_message("Serious error!", e)
        # Potentially reset the device or take other action here

async def run_ap_mode():
    print("Entering AP mode...")
    log_message("Entering AP mode...")
    enter_AP_mode()

try:
    asyncio.run(main())
except OSError as e:
    print("Error found. Restarting without MQTT")
    try:
        asyncio.run(main_without_mqtt())
    except OSError as e:
        print("Error found again. Restarting without MQTT or WIFI")
        try:
            asyncio.run(main_without_mqtt_or_wifi())
        except OSError as e:
            print("Serious error!")