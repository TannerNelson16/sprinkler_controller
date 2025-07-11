from microdot import Microdot, send_file
import network
from machine import Pin, RTC, reset
import machine
import ujson
import json
import time
from time import sleep
from _thread import start_new_thread
import uasyncio as asyncio
from umqtt.simple import MQTTClient
import ntptime
import utime
import uos as os
import gc
import socket
import usocket

machine.sleep(0)       # Disable light sleep
global MQTT

timers = {}


def load_settings():
    try:
        with open('settings.json', 'r') as f:
            config = ujson.load(f)  # Try loading the settings using ujson
            # Ensure the necessary keys exist, if not, provide defaults
            if not isinstance(config, dict):
                raise ValueError("Config is not a valid dictionary.")
            return {
                "ssid": config.get("ssid", ""),
                "wifi_password": config.get("wifi_password", ""),
                "mqtt_server": config.get("mqtt_server", ""),
                "mqtt_username": config.get("mqtt_username", ""),
                "mqtt_password": config.get("mqtt_password", ""),
                "mqtt_enabled": config.get("mqtt_enabled", 0)  # Default to disabled (0) if not present
            }
    except (OSError, ValueError) as e:
        print(f"Error loading settings.json: {e}, loading default settings...")
        return {
            "ssid": "",
            "wifi_password": "",
            "mqtt_server": "",
            "mqtt_username": "",
            "mqtt_password": "",
            "mqtt_enabled": 0  # Default to disabled
        }


    
config = load_settings()


SSID = config['ssid']
PASSWORD = config['wifi_password']
MQTT_BROKER = config['mqtt_server']
MQTT_USER = config['mqtt_username']
MQTT_PASSWORD = config['mqtt_password']
MQTT = config.get('mqtt_enabled', 1) 
# Constants
RELAY_PINS = [13, 21, 14, 27, 26, 25, 33, 32, 19, 18]
relays = [Pin(pin, Pin.OUT) for pin in RELAY_PINS]
wifi = network.WLAN(network.STA_IF)
for relay in relays:
    relay.value(0)


CLIENT_ID = "intellidwell_SC"
TOPIC_BASE = "home/sprinklers/"

client = MQTTClient(CLIENT_ID, MQTT_BROKER, user=MQTT_USER, password=MQTT_PASSWORD, keepalive=0)
client.set_last_will(f"{TOPIC_BASE}status", "Offline", retain=True)

LOG_FILE = 'logs.txt'
LOG_MAX_LINES = 25  # Keep only the last 25 lines of logs
MAX_RECONNECT_ATTEMPTS = 10  # Maximum number of reconnection attempts

#WIFI and MQTT Credentials

def load_settings():
    try:
        with open('settings.json', 'r') as f:
            return json.load(f)
    except OSError:
        # Default settings if file does not exist
        return {
            "ssid": "",
            "wifi_password": "",
            "mqtt_server": "",
            "mqtt_username": "",
            "mqtt_password": ""
        }

def save_settings(settings):
    if settings is None or not isinstance(settings, dict):
        raise ValueError("Invalid settings format. Expected a dictionary.")
    try:
        # Ensure mqtt_enabled is treated as integer 0 or 1
        settings['mqtt_enabled'] = int(settings.get('mqtt_enabled', 0))  
        with open('settings.json', 'w') as f:
            ujson.dump(settings, f)
        log_message("Settings saved successfully.")
    except Exception as e:
        log_message(f"Error saving settings: {e}")




def log_message(message):
    current_time = time.localtime()
    timestamp = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(*current_time[:6])
    log_entry = f"{timestamp}: {message}\n"

    try:
        # Open the log file in append mode and write the log entry
        with open(LOG_FILE, 'a') as f:
            f.write(log_entry)

        # Read the log file, ensuring we keep only the last LOG_MAX_LINES lines
        with open(LOG_FILE, 'r') as f:
            lines = f.read().splitlines()  # Read as a list of strings without the newline characters

        # If the log exceeds the maximum size, write back the trimmed version
        if len(lines) > LOG_MAX_LINES:
            with open(LOG_FILE, 'w') as f:
                f.write("\n".join(lines[-LOG_MAX_LINES:]) + "\n")  # Write lines back as a single string

    except (OSError, AttributeError) as e:
        # Log an error message to the console if writing to the log fails
        print(f"Failed to log message: {e}")
        print(f"LOG ENTRY (Fallback): {log_entry}")

    # Print the log entry to the console for immediate feedback
    print(f"LOG: {timestamp}: {message}")


def command_callback(topic, msg):
    topic = topic.decode()
    msg = msg.decode()
    log_message(f"Command received: Topic: {topic}, Message: {msg}")
    parts = topic.split('/')
    
    if len(parts) == 4 and parts[0] == "cmnd" and parts[1] == "zone" and parts[3] == "power":
        pin = int(parts[2])
        if msg == "ON":
            relays[pin].value(1)
            log_message(f"Relay {pin} turned ON via MQTT")
        elif msg == "OFF":
            relays[pin].value(0)
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
        await reconnect()

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
    max_mqtt_retries = 5  # Try MQTT reconnections multiple times before giving up
    mqtt_retry_delay = 2  # Start with a 2-second delay
    mqtt_attempt_count = 0

    log_message("Reconnecting to MQTT broker...")
    while mqtt_attempt_count < max_mqtt_retries:
        try:
            # Disconnect MQTT client if still connected
            try:
                client.disconnect()
            except Exception as e:
                log_message(f"Error during MQTT disconnect: {e}")

            await asyncio.sleep(5)  # Short delay before attempting to reconnect

            # Reconnect to the MQTT broker
            client.connect()
            log_message("Reconnected to MQTT broker.")
            await subscribe_to_topics()  # Re-subscribe to topics after reconnecting
            return  # Exit on successful reconnection

        except OSError as e:
            log_message(f"Failed to reconnect to MQTT broker: {e}")
            mqtt_attempt_count += 1
            await asyncio.sleep(mqtt_retry_delay)

            # Exponential backoff for MQTT retries
            mqtt_retry_delay = min(mqtt_retry_delay * 2, 60)  # Cap retry delay at 60 seconds

    log_message("Max MQTT reconnection attempts reached. Will continue running without MQTT.")
    # Consider staying in Wi-Fi mode without MQTT if desired
    return

# New helper to monitor memory usage
def monitor_memory():
    free_memory = gc.mem_free()
    #log_message(f"Free memory: {free_memory}")
    if free_memory < 10000:  # Set a threshold that you feel is safe
        log_message("Low memory warning!")
        gc.collect()

# Web server app
app = Microdot()

@app.route('/logs-page')
def logs_page(request):
    return send_file('logs.html')

@app.route('/get-logs')
def get_logs(request):
    try:
        with open(LOG_FILE, 'r') as f:
            logs = f.read()
        return logs, {'Content-Type': 'text/plain'}
    except OSError as e:
        log_message(f"Error reading logs: {e}")
        return 'Error reading logs', 500
    except Exception as e:
        log_message(f"Unexpected error: {e}")
        return 'Unexpected error', 500


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
    gc.collect()  # Run garbage collection
    reset()
    return 'Restarting...', 200

@app.route('/relay/<pin>/<state>')
def toggle_relay(request, pin, state):
    pin = int(pin)
    if 0 <= pin < len(relays):
        if state == 'on':
            relays[pin].value(1)
            log_message(f"Relay {pin+1} turned on manually.")
        elif state == 'off':
            relays[pin].value(0)
            log_message(f"Relay {pin+1} turned off manually.")
        else:
            return 'Invalid state', 400

        # Check if MQTT is enabled and client is connected
        if MQTT == 1:
            try:
                publish_relay_status(client, pin, relays[pin].value())
                log_message(f"Published MQTT message for Relay {pin+1} state: {state}")
            except Exception as e:
                log_message(f"Failed to publish MQTT message for Relay {pin+1}: {e}")
        
        return f'Relay {state}!'
    
    return 'Invalid relay or command', 400
@app.route('/')
def index(request):
    return send_file('index.html')

@app.route('/settings')
def settings(request):
    return send_file('settings.html')

@app.route('/save-settings', methods=['POST'])
def save_settings_route(request):
    settings = request.json
    if settings is None:
        return 'Invalid settings data', 400  # Respond with an error if no data is received
    try:
        save_settings(settings)
        return 'Settings saved successfully', 200
    except ValueError as e:
        log_message(f"Error saving settings: {e}")
        return 'Failed to save settings', 500


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

@app.route('/relay-timer/<pin>/<minutes>')
def start_timer(request, pin, minutes):
    try:
        pin = int(pin)
        minutes = int(minutes)

        if 0 <= pin < len(relays) and minutes > 0:
            relays[pin].value(1)
            timers[pin] = {
                'end_time': time.time() + minutes * 60
            }
            log_message(f"Zone {pin + 1} turned ON (timer set for {minutes} minutes).")
            if MQTT == 1:
                publish_relay_status(client, pin, 1)
            return 'Timer started', 200

        return 'Invalid input', 400
    except Exception as e:
        log_message(f"Error in /relay-timer route: {e}")
        return 'Failed to start timer', 500

@app.route('/timer-status')
def timer_status(request):
    try:
        now = time.time()
        status = {}

        expired = []

        for pin, data in list(timers.items()):  # safe iteration over changing dict
            end_time = data['end_time']
            remaining = int(end_time - now)

            if remaining > 0:
                status[str(pin)] = remaining
            else:
                relays[pin].value(0)
                log_message(f"Zone {pin + 1} timer expired. Relay turned OFF.")
                if MQTT == 1:
                    publish_relay_status(client, pin, 0)
                expired.append(pin)

        # Remove expired timers
        for pin in expired:
            del timers[pin]

        #log_message(f"Timer status response: {status}")
        return ujson.dumps(status), {'Content-Type': 'application/json'}

    except Exception as e:
        log_message(f"Error in /timer-status: {e}")
        return '{}', 500


@app.route('/cancel-timer/<pin>')
def cancel_timer(request, pin):
    try:
        pin = int(pin)
        if pin in timers:
            del timers[pin]
            relays[pin].value(0)
            log_message(f"Manual timer canceled for Zone {pin + 1}. Relay turned OFF.")
            if MQTT == 1:
                publish_relay_status(client, pin, 0)
        return 'Timer canceled', 200
    except Exception as e:
        log_message(f"Error in /cancel-timer route: {e}")
        return 'Failed to cancel timer', 500


@app.route('/relay/<int:pin>/<state>')
async def relay_toggle(request, pin, state):
    try:
        if 0 <= pin < len(relays):
            if state == 'on':
                relays[pin].value(1)
                log_message(f"Zone {pin + 1} manually turned ON.")
                if MQTT == 1:
                    publish_relay_status(client, pin, 1)
            else:
                relays[pin].value(0)
                log_message(f"Zone {pin + 1} manually turned OFF.")
                if MQTT == 1:
                    publish_relay_status(client, pin, 0)
            return 'OK', 200
        return 'Invalid pin', 400
    except Exception as e:
        log_message(f"Error in /relay route: {e}")
        return 'Error', 500

@app.route('/set-rain-delay/<days>')
def set_rain_delay(request, days):
    try:
        days = int(days)
        with open("rain_delay.json", "w") as f:
            ujson.dump({"days_remaining": days}, f)
        log_message(f"Rain delay set for {days} days.")
        return "Rain delay set", 200
    except:
        return "Invalid input", 400
    
@app.route('/get-rain-delay')
def get_rain_delay(request):
    try:
        with open("rain_delay.json") as f:
            data = ujson.load(f)
        return ujson.dumps(data), {'Content-Type': 'application/json'}
    except:
        return ujson.dumps({"days_remaining": 0}), {'Content-Type': 'application/json'}



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
    max_wifi_attempts = 10
    retry_delay = 2
    attempt_count = 0

    while attempt_count < max_wifi_attempts:
        try:
            ap = network.WLAN(network.AP_IF)
            ap.active(False)

            wifi = network.WLAN(network.STA_IF)
            wifi.active(True)
            wifi.config(pm=0)

            # Set a fixed mDNS hostname to "sprinklers"
            wifi.config(dhcp_hostname="sprinklers")

            wifi.connect(SSID, PASSWORD)

            if wifi.isconnected():
                log_message('Connected to Wi-Fi as sprinklers.local')

                # Print the network configuration for debugging
                print('Wifi connected as sprinklers.local, net={}, gw={}, dns={}'.format(*wifi.ifconfig()))

                return  # Successfully connected

            else:
                log_message(f"Wi-Fi connection attempt {attempt_count + 1} failed. Retrying...")
                attempt_count += 1
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)

        except Exception as e:
            log_message(f"Exception during Wi-Fi connection: {e}")
            attempt_count += 1
            await asyncio.sleep(retry_delay)

    log_message('Failed to connect to Wi-Fi after all attempts. Entering AP mode as a last resort.')
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
        await asyncio.sleep(86400)  # Sync time every day
        gc.collect()  # Run garbage collection

async def check_schedules():
    global timers
    last_minute_check = -1

    def get_rain_delay_days_remaining():
        try:
            with open("rain_delay.json") as f:
                return int(ujson.load(f).get("days_remaining", 0))
        except Exception as e:
            log_message(f"Error reading rain delay: {e}")
            return 0

    def decrement_rain_delay():
        try:
            with open("rain_delay.json") as f:
                delay_data = ujson.load(f)
        except:
            delay_data = {"days_remaining": 0}

        remaining = max(0, int(delay_data.get("days_remaining", 0)) - 1)
        with open("rain_delay.json", "w") as f:
            ujson.dump({"days_remaining": remaining}, f)
        log_message(f"Rain delay updated: {remaining} days remaining.")

    last_checked_day = None  # So we only decrement once per new day

    while True:
        try:
            now = time.localtime()
            current_hour = now[3]
            current_minute = now[4]
            current_time = f"{current_hour:02}:{current_minute:02}"
            current_day = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][now[6]]

            # 🌧 Decrement rain delay once per day
            today_date = now[2]
            if today_date != last_checked_day:
                decrement_rain_delay()
                last_checked_day = today_date

            rain_delay = get_rain_delay_days_remaining()

            # 🕒 Run schedule logic once per minute
            if current_minute != last_minute_check:
                last_minute_check = current_minute

                try:
                    with open('schedules.json') as f:
                        schedules = ujson.load(f)
                except Exception as e:
                    log_message(f"Failed to load schedule: {e}")
                    schedules = []

                
                for pin, schedule in enumerate(schedules):
                    if not schedule.get('enabled', False):
                        continue

                    days = schedule.get('days', [])
                    on_time = schedule.get('onTime')
                    off_time = schedule.get('offTime')

                    if current_day in days:
                        if current_time == on_time:
                            if rain_delay > 0:
                                log_message(f"Rain delay active — skipping schedules (days remaining: {rain_delay})")
                            else:    
                                relays[pin].value(1)
                                timers[pin] = {
                                'end_time': time.time() + 3600,  # fallback 1 hr
                                'task': None
                                }
                                log_message(f"Relay {pin+1} turned ON at {current_time} (schedule).")
                                if MQTT == 1:
                                    publish_relay_status(client, pin, 1)

                        elif current_time == off_time:
                            if rain_delay > 0:
                                log_message(f"Rain delay active — skipping schedules (days remaining: {rain_delay})")
                            else: 
                                relays[pin].value(0)
                                if pin in timers:
                                    del timers[pin]
                                log_message(f"Relay {pin+1} turned OFF at {current_time} (schedule).")
                                if MQTT == 1:
                                    publish_relay_status(client, pin, 0)

            # ⏳ Timer expiration logic (manual or scheduled)
            current_epoch = time.time()
            expired = []

            for pin, data in list(timers.items()):
                if current_epoch >= data['end_time']:
                    relays[pin].value(0)
                    log_message(f"Zone {pin+1} timer expired. Relay turned OFF.")
                    if MQTT == 1:
                        publish_relay_status(client, pin, 0)
                    expired.append(pin)

            for pin in expired:
                del timers[pin]

            monitor_memory()

        except Exception as e:
            log_message(f"Exception in check_schedules: {e}")
            gc.collect()

        await asyncio.sleep(1)






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
        payload = "ON" if status else "OFF"
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
        ap.config(essid=ap_ssid, password=ap_password, authmode=3)

        log_message(f"Configuration mode activated. Connect to AP: {ap_ssid} with password:{ap_password}")
        log_message("Visit http://192.168.4.1 in your web browser to configure.")

        # Start the web server to handle the configuration page
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

    try:
        # Manually create a socket for Microdot
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.bind(('0.0.0.0', 80))
        server_socket.listen(5)

        log_message("Microdot server running and listening for connections...")

        while True:
            try:
                client_sock, addr = server_socket.accept()
                #log_message(f"Accepted connection from {addr}")

                try:
                    app.handle_request(client_sock, addr)
                except OSError as e:
                    if e.errno in [104, 128]:  # ECONNRESET, ENOTCONN
                        log_message(f"Connection error: {e} from {addr}")
                    else:
                        raise
                finally:
                    client_sock.close()

            except OSError as e:
                log_message(f"Server socket error: {e}")
                if e.errno == 128:  # ENOTCONN
                    log_message(f"ENOTCONN error: {e}")
                else:
                    raise

    except Exception as e:
        log_message(f"Failed to start or run Microdot server: {e}")

        
        
async def main():
    if MQTT == 0:
        await main_without_mqtt()
    try:
        disconnect_from_wifi()
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
        disconnect_from_wifi()
        await connect_to_wifi()
        start_new_thread(run_server, ())
        await asyncio.gather(
            #sync_time(),
            check_schedules()
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