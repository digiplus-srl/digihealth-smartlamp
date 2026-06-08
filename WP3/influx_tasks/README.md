# InfluxDB Flux Tasks

Questi task Flux vanno importati nell'interfaccia di InfluxDB (Tasks → Create Task → Import).

## Prerequisiti

### Bucket `health_data`
Deve contenere le misurazioni dei sensori con questo schema:
- **measurement**: `ZPHSensor_sensore` (configurabile)
- **tags**: `lampada` (ID device), `stanza` (nome stanza), `host`
- **fields**: le metriche elencate in `allowed_alert_fields`

### Bucket `health_settings`
Contiene le soglie per ogni device/metrica:
- **measurement**: `thresholds`
- **tags**: `lampada`, `metric`
- **fields**: `warning` (float), `critical` (float, opzionale)

Esempio di scrittura soglia in InfluxDB line protocol:
```
thresholds,lampada=device-001,metric=CO2-AnidrideCarbonica-[ppm] warning=801.0,critical=1001.0
```

## Configurazione

In entrambi i file `.flux`, modificare la sezione `CONFIGURAZIONE` in alto:

| Variabile | Valore da inserire |
|---|---|
| `data_bucket` | Bucket InfluxDB con i dati sensore |
| `settings_bucket` | Bucket InfluxDB con le soglie |
| `threshold_measurement` | Measurement delle soglie |
| `sensor_measurement` | Measurement dei dati sensore |
| `rule_service_url` | URL completo dell'endpoint `/api/rule/evaluate` |
| `monitored_devices` | Elenco `{lampada, client_id}` da monitorare |

Esempio mapping:

```flux
monitored_devices =
    array.from(rows: [
        {lampada: "device-001", client_id: "customer-001"},
        {lampada: "device-002", client_id: "customer-002"},
    ])
```

## Task disponibili

| File | Frequenza | Scopo |
|---|---|---|
| `alert_task.flux` | ogni 15 min | Alert quando la soglia è già superata |
| `predictive_task.flux` | ogni 6 min | Alert predittivo prima del superamento |

## Orario di monitoraggio

I task usano `hourSelection(start: 9, stop: 18)` (orario lavorativo, fuso `Europe/Rome`).
`stop: 18` è esclusivo, quindi copre 9:00–17:59.
Modificare i valori per adattarli al proprio contesto.
