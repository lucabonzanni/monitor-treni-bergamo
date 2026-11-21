#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AGGREGAZIONE - Monitor treni Bergamo

Legge tutti i CSV giornalieri (docs/dati/corse/*.csv) e produce:
  - docs/dati/riepilogo.json      i numeri che la pagina trasforma in grafici
  - docs/dati/corse_complete.csv  tutte le corse in un unico file, da scaricare

Le "direttrici" si modificano in config/direttrici.csv (si ricalcola tutto lo storico).
Solo libreria standard.
"""
import csv
import io
import json
import os
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

RM = ZoneInfo("Europe/Rome")
CARTELLA = Path(os.environ.get("CARTELLA_CORSE", "docs/dati/corse"))
USCITA = Path(os.environ.get("USCITA_DATI", "docs/dati"))
CONFIG = Path(os.environ.get("CONFIG_DIRETTRICI", "config/direttrici.csv"))
SOGLIA = 5    # minuti: "ritardo" (da almeno 5')
GRAVE = 15    # minuti: "ritardo grave" (da almeno 15')
ALTRE = "Altre direttrici"
DATA_INIZIO = os.environ.get("DATA_INIZIO", "2026-11-20")  # prima giornata piena di rilevazione
SOGLIA_ACCUMULO = 2  # minuti: un ritardo di 1' può essere un arrotondamento, quindi non entra nei ritardi accumulati
GIORNI_IT = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]


def leggi_regole():
    regole = []
    if CONFIG.exists():
        with CONFIG.open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                kw = (r.get("parola_chiave") or "").strip().upper()
                nome = (r.get("direttrice") or "").strip()
                # terza colonna facoltativa: percorso richiesto (es. Carnate); vuota = qualsiasi
                vie = [v.strip().upper() for v in (r.get("via") or "").split("|") if v.strip()]
                if kw and nome:
                    regole.append((kw, nome, vie))
    return regole


def direttrice(controparte, regole, via=""):
    su = (controparte or "").upper()
    vu = (via or "").strip().upper()
    for kw, nome, vie in regole:
        if kw in su and (not vie or vu in vie):
            return nome
    return ALTRE


def leggi_corse():
    righe, intestazione = [], None
    for f in sorted(CARTELLA.glob("*.csv")):
        with f.open(encoding="utf-8", newline="") as fh:
            rd = csv.DictReader(fh)
            intestazione = list(intestazione or []) + [c for c in (rd.fieldnames or []) if c not in (intestazione or [])]
            for r in rd:
                if (r.get("data") or "") < DATA_INIZIO:
                    continue
                try:
                    r["_ts"] = int(r["ts_programmato"])
                except (KeyError, ValueError, TypeError):
                    continue
                r["_rit"] = int(r["ritardo_min"]) if (r.get("ritardo_min") or "").lstrip("-").isdigit() else None
                dt = datetime.fromtimestamp(r["_ts"] / 1000, RM)
                r["_ora"] = dt.hour
                r["_dow"] = dt.weekday()
                righe.append(r)
    return righe, intestazione


def pct(a, b):
    return round(100.0 * a / b, 1) if b else None


def statistiche(rows):
    """Indicatori su un gruppo di corse."""
    eff = [r for r in rows if r["stato"] == "effettuato" and r["_rit"] is not None]
    sopp = [r for r in rows if r["stato"] == "soppresso"]
    nd = [r for r in rows if r["stato"] == "nd"]
    parz = [r for r in rows if r.get("parziale") == "1" and r["stato"] in ("effettuato", "parziale")]
    parz_fuori = [r for r in rows if r["stato"] == "parziale"]  # Bergamo non servita: nessun orario da misurare
    diss = [r for r in rows if r["stato"] == "soppresso" or r.get("parziale") == "1"
            or (r["stato"] == "effettuato" and r["_rit"] is not None and r["_rit"] >= GRAVE)]  # ogni corsa conta una volta sola
    rit = [max(0, r["_rit"]) for r in eff]  # un treno in anticipo conta come puntuale
    prog = len(eff) + len(sopp) + len(parz_fuori)
    n5 = sum(1 for x in rit if x >= SOGLIA)
    n15 = sum(1 for x in rit if x >= GRAVE)
    return {
        "programmate": prog,
        "effettuate": len(eff),
        "soppresse": len(sopp),
        "sopp_parziali": len(parz),
        "parziali_senza_passaggio": len(parz_fuori),
        "disservizi": len(diss),
        "pct_disservizi": pct(len(diss), prog),
        "nd": len(nd),
        "stimate": sum(1 for r in eff if r.get("stimato") == "1"),
        "ritardo_medio": round(statistics.mean(rit), 1) if rit else None,
        "ritardo_mediano": round(statistics.median(rit), 1) if rit else None,
        "ritardo_max": max(rit) if rit else None,
        "n_ritardi_5": n5,
        "pct_ritardi_5": pct(n5, len(eff)),
        "n_ritardi_15": n15,
        "pct_ritardi_15": pct(n15, len(eff)),
        "pct_soppresse": pct(len(sopp), prog),
        "pct_sopp_parziali": pct(len(parz), prog),
    }


def accumulo(rows_giorno):
    """Minuti di ritardo accumulati dai treni in una giornata.
    - solo_ritardi: somma dei ritardi delle corse effettuate (da 2' in su; gli anticipi valgono zero e non compensano nulla).
    - attesa: per ogni corsa soppressa (totale, o parziale senza passaggio a Bergamo) i minuti tra il suo orario
      programmato e il passaggio della corsa successiva della stessa tratta (stessa direzione e stesso capolinea).
      L'ultima corsa della giornata di ogni tratta non ha successiva e non viene conteggiata."""
    def ritardo_valido(r):
        return r["_rit"] if (r["stato"] == "effettuato" and r["_rit"] is not None and r["_rit"] >= SOGLIA_ACCUMULO) else 0

    solo = sum(ritardo_valido(r) for r in rows_giorno)
    corse_ritardo = sum(1 for r in rows_giorno if ritardo_valido(r) > 0)
    attesa, n_attese, esclusi, provvisorio = 0, 0, 0, False
    tratte = raggruppa(rows_giorno, lambda r: (r["direzione"], (r.get("controparte") or "").upper()))
    for gr in tratte.values():
        gr = sorted(gr, key=lambda r: r["_ts"])
        for i, r in enumerate(gr):
            if r["stato"] not in ("soppresso", "parziale"):
                continue
            succ = next((x for x in gr[i + 1:] if x["stato"] in ("effettuato", "in_corso", "programmato")), None)
            if succ is None:
                esclusi += 1
                continue
            ts_succ = succ["_ts"] + ritardo_valido(succ) * 60000
            attesa += max(0, round((ts_succ - r["_ts"]) / 60000))
            n_attese += 1
            if succ["stato"] != "effettuato":
                provvisorio = True  # il successivo non è ancora passato: l'attesa è calcolata sull'orario programmato
    return {"solo_ritardi": solo, "corse_ritardo": corse_ritardo, "attesa_soppressioni": attesa,
            "soppresse_con_attesa": n_attese, "soppresse_senza_successivo": esclusi,
            "totale": solo + attesa, "provvisorio": provvisorio}


def raggruppa(rows, chiave):
    gruppi = defaultdict(list)
    for r in rows:
        gruppi[chiave(r)].append(r)
    return gruppi


def blocco(rows, oggi, regole, giorni_attesi=None):
    """Tutti i numeri per un periodo (rows = corse del periodo).
    giorni_attesi: elenco delle date che dovrebbero avere dati; quelle senza righe compaiono come "senza_dati"."""
    rows = [r for r in rows if r["stato"] in ("effettuato", "soppresso", "parziale", "nd")]
    per_giorno = raggruppa(rows, lambda r: r["data"])
    giorni = []
    for g in sorted(per_giorno):
        s = statistiche(per_giorno[g])
        s["data"] = g
        s["parziale"] = (g == oggi)
        giorni.append(s)
    for g in (giorni_attesi or []):
        if g not in per_giorno:
            s = statistiche([])
            s["data"] = g
            s["parziale"] = (g == oggi)
            s["senza_dati"] = True
            giorni.append(s)
    giorni.sort(key=lambda s: s["data"])

    chiuse = [r for r in rows if r["data"] != oggi]  # il giorno in corso è parziale
    dow = []
    for i in range(7):
        gr = [r for r in chiuse if r["_dow"] == i]
        s = statistiche(gr)
        s["nome"] = GIORNI_IT[i]
        s["giorni_osservati"] = len({r["data"] for r in gr})
        dow.append(s)

    dirett = []
    for nome, gr in sorted(raggruppa(rows, lambda r: direttrice(r["controparte"], regole, r.get("via"))).items()):
        for dirz, sel in (("tutte", gr),
                          ("partenza", [r for r in gr if r["direzione"] == "partenza"]),
                          ("arrivo", [r for r in gr if r["direzione"] == "arrivo"])):
            s = statistiche(sel)
            s["direttrice"] = nome
            s["direzione"] = dirz
            dirett.append(s)

    fasce = []
    for h in range(24):
        gr = [r for r in rows if r["_ora"] == h]
        if gr:
            s = statistiche(gr)
            s["ora"] = h
            fasce.append(s)

    heat = []
    for i in range(7):
        for h in range(24):
            gr = [r for r in chiuse if r["_dow"] == i and r["_ora"] == h]
            if gr:
                s = statistiche(gr)
                heat.append({"dow": i, "ora": h, "effettuate": s["effettuate"],
                             "pct_ritardi_5": s["pct_ritardi_5"], "ritardo_medio": s["ritardo_medio"]})

    treni = []
    for (num, dirz), gr in raggruppa(rows, lambda r: (r["numero_treno"], r["direzione"])).items():
        s = statistiche(gr)
        s["numero"] = num
        s["direzione"] = dirz
        s["controparte"] = Counter(r["controparte"] for r in gr if r["controparte"]).most_common(1)[0][0] \
            if any(r["controparte"] for r in gr) else ""
        vie = Counter(r.get("via") for r in gr if r.get("via") and r["via"] != "-").most_common(1)
        s["direttrice"] = direttrice(s["controparte"], regole, vie[0][0] if vie else "")
        s["orario"] = Counter(r["orario_programmato"] for r in gr).most_common(1)[0][0]
        treni.append(s)
    treni.sort(key=lambda s: (-s["programmate"], s["numero"]))

    return {
        "totale": statistiche(rows),
        "giorni": giorni,
        "giorno_settimana": dow,
        "direttrici": dirett,
        "fasce_orarie": fasce,
        "heatmap": heat,
        "treni": treni,
    }


def scrivi_se_cambiato(percorso, testo):
    percorso = Path(percorso)
    if percorso.exists() and percorso.read_text(encoding="utf-8") == testo:
        return False
    percorso.parent.mkdir(parents=True, exist_ok=True)
    percorso.write_text(testo, encoding="utf-8")
    return True


def main():
    righe, intestazione = leggi_corse()
    regole = leggi_regole()
    oggi = os.environ.get("OGGI") or datetime.now(RM).date().isoformat()
    oggi_d = datetime.fromisoformat(oggi).date()

    righe.sort(key=lambda r: (r["_ts"], r["id"]))
    date_con_dati = sorted({r["data"] for r in righe})

    def da(n_giorni):
        limite = (oggi_d - timedelta(days=n_giorni - 1)).isoformat()
        return [r for r in righe if r["data"] >= limite]

    def attesi(n_giorni=None):
        """Date comprese tra la prima giornata con dati e ieri (o l'ultima con dati): quelle senza righe sono buchi."""
        if not date_con_dati:
            return []
        inizio = datetime.fromisoformat(date_con_dati[0]).date()
        if n_giorni:
            inizio = max(inizio, oggi_d - timedelta(days=n_giorni - 1))
        fine = max(datetime.fromisoformat(date_con_dati[-1]).date(), oggi_d - timedelta(days=1))
        fine = min(fine, oggi_d)
        out, g = [], inizio
        while g <= fine:
            out.append(g.isoformat())
            g += timedelta(days=1)
        return out

    periodi = {}
    for chiave, rr, n in (("tutto", righe, None), ("30", da(30), 30), ("7", da(7), 7)):
        periodi[chiave] = blocco(rr, oggi, regole, attesi(n))
        # giorni lavorativi (lunedì-venerdì) e fine settimana (sabato-domenica)
        periodi[chiave + "_lav"] = blocco([r for r in rr if r["_dow"] < 5], oggi, regole)
        periodi[chiave + "_we"] = blocco([r for r in rr if r["_dow"] >= 5], oggi, regole)

    def elenco(giorno):
        out = []
        for r in righe:
            if r["data"] == giorno:
                out.append({
                    "orario": r["orario_programmato"], "reale": r["orario_reale"],
                    "direzione": r["direzione"], "numero": r["numero_treno"],
                    "controparte": r["controparte"], "direttrice": direttrice(r["controparte"], regole, r.get("via")),
                    "stato": r["stato"], "ritardo": r["_rit"], "motivo": r["motivo_ritardo"],
                    "parziale": r.get("parziale") == "1",
                    "stimato": r.get("stimato") == "1",
                    "saltate": [x for x in (r.get("fermate_soppresse") or "").split("|") if x and not x.startswith("[")],
                })
        return out

    per_data = raggruppa(righe, lambda r: r["data"])
    ritardi_accumulati = {g: accumulo(rr) for g, rr in per_data.items()}

    ieri = (oggi_d - timedelta(days=1)).isoformat()
    lista_oggi = elenco(oggi)
    lista_ieri = elenco(ieri) if ieri >= DATA_INIZIO else []

    non_classificate = Counter(r["controparte"] for r in righe
                               if r["controparte"] and direttrice(r["controparte"], regole, r.get("via")) == ALTRE)
    ultimi = [datetime.fromisoformat(r["aggiornato_il"]) for r in righe if r.get("aggiornato_il")]

    payload = {
        "aggiornato_il": max(ultimi).isoformat() if ultimi else None,
        "oggi": oggi,
        "prima_data": date_con_dati[0] if date_con_dati else None,
        "ultima_data": date_con_dati[-1] if date_con_dati else None,
        "giorni_con_dati": len(date_con_dati),
        "soglie": {"ritardo": SOGLIA, "ritardo_grave": GRAVE},
        "periodi": periodi,
        "ritardi_accumulati": ritardi_accumulati,
        "oggi_elenco": lista_oggi,
        "ieri": ieri,
        "ieri_elenco": lista_ieri,
        "controparti_non_classificate": non_classificate.most_common(30),
        "file_giornalieri": date_con_dati[-30:],
        "totale_righe": len(righe),
    }
    testo = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    c1 = scrivi_se_cambiato(USCITA / "riepilogo.json", testo)

    buf = io.StringIO()
    if intestazione:
        w = csv.DictWriter(buf, fieldnames=intestazione, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        for r in righe:
            w.writerow({c: r.get(c) or "" for c in intestazione})
    c2 = scrivi_se_cambiato(USCITA / "corse_complete.csv", buf.getvalue())
    print(f"Aggregazione: {len(righe)} corse in {len(date_con_dati)} giorni | "
          f"riepilogo.json {'aggiornato' if c1 else 'invariato'} | corse_complete.csv {'aggiornato' if c2 else 'invariato'}")
    if non_classificate:
        print("Controparti non assegnate a una direttrice:", ", ".join(f"{n} ({c})" for n, c in non_classificate.most_common(10)))


if __name__ == "__main__":
    main()
