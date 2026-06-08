import "http"
import "json"
import "timezone"
import "array"

// ============================================================
// Task: Alert in tempo reale
// Frequenza: ogni 15 minuti
// Invia un evento al rule_service per ogni metrica che supera
// la soglia warning o critical configurata in health_settings.
// ============================================================

option task = {name: "AirQuality_Alert_Task", every: 15m}
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

// Metriche monitorate — adattare ai propri field name in InfluxDB
allowed_alert_fields = [
    "CO2-AnidrideCarbonica-[ppm]",
    "PM2_5-Particolato-[µg/m^3]",
    "PM10-Particolato-[µg/m^3]",
    "PM1-Particolato-[µg/m^3]",
    "TVOC-QualitaAria-[G]",
    "CO-MonossidoDiCarbonio-[ppm]",
    "O3-Ozono-[ppm]",
    "NO2-BiossidoDiAzoto-[ppm]",
    "TEMP-[C]",
    "HUM-[%]",
    "lux-IntensitaLuminosa",
    "CH2O-Formaldeide-[mg/m^3]",
]

lux_alert_fields = [
    "lux-IntensitaLuminosa",
]

// =========================
// SOGLIE
// Lette dal bucket health_settings, measurement "thresholds".
// Ogni record deve avere: tag "lampada", tag "metric", field "warning", field "critical" (opzionale).
// =========================
soglie =
    from(bucket: settings_bucket)
        |> range(start: -1y)
        |> filter(fn: (r) => r._measurement == threshold_measurement)
        |> filter(fn: (r) => contains(value: r.metric, set: allowed_alert_fields))
        |> group(columns: ["lampada", "metric", "_field"])
        |> last()
        |> pivot(rowKey: ["lampada", "metric"], columnKey: ["_field"], valueColumn: "_value")

// =========================
// DATI RECENTI
// Letti dal bucket health_data, finestra oraria 9-18.
// Adattare measurement e tag ai propri dati.
// =========================
dati =
    from(bucket: data_bucket)
        |> range(start: -16m)
        |> filter(fn: (r) => r._measurement == sensor_measurement)
        |> filter(fn: (r) => contains(value: r._field, set: allowed_alert_fields))
        |> hourSelection(start: 9, stop: 18, location: location)
        |> group(columns: ["lampada", "stanza", "host", "_field"])
        |> last()
        |> rename(columns: {_field: "metric"})

// =========================
// JOIN + MAPPATURA CLIENT
// Il client_id viene inviato al rule_service per l'associazione CRM.
// =========================
alert_rows =
    join(tables: {d: dati, s: soglie}, on: ["lampada", "metric"])
        |> filter(fn: (r) => exists r._value and exists r.warning)

join(tables: {a: alert_rows, c: monitored_devices}, on: ["lampada"])
    |> filter(fn: (r) => exists r._value and exists r.warning)
    |> filter(
        fn: (r) =>
            if contains(value: r.metric, set: lux_alert_fields) then
                r._value <= r.warning
            else
                r._value >= r.warning,
    )
    // =========================
    // INVIO EVENTO AL RULE SERVICE
    // Sostituire l'URL con quello del proprio rule_service.
    // =========================
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
                level:
                    if contains(value: r.metric, set: lux_alert_fields) then
                        if exists r.critical and r._value <= r.critical then "CRITICAL" else "WARNING"
                    else
                        if exists r.critical and r._value >= r.critical then "CRITICAL" else "WARNING",
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
    |> yield(name: "alert_status")
