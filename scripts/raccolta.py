#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RACCOLTA DATI - Monitor treni Bergamo

Ogni volta che viene eseguito (di norma ogni 15 minuti, da GitHub Actions):
  1. legge da ViaggiaTreno le tabelle di partenze e arrivi delle stazioni monitorate
     (elenco in config/stazioni.csv: Bergamo, Treviglio, Ponte San Pietro);
  2. aggiunge al file CSV del giorno i treni nuovi;
  3. per i treni il cui orario è arrivato (o sta per arrivare) chiede il dettaglio
     della corsa e registra il ritardo REALE nella stazione, oppure la soppressione.
Un treno che passa da più stazioni genera una riga per stazione: i doppioni
vengono eliminati in fase di aggregazione (aggrega.py).

Un file CSV per giorno in docs/dati/corse/AAAA-MM-GG.csv. Solo libreria standard.
"""
import csv
import io
import json
import re
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

RM = ZoneInfo("Europe/Rome")
STAZIONE = "S01529"  # codice ViaggiaTreno di Bergamo (verificato dal test): è la stazione "di riferimento"
CONFIG_STAZIONI = Path(os.environ.get("CONFIG_STAZIONI", "config/stazioni.csv"))
STAZIONI_DEFAULT = [
    {"codice": "S01529", "nome": "Bergamo", "nome_vt": "BERGAMO"},
    {"codice": "S01708", "nome": "Treviglio", "nome_vt": "TREVIGLIO"},
    {"codice": "S01528", "nome": "Ponte San Pietro", "nome_vt": "PONTE S.PIETRO"},
]
# categorie di treno escluse (alta velocità e lunga percorrenza): il monitor riguarda i treni regionali
CATEGORIE_ESCLUSE = {"FR", "FA", "FB", "IC", "ICN", "EC", "EN", "ES", "EXP", "ITA", "EE", "TGV", "RJ", "NJ"}
BASI = [
    "https://www.viaggiatreno.it/infomobilita/resteasy/viaggiatreno/",
    "https://www.viaggiatreno.it/infomobilitamobile/resteasy/viaggiatreno/",
]
CARTELLA = Path(os.environ.get("CARTELLA_CORSE", "docs/dati/corse"))
OFFSET_TABELLE_MIN = (-30, 0, 45, 90, 135)  # a che ora "guardiamo" le tabelle, rispetto ad adesso
ANTICIPO_DETTAGLIO_MIN = 10   # chiediamo il dettaglio dai 10 minuti prima dell'orario previsto
SCADENZA_ORE = 6              # dopo 6 ore senza dati definitivi il treno diventa "nd"
MAX_DETTAGLI = 80             # limite di richieste di dettaglio per esecuzione
ATTESA_STIMA_MIN = 30         # minuti oltre l'arrivo atteso, dopo i quali un capolinea non registrato viene stimato
MAX_RICONTROLLI = 20          # limite di riletture di corse già effettuate (per cogliere soppressioni parziali tardive)
RICONTROLLO_ORE = 2           # le corse effettuate vengono rilette per questo tempo dopo l'orario programmato
MAX_PERCORSI = 25             # limite di richieste per completare il percorso ("via") dei treni da/per Milano
PAUSA = float(os.environ.get("PAUSA_RICHIESTE", "0.4"))

CAMPI = [
    "id", "data", "direzione", "numero_treno", "controparte",
    "orario_programmato", "orario_reale", "ritardo_min", "stato",
    "provvedimento", "tipo_treno", "fermate_soppresse", "motivo_ritardo",
    "definitivo", "aggiornato_il", "ts_programmato", "cod_origine", "data_corsa_ms", "via", "parziale", "stimato",
    "stazione", "origine", "destinazione",
]
CAMPI_CONFRONTO = ["orario_reale", "ritardo_min", "stato", "provvedimento", "tipo_treno",
                   "fermate_soppresse", "motivo_ritardo", "definitivo", "controparte", "via", "parziale", "stimato",
                   "origine", "destinazione"]

_base_ok = None
_rifiuti_403 = 0          # richieste consecutive rifiutate con "403 Forbidden"
MAX_RIFIUTI_403 = 4       # oltre questa soglia ci fermiamo: ViaggiaTreno sta bloccando questo giro


class Bloccato(Exception):
    """ViaggiaTreno rifiuta le richieste (403): inutile insistere in questo giro."""
_cache_dettagli = {}


def leggi_stazioni():
    """Stazioni monitorate, da config/stazioni.csv (colonne: codice, nome, nome_viaggiatreno, data_inizio)."""
    stazioni = []
    if CONFIG_STAZIONI.exists():
        with CONFIG_STAZIONI.open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                cod = (r.get("codice") or "").strip()
                nome = (r.get("nome") or "").strip()
                if cod and nome:
                    stazioni.append({"codice": cod, "nome": nome,
                                     "nome_vt": ((r.get("nome_viaggiatreno") or nome).strip()).upper()})
    return stazioni or [dict(x) for x in STAZIONI_DEFAULT]


STAZIONI = leggi_stazioni()
NOME_VT = {x["codice"]: x["nome_vt"] for x in STAZIONI}


# ------------------------------------------------------------------ rete
def chiedi(percorso):
    """Scarica un indirizzo di ViaggiaTreno. Restituisce il testo, o None se non risponde.
    Dopo alcuni rifiuti 403 consecutivi solleva Bloccato, per non insistere (e non appesantire il servizio)."""
    global _base_ok, _rifiuti_403
    basi = ([_base_ok] if _base_ok else []) + [b for b in BASI if b != _base_ok]
    ultimo = "nessuna risposta"
    for base in basi:
        for tentativo in range(2):
            try:
                req = urllib.request.Request(
                    base + percorso,
                    headers={"User-Agent": "Mozilla/5.0 (monitor-treni-bergamo)"},
                )
                with urllib.request.urlopen(req, timeout=30) as r:
                    if r.status == 200:
                        _base_ok = base
                        _rifiuti_403 = 0
                        return r.read().decode("utf-8", "replace")
                    ultimo = f"HTTP {r.status}"
            except urllib.error.HTTPError as e:
                ultimo = f"HTTPError: {e}"
                if e.code == 403:  # rifiuto esplicito: non si riprova subito
                    _rifiuti_403 += 1
                    if _rifiuti_403 >= MAX_RIFIUTI_403:
                        raise Bloccato(ultimo)
                    break
            except Exception as e:  # noqa: BLE001
                ultimo = f"{type(e).__name__}: {e}"
            time.sleep(1.5)
    print(f"  richiesta fallita ({percorso[:70]}): {ultimo}")
    return None


def json_da(percorso):
    testo = chiedi(percorso)
    if testo is None:
        return None
    try:
        return json.loads(testo)
    except ValueError:
        return None


def dettaglio_corsa(riga):
    """Dettaglio di una corsa. Se ViaggiaTreno non risponde con la stazione di origine registrata
    (succede quando la corsa viene cancellata in parte e parte da un'altra stazione, es. da Treviglio Ovest
    invece che da Bergamo), cerca l'origine attuale tramite il numero del treno e riprova."""
    numero = riga["numero_treno"]
    chiave = (riga["cod_origine"], numero, riga["data_corsa_ms"])
    if chiave in _cache_dettagli:  # lo stesso treno compare in più stazioni: una sola richiesta per esecuzione
        return _cache_dettagli[chiave]
    d = json_da(f"andamentoTreno/{riga['cod_origine']}/{numero}/{riga['data_corsa_ms']}")
    if isinstance(d, dict) and d:
        _cache_dettagli[chiave] = d
        return d
    testo = chiedi(f"cercaNumeroTrenoTrenoAutocomplete/{numero}")
    if not testo:
        return None
    try:
        attesa = int(riga["data_corsa_ms"])
    except ValueError:
        attesa = 0
    trovati = []
    for m in re.finditer(r"\|(\d+)-([A-Z]\d+)-(\d{10,})", testo):
        if m.group(1) == numero and m.group(2) != riga["cod_origine"]:
            trovati.append((abs(int(m.group(3)) - attesa), m.group(2), m.group(3)))
    trovati.sort()
    for scarto, cod, ms in trovati[:2]:
        if attesa and scarto > 36 * 3600000:
            continue
        d = json_da(f"andamentoTreno/{cod}/{numero}/{ms}")
        time.sleep(PAUSA)
        if isinstance(d, dict) and d:
            print(f"  treno {numero}: dettaglio trovato con origine {cod} (diversa da {riga['cod_origine']})")
            _cache_dettagli[chiave] = d
            return d
    print(f"  treno {numero}: dettaglio non disponibile (origine {riga['cod_origine']}; ricerca per numero: {testo.strip()[:80]!r})")
    return None


def data_vt(dt):
    """Data nel formato che si aspetta ViaggiaTreno, es. 'Thu Nov 19 2026 13:14:00 GMT+0100 (CET)' (in estate CEST)."""
    s = dt.strftime("%a %b %d %Y %H:%M:%S GMT%z") + f" ({dt.tzname() or 'CET'})"   # CET in inverno, CEST con l'ora legale
    return urllib.parse.quote(s, safe="")


def tabella(tipo, dt, codice=STAZIONE):
    dati = json_da(f"{tipo}/{codice}/{data_vt(dt)}")
    return dati if isinstance(dati, list) else None


# ------------------------------------------------------------------ utilità
def a_ms(v):
    """Converte un valore in millisecondi (accetta anche secondi). None se non valido."""
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return v * 1000 if v < 10 ** 11 else v


def hhmm(ms):
    return datetime.fromtimestamp(ms / 1000, RM).strftime("%H:%M")


def giorno_di(ms):
    return datetime.fromtimestamp(ms / 1000, RM).date().isoformat()


def mezzanotte_ms(giorno):
    d = datetime.fromisoformat(giorno).replace(tzinfo=RM)
    return int(d.timestamp() * 1000)


# ------------------------------------------------------------------ scrittura sicura
def scrivi_atomico(percorso, testo):
    """Scrive in un file temporaneo e poi lo rinomina: se il programma si interrompe, il file vecchio resta intatto."""
    percorso = Path(percorso)
    percorso.parent.mkdir(parents=True, exist_ok=True)
    tmp = percorso.with_name(percorso.name + ".tmp")
    tmp.write_text(testo, encoding="utf-8")
    os.replace(tmp, percorso)


_PERICOLOSI = ("=", "+", "@", "\t", "\r")


def proteggi(v):
    """Excel interpreta come formula un testo che inizia con = + @: si antepone un apice (visibile solo nel file .csv)."""
    return "'" + v if v.startswith(_PERICOLOSI) else v


def ripristina(v):
    return v[1:] if v.startswith("'") and v[1:].startswith(_PERICOLOSI) else v


# ------------------------------------------------------------------ archivio CSV
class Archivio:
    """Tiene in memoria i file CSV dei giorni toccati e li riscrive solo se cambiano."""

    def __init__(self):
        self.giorni = {}
        self.originali = {}

    def _file(self, giorno):
        return CARTELLA / f"{giorno}.csv"

    def righe(self, giorno):
        if giorno not in self.giorni:
            rows, testo = {}, ""
            f = self._file(giorno)
            if f.exists():
                testo = f.read_text(encoding="utf-8")
                for r in csv.DictReader(io.StringIO(testo)):
                    r = {k: ripristina(v) if isinstance(v, str) else v for k, v in r.items()}
                    if not r.get("stazione"):
                        r["stazione"] = STAZIONE  # righe registrate prima dell'arrivo delle altre stazioni: Bergamo
                    rows[r["id"]] = r
            self.originali[giorno] = testo
            self.giorni[giorno] = rows
        return self.giorni[giorno]

    def tutte(self):
        for rows in self.giorni.values():
            yield from rows.values()

    def salva(self):
        CARTELLA.mkdir(parents=True, exist_ok=True)
        modificati = 0
        for giorno, rows in self.giorni.items():
            if not rows:
                continue
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=CAMPI, lineterminator="\n", extrasaction="ignore")
            w.writeheader()
            for r in sorted(rows.values(), key=lambda r: (int(r.get("ts_programmato") or 0), r["id"])):
                w.writerow({c: proteggi(r.get(c) or "") for c in CAMPI})
            nuovo = buf.getvalue()
            if nuovo != self.originali.get(giorno):
                scrivi_atomico(self._file(giorno), nuovo)
                modificati += 1
        return modificati


# ------------------------------------------------------------------ passo 1: tabelle
def aggiungi_da_tabella(arch, item, direzione, adesso_iso, stazione=STAZIONE):
    if str(item.get("categoria") or "").strip().upper() in CATEGORIE_ESCLUSE:
        return False
    if direzione == "partenza":
        ts = a_ms(item.get("orarioPartenza")) or a_ms(item.get("orarioPartenzaZero"))
        controparte = item.get("destinazione") or item.get("destinazioneZero") or ""
    else:
        ts = a_ms(item.get("orarioArrivo")) or a_ms(item.get("orarioArrivoZero"))
        controparte = item.get("origine") or item.get("origineZero") or ""
    numero = str(item.get("numeroTreno") or "").strip()
    if not ts or not numero:
        return False
    giorno = giorno_di(ts)
    cod_origine = str(item.get("codOrigine") or item.get("idOrigine") or "")
    data_corsa = (a_ms(item.get("millisDataPartenza")) or a_ms(item.get("dataPartenzaTreno"))
                  or mezzanotte_ms(giorno))
    rid = f"{cod_origine}-{numero}-{data_corsa}-{direzione[0]}"
    if stazione != STAZIONE:
        rid = f"{stazione}-{rid}"  # il treno è lo stesso, ma la riga riguarda un'altra stazione
    righe = arch.righe(giorno)
    if rid in righe:
        if not righe[rid].get("controparte") and controparte:
            righe[rid]["controparte"] = controparte
        return False
    righe[rid] = {
        "id": rid, "data": giorno, "direzione": direzione, "numero_treno": numero,
        "controparte": controparte, "orario_programmato": hhmm(ts), "orario_reale": "",
        "ritardo_min": "", "stato": "programmato", "provvedimento": "", "tipo_treno": "",
        "fermate_soppresse": "", "motivo_ritardo": "", "definitivo": "0",
        "aggiornato_il": adesso_iso, "ts_programmato": str(ts),
        "cod_origine": cod_origine, "data_corsa_ms": str(data_corsa), "via": "",
        "stazione": stazione, "origine": "", "destinazione": "",
    }
    return True


# ------------------------------------------------------------------ passo 2: dettaglio
def fermata_stazione(d, codice=STAZIONE, nome="BERGAMO"):
    """La fermata della corsa nella stazione monitorata (per codice, altrimenti per nome identico)."""
    fermate = d.get("fermate") or []
    for f in fermate:
        if f.get("id") == codice:
            return f
    for f in fermate:
        if str(f.get("stazione", "")).strip().upper() == nome:
            return f
    return None


fermata_bergamo = fermata_stazione  # nome storico

_dettagli_scartati = 0


def dettaglio_incoerente(riga, d, f):
    """True se il dettaglio appartiene a un altro treno con lo stesso numero (es. 2255 Milano Cadorna-Novara Nord al posto
    del 2255 Milano Porta Garibaldi-Bergamo): la stazione monitorata non compare tra le fermate e nemmeno la controparte
    indicata nella tabella della stazione. In dubbio (nessuna fermata, nessuna controparte) il dettaglio si considera valido."""
    fermate = d.get("fermate") or []
    if f is not None or not fermate:
        return False
    ctrp = (riga.get("controparte") or "").strip().upper()
    if not ctrp:
        return False
    nomi = {str(x.get("stazione", "")).strip().upper() for x in fermate}
    nomi |= {str(d.get("origine") or "").strip().upper(), str(d.get("destinazione") or "").strip().upper()}
    return ctrp not in nomi


def stazione_soppressa(lista, codice=STAZIONE, nome="BERGAMO"):
    """True se tra le fermate soppresse c'è la stazione (confronto esatto: Treviglio non è Treviglio Ovest)."""
    for x in lista or []:
        valori = [str(v).strip().upper() for v in x.values()] if isinstance(x, dict) else [str(x).strip().upper()]
        if codice in valori or nome in valori:
            return True
    return False


bergamo_soppressa = stazione_soppressa  # nome storico


def _nome_fermata(x):
    if isinstance(x, dict):
        x = x.get("stazione") or x.get("nomeFermata") or x.get("descrizione") or ""
    return str(x).strip().upper()


def fermata_soppressa(f):
    """True se la fermata risulta soppressa (actualFermataType = 3), sia che il valore arrivi come numero sia come testo."""
    try:
        return int(str((f or {}).get("actualFermataType")).strip()) == 3
    except (ValueError, TypeError):
        return False


def fermate_saltate(d):
    """Nomi delle fermate (diverse da Bergamo) che la corsa non effettua: dall'elenco fermateSoppresse
    e dalle fermate con actualFermataType = 3 (soppressa)."""
    nomi = []
    for x in d.get("fermateSoppresse") or []:
        n = _nome_fermata(x)
        if n:
            nomi.append(n)
    for f in d.get("fermate") or []:
        if fermata_soppressa(f):
            n = _nome_fermata(f)
            if n:
                nomi.append(n)
    visti = []
    for n in nomi:
        if n not in visti:
            visti.append(n)
    return visti


def percorso(d):
    """Dalle fermate della corsa ricava il percorso: "Carnate", "Pioltello" oppure "-" se non riconosciuto.
    Restituisce "" se la corsa non riporta fermate."""
    nomi = [str(f.get("stazione", "")).upper() for f in (d.get("fermate") or [])]
    if not nomi:
        return ""
    if any("CARNATE" in n or "PADERNO" in n for n in nomi):
        return "Carnate"
    if any("PIOLTELLO" in n or "TREVIGLIO" in n for n in nomi):
        return "Pioltello"
    return "-"


def aggiorna_da_dettaglio(riga, d, adesso_ms):
    """Aggiorna la riga con il dettaglio della corsa. Restituisce True se qualcosa è cambiato."""
    prima = {c: riga.get(c, "") for c in CAMPI_CONFRONTO}
    ts = int(riga["ts_programmato"])
    prov = d.get("provvedimento")
    tipo = d.get("tipoTreno")
    sopp_lista = d.get("fermateSoppresse") or []
    cod_st = riga.get("stazione") or STAZIONE
    nome_st = NOME_VT.get(cod_st, "BERGAMO")
    f = fermata_stazione(d, cod_st, nome_st)
    if dettaglio_incoerente(riga, d, f):
        global _dettagli_scartati
        _dettagli_scartati += 1
        print(f"  treno {riga['numero_treno']}: dettaglio scartato ({d.get('origine')} → {d.get('destinazione')}), non passa da {nome_st} né dalla controparte {riga.get('controparte')}")
        return False
    for k_d, k_r in (("origine", "origine"), ("destinazione", "destinazione")):
        if d.get(k_d):
            riga[k_r] = str(d.get(k_d)).strip().upper()

    if riga["direzione"] == "partenza":
        k_teo, k_rea, k_rit = "partenza_teorica", "partenzaReale", "ritardoPartenza"
    else:
        k_teo, k_rea, k_rit = "arrivo_teorico", "arrivoReale", "ritardoArrivo"
    teo = rea = None
    rit_ufficiale = None
    if f:
        teo = a_ms(f.get(k_teo)) or a_ms(f.get("programmata"))
        rea = a_ms(f.get(k_rea))
        rit_ufficiale = f.get(k_rit)

    # Capolinea a Bergamo: ViaggiaTreno spesso non registra l'arrivo reale nell'ultima fermata, ma a corsa conclusa
    # ("arrivato") riporta il ritardo finale del treno, che coincide con quello di arrivo a Bergamo.
    if (not rea and riga["direzione"] == "arrivo" and d.get("arrivato") is True and f is not None
            and (d.get("idDestinazione") == cod_st or d.get("codDestinazione") == cod_st
                 or f is (d.get("fermate") or [None])[-1])):
        rit_fin = d.get("ritardo")
        if isinstance(rit_fin, int) and not isinstance(rit_fin, bool):
            rea = (teo or ts) + rit_fin * 60000
            rit_ufficiale = rit_fin

    # Capolinea non registrato: ViaggiaTreno a volte perde il treno nell'ultima stazione (ultimo rilevamento prima di Bergamo,
    # "arrivato" sempre falso). Se Bergamo è il capolinea, nessuna soppressione è segnalata e sono passati
    # ATTESA_STIMA_MIN minuti oltre l'arrivo atteso, si usa l'ultimo ritardo noto e si segna la riga come stimata.
    stimato = False
    if (not rea and riga["direzione"] == "arrivo" and f is not None and not d.get("fermateSoppresse")
            and (d.get("idDestinazione") == cod_st or f is (d.get("fermate") or [None])[-1])
            and d.get("provvedimento") in (0, None, "0")):
        rit_ult = d.get("ritardo")
        if isinstance(rit_ult, int) and not isinstance(rit_ult, bool) and d.get("oraUltimoRilevamento"):
            atteso = (teo or ts) + max(rit_ult, 0) * 60000
            if adesso_ms >= atteso + ATTESA_STIMA_MIN * 60000:
                rea = atteso
                rit_ufficiale = rit_ult
                stimato = True
    riga["stimato"] = "1" if stimato else ""

    via = percorso(d)
    if via:
        riga["via"] = via
    riga["provvedimento"] = "" if prov is None else str(prov)
    riga["tipo_treno"] = "" if tipo is None else str(tipo)
    saltate = fermate_saltate(d)
    riga["fermate_soppresse"] = "|".join(saltate)[:200]
    motivo = d.get("motivoRitardoPrevalente")
    riga["motivo_ritardo"] = str(motivo)[:80] if motivo else ""

    # Soppressione totale: la corsa non è stata eseguita per intero.
    # Soppressione parziale: la corsa circola, ma parte da una stazione diversa o termina in una diversa dal previsto
    # (o salta altre fermate). Se Bergamo non è servita, per Bergamo non c'è nessun passaggio da misurare.
    nomi_fermate = {_nome_fermata(x) for x in (d.get("fermate") or [])}
    totale = (prov == 1 or str(prov) == "1" or tipo == "ST"
              or bool(nomi_fermate and nomi_fermate <= set(saltate)))
    bergamo_non_servita = (nome_st in saltate or stazione_soppressa(sopp_lista, cod_st, nome_st)
                           or fermata_soppressa(f))  # (la stazione monitorata non è servita)
    parziale = (not totale) and (bergamo_non_servita or bool(saltate) or prov == 2 or str(prov) == "2")
    riga["parziale"] = "1" if parziale else ""
    if totale:
        riga["stato"] = "soppresso"
        riga["orario_reale"] = ""
        riga["ritardo_min"] = ""
        riga["definitivo"] = "1" if adesso_ms >= ts else "0"
    elif parziale and bergamo_non_servita:
        riga["stato"] = "parziale"
        riga["orario_reale"] = ""
        riga["ritardo_min"] = ""
        riga["definitivo"] = "1" if adesso_ms >= ts else "0"
    elif rea:
        if isinstance(rit_ufficiale, int) and not isinstance(rit_ufficiale, bool):
            ritardo = rit_ufficiale
        elif teo:
            ritardo = int(round((rea - teo) / 60000.0))
        else:
            ritardo = int(round((rea - ts) / 60000.0))
        riga["stato"] = "effettuato"
        riga["orario_reale"] = hhmm(rea)
        riga["ritardo_min"] = str(ritardo)
        riga["definitivo"] = "1"
    else:
        riga["stato"] = "in_corso" if adesso_ms >= ts else "programmato"
        riga["definitivo"] = "0"

    return {c: riga.get(c, "") for c in CAMPI_CONFRONTO} != prima


# ------------------------------------------------------------------ programma principale
def esegui(adesso=None):
    adesso = adesso or datetime.now(RM)
    adesso_ms = int(adesso.timestamp() * 1000)
    adesso_iso = adesso.replace(microsecond=0).isoformat()
    _cache_dettagli.clear()
    arch = Archivio()
    oggi = adesso.date()
    arch.righe((oggi - timedelta(days=1)).isoformat())
    arch.righe(oggi.isoformat())

    # --- 1. tabelle
    nuovi, viste, tabelle_ok = 0, 0, 0
    per_stazione = {}
    for st in STAZIONI:
        ok_st = 0
        for off in OFFSET_TABELLE_MIN:
            dt = adesso + timedelta(minutes=off)
            for tipo, direzione in (("partenze", "partenza"), ("arrivi", "arrivo")):
                dati = tabella(tipo, dt, st["codice"])
                time.sleep(PAUSA)
                if dati is None:
                    continue
                tabelle_ok += 1
                ok_st += 1
                for item in dati:
                    viste += 1
                    if aggiungi_da_tabella(arch, item, direzione, adesso_iso, st["codice"]):
                        nuovi += 1
        per_stazione[st["nome"]] = ok_st
    if tabelle_ok == 0:
        print("ERRORE: nessuna tabella raggiungibile, niente da salvare.")
        return 1

    # --- 2. dettagli
    candidati = [
        r for r in arch.tutte()
        if r["definitivo"] != "1"
        and int(r["ts_programmato"]) <= adesso_ms + ANTICIPO_DETTAGLIO_MIN * 60000
    ]
    candidati.sort(key=lambda r: int(r["ts_programmato"]))
    richiesti = aggiornati = 0
    for riga in candidati[:MAX_DETTAGLI]:
        richiesti += 1
        d = dettaglio_corsa(riga)
        time.sleep(PAUSA)
        if isinstance(d, dict) and d:
            if aggiorna_da_dettaglio(riga, d, adesso_ms):
                riga["aggiornato_il"] = adesso_iso
                aggiornati += 1

    # --- 2a. riletture: una soppressione parziale (es. corsa che termina prima) può comparire dopo il passaggio da Bergamo
    ricontrollati = 0
    gia_letti = {r["id"] for r in candidati[:MAX_DETTAGLI]}
    recenti = [
        r for r in arch.tutte()
        if r["definitivo"] == "1" and r["stato"] == "effettuato" and r["id"] not in gia_letti
        and adesso_ms - RICONTROLLO_ORE * 3600000 <= int(r["ts_programmato"]) <= adesso_ms
    ]
    recenti.sort(key=lambda r: -int(r["ts_programmato"]))
    for riga in recenti[:MAX_RICONTROLLI]:
        d = dettaglio_corsa(riga)
        time.sleep(PAUSA)
        if isinstance(d, dict) and d:
            ricontrollati += 1
            if aggiorna_da_dettaglio(riga, d, adesso_ms):
                riga["aggiornato_il"] = adesso_iso
                aggiornati += 1

    # --- 2b. percorso (via Pioltello o via Carnate) dei treni da/per Milano che non lo hanno ancora
    percorsi = 0
    gia = gia_letti | {r["id"] for r in recenti[:MAX_RICONTROLLI]}
    for riga in sorted(arch.tutte(), key=lambda r: -int(r["ts_programmato"])):
        if percorsi >= MAX_PERCORSI:
            break
        if riga.get("via") or riga["id"] in gia or "MILANO" not in (riga.get("controparte") or "").upper():
            continue
        d = dettaglio_corsa(riga)
        time.sleep(PAUSA)
        percorsi += 1
        if isinstance(d, dict) and d:
            riga["via"] = percorso(d) or "-"
            riga["aggiornato_il"] = adesso_iso

    # --- 3. scadenza: dopo 6 ore senza dati il treno resta "nd" (non disponibile)
    scaduti = 0
    for riga in arch.tutte():
        if riga["definitivo"] != "1" and adesso_ms > int(riga["ts_programmato"]) + SCADENZA_ORE * 3600000:
            riga["stato"] = "nd"
            riga["definitivo"] = "1"
            riga["aggiornato_il"] = adesso_iso
            scaduti += 1

    file_modificati = arch.salva()
    # segnala che il giro è andato a buon fine (la pagina mostra quest'ora come "ultimo aggiornamento")
    scrivi_atomico(CARTELLA.parent / "stato.json", json.dumps(
        {"ultima_raccolta": adesso_iso, "tabelle_lette": tabelle_ok, "tabelle_totali": len(OFFSET_TABELLE_MIN) * 2 * len(STAZIONI)},
        separators=(",", ":")))
    print("Tabelle lette per stazione: " + ", ".join(f"{n} {k}/{len(OFFSET_TABELLE_MIN) * 2}" for n, k in per_stazione.items()))
    print(f"Tabelle lette: {tabelle_ok}/{len(OFFSET_TABELLE_MIN) * 2 * len(STAZIONI)} | treni visti: {viste} | nuovi: {nuovi} | "
          f"dettagli richiesti: {richiesti} | dettagli scartati (altro treno con lo stesso numero): {_dettagli_scartati} | riletture: {ricontrollati} | aggiornati: {aggiornati} | percorsi: {percorsi} | scaduti (nd): {scaduti} | "
          f"file modificati: {file_modificati}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(esegui())
    except Bloccato as e:
        # ViaggiaTreno rifiuta temporaneamente le richieste da questo indirizzo: niente da salvare, si riprova al prossimo giro
        print(f"ATTENZIONE: ViaggiaTreno ha rifiutato le richieste ({e}). Giro saltato, nessun dato modificato.")
        sys.exit(0)
