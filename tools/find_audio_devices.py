#!/usr/bin/env python3
"""
Diagnostica audio per Raspberry Pi / Linux.
Trova i device_index corretti da mettere in config/default.yaml.

Esecuzione:
    cd /path/to/digihealth-smartlamp
    python3 tools/find_audio_devices.py
"""

import subprocess
import sys

# ── 1. ALSA: aplay -l  e  arecord -l ─────────────────────────────────────────
print("=" * 60)
print("ALSA — Dispositivi di OUTPUT (aplay -l)")
print("=" * 60)
try:
    out = subprocess.check_output(["aplay", "-l"], text=True, stderr=subprocess.DEVNULL)
    print(out)
except FileNotFoundError:
    print("  aplay non trovato — installa: sudo apt install alsa-utils")
except Exception as e:
    print(f"  Errore: {e}")

print("=" * 60)
print("ALSA — Dispositivi di INPUT (arecord -l)")
print("=" * 60)
try:
    out = subprocess.check_output(["arecord", "-l"], text=True, stderr=subprocess.DEVNULL)
    print(out)
except FileNotFoundError:
    print("  arecord non trovato — installa: sudo apt install alsa-utils")
except Exception as e:
    print(f"  Errore: {e}")

# ── 2. Verifica tool necessari ────────────────────────────────────────────────
print("=" * 60)
print("Tool necessari per la riproduzione audio")
print("=" * 60)
for tool in ["aplay", "mpg123"]:
    try:
        subprocess.check_output(["which", tool], stderr=subprocess.DEVNULL)
        print(f"  {tool}: OK")
    except subprocess.CalledProcessError:
        cmd = "sudo apt install alsa-utils" if tool == "aplay" else "sudo apt install mpg123"
        print(f"  {tool}: MANCANTE  →  installa con: {cmd}")

# ── 3. PyAudio — lista tutti i device ────────────────────────────────────────
print()
print("=" * 60)
print("PyAudio — tutti i dispositivi")
print("=" * 60)
try:
    import pyaudio
    pa = pyaudio.PyAudio()
    print(f"Totale dispositivi: {pa.get_device_count()}")
    print()

    input_candidates  = []
    output_candidates = []

    for i in range(pa.get_device_count()):
        d = pa.get_device_info_by_index(i)
        parts = []
        if d['maxInputChannels'] > 0:
            parts.append(f"IN={d['maxInputChannels']}ch")
            input_candidates.append(i)
        if d['maxOutputChannels'] > 0:
            parts.append(f"OUT={d['maxOutputChannels']}ch")
            output_candidates.append(i)
        print(f"  [{i:2d}] {d['name'][:50]:<50}  {' | '.join(parts)}")

    # ── 4. Test apertura stream input ────────────────────────────────────────
    print()
    print("=" * 60)
    print("Test apertura MICROFONO (channels=1, rate=44100)")
    print("=" * 60)
    RATE, CHUNK = 44100, 1024
    working_inputs = []
    for i in input_candidates:
        d = pa.get_device_info_by_index(i)
        try:
            s = pa.open(format=pyaudio.paInt16, channels=1, rate=RATE,
                        input=True, frames_per_buffer=CHUNK, input_device_index=i)
            s.close()
            print(f"  [{i:2d}] {d['name'][:50]:<50}  OK")
            working_inputs.append(i)
        except Exception as e:
            print(f"  [{i:2d}] {d['name'][:50]:<50}  FAIL: {e}")

    pa.terminate()

    # ── 5. Suggerimento ──────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("SUGGERIMENTO per config/default.yaml")
    print("=" * 60)

    usb_in = next(
        (i for i in working_inputs
         if "usb" in pa._get_default_input_device_info()['name'].lower()
            or "pnp" in pa.get_device_info_by_index(i)['name'].lower()
            or "usb" in pa.get_device_info_by_index(i)['name'].lower()),
        working_inputs[0] if working_inputs else None
    )

    # cerca output USB o casse esterne
    pa2 = pyaudio.PyAudio()
    usb_out = None
    for i in output_candidates:
        name = pa2.get_device_info_by_index(i)['name'].lower()
        if any(k in name for k in ["usb", "pnp", "external", "speaker"]):
            usb_out = i
            break

    print()
    if working_inputs:
        print("Microfono USB consigliato:")
        for i in working_inputs:
            pa2_ = pyaudio.PyAudio()
            name = pa2_.get_device_info_by_index(i)['name']
            pa2_.terminate()
            marker = " <-- consigliato" if i == usb_in else ""
            print(f"  device_index: {i}  # {name}{marker}")
    else:
        print("  Nessun microfono funzionante trovato!")

    print()
    print("Output (casse esterne):")
    pa3 = pyaudio.PyAudio()
    for i in output_candidates:
        name = pa3.get_device_info_by_index(i)['name']
        marker = " <-- consigliato" if i == usb_out else ""
        print(f"  output_device_index: {i}  # {name}{marker}")
    pa3.terminate()

except ImportError:
    print("  PyAudio non installato — installa con: pip install pyaudio")
except Exception as e:
    print(f"  Errore PyAudio: {e}")

print()
print("Fatto. Copia i valori di device_index e output_device_index")
print("in sensors.microphone dentro config/default.yaml.")
