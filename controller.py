from microdot import Microdot, send_file
import network
from machine import Pin, RTC
import ujson
import time
from _thread import start_new_thread
from machine import UART
import uasyncio as asyncio
from umqtt.simple import MQTTClient
import usocket


# Constants
SSID = 'your_wifi_SSID'
PASSWORD = 'your wifi password'
RELAY_PINS = [13, 21, 14, 27, 26, 25, 33, 32, 19, 18]  # Update your GPIO pin numbers here
relays = [Pin(pin, Pin.OUT) for pin in RELAY_PINS]

for i in range(10):
    relays[i].value(1)

global MQTT
MQTT=1
MQTT_BROKER = "your.mqtt.ip.address"
MQTT_USER = "your mqtt username"
MQTT_PASSWORD = "your mqtt password"
CLIENT_ID = "intellidwell_sprinkler_controller"
TOPIC_BASE = "home/sprinkler_zones/"

    
def connect_to_wifi():
    ap = network.WLAN(network.AP_IF)
    ap.active(False)
    # Set up WiFi
    wifi = network.WLAN(network.STA_IF)
    wifi.active(True)
    wifi.connect(SSID, PASSWORD)
    count = 0
    while not wifi.isconnected():
        count = count + 1
        print("Connecting to WiFi...")
        time.sleep(1)
        if count > 15 and not wifi.isconnected():    
            pass

    print('Connected to WiFi at', wifi.ifconfig()[0])



# Web server app
app = Microdot()

@app.route('/relay/<pin>/<state>')
def toggle_relay(request, pin, state):
    pin = int(pin)
    if pin >= 0 and pin < len(relays):
        if state == 'on':
            relays[pin].value(0)  # Set GPIO high
            print(f"Relay {pin+1} turned on manually.")
            if MQTT == 1:
                publish_relay_status(client, pin, relays[pin].value())
            return 'Relay turned on!'
            
        elif state == 'off':
            relays[pin].value(1)  # Set GPIO low
            print(f"Relay {pin+1} turned off manually.")
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



def check_schedules():
    while True:
        now = time.localtime()
        current_time = f"{now[3]:02}:{now[4]:02}"
        current_day = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][now[6]]
        
        for pin, schedule in enumerate(schedules):
            if schedule.get('enabled', False):  # Assume False as default if not specified
                if current_day in schedule.get('days', []) and schedule.get('onTime') == current_time:
                    relays[pin].value(0)
                    if MQTT == 1:
                        publish_relay_status(client, pin, relays[pin].value())
                    print(f"Relay {pin+1} turned on at {current_time}")
                elif schedule.get('offTime') == current_time:
                    relays[pin].value(1)
                    if MQTT == 1:
                        publish_relay_status(client, pin, relays[pin].value())
                    print(f"Relay {pin+1} turned off at {current_time}")
        time.sleep(15)    

def publish_discovery(client):
    base_topic = "homeassistant/switch/zone_{}"
    for i in range(len(relays)):
        topic = base_topic.format(i) + "/config"
        payload = {
            "name": f"Sprinkler Controller",
            "command_topic": f"cmnd/zones/{i}/power",
            "state_topic": f"stat/zones/{i}/state",
            "payload_on": "ON",
            "payload_off": "OFF",
            "unique_id": f"esp32_zone_{i}",
            "device": {
                "identifiers": [f"esp32_zone_{i}"],
                "name": f"Zone {i + 1}",
                "manufacturer": "Intellidwell",
                "model": "Sprinkler Controller",
                "sw_version": "1.0"
            },
            "platform": "mqtt"
        }
        client.publish(topic, ujson.dumps(payload), retain=True)


def publish_relay_status(client, relay, status):
    
    topic = f"stat/zones/{relay}/state"
    payload = "OFF" if status else "ON"
    client.publish(topic, payload)
    
def update_schedule_status(pin, status):
    if 0 <= pin < len(schedules):
        schedules[pin]['enabled'] = status
        with open('schedules.json', 'w') as f:
            ujson.dump(schedules, f)
        print(f"Schedule for Relay {pin+1} {'enabled' if status else 'disabled'}!")
        return True
    return False

def command_callback(topic, msg):
    print(f"Command received: Topic: {topic}, Message: {msg}")
    # Decode bytes to string for easier handling
    topic = topic.decode()
    msg = msg.decode()
    parts = topic.split('/')
    
    # Check for relay control commands
    if len(parts) == 4 and parts[0] == "cmnd" and parts[3] == "power":
        pin = int(parts[2])
        if msg == "ON":
            relays[pin].value(0)  # Assuming '0' is ON for your relays
        elif msg == "OFF":
            relays[pin].value(1)  # Assuming '1' is OFF for your relays
        publish_relay_status(client, pin, relays[pin].value())

    # Check for schedule control commands
    elif len(parts) == 4 and parts[1] == "zones" and parts[3] == "schedule":
        pin = int(parts[2])
        status = msg.lower() == "true"
        if update_schedule_status(pin, status):
            publish_schedule_status(client, pin, status)
        else:
            print("Invalid schedule operation")

client = MQTTClient(CLIENT_ID, MQTT_BROKER, user=MQTT_USER, password=MQTT_PASSWORD)
client.set_callback(command_callback)

def check_messages():
    while True:
        try:
            client.check_msg()
        except OSError as e:
            print("Failed to check MQTT messages:", e)
            reconnect()
    time.sleep(1)
    
def reconnect():
    print("Reconnecting to MQTT broker...")
    try:
        client.connect()
        #client.subscribe(f"{TOPIC_BASE}#")
    except OSError as e:
        print("Failed to reconnect:", e)
        client.connect()
        
 
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
    #ap_ssid = "Mosby2"
    ap_password = "Sprinkler12345"
    
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid=ap_ssid, password=ap_password)

    print("Configuration mode activated. Connect to AP:", ap_ssid, "with password:", ap_password)
    print("Visit http://192.168.4.1 in your web browser to configure.")

    # Serve the configuration page
    app.run(host='0.0.0.0', port=80)
    
    while True:
        time.sleep(300)
        machine.reset()


def setup_mqtt_topics(client):
    for i in range(10):
        command_topic = f"cmnd/zones/{i}/schedule"
        status_topic = f"stat/zones/{i}/schedule"
        client.subscribe(command_topic)
        publish_schedule_discovery(client, i, command_topic, status_topic)

def publish_schedule_discovery(client, zone, command_topic, status_topic):
    topic = f"homeassistant/switch/zone_{zone}_schedule/config"
    payload = {
        "name": f"Scheduler",
        "command_topic": command_topic,
        "state_topic": status_topic,
        "payload_on": "true",
        "payload_off": "false",
        "unique_id": f"zone_{zone}_schedule_switch",
        "device": {
            "identifiers": [f"zone_{zone}"],
            "name": f"Zone {zone + 1}",
            "manufacturer": "Intellidwell",
            "model": "Sprinkler Scheduler",
            "sw_version": "1.0"
        },
        "platform": "mqtt"
    }
    client.publish(topic, ujson.dumps(payload), retain=True)
    
def publish_schedule_status(client, pin, enabled):
    topic = f"stat/zones/{pin}/schedule"
    payload = "true" if enabled else "false"
    client.publish(topic, payload)
        

def main():
    
    time.sleep(3)
    connect_to_wifi()
    # Set up relay pins

    client.connect()

    for i in range(len(relays)):
        client.subscribe(f"cmnd/zones/{i}/power")
    
    publish_discovery(client)
    setup_mqtt_topics(client)
    start_new_thread(check_schedules, ())
    start_new_thread(check_messages, ())
    # Start server
    app.run(host='0.0.0.0', port=80)

def main_without_mqtt():
    global MQTT
    MQTT = 0
    time.sleep(3)
    connect_to_wifi()

    start_new_thread(check_schedules, ())
    # Start server
    app.run(host='0.0.0.0', port=80)

def main_without_mqtt_or_wifi():
    global MQTT
    MQTT = 0
    start_new_thread(check_schedules, ())
    enter_AP_mode()
    

try:
    main()
    #client.subscribe(f"{TOPIC_BASE}#")
except OSError as e:
    print("Error found. Restarting without MQTT")
    
    try:
        main_without_mqtt()
    except OSError as e:
        print("Error found again. Restarting without MQTT or WIFI")
        try:
            main_without_mqtt_or_wifi()
        except OSError as e:
            print("Serious error!")
            