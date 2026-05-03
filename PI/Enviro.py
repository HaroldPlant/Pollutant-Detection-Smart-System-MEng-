import time
import math
import csv
import os
from datetime import datetime
from smbus2 import SMBus
from bme280 import BME280
from ltr559 import LTR559
from enviroplus.gas import read_all
from PIL import Image, ImageDraw, ImageFont
import st7735 as ST7735

TEMP_OFFSET   = 10
LOG_FILE      = "/home/pi/enviro_log.csv"
LOG_INTERVAL  = 10
DISPLAY_CYCLE = 3

#Hardware initialisation.

#Open communication channel (the I2C communication bus).
bus = SMBus(1)

#Connects to BME280 sensor over the bus and allows it to wake for half a second before forcing it to #continuously take readings.
bme280 = BME280(i2c_dev=bus)
time.sleep(0.5)
bme280.setup(mode="forced")
time.sleep(0.5)

#Connects to light sensor over the bus and allows it to wake for half a second.
ltr559 = LTR559()
time.sleep(0.5)

#Configures the small LCD display mounted to the Enviro+ hat.
#Configures relevant information such as which pins is connected and correct rotation for display.
#Begins the displays (powers on).
disp = ST7735.ST7735(
    port=0, cs=1, dc=9, backlight=12,
    rotation=270, spi_speed_hz=10000000
)
disp.begin()
WIDTH  = disp.width
HEIGHT = disp.height

#Error handling - if desired font files not found, it falls back to original settings.
try:
    font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
    font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
except IOError:
    font_large = ImageFont.load_default()
    font_small = ImageFont.load_default()


#Sensors on Enviro+ hat measure resistance to the pollutants rather than their concentration.
#Conversion calculations performed as the resistance to a pollutant is logarithmically proportional to its #concentration.
#Error handling - if sensor reads a 0 or negative value, can happen during warm up, then no reading returned
#so as to avoid crashing.
def ohms_to_ppm_co(ohms):
    try:
        return round(math.pow(10, (math.log10(ohms / 750000) / -0.75)), 2)
    except (ValueError, ZeroDivisionError):
        return None

def ohms_to_ppm_no2(ohms):
    try:
        return round(math.pow(10, (math.log10(ohms / 15000) / 0.8)), 2)
    except (ValueError, ZeroDivisionError):
        return None

def ohms_to_ppm_nh3(ohms):
    try:
        return round(math.pow(10, (math.log10(ohms / 1500000) / -1.0)), 2)
    except (ValueError, ZeroDivisionError):
        return None


#Overwrites the CSV file so that the correct headers of the information is written.
def init_csv():
    with open(LOG_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "timestamp", "co_ohms", "co_ppm",
            "no2_ohms", "no2_ppm", "nh3_ohms", "nh3_ppm"
        ])


#Appends a single new row to the CSV file under the correct headers of all the relevant readings as well as
#the new calculated concentrations.
def log_to_csv(data):
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            data.get("co_ohms"),
            data.get("co_ppm"),
            data.get("no2_ohms"),
            data.get("no2_ppm"),
            data.get("nh3_ohms"),
            data.get("nh3_ppm"),
        ])


#Defines to screens the LCD display cycles between.
SCREENS = ["weather", "gas"]


#Blank screen generated
def draw_screen(screen, data):
    img  = Image.new("RGB", (WIDTH, HEIGHT), (15, 15, 25))
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0, 0), (WIDTH, 20)], fill=(30, 30, 50))

#Outputs relevant information to the weather screen.
    if screen == "weather":
        draw.text((4, 2),  "WEATHER", font=font_small, fill=(140, 180, 255))
        draw.text((4, 24), f"{data.get('temperature', '--')}°C", font=font_large, fill=(255, 255, 255))
        draw.text((4, 46), f"Humidity:  {data.get('humidity', '--')}%", font=font_small, fill=(180, 220, 255))
        draw.text((4, 60), f"Pressure:  {data.get('pressure', '--')} hPa", font=font_small, fill=(180, 220, 255))
        draw.text((4, 74), f"Light:     {data.get('light', '--')} lux", font=font_small, fill=(180, 220, 255))

#Outputs relevant information to the gas screen.
    elif screen == "gas":
        draw.text((4, 2),  "AIR QUALITY", font=font_small, fill=(140, 255, 180))
        draw.text((4, 24), f"CO:   {data.get('co_ppm', '--')} ppm", font=font_small, fill=(255, 255, 255))
        draw.text((4, 40), f"NO2:  {data.get('no2_ppm', '--')} ppm", font=font_small, fill=(255, 220, 120))
        draw.text((4, 56), f"NH3:  {data.get('nh3_ppm', '--')} ppm", font=font_small, fill=(180, 255, 220))

#Cuurent time always displayed across both screens.
    ts = datetime.now().strftime("%H:%M:%S")
    draw.text((4, HEIGHT - 14), ts, font=font_small, fill=(80, 80, 100))
    disp.display(img)

#Pulls data from every sensor ready to be sent elsewhere in the script for handling.
def read_sensors():
    data = {}


#Error handling - Each sensor is read separately so in the event of  failure, the subsequent sensor readings
#are not affected.
#Temperature offset is implemented to counteract the heat generated from the Pi itself.
    try:
        data["temperature"] = round(bme280.get_temperature() - TEMP_OFFSET, 1)
        data["pressure"]    = round(bme280.get_pressure(), 1)
        data["humidity"]    = round(bme280.get_humidity(), 1)
    except Exception as e:
        print(f"BME280 error: {e}")

    try:
        data["light"] = round(ltr559.get_lux(), 1)
    except Exception as e:
        print(f"LTR559 error: {e}")

#Passes the resistance values to the conversion method before storing the concentration calculations.
    try:
        gas = read_all()
        data["co_ohms"]  = round(gas.reducing, 0)
        data["no2_ohms"] = round(gas.oxidising, 0)
        data["nh3_ohms"] = round(gas.nh3, 0)
        data["co_ppm"]   = ohms_to_ppm_co(gas.reducing)
        data["no2_ppm"]  = ohms_to_ppm_no2(gas.oxidising)
        data["nh3_ppm"]  = ohms_to_ppm_nh3(gas.nh3)
    except Exception as e:
        print(f"MICS6814 error: {e}")

    return data


#Prints organised summary of all data to the terminal for monitoring purposes.
def print_readings(data):
    print(f"[{datetime.now().strftime('%H:%M:%S')}]")
    print(f"  Temperature : {data.get('temperature', 'N/A')}°C")
    print(f"  Pressure    : {data.get('pressure',    'N/A')} hPa")
    print(f"  Humidity    : {data.get('humidity',    'N/A')}%")
    print(f"  Light       : {data.get('light',       'N/A')} lux")
    print(f"  CO          : {data.get('co_ohms',     'N/A')} Ω  (~{data.get('co_ppm',  'N/A')} ppm)")
    print(f"  NO2         : {data.get('no2_ohms',    'N/A')} Ω  (~{data.get('no2_ppm', 'N/A')} ppm)")
    print(f"  NH3         : {data.get('nh3_ohms',    'N/A')} Ω  (~{data.get('nh3_ppm', 'N/A')} ppm)")
    print("─" * 40)


#Creates a fresh CSV file.
def main():
    init_csv()
    screen_index  = 0
    last_log_time = 0
    last_scr_time = 0
    data          = {}

#Start-up message
    print("Enviro+ monitor running. Press Ctrl+C to stop.")
    print(f"Logging to: {LOG_FILE}")
    print("─" * 40)

#Infinte loop until manually stopped so that data collection can occur constantly.
#Reads all data before checking if it is time to log the collected values.
#Updates timestamp on screen.
#Manual ceasing of script execution handled.
    try:
        while True:
            now = time.time()

            data = read_sensors()
            print_readings(data)

            if now - last_log_time >= LOG_INTERVAL:
                log_to_csv(data)
                last_log_time = now

            if now - last_scr_time >= DISPLAY_CYCLE:
                draw_screen(SCREENS[screen_index], data)
                screen_index  = (screen_index + 1) % len(SCREENS)
                last_scr_time = now

            time.sleep(2)

    except KeyboardInterrupt:
        print("\nStopped.")
        img = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
        disp.display(img)

#Only runs if this script is called directly. Importation from another script wouldn't commence sensor #readings.
if __name__ == "__main__":
    main()





