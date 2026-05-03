import sqlite3
import csv
import os
import glob

DB_PATH = '/var/lib/sensor-data/sensors.db'
CSV_DIR = os.path.expanduser('~/sensor-data/')

EXPECTED_HEADERS = {'timestamp', 'co_ohms', 'co_ppm', 'no2_ohms', 'no2_ppm', 'nh3_ohms', 'nh3_ppm'}

#Searches for CSV files on VM.
#Exits if none found.
def import_csv_files():
    csv_files = glob.glob(os.path.join(CSV_DIR, '*.csv'))
    if not csv_files:
        print("No CSV files found.")
        return

#If CSV files found, opens connection to sensor reading database ready to write to.
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()


#Extract filename from full path
    for csv_file in csv_files:
        filename = os.path.basename(csv_file)

#Check header is valid before importing, built to handle (remove) header-less files that were causing issues earlier in the project.
        with open(csv_file, 'r') as f:
            first_line = f.readline().strip()
        headers = set(first_line.split(','))
        if not EXPECTED_HEADERS.issubset(headers):
            print(f"Skipping {filename} - invalid or missing header.")
            os.remove(csv_file)
            continue

#Opens file and reads it incrementally by row.
        print(f"Importing {filename}...")
        imported = 0
        skipped = 0

        with open(csv_file, 'r') as f:
            reader = csv.DictReader(f)

#Duplication check - if row has already been imported, it is skipped.
            for row in reader:
                cursor.execute(
                    "SELECT 1 FROM readings WHERE timestamp = ? AND filename = ?",
                    (row['timestamp'], filename)
                )
                if cursor.fetchone():
                    skipped += 1
                    continue

#Missing value check - if row is missing, saves it as an empty value to avoid crashing.
                def safe(key):
                    val = row.get(key, '').strip()
                    return float(val) if val not in ('', 'None', 'N/A') else None

#Inserts each valid row into the readings table.
                cursor.execute('''
                    INSERT INTO readings (
                        timestamp, filename,
                        co_ohms, co_ppm, no2_ohms, no2_ppm, nh3_ohms, nh3_ppm
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    row['timestamp'], filename,
                    safe('co_ohms'), safe('co_ppm'),
                    safe('no2_ohms'), safe('no2_ppm'),
                    safe('nh3_ohms'), safe('nh3_ppm'),
                ))
                imported += 1

#Permanently saves data and renames file to prevent further importation and act as archive.
#Confirmation printed to terminal.
        conn.commit()
        os.rename(csv_file, csv_file + '.imported')
        print(f"Done - {imported} rows imported, {skipped} duplicates skipped.")

    conn.close()

#Only runs if this script is called directly. Importation from another script wouldn't commence importing.
#Cronjob imports this script directly.
if __name__ == '__main__':
    import_csv_files()
