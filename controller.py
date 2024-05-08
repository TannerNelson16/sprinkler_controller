from microdot import Microdot, send_file
import network
from machine import Pin, RTC
import ujson
import time
from _thread import start_new_thread
from machine import UART
import uasyncio as asyncio
from umqtt.simple import MQTTClient


# Constants
SSID = 'yourssid'
PASSWORD = 'yourwifipassword'
RELAY_PINS = [13, 5, 14, 27, 26, 25, 33, 32, 18, 19]  # Update your GPIO pin numbers here

MQTT_BROKER = "MQTT.BROKER.IP.ADDRESS"
MQTT_USER = "MQTT_Username"
MQTT_PASSWORD = "MQTT_Password"
CLIENT_ID = "intellidwell_sprinkler_controller"
TOPIC_BASE = "home/sprinkler_zones/"
# Set up WiFi
wifi = network.WLAN(network.STA_IF)
wifi.active(True)
wifi.connect(SSID, PASSWORD)

while not wifi.isconnected():
    pass

print('Connected to WiFi at', wifi.ifconfig()[0])

# Set up relay pins
relays = [Pin(pin, Pin.OUT) for pin in RELAY_PINS]


# Load or create a schedule store
try:
    with open('schedules.json', 'r') as f:
        schedules = ujson.load(f)
except (OSError, ValueError):
    schedules = [{} for _ in RELAY_PINS]
    with open('schedules.json', 'w') as f:
        ujson.dump(schedules, f)

# Web server app
app = Microdot()

@app.route('/relay/<pin>/<state>')
def toggle_relay(request, pin, state):
    pin = int(pin)
    if pin >= 0 and pin < len(relays):
        if state == 'on':
            relays[pin].value(1)  # Set GPIO high
            print(f"Relay {pin+1} turned on manually.")
            publish_relay_status(client, pin, relays[pin].value())
            return 'Relay turned on!'
            
        elif state == 'off':
            relays[pin].value(0)  # Set GPIO low
            print(f"Relay {pin+1} turned off manually.")
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

@app.route('/get-schedules')
def get_schedules(request):
    return ujson.dumps(schedules), {'Content-Type': 'application/json'}


def check_schedules():
    while True:
        now = time.localtime()
        day = now[6]  # Day of the week, 0-6, Monday is 0
        current_time = f"{now[3]:02}:{now[4]:02}"  # Current time in HH:MM format
       # print(f"Checking schedules at {current_time}...")  # Debug output
       # print(day)
        
        if day == 0:
            current_day = "Mon"
        elif day == 1:
            current_day = "Tue"
        elif day == 2:
            current_day = "Wed"
        elif day == 3:
            current_day = "Thu"
        elif day == 4:
            current_day = "Fri"
        elif day == 5:
            current_day = "Sat"
        elif day == 6:
            current_day = "Sun"
            
        for pin, schedule in enumerate(schedules):
            days = schedule.get('days', [])
            on_time = schedule.get('onTime')
            off_time = schedule.get('offTime')
#            print(f"Relay {pin+1}: Days: {days}, On: {on_time}, Off: {off_time}")  # More debug output

            if str(current_day) in days:
                if current_time == on_time:
                    relays[pin].value(1)
                    publish_relay_status(client, pin, relays[pin].value())
                    print(f"Relay {pin+1} turned on at {current_time}")
                elif current_time == off_time:
                    relays[pin].value(0)
                    publish_relay_status(client, pin, relays[pin].value())
                    print(f"Relay {pin+1} turned off at {current_time}")

        time.sleep(15)  # Check every minute

# Setup MQTT client


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
    payload = "ON" if status else "OFF"
    client.publish(topic, payload)
    
def command_callback(topic, msg):
    print(f"Command received: Topic: {topic}, Message: {msg}")
    # Convert bytes to string for easier handling
    topic = topic.decode()
    msg = msg.decode()
    # Assuming topic format "cmnd/relays/{pin}/power"
    parts = topic.split('/')
    pin = int(parts[2])
    if msg == "ON":
        relays[pin].value(1)
    elif msg == "OFF":
        relays[pin].value(0)
    publish_relay_status(client, pin, relays[pin].value())


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

client = MQTTClient(CLIENT_ID, MQTT_BROKER, user=MQTT_USER, password=MQTT_PASSWORD)
client.set_callback(command_callback)
client.connect()

for i in range(len(relays)):
    client.subscribe(f"cmnd/zones/{i}/power")

publish_discovery(client)

start_new_thread(check_schedules, ())
start_new_thread(check_messages, ())
# Start server
app.run(host='0.0.0.0', port=80)