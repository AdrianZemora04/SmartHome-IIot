import serial
import mraa
import time
import threading
import paho.mqtt.client as mqtt
from opcua import Server
import sys

# Watchdog
def watchdog_feed():
    try:
        wd = open('/dev/watchdog', 'w')
        while True:
            wd.write('1')
            wd.flush()
            time.sleep(10)
    except Exception as e:
        print(f"Watchdog error: {e}")

wd_thread = threading.Thread(target=watchdog_feed, daemon=True)
wd_thread.start()
print("Watchdog pornit!")

# MQTT
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqtt_client.connect("localhost", 1883)
mqtt_client.loop_start()

# OPC-UA Server
opcua_server = Server()
opcua_server.set_endpoint("opc.tcp://0.0.0.0:4840/smarthome")
uri = "http://smarthome.iot2050"
idx = opcua_server.register_namespace(uri)
objects = opcua_server.get_objects_node()
smarthome = objects.add_object(idx, "SmartHome")
temperatura_node = smarthome.add_variable(idx, "temperatura", 0.0)
umiditate_node = smarthome.add_variable(idx, "umiditate", 0.0)
distanta_node = smarthome.add_variable(idx, "distanta", 0.0)
usa_node = smarthome.add_variable(idx, "usa", "inchisa")
geam_node = smarthome.add_variable(idx, "geam", "inchis")
alarm_node = smarthome.add_variable(idx, "alarm", "OK")
temperatura_node.set_writable()
umiditate_node.set_writable()
distanta_node.set_writable()
usa_node.set_writable()
geam_node.set_writable()
alarm_node.set_writable()
opcua_server.start()
print("OPC-UA Server pornit!")

# LCD setup
rs = mraa.Gpio(12)
en = mraa.Gpio(11)
d4 = mraa.Gpio(5)
d5 = mraa.Gpio(4)
d6 = mraa.Gpio(3)
d7 = mraa.Gpio(2)

for pin in [rs, en, d4, d5, d6, d7]:
    pin.dir(mraa.DIR_OUT)
    pin.write(0)

def pulse_enable():
    en.write(0); time.sleep(0.001)
    en.write(1); time.sleep(0.001)
    en.write(0); time.sleep(0.001)

def send_nibble(nibble):
    d4.write((nibble >> 0) & 1)
    d5.write((nibble >> 1) & 1)
    d6.write((nibble >> 2) & 1)
    d7.write((nibble >> 3) & 1)
    pulse_enable()

def send_byte(byte, mode):
    rs.write(mode)
    send_nibble(byte >> 4)
    send_nibble(byte & 0x0F)

def lcd_init():
    time.sleep(0.1)
    send_nibble(0x03); time.sleep(0.005)
    send_nibble(0x03); time.sleep(0.005)
    send_nibble(0x03); time.sleep(0.005)
    send_nibble(0x02); time.sleep(0.005)
    send_byte(0x28, 0)
    send_byte(0x0C, 0)
    send_byte(0x06, 0)
    send_byte(0x01, 0)
    time.sleep(0.005)

def lcd_clear():
    send_byte(0x01, 0)
    time.sleep(0.002)

def lcd_print(text):
    for char in text:
        send_byte(ord(char), 1)

def lcd_set_cursor(col, row):
    offsets = [0x00, 0x40]
    send_byte(0x80 | (col + offsets[row]), 0)

# Servo usa
TRIG = mraa.Gpio(10)
ECHO = mraa.Gpio(9)
SERVO = mraa.Pwm(6)

TRIG.dir(mraa.DIR_OUT)
ECHO.dir(mraa.DIR_IN)
SERVO.period_us(20000)
SERVO.enable(True)

def set_servo(degrees):
    pulse = 500 + (degrees / 180.0) * 2000
    duty = pulse / 20000.0
    SERVO.write(duty)

def get_distance():
    readings = []
    for _ in range(5):
        TRIG.write(0)
        time.sleep(0.000002)
        TRIG.write(1)
        time.sleep(0.00001)
        TRIG.write(0)
        timeout = time.time() + 0.03
        while ECHO.read() == 0:
            if time.time() > timeout:
                break
        start = time.time()
        timeout = time.time() + 0.03
        while ECHO.read() == 1:
            if time.time() > timeout:
                break
        end = time.time()
        dist = (end - start) * 34300 / 2
        if 0 < dist < 200:
            readings.append(dist)
        time.sleep(0.01)

    if readings:
        return sum(readings) / len(readings)
    return -1

# Servo geam
SERVO_GEAM = mraa.Pwm(7)
SERVO_GEAM.period_us(20000)
SERVO_GEAM.enable(True)

def set_servo_geam(degrees):
    pulse = 500 + (degrees / 180.0) * 2000
    duty = pulse / 20000.0
    SERVO_GEAM.write(duty)

# LED
LED = mraa.Gpio(0)
LED.dir(mraa.DIR_OUT)
LED.write(0)

# E-Stop
ESTOP = mraa.Gpio(1)
ESTOP.dir(mraa.DIR_IN)
ESTOP.mode(mraa.MODE_PULLUP)

alarm_active = False

def estop_handler(args):
    global alarm_active
    alarm_active = True
    print("E-STOP ACTIVAT")
    SERVO.enable(False)
    SERVO_GEAM.enable(False)
    LED.write(0)
    mqtt_client.publish("smarthome/alarm", "ESTOP")
    alarm_node.set_value("ESTOP")
    lcd_clear()
    lcd_set_cursor(0, 0)
    lcd_print("ALARM")
    lcd_set_cursor(0, 1)
    lcd_print("E-STOP ACTIVAT")

ESTOP.isr(mraa.EDGE_RISING, estop_handler, None)

# Serial DHT22
ser = serial.Serial('/dev/ttyACM0', 9600, timeout=1)

time.sleep(1)
lcd_init()
lcd_print("Smart Home")
time.sleep(2)
lcd_clear()
set_servo(10)
set_servo_geam(90)
time.sleep(0.5)

temperatura = "--"
umiditate = "--"
distanta = "--"
usa = "inchisa"
geam = "deschis"
display_state = 0
last_switch = time.time()

print("Pornit!")

try:
    while True:
        # Daca e alarm activ=>stop
        if alarm_active:
            time.sleep(0.1)
            continue

        #dht22
        if ser.in_waiting > 0:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if line:
                print(line)
                if "Temperatura" in line:
                    temperatura = line.split(":")[1].strip().replace("°C", "").strip()
                    mqtt_client.publish("smarthome/temperatura", temperatura)
                    try:
                        temperatura_node.set_value(float(temperatura))
                    except:
                        pass
                elif "Umiditate" in line:
                    umiditate = line.split(":")[1].strip().replace("%", "").strip()
                    mqtt_client.publish("smarthome/umiditate", umiditate)
                    try:
                        umiditate_node.set_value(float(umiditate))
                        if float(umiditate) > 60:
                            set_servo_geam(10)
                            geam = "inchis"
                            mqtt_client.publish("smarthome/geam", "inchis")
                            geam_node.set_value("inchis")
                        else:
                            set_servo_geam(90)
                            geam = "deschis"
                            mqtt_client.publish("smarthome/geam", "deschis")
                            geam_node.set_value("deschis")
                    except:
                        pass

        #distanta si servo usa
        dist = get_distance()
        if dist != -1:
            distanta = f"{dist:.1f}"
            mqtt_client.publish("smarthome/distanta", distanta)
            distanta_node.set_value(float(distanta))
            if dist < 10:
                set_servo(90)
                usa = "deschisa"
                LED.write(1)
                mqtt_client.publish("smarthome/usa", "deschisa")
                usa_node.set_value("deschisa")
            else:
                set_servo(10)
                usa = "inchisa"
                LED.write(0)
                mqtt_client.publish("smarthome/usa", "inchisa")
                usa_node.set_value("inchisa")

        # LCD 10 secunde
        if time.time() - last_switch >= 10:
            display_state = (display_state + 1) % 3
            last_switch = time.time()
            lcd_clear()

        if display_state == 0:
            lcd_set_cursor(0, 0)
            lcd_print(f"Temp: {temperatura} C  ")
            lcd_set_cursor(0, 1)
            lcd_print(f"Umid: {umiditate} %  ")
        elif display_state == 1:
            lcd_set_cursor(0, 0)
            lcd_print(f"Dist: {distanta} cm  ")
            lcd_set_cursor(0, 1)
            lcd_print(f"Usa: {usa}    ")
        else:
            lcd_set_cursor(0, 0)
            lcd_print(f"Geam: {geam}    ")
            lcd_set_cursor(0, 1)
            lcd_print(f"Umid: {umiditate} %  ")

        time.sleep(0.05)

except KeyboardInterrupt:
    mqtt_client.publish("smarthome/usa", "inchisa")
    mqtt_client.publish("smarthome/geam", "inchis")
    mqtt_client.loop_stop()
    SERVO.enable(False)
    SERVO_GEAM.enable(False)
    LED.write(0)
    opcua_server.stop()
    lcd_clear()
    print("Oprit.")