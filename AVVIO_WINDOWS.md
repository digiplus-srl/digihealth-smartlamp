# Avvio DigiHealth su Windows

## Prerequisiti

- Python 3.13
- Tutte le dipendenze già installate su questo PC

Se dovessi reinstallare le dipendenze:

```powershell
pip install flask tinytuya pydantic pyyaml pyaudio pyserial numpy
```

## Comando di avvio

Apri PowerShell (o CMD) nella cartella del progetto ed esegui:

```powershell
cd C:\Users\digip\Desktop\digihealth-smartlamp
.\venv\Scripts\python.exe -m digihealth.main
```

## Pagine web disponibili

| URL | Descrizione |
|-----|-------------|
| `http://127.0.0.1:5000/` | Dashboard principale (audio, aria, attuatori) |
| `http://127.0.0.1:5000/alerts` | Log degli alert ricevuti e azioni eseguite |
| `http://127.0.0.1:5000/config` | Configurazione attuatori, sensori, processori |
| `http://127.0.0.1:5000/thresholds` | Soglie variabili fisiche (purificatore e climatizzatore) |

## File di configurazione attivo su Windows

```
config/windows.yaml
```

Viene selezionato automaticamente in base al sistema operativo.
Su Linux/Raspberry Pi viene usato `config/default.yaml`.

## Note sugli attuatori su Windows

- **Shelly**: attiva, tenta connessione alla lampada in rete locale
- **TuyaAC**: attivo se `enabled: true` in `windows.yaml`
- **TuyaPurifier**: disabilitato di default su Windows (`enabled: false`)
- **Sensore ZPH01B**: disabilitato su Windows (porta seriale non disponibile)
- **Microfono**: attivo, usa il microfono di default del sistema (`device_index: 1` = Microphone Array Realtek)

## Microfono — trovare il device_index corretto

Gli indici audio su Windows possono cambiare dopo un riavvio del PC.
Se il microfono non funziona, esegui lo script di diagnostica:

```powershell
.\venv\Scripts\python.exe tools\find_audio_devices.py
```

Lo script mostra tutti i dispositivi disponibili e testa quali funzionano.
Aggiorna poi `sensors.microphone.device_index` in `config/windows.yaml` con il valore corretto.

## Audio comfort — rumore rosa

Su Windows il rumore rosa viene riprodotto tramite `winsound` (nativo, nessun tool esterno necessario).
I file audio MP3/WAV di comfort vengono riprodotti tramite `mpg123`, `vlc` o PowerShell MediaPlayer
(il primo disponibile nel sistema viene usato automaticamente).

## Soglie (pagina `/thresholds`)

Le soglie determinano quando gli attuatori intervengono:

- **Purificatore**: si accende se PM2.5 > tolleranza PM2.5 **oppure** CO2 > tolleranza CO2
- **Climatizzatore**: si accende se Temperatura > tolleranza TEMP

Dopo aver modificato e salvato le soglie, clicca **Riavvia** per applicarle.

## Notifiche Telegram

Per ricevere gli alert su Telegram, imposta le variabili d'ambiente prima di avviare l'app:

```powershell
$env:TELEGRAM_BOT_TOKEN = "il-tuo-token"
$env:TELEGRAM_CHAT_ID   = "il-tuo-chat-id"
.\venv\Scripts\python.exe -m digihealth.main
```

Oppure crea un file `.env` nella root del progetto (viene caricato automaticamente):

```
TELEGRAM_BOT_TOKEN=il-tuo-token
TELEGRAM_CHAT_ID=il-tuo-chat-id
```

Il bot invia un messaggio ad ogni alert ricevuto su `/api/alerts`.
Per disabilitare le notifiche senza rimuovere le env var: `telegram.enabled: false` in `windows.yaml`.

## Fermare l'app

```
Ctrl+C
```
nel terminale dove gira l'app.
