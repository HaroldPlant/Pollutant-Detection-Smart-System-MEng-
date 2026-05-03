#!/bin/bash

CSV_FILE=/home/pi/enviro_log.csv
VM_USER=harry
VM_IP=192.168.1.134
VM_DIR=/home/harry/sensor-data/


#Checks if the csv file exists first.
if [ ! -f "$CSV_FILE" ]; then
    exit 0
fi


#Checks if there is data stored in the file other than the headers.
LINES=$(wc -l < "$CSV_FILE")
if [ "$LINES" -le 1 ]; then
    exit 0
fi


#Adds a timestamp to a blank CSV file before copying over the contents of the original to the new.
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
TEMP_COPY=/tmp/enviro_${TIMESTAMP}.csv

cp "$CSV_FILE" "$TEMP_COPY"


#Transfer the timestamped copy to the VM
scp "$TEMP_COPY" "$VM_USER@$VM_IP:$VM_DIR"


# Reset original file to just the header so logging resumes cleanly as well as print transfer status to terminal.
if [ $? -eq 0 ]; then
    rm "$TEMP_COPY"
    echo "timestamp,co_ohms,co_ppm,no2_ohms,no2_ppm,nh3_ohms,nh3_ppm" > "$CSV_FILE"
    echo "$(date): Transfer successful"
else
    rm "$TEMP_COPY"
    echo "$(date): Transfer failed"
fi






