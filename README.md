# DigiHealth Lamp

Sistema di monitoraggio ambientale indoor basato su Raspberry Pi. Legge sensori di qualità dell'aria, calcola l'IAQI (Indoor Air Quality Index), controlla una striscia LED NeoPixel, lampadine Shelly e dispositivi Tuya (purificatore + climatizzatore) via rete locale. Invia i dati a InfluxDB e offre una dashboard web con configurazione e riavvio integrati.

---

## Funzionalità principali

| Modulo | Descrizione |
|---|---|
| **Sensori** | ZPH01B (PM1/PM2.5/PM10, CO2, TVOC, CH2O, temperatura, umidità) via UART; BH1750 (lux) via I2C; microfono USB |
| **IAQI** | Calcolo Indice di Qualità dell'Aria Interna secondo breakpoint standard (PM2.5, PM10, CO, CO2, TVOC, CH2O) |
| **NeoPixel** | Striscia 144 LED: tutti i pixel visualizzano l'IAQI con effetto breathing colorato |
| **Shelly** | Lampadine smart via HTTP API: luce circadiana (6500K giorno / 2700K sera) e dimming adattivo in base al lux |
| **Purificatore Tuya** | PNI PTA200 via Tuya local API: accensione automatica se PM2.5 > 25 µg/m³ o CO2 > 800 ppm |
| **Climatizzatore Tuya** | Solight DAC-12000 via Tuya local API (v3.4): accensione/spegnimento automatico in base alla temperatura, con isteresi |
| **Audio comfort** | Monitoraggio livello sonoro, calibrazione automatica, riproduzione pink noise o file audio se la soglia viene superata |
| **Dashboard web** | Flask su porta 5000: spettro FFT, livello dB, qualità aria, stato attuatori in tempo reale |
| **Pagina Config** | Interfaccia web per modificare `config/default.yaml` (o `windows.yaml`) senza toccare i file; bottone **Riavvia** integrato |
| **InfluxDB** | Invio diretto a InfluxDB Cloud via `influxdb-client` |
| **Telegram** | Notifiche push su bot Telegram ad ogni alert ricevuto (livello, inquinante, azioni eseguite) |

---

## Hardware richiesto

- Raspberry Pi 4 (o superiore)
- Sensore ZPH01B collegato a `/dev/serial0` (UART, 9600 baud)
- Sensore BH1750 collegato a I2C bus 1, indirizzo `0x23`
- Striscia NeoPixel (144 pixel) su GPIO 12
- Microfono USB (indice da verificare con `python3 tools/find_audio_devices.py`)
- Connessione di rete (per Shelly, Tuya, InfluxDB, dashboard)

---

## Installazione

### 1. Sistema operativo e dipendenze di sistema

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip python3-dev python3-venv \
    portaudio19-dev libasound2-dev mpg123 git
```

### 2. Abilitare le interfacce hardware

```bash
sudo raspi-config
```

- **Interfacing Options → Serial Port** → disable login shell, enable serial hardware
- **Interfacing Options → I2C** → Enable
- Riavviare: `sudo reboot`

### 3. Clonare il repository

```bash
cd /home/digip
git clone https://github.com/yourusername/digihealth-smartlamp.git digihealth-lamp
cd digihealth-lamp
```

### 4. Creare e attivare l'ambiente virtuale

```bash
python3 -m venv venv
source venv/bin/activate
```

### 5. Installare le dipendenze

```bash
pip install --upgrade pip
pip install -e .
```

### 6. Configurare il servizio systemd

```bash
sudo cp systemd/digihealth-lamp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable digihealth-lamp
sudo systemctl start digihealth-lamp
```

### 7. Verificare

```bash
sudo systemctl status digihealth-lamp
sudo journalctl -u digihealth-lamp -f
```

---

## Installazione su Windows (sviluppo/test)

```powershell
cd C:\...\digihealth-smartlamp
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install flask pyyaml pydantic requests tinytuya numpy
pip install pyaudio   # oppure: pip install pipwin && pipwin install pyaudio
.\venv\Scripts\python.exe -m digihealth.main
```

Il file di configurazione caricato automaticamente su Windows è `config/windows.yaml`.

> Vedi [AVVIO_WINDOWS.md](AVVIO_WINDOWS.md) per dettagli su microfono e audio comfort su Windows.

---

## Configurazione

Il file principale è `config/default.yaml` (Raspberry Pi) o `config/windows.yaml` (Windows).  
È possibile sovrascrivere il file con la variabile d'ambiente `DIGIHEALTH_CONFIG`.

### Variabili d'ambiente (segreti)

Copiare `.env.example` in `.env` e compilare i valori:

```bash
INFLUXDB_TOKEN=...           # token InfluxDB Cloud
DIGIHEALTH_API_KEY=...       # chiave per l'endpoint /api/alerts
TELEGRAM_BOT_TOKEN=...       # token del bot (da @BotFather)
TELEGRAM_CHAT_ID=...         # chat o gruppo destinatario (es. -1001234567890)
```

> Il file `.env` viene caricato automaticamente all'avvio tramite `python-dotenv`.

La configurazione è modificabile anche dalla **pagina web** `http://<ip>:5000/config` senza toccare i file.

### Parametri chiave

```yaml
sensors:
  microphone:
    device_index: 1          # Linux: verificare con arecord -l o tools/find_audio_devices.py
    output_device_index: 0   # Linux: verificare con aplay -l; Windows: altoparlanti di default

processors:
  audio_comfort:
    tolerance_threshold: 45.0   # dB soglia comfort
    critical_threshold: 55.0    # dB soglia critica
    check_duration: 10          # sec di ascolto prima di intervenire
    comfort_duration: 300       # sec di riproduzione audio comfort

actuators:
  neopixel:
    enabled: true
    pin: 12
    num_pixels: 144

  shelly:
    enabled: true
    devices:
      - name: "Lampada1 Uff Sensorizzato"
        ip: "192.168.1.191"
        enabled: true

  tuya_purifier:
    enabled: false
    device_id: "..."
    ip: "192.168.0.108"
    local_key: "..."
    pm25_limit: 25        # µg/m³ — accende se superato
    co2_limit: 800        # ppm  — accende se superato

  tuya_ac:
    enabled: false
    device_id: "..."
    ip: "192.168.0.127"
    local_key: "..."
    temp_on: 26           # accende se temp > 26°C
    temp_off: 24          # spegne se temp < 24°C
    temp_target: 22       # temperatura impostata sull'AC
    mode: "c"             # c=freddo | h=caldo | d=deumidifica | f=ventola | a=auto
    fan_speed: "auto"

web:
  enabled: true
  port: 5000

telegram:
  enabled: true   # richiede TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID come env var
```

### Trovare IP e local_key di un dispositivo Tuya

```bash
python -m tinytuya scan        # individua IP e Device ID sulla LAN
python -m tinytuya wizard      # guida interattiva per ottenere local_key
```

---

## Utilizzo

### Dashboard web

`http://<ip-raspberry>:5000`

Mostra:
- Livello sonoro in dB e spettro FFT in tempo reale
- Temperatura, umidità, CO2, IAQI
- **Riga attuatori**: stato in tempo reale di NeoPixel (colore IAQI), Shelly (lampadina colorata con glow), purificatore Tuya (ON/OFF + PM2.5/CO2), climatizzatore Tuya (ON/OFF + temperatura)
- Pulsanti calibrazione, avvio/stop, selezione modalità comfort, volume

### Pagina configurazione web

`http://<ip-raspberry>:5000/config`

- Modifica tutti i parametri di configurazione direttamente dal browser
- Bottone **Salva** → scrive il file YAML
- Bottone **Riavvia** → riavvia automaticamente l'applicazione e ricarica la pagina

### Avvio manuale (debug)

```bash
sudo systemctl stop digihealth-lamp   # ferma il servizio prima
source venv/bin/activate
sudo ./venv/bin/python3 -m digihealth.main
# CTRL+C per fermare
```

---

## Struttura del progetto

```
digihealth-lamp/
├── digihealth/
│   ├── main.py                     # Entry point, loop principale (ciclo 30s)
│   ├── config.py                   # Caricamento e validazione config (Pydantic)
│   ├── logger.py
│   ├── audio_worker.py             # Processo separato per PyAudio (spawn)
│   ├── sensors/
│   │   ├── base.py
│   │   ├── zph.py                  # Sensore ZPH01B (UART)
│   │   ├── light.py                # Sensore BH1750 (I2C)
│   │   └── microphone.py
│   ├── processors/
│   │   ├── iaqi.py
│   │   └── audio_comfort.py
│   ├── actuators/
│   │   ├── __init__.py             # ActuatorManager con get_status()
│   │   ├── neopixel_controller.py
│   │   ├── shelly_controller.py
│   │   ├── tuya_purifier.py        # Purificatore PNI PTA200 (Tuya v3.3)
│   │   └── tuya_ac.py              # Climatizzatore Solight DAC-12000 (Tuya v3.4)
│   ├── communicator/
│   │   └── telegraf_client.py
│   ├── notifications/
│   │   └── telegram_notifier.py    # Notifiche Telegram via Bot API
│   └── web/
│       ├── __init__.py             # Flask: dashboard, config, restart
│       ├── api.py                  # Endpoint /api/alerts (ingestion alert)
│       ├── dispatcher.py           # Dispatch alert → attuatori + Telegram
│       ├── schemas.py              # Modelli Pydantic per gli alert
│       ├── storage.py              # Persistenza alert su SQLite
│       └── templates/
│           ├── dashboard.html      # Dashboard real-time + stato attuatori
│           ├── alerts.html         # Log alert ricevuti
│           ├── thresholds.html     # Soglie attuatori
│           └── config.html         # Pagina configurazione YAML + riavvio
├── config/
│   ├── default.yaml                # Configurazione Raspberry Pi
│   └── windows.yaml                # Configurazione Windows
├── tools/
│   └── find_audio_devices.py       # Diagnostica dispositivi audio (PyAudio + ALSA)
├── systemd/
│   └── digihealth-lamp.service
├── audio/
├── tests/
├── requirements.txt
├── setup.py
└── GUIDA_SERVIZIO.txt
```

### Flusso dati

```
ZPH01B (UART) ──┐
BH1750  (I2C) ──┤→ SensorManager → ProcessorManager (IAQI, AudioComfort)
Microfono USB ──┘                          │
                        ┌──────────────────┼──────────────────┬──────────────┐
                        ↓                  ↓                  ↓              ↓
                   InfluxDB          NeoPixel LED         Dashboard      Attuatori
                   (Cloud)           (GPIO 12)         (Flask :5000)   rete locale
                                                        /config            ├ Shelly HTTP
                                                        /alerts            ├ Tuya Purif.
                                                        /thresholds        └ Tuya AC

Alert in ingresso → /api/alerts → dispatcher → attuatori + Telegram Bot
```

---

## Troubleshooting

| Problema | Soluzione |
|---|---|
| `Serial: no such device /dev/serial0` | Abilitare UART in `raspi-config`, disabilitare console seriale |
| `I2C error` / sensore luce non trovato | Verificare con `i2cdetect -y 1`; deve apparire `0x23` |
| LED NeoPixel non si accendono | GPIO 12 richiede permessi root; verificare il cablaggio |
| Microfono non trovato / `Invalid number of channels` | `device_index` errato: eseguire `python3 tools/find_audio_devices.py` per trovare l'indice corretto |
| Microfono non trovato dopo riavvio (Windows) | Gli indici audio cambiano al riavvio: ri-eseguire `tools/find_audio_devices.py` e aggiornare `windows.yaml` |
| Rumore rosa non si sente (Windows) | Verificare che `audio_worker.py` sia aggiornato: il watchdog non deve interferire con `winsound` |
| Audio comfort: nessun suono su Linux | Verificare che `mpg123` sia installato: `sudo apt install mpg123` |
| Dashboard non raggiungibile | Verificare `web.enabled: true` e che la porta 5000 sia aperta |
| Telegram: nessun messaggio | Verificare `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` nelle env var; controllare `telegram.enabled: true` nel YAML |
| Telegram: `400 Bad Request` | Il `CHAT_ID` è errato o il bot non è nel gruppo; aggiungere il bot al gruppo e riprovare |
| Shelly offline nel log | Verificare IP in config e che sia sulla stessa rete WiFi |
| Tuya: `Connection refused` | Verificare IP e `local_key`; il dispositivo deve essere sulla LAN locale |
| Tuya: `key` errata | Riottenere la `local_key` con `tinytuya wizard` o dall'API Tuya Cloud |
| AC Tuya: DPS non corretti | Eseguire `python -m tinytuya scan` e verificare la mappa DPS con `d.status()` |

---

## Test

```bash
source venv/bin/activate
pytest tests/
```

---

## Licenza

MIT License
