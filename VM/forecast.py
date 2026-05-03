import sqlite3
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from sklearn.preprocessing import MinMaxScaler
from scipy import stats
import tensorflow as tf
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout
from tensorflow.keras.callbacks import Callback
import warnings
import logging
import os
warnings.filterwarnings('ignore')



SENSORS_DB     = '/var/lib/sensor-data/sensors.db'
PREDICTIONS_DB = '/var/lib/sensor-data/predictions.db'
LOG_FILE       = '/var/lib/sensor-data/forecast.log'
FORECAST_HOURS = 6
READING_INTERVAL_SEC = 10
SEQ_LEN        = 360
Z_THRESHOLD    = 3.0
EPOCHS         = 50
BATCH_SIZE     = 32


#Method for setting up the logging of data in forecast log as well as outputting to terminal.
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def log(msg):
    print(msg)
    logging.info(msg)




#Constructs message that is outputted to terminal and log file after each EPOCH is complete.
#Message contains basic loss information of each EPOCH respectively.
class LoggingCallback(Callback):
    def on_epoch_end(self, epoch, logs=None):
        msg = f"Epoch {epoch + 1}/{self.params['epochs']} - loss: {logs['loss']:.6f} - val_loss: {logs.get('val_loss', 0):.6f}"
        log(msg)



#Constructs predictions database with three tables.
#Checks if tables are pre-existing.
def setup_predictions_db():
    conn = sqlite3.connect(PREDICTIONS_DB)
    cursor = conn.cursor()
    cursor.execute('''

#Table contains timestamp of generation, timestamp of prediction, predicted value, confidence boundaries.
        CREATE TABLE IF NOT EXISTS co_forecast (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at     TEXT NOT NULL,
            forecast_time    TEXT NOT NULL,
            co_ppm_predicted REAL,
            lower_bound      REAL,
            upper_bound      REAL
        )
    ''')

#Table contains timestamp of script execution, number of readings, number of outliers, number of remaining data values, number of sequences, total lost, confidence & mean of prediction as well as the predicted value.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS forecast_runs (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at              TEXT NOT NULL,
            readings_loaded     INTEGER,
            outliers_removed    INTEGER,
            clean_readings      INTEGER,
            sequences_trained   INTEGER,
            final_loss          REAL,
            forecast_min        REAL,
            forecast_max        REAL,
            forecast_mean       REAL,
            next_predicted      REAL
        )
    ''')

#Table contains all readings that have been removed as outliers, the timestamp, how far removed from mean of the data set.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS outliers_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at      TEXT NOT NULL,
            timestamp   TEXT NOT NULL,
            co_ppm      REAL,
            z_score     REAL
        )
    ''')
    conn.commit()
    conn.close()



#Data retrieval from sensor readings database.
#Only retrieves records where CO data is present in chronological order of timestamp value.
def load_data():
    conn = sqlite3.connect(SENSORS_DB)
    df = pd.read_sql(
        "SELECT timestamp, co_ppm FROM readings WHERE co_ppm IS NOT NULL ORDER BY timestamp ASC",
        conn
    )
    conn.close()

#Converts the timestamp string to date-time format.
    df['timestamp'] = pd.to_datetime(df['timestamp'])

#Logs key info such as number of readings, range of time of readings, average & boundaries of data
    log(f"Loaded {len(df)} readings from sensors.db.")
    log(f"Date range: {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}")
    log(f"CO ppm stats - min: {df['co_ppm'].min():.3f}, max: {df['co_ppm'].max():.3f}, mean: {df['co_ppm'].mean():.3f}")

#Passes processed data back for further processing such as outlier removal & ML.
    return df



#Outlier removal
#Calculates a Z-Score of every CO reading in the dataset (it's standard deviation from mean).
#Defines a reading as an outlier of the calculated Z-Score is 3 standard deviations from the mean.
def remove_outliers(df, run_at):
    z_scores = np.abs(stats.zscore(df['co_ppm']))
    outlier_mask = z_scores >= Z_THRESHOLD
    outliers = df[outlier_mask].copy()
    outliers['z_score'] = z_scores[outlier_mask]

#Two groups created: outliers and non-outliers, size of groups ascertained.
    before = len(df)
    df_clean = df[~outlier_mask].copy()
    after = len(df_clean)

#Records volume of outliers as well as their values.
    log(f"Outlier removal (Z-score threshold: {Z_THRESHOLD}):")
    log(f"  {before - after} outliers removed, {after} clean readings remain.")

    if len(outliers) > 0:
        log(f"  Outlier details:")
        for _, row in outliers.iterrows():
            log(f"    {row['timestamp']} - co_ppm: {row['co_ppm']:.3f} ppm (z-score: {row['z_score']:.3f})")

#Saves outliers to database.
        conn = sqlite3.connect(PREDICTIONS_DB)
        cursor = conn.cursor()
        for _, row in outliers.iterrows():
            cursor.execute(
                "INSERT INTO outliers_log (run_at, timestamp, co_ppm, z_score) VALUES (?, ?, ?, ?)",
                (run_at, str(row['timestamp']), float(row['co_ppm']), float(row['z_score']))
            )
        conn.commit()
        conn.close()

#Returns the remaining data for ML 
    return df_clean.reset_index(drop=True)


#Creates look back pairs for LSTM model of every single element in dataset
#One side of pair = previous 360 readings (1 hour of recorded data.
#Other side of pair is the net immediate recorded value after the hour of data.
def prepare_sequences(values, seq_len):
    X, y = [], []
    for i in range(len(values) - seq_len):
        X.append(values[i:i + seq_len])
        y.append(values[i + seq_len])
    return np.array(X), np.array(y)



#Construction of neural network.
#Receives 360 data values for processing by 64 units.
#20% units (of 64) cease processing to prevent overfitting.
#Further processing of 32 units for higher level pattern detection.
#20% units (of 32) cease processing to prevent overfitting.
#Single predicted value generated from all data processing.
def build_model(seq_len):
    model = Sequential([
        LSTM(64, return_sequences=True, input_shape=(seq_len, 1)),
        Dropout(0.2),
        LSTM(32),
        Dropout(0.2),
        Dense(1)
    ])

#Model used: adam (self-adjusting)
#MSE: Mean Squared Error - inaccuracy measuring during model training.
    model.compile(optimizer='adam', loss='mse')
    return model



#LSTM model in use.
#Volume of predictions calculated: 6 hours / 10 seconds (each reading) = 2160 individual predictions required.
def generate_forecast(model, last_sequence, scaler, last_timestamp):
    steps = int((FORECAST_HOURS * 3600) / READING_INTERVAL_SEC)
    predictions = []
    current_seq = last_sequence.copy()

#Begins with previous 360 'real' values to predict the next one.
#Then uses its on predicted values over 'real' recordings.
    for _ in range(steps):
        pred = model.predict(current_seq.reshape(1, SEQ_LEN, 1), verbose=0)
        predictions.append(pred[0][0])
        current_seq = np.append(current_seq[1:], pred[0][0])

#Converts predicted values back to 'ppm'.
    predictions = np.array(predictions).reshape(-1, 1)
    predictions_actual = scaler.inverse_transform(predictions).flatten()

#Calculates confidence boundaries
    lower = predictions_actual * 0.90
    upper = predictions_actual * 1.10

#Generates timestamps of when predicted values should occur.
    forecast_times = [
        last_timestamp + timedelta(seconds=(i + 1) * READING_INTERVAL_SEC)
        for i in range(steps)
    ]

    return forecast_times, predictions_actual, lower, upper



#Prediction saving
def save_predictions(forecast_times, predictions, lower, upper, run_at,
                     readings_loaded, outliers_removed, clean_readings,
                     sequences_trained, final_loss):
    conn = sqlite3.connect(PREDICTIONS_DB)
    cursor = conn.cursor()

#Removes any and all previously saved predictions
    cursor.execute("DELETE FROM co_forecast")

#Saves the 2160 newly calculated values as rows so that it can be displayed on interface to user.
    rows = [
        (
            run_at,
            t.strftime("%Y-%m-%d %H:%M:%S"),
            float(p),
            float(l),
            float(u)
        )
        for t, p, l, u in zip(forecast_times, predictions, lower, upper)
    ]

    cursor.executemany(
        "INSERT INTO co_forecast (generated_at, forecast_time, co_ppm_predicted, lower_bound, upper_bound) VALUES (?, ?, ?, ?, ?)",
        rows
    )

#Saves all relevant information of the prediction algorithm in its own permanent table.
    cursor.execute(
        '''INSERT INTO forecast_runs (
            run_at, readings_loaded, outliers_removed, clean_readings,
            sequences_trained, final_loss, forecast_min, forecast_max,
            forecast_mean, next_predicted
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            run_at,
            readings_loaded,
            outliers_removed,
            clean_readings,
            sequences_trained,
            final_loss,
            float(predictions.min()),
            float(predictions.max()),
            float(predictions.mean()),
            float(predictions[0])
        )
    )

    conn.commit()
    conn.close()
    log(f"Saved {len(rows)} forecast points to predictions database.")



#Main for subsequent method calling.
#Logs precise time that forecasting began.
def main():
    run_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log(f"\n{'='*60}")
    log(f"CO FORECAST RUN STARTED: {run_at}")
    log(f"{'='*60}")

#Sets databases and loads in data values.
    setup_predictions_db()

    df = load_data()
    readings_loaded = len(df)

#Commences outlier handling.
    df = remove_outliers(df, run_at)
    clean_readings = len(df)
    outliers_removed = readings_loaded - clean_readings

#Checks if amount of remaining clean data is sufficient for LSTM model.
    if clean_readings < SEQ_LEN + 100:
        log(f"Not enough data to train. Need at least {SEQ_LEN + 100} readings.")
        return

#Scales data from 'ppm' to between 0-1.
    scaler = MinMaxScaler()
    scaled = scaler.fit_transform(df[['co_ppm']]).flatten()

#Creates lookback pairs.
    X, y = prepare_sequences(scaled, SEQ_LEN)
    X = X.reshape(X.shape[0], X.shape[1], 1)
    sequences_trained = len(X)

    log(f"Training LSTM on {sequences_trained} sequences ({EPOCHS} epochs)...")

#Neural network building.
    model = build_model(SEQ_LEN)
    history = model.fit(
        X, y,
        epochs=EPOCHS,
        batch_size=BATCH_SIZE,
        validation_split=0.1,
        verbose=0,
        callbacks=[LoggingCallback()]
    )

#Records final accuracy.
    final_loss = history.history['loss'][-1]
    log(f"Training complete. Final loss: {final_loss:.6f}")

    last_sequence = scaled[-SEQ_LEN:]
    last_timestamp = df['timestamp'].iloc[-1]

#Forecast generation.
    log(f"Generating {FORECAST_HOURS}-hour forecast from {last_timestamp}...")

    forecast_times, predictions, lower, upper = generate_forecast(
        model, last_sequence, scaler, last_timestamp
    )

#Final saving of attributes & announces completion of ML.
    save_predictions(
        forecast_times, predictions, lower, upper, run_at,
        readings_loaded, outliers_removed, clean_readings,
        sequences_trained, final_loss
    )

    log(f"Next predicted CO reading: {predictions[0]:.3f} ppm")
    log(f"6-hour forecast range: {predictions.min():.3f} - {predictions.max():.3f} ppm")
    log(f"6-hour forecast mean:  {predictions.mean():.3f} ppm")
    log(f"{'='*60}")
    log(f"CO FORECAST RUN COMPLETE: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"{'='*60}\n")

#Only runs if this script is called directly. Importation from another script wouldn't commence forecasting.
if __name__ == '__main__':
    main()
