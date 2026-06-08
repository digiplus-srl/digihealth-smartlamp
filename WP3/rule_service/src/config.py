"""
Configurazione globale del progetto AirQualityPred.
Percorsi, soglie D4.3, parametri sensori.
"""
import os

# === PATHS ===
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATASET_BASE = os.path.join(PROJECT_ROOT, "dalton-dataset-files", "dalton-dataset-files")
RAW_DIR = os.path.join(DATASET_BASE, "Data")
PROCESSED_DIR = os.path.join(DATASET_BASE, "Processed")
FEATURES_DIR = os.path.join(DATASET_BASE, "Features")
METADATA_DIR = os.path.join(DATASET_BASE, "Metadata")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")
PLOTS_DIR = os.path.join(OUTPUT_DIR, "plots")
CACHE_DIR = os.path.join(OUTPUT_DIR, "cache")

# Crea cartelle output se non esistono
for d in [OUTPUT_DIR, PLOTS_DIR, CACHE_DIR]:
    os.makedirs(d, exist_ok=True)

# === SENSORI ===
# Colonne dei 10 parametri aria nei dati Processed
SENSOR_COLS = ["T", "H", "PMS1", "PMS2_5", "PMS10", "CO2", "NO2", "CO", "O3", "VoC", "CH2O", "lux", "C2H5OH"]

# Sottoinsiemi per analisi
POLLUTANT_COLS = ["PMS1", "PMS2_5", "PMS10", "CO2", "NO2", "CO", "O3", "VoC", "CH2O", "C2H5OH"]
ENV_COLS = ["T", "H"]
KEY_POLLUTANTS = ["CO2", "PMS2_5", "PMS10", "VoC", "CH2O", "PMS1", "CO", "O3", "NO2"]

# Unità di misura
UNITS = {
    "T": "°C", "H": "%",
    "PMS1": "µg/m³", "PMS2_5": "µg/m³", "PMS10": "µg/m³",
    "CO2": "ppm", "NO2": "ppm", "CO": "ppm",
    "VoC": "ppb", "C2H5OH": "ppb",
}

# Nomi leggibili
NAMES = {
    "T": "Temperatura", "H": "Umidità",
    "PMS1": "PM1", "PMS2_5": "PM2.5", "PMS10": "PM10",
    "CO2": "CO₂", "NO2": "NO₂", "CO": "CO",
    "VoC": "VOC", "C2H5OH": "Etanolo",
}

# === SOGLIE D4.3 (documento INAIL-Artes 4.0) ===
# Livello 1 = Tolleranza, Livello 2 = Alert
THRESHOLDS = {
    "CO2":   {"tolleranza": 801,   "alert": 1001},     # ppm
    "VoC":   {"tolleranza": 2,     "alert": 3},        # TVOC quality grade
    "CH2O":  {"tolleranza": 0.06,  "alert": 0.11},     # mg/m3
    "PMS1":  {"tolleranza": 10,    "alert": 20},       # ug/m3
    "PMS2_5": {"tolleranza": 15,   "alert": 35},       # ug/m3
    "PMS10": {"tolleranza": 45,    "alert": 90},       # ug/m3
    "CO":    {"tolleranza": 7,     "alert": 10},       # ppm
    "O3":    {"tolleranza": 0.07,  "alert": 0.12},     # ppm
    "NO2":   {"tolleranza": 0.05,  "alert": 0.1},      # ppm
    "T":     {"tolleranza": 26,    "alert": 28.1},     # C
    "H":     {"tolleranza": 60,    "alert": 70.1},     # %
    "lux":   {"tolleranza": 200,   "alert": 100},      # lux, inverse threshold in Flux task
}

# === FEATURES (62 colonne nei file Features/) ===
FEATURE_CHANNELS = ["CO2", "VoC", "PMS2_5", "PMS10", "H", "T"]
FEATURE_STATS = ["avg", "min", "max", "std", "roc_min", "roc_max", "pc", "pd", "lg_stay"]
INDEX_FEATURES = ["I_co2", "I_voc", "I_pm2_5", "I_pm10", "IAQI", "HT_idx", "BAQI"]

# === TIPI DI SITO ===
SITE_TYPES = {
    "house": [f"H{i}" for i in range(1, 14)],
    "apartment": [f"A{i}" for i in range(1, 9)],
    "lab": [f"R{i}" for i in range(1, 6)],
    "food_court": ["F1", "F2"],
    "classroom": ["C1", "C2"],
}

# === PARAMETRI EDA ===
RESAMPLE_FREQ = "1min"  # Frequenza di resampling per l'EDA
VALID_THRESHOLD = 0.5    # Minimo ratio di dati validi per includere una finestra
