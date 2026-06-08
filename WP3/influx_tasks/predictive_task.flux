import "http"
import "json"
import "timezone"
import "array"

// ============================================================
// Task: Alert predittivo
// Frequenza: ogni 6 minuti
// Invia al rule_service le metriche che sono ancora sotto soglia
// ma in trend crescente. Il rule_service calcola il TTT
// (time-to-threshold) e invia l'alert solo se il superamento
// è stimato entro la finestra configurata.
// ============================================================

option task = {name: "AirQuality_Predictive_Task", every: 6m}
option location = timezone.location(name: "Europe/Rome")

// ============================================================
// CONFIGURAZIONE
// Modificare solo questa sezione per adattare il task al proprio ambiente.
// ============================================================
data_bucket = "health_data"
settings_bucket = "health_settings"
threshold_measurement = "thresholds"
sensor_measurement = "ZPHSensor_sensore"
rule_service_url = "http://rule-service.example.com:8010/api/rule/evaluate"

monitored_devices =
    array.from(rows: [
        {lampada: "YOUR_DEVICE_ID", client_id: "YOUR_CLIENT_ID"},
    ])

// Metriche per cui si vuole la predizione (sottoinsieme di quelle monitorate)
predictive_alert_fields = [
    "CO2-AnidrideCarbonica-[ppm]",
    "PM2_5-Particolato-[µg/m^3]",
    "PM10-Particolato-[µg/m^3]",
]

// =========================
// SOGLIE
// =========================
soglie =
    from(bucket: settings_bucket)
        |> range(start: -1y)
        |> filter(fn: (r) => r._measurement == threshold_measurement)
        |> filter(fn: (r) => contains(value: r.metric, set: predictive_alert_fields))
        |> last()
        |> pivot(rowKey: ["lampada", "metric"], columnKey: ["_field"], valueColumn: "_value")

// =========================
// DATI RECENTI
// =========================
dati =
    from(bucket: data_bucket)
        |> range(start: -10m)
        |> filter(fn: (r) => r._measurement == sensor_measurement)
        |> filter(fn: (r) => contains(value: r._field, set: predictive_alert_fields))
        |> hourSelection(start: 9, stop: 17, location: location)
        |> last()
        |> rename(columns: {_field: "metric"})

// =========================
// JOIN + MAPPATURA CLIENT
// Solo device con valore SOTTO soglia: il rule_service
// valuterà il trend e deciderà se emettere l'alert predittivo.
// =========================
predictive_rows =
    join(tables: {d: dati, s: soglie}, on: ["lampada", "metric"])
        |> filter(fn: (r) => exists r._value and exists r.warning)

join(tables: {p: predictive_rows, c: monitored_devices}, on: ["lampada"])
    |> filter(fn: (r) => exists r._value and exists r.warning)
    |> filter(fn: (r) => r._value < r.warning)
    |> map(
        fn: (r) => {
            payload = {
                client_id: r.client_id,
                lampada: r.lampada,
                stanza: r.stanza,
                host: r.host,
                trigger_metrica: r.metric,
                trigger_valore: r._value,
                threshold_warning: r.warning,
                threshold_critical: if exists r.critical then r.critical else 0.0,
                level: "OK",
                predictive_only: true,
                prediction_target_level: "WARNING",
                timestamp_alert: string(v: r._time),
            }

            response =
                http.post(
                    url: rule_service_url,
                    headers: {"Content-Type": "application/json"},
                    data: json.encode(v: payload),
                )

            return {r with status_code: response}
        },
    )
    |> yield(name: "predictive_status")
