# Esposizione su internet — Cloudflare Tunnel + API di ingestion

Questa guida spiega come esporre il Raspberry su internet tramite **Cloudflare
Tunnel** per ricevere gli alert HTTP POST dal sistema esterno, mantenendo la
dashboard locale privata.

## Architettura

```
   Sistema esterno (CRM / predizioni)
        │  HTTPS POST /api/alerts  (Authorization: Bearer <API_KEY>)
        ▼
   Cloudflare Edge ──(tunnel cifrato)──► cloudflared sul Raspberry
                                              │  http://localhost:5000
                                              ▼
                                       Flask: /api/* (auth API key)
                                              │
                                  valida → SQLite → dispatcher (stub)
```

- Pubblicamente raggiungibile **solo** `/api/*` (vedi `cloudflared/config.example.yml`).
- La dashboard (`/`, `/status`, `/toggle`, ...) resta su LAN, non esposta dal tunnel.
- Nessuna porta aperta sul router: il tunnel è in uscita dal Raspberry.

## 1. Prerequisiti applicativi (già fatti nel codice)

1. Copia `.env.example` in `.env` e compila:
   - `INFLUXDB_TOKEN` — **nuovo** token (quello vecchio era in git, va revocato).
   - `DIGIHEALTH_API_KEY` — chiave per gli endpoint di ingestion. Generala con:
     ```bash
     python3 -c "import secrets; print(secrets.token_urlsafe(32))"
     ```
2. Comunica al mittente (Riccardo) la chiave: la userà come
   `Authorization: Bearer <chiave>` (oppure header `X-Api-Key: <chiave>`).

## 2. Installazione cloudflared sul Raspberry

```bash
# Architettura ARM (Raspberry Pi a 64 bit)
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64 -o cloudflared
sudo install -m 755 cloudflared /usr/local/bin/cloudflared
cloudflared --version
```

## 3. Autenticazione e creazione del tunnel

> Questi comandi richiedono il **login interattivo** al tuo account Cloudflare
> e un dominio gestito da Cloudflare. Eseguili tu sul Raspberry.

```bash
cloudflared tunnel login                      # apre il browser per autorizzare
cloudflared tunnel create digihealth-lamp     # crea il tunnel e il file di credenziali
cloudflared tunnel route dns digihealth-lamp lampada.tuodominio.it
```

Annota il `TUNNEL_ID` mostrato e il path del file credenziali
(`~/.cloudflared/<TUNNEL_ID>.json`).

## 4. Configurazione del tunnel

```bash
cp cloudflared/config.example.yml ~/.cloudflared/config.yml
# Modifica: TUNNEL_ID, credentials-file, hostname (lampada.tuodominio.it)
nano ~/.cloudflared/config.yml
```

La config espone solo `/api/.*`; ogni altro path risponde `404`.

## 5. Avvio come servizio

```bash
sudo cp systemd/cloudflared.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cloudflared
sudo systemctl status cloudflared
```

## 6. Verifica

```bash
# Health check (pubblico, senza auth)
curl https://lampada.tuodominio.it/api/health
# -> {"status":"ok"}

# Senza chiave -> 401
curl -i -X POST https://lampada.tuodominio.it/api/alerts \
  -H "Content-Type: application/json" -d '{"event_type":"air_quality_alert"}'

# Con chiave -> 200 {"status":"received","id":N}
curl -i -X POST https://lampada.tuodominio.it/api/alerts \
  -H "Authorization: Bearer <API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"event_type":"air_quality_alert","action_code":"WP3_11","level":"CRITICAL"}'

# La dashboard NON deve essere raggiungibile dal tunnel:
curl -i https://lampada.tuodominio.it/        # -> 404
```

## 7. Contratto dell'endpoint (per il mittente)

- **URL**: `POST https://lampada.tuodominio.it/api/alerts`
- **Header**: `Content-Type: application/json` + `Authorization: Bearer <API_KEY>`
  (in alternativa `X-Api-Key: <API_KEY>`)
- **Body**: evento `air_quality_alert`, variante semplice o completa (`schema_version: "1.0"`).
- **Risposte**: `200 {"status":"received","id":N}` se ok, `400` payload non valido,
  `401` non autorizzato.
- **Timeout consigliato lato mittente**: 10–20 s.

## Opzionale — proteggere anche la dashboard

Se in futuro vuoi raggiungere la dashboard da remoto, **non** rimuovere la
restrizione di path: aggiungi un secondo hostname dietro **Cloudflare Access**
(Zero Trust) con login email/Google, così gli umani autenticano via Cloudflare
e i dispositivi continuano a usare l'API key sugli endpoint `/api/*`.
