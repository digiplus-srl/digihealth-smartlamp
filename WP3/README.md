# AirQuality WP3 — Indoor Air Quality Monitoring & Prediction

Sistema open source per il **monitoraggio in tempo reale** e la **previsione** della qualità dell'aria indoor, sviluppato nell'ambito del progetto Artes 4.0 / INAIL.

Il sistema è composto da due microservizi Docker indipendenti:

| Servizio | Porta | Funzione |
|---|---|---|
| `api_service` | 8000 | Previsione LSTM multi-orizzonte + consigli proattivi |
| `rule_service` | 8010 | Motore rule-based, gestione alert, integrazione CRM |

---

## Requisiti

- Docker ≥ 24 e Docker Compose v2
- InfluxDB 2.x (con bucket `health_data` e `health_settings`)
- (Opzionale) CRM compatibile con le API DigiE per la ricezione degli alert

---

## Avvio rapido

### 1. Entra nella cartella WP3

```bash
cd WP3
```

### 2. Configura le variabili d'ambiente

```bash
cp .env.example .env
```

Apri `.env` e compila tutti i valori richiesti (vedi sezione [Configurazione](#configurazione)).

### 3. Build e avvio

```bash
docker compose build
docker compose up -d
```

Verifica che entrambi i container siano attivi:

```bash
docker compose ps
```

### 4. Verifica health check

```bash
curl http://localhost:8000/api/health
curl http://localhost:8010/api/rule/health
```

---

## Configurazione

Tutti i parametri si configurano tramite il file `.env` (mai committare questo file su git).

### Variabili obbligatorie per il `rule_service`

| Variabile | Descrizione |
|---|---|
| `INFLUX_URL` | URL dell'istanza InfluxDB (es. `https://influxdb.example.com/`) |
| `INFLUX_TOKEN` | Token di accesso InfluxDB (lettura da `health_data` e `health_settings`) |
| `INFLUX_ORG` | Nome dell'organizzazione in InfluxDB |
| `THIRD_PARTY_BASE_URL` | URL base del CRM per l'invio degli alert |
| `THIRD_PARTY_USER_TOKEN` | Token utente per l'autenticazione CRM |
| `THIRD_PARTY_COMPANY_ID` | ID azienda nel CRM |
| `THIRD_PARTY_ALARM_CATEGORY_ID` | ID categoria "alert" nel CRM |
| `THIRD_PARTY_TOLERANCE_CATEGORY_ID` | ID categoria "tolleranza" nel CRM |
| `THIRD_PARTY_PREDICTIVE_CATEGORY_ID` | ID categoria "predittivo" nel CRM |
| `THIRD_PARTY_ASSET_ID_MAP` | Mapping device→asset CRM in JSON (es. `{"mac1":10,"mac2":11}`) |

### Variabili opzionali

| Variabile | Default | Descrizione |
|---|---|---|
| `INFLUX_BUCKET` | `health_data` | Bucket InfluxDB con i dati sensore |
| `INFLUX_SETTINGS_BUCKET` | `health_settings` | Bucket InfluxDB con soglie e impostazioni |
| `INFLUX_MEASUREMENT` | `ZPHSensor_sensore` | Measurement InfluxDB |
| `API_SERVICE_PORT` | `8000` | Porta host esposta per `api_service` |
| `RULE_SERVICE_PORT` | `8010` | Porta host esposta per `rule_service` |
| `WP3_MODEL_DIR` | `/app/output/phase3_v5` | Percorso del modello nel container o nel runtime locale |
| `THIRD_PARTY_ALERT_TIMEZONE` | `Europe/Rome` | Timezone per i timestamp degli alert |
| `PREDICTIVE_TTT_MAX_MINUTES` | `15` | Finestra massima (minuti) per gli alert predittivi |
| `PREDICTIVE_ALERT_COOLDOWN_MINUTES` | `60` | Cooldown tra alert predittivi per la stessa metrica |
| `ALERT_BATCH_WINDOW_MS` | `5000` | Finestra di batching alert in millisecondi |
| `DIGIHEALTH_ALERT_URL` | _(vuoto)_ | URL endpoint DigiHealth (opzionale) |
| `DIGIHEALTH_API_KEY` | _(vuoto)_ | API key DigiHealth (opzionale) |
| `DIGIHEALTH_LAMP1_ALERT_URL` | _(vuoto)_ | URL endpoint lamp1 per le lampade dedicate |
| `DIGIHEALTH_LAMP1_API_KEY` | _(vuoto)_ | API key endpoint lamp1 |
| `DIGIHEALTH_LAMP1_LAMPS` | _(vuoto)_ | Lista seriali lampade da inviare a lamp1, separati da virgola |

---

## Architettura

```
InfluxDB (health_data)
        │
        │  Flux Task (ogni 15 min)
        ▼
rule_service :8010  ──► CRM (alert + storico)
        │               ──► DigiHealth (opzionale)
        │
        │  Flux Task (ogni 6 min, predittivo)
        ▼
rule_service :8010  ──► CRM (alert predittivo)

api_service :8000
  POST /api/forecast  ──► previsione LSTM a +5/+15/+30/+60 min
  POST /api/advise    ──► previsione + analisi soglie + raccomandazioni
```

### Flusso alert (rule_service)

1. Il **Flux task** in InfluxDB interroga `health_data` ogni 15 minuti
2. Confronta i valori con le soglie in `health_settings` (warning / critical)
3. Per ogni metrica fuori soglia, invia un evento HTTP POST a `/api/rule/evaluate`
4. Il rule_service:
   - Recupera la serie storica da InfluxDB per calcolare trend e accelerazione
   - Applica il motore rule-based (Fase 2) per classificare la sorgente e raccomandare l'azione
   - Consulta la **matrice decisionale del CRM** per determinare il tipo di azione (aggiornamento automatico ogni ora)
   - Invia la comunicazione e lo storico alert al CRM

### Flusso predittivo

Il task predittivo gira ogni 6 minuti e invia al rule_service le metriche ancora sotto soglia. Il rule_service calcola il TTT (time-to-threshold) e invia l'alert predittivo solo se il superamento è stimato entro `PREDICTIVE_TTT_MAX_MINUTES`.

---

## Endpoint API

### api_service (porta 8000)

#### `GET /api/health`
Health check del servizio.

#### `POST /api/forecast`
Previsione multi-orizzonte dei 4 inquinanti principali.

**Request body:**
```json
{
  "readings": [
    {
      "ts": "2024-01-01T09:00:00",
      "CO2": 650.0,
      "VoC": 1.2,
      "PMS2_5": 8.5,
      "PMS10": 12.0,
      "T": 22.5,
      "H": 45.0
    }
  ]
}
```
*(minimo 30 letture, una al minuto)*

**Response:** previsioni a +5, +15, +30, +60 minuti con intervalli di confidenza al 95%.

#### `POST /api/advise`
Stessa request di `/api/forecast`, risposta arricchita con analisi soglie e raccomandazioni operative.

### rule_service (porta 8010)

#### `GET /api/rule/health`
Health check.

#### `POST /api/rule/evaluate`
Endpoint chiamato dai Flux task. Riceve un evento di alert da InfluxDB, applica il motore rule-based e invia l'alert al CRM.

---

## Soglie di qualità dell'aria (D4.3)

Le soglie di default sono quelle del documento INAIL-Artes 4.0:

| Parametro | Tolleranza | Alert |
|---|---|---|
| CO₂ | 801 ppm | 1001 ppm |
| TVOC (qualità) | 2 | 3 |
| CH₂O | 0.06 mg/m³ | 0.11 mg/m³ |
| PM1 | 10 µg/m³ | 20 µg/m³ |
| PM2.5 | 15 µg/m³ | 35 µg/m³ |
| PM10 | 45 µg/m³ | 90 µg/m³ |
| CO | 7 ppm | 10 ppm |
| O₃ | 0.1 ppm | 0.2 ppm |
| NO₂ | 0.11 ppm | 0.21 ppm |
| Temperatura | 26 °C | 28.1 °C |
| Umidità | 60 % | 70.1 % |

Le soglie operative vengono lette dinamicamente da InfluxDB (`health_settings`) e possono essere aggiornate senza riavvio del servizio.

---

## Configurazione InfluxDB

### Task Flux
Vedi la cartella [`influx_tasks/`](influx_tasks/README.md) per i task da importare in InfluxDB.
Ogni task contiene in alto una sezione `CONFIGURAZIONE` con bucket, measurement, endpoint del `rule_service` e mapping device-cliente.

### Schema dati atteso

**Bucket `health_data`** — dati sensore in ingresso:
```
measurement: ZPHSensor_sensore
tags: lampada=<device_id>, stanza=<nome_stanza>, host=<hostname>
fields: CO2-AnidrideCarbonica-[ppm], TVOC-QualitaAria-[G], PM2_5-Particolato-[µg/m^3], ...
```

**Bucket `health_settings`** — soglie per device e metrica:
```
measurement: thresholds
tags: lampada=<device_id>, metric=<nome_field>
fields: warning=<float>, critical=<float>
```

---

## Modello LSTM

Il modello preaddestrato (`api_service/output/phase3_v5/`) è stato addestrato sul dataset **DALTON** (NeurIPS 2024), che include misurazioni di qualità dell'aria indoor da oltre 30 ambienti diversi (abitazioni, uffici, laboratori, aule).

- **Architettura**: LSTM multi-orizzonte
- **Input**: finestra di 30 minuti (CO₂, VOC, PM2.5, PM10, T, H)
- **Output**: previsioni a +5, +15, +30, +60 minuti

---

## Codici azione WP3

Il rule_service assegna un codice WP3 a ogni alert. Il mapping verso il tipo di azione CRM viene letto dinamicamente dalla matrice decisionale del CRM (aggiornamento automatico ogni ora). I codici predefiniti sono:

| Codice | Azione consigliata |
|---|---|
| WP3_01 | Solo monitoraggio (comunicazione) |
| WP3_02–WP3_05 | Attivare purificatore d'aria |
| WP3_06–WP3_08, WP3_10 | Aprire finestre / ventilazione naturale |
| WP3_09, WP3_11, WP3_12 | Purificatore (fallback) |
| WP3_13 | Verifica illuminazione |
| WP3_PRED_01 | Purificatore preventivo (predittivo) |
| WP3_PRED_02 | Ventilazione preventiva (predittivo) |

---

## Sviluppo locale

Per eseguire un singolo servizio senza Docker:

```bash
cd rule_service
pip install -r requirements.txt
export $(cat ../.env | xargs)
uvicorn src.rule_api:app --host 0.0.0.0 --port 8010 --reload
```

```bash
cd api_service
pip install -r requirements.txt
uvicorn src.api:app --host 0.0.0.0 --port 8000 --reload
```

---

## Licenza

Progetto sviluppato nell'ambito di Artes 4.0 in collaborazione con INAIL.
