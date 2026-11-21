"""
TEST DELLA FONTE - Monitor treni Bergamo
Interroga ViaggiaTreno per la stazione di Bergamo e scrive in test_output/:
  - report.md   (riassunto leggibile: da incollare a Claude)
  - raw_*.json  (risposte grezze, per ispezione)
Usa solo librerie standard di Python. Non scrive niente fuori da test_output/.
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASI = [
    "https://www.viaggiatreno.it/infomobilita/resteasy/viaggiatreno/",
    "https://www.viaggiatreno.it/infomobilitamobile/resteasy/viaggiatreno/",
]
CODICE_BERGAMO_IPOTIZZATO = "S01529"
USCITA = Path("test_output")
RM = ZoneInfo("Europe/Rome")
righe = []  # il report


def scrivi(testo=""):
    print(testo)
    righe.append(testo)


def chiedi(percorso, tentativi=2):
    """Prova le due basi di indirizzo. Restituisce (stato, testo, base_usata)."""
    ultimo = (None, "nessuna risposta", None)
    for base in BASI:
        for t in range(tentativi):
            try:
                req = urllib.request.Request(
                    base + percorso,
                    headers={"User-Agent": "Mozilla/5.0 (monitor-treni-bergamo; test)"},
                )
                with urllib.request.urlopen(req, timeout=30) as r:
                    return r.status, r.read().decode("utf-8", "replace"), base
            except Exception as e:  # noqa
                ultimo = (None, f"{type(e).__name__}: {e}", base)
                time.sleep(2)
    return ultimo


def json_o_none(testo):
    try:
        return json.loads(testo)
    except Exception:
        return None


def salva_raw(nome, testo):
    USCITA.mkdir(exist_ok=True)
    (USCITA / nome).write_text(testo, encoding="utf-8")


def data_viaggiatreno(dt):
    # es. "Thu Nov 19 2026 13:14:00 GMT+0100 (CET)"
    s = dt.strftime("%a %b %d %Y %H:%M:%S GMT%z")
    return urllib.parse.quote(s + " (CET)", safe="")


def tabella(codice, tipo, ora):
    percorso = f"{tipo}/{codice}/{data_viaggiatreno(ora)}"
    stato, testo, base = chiedi(percorso)
    salva_raw(f"raw_{tipo}.json", testo or "")
    dati = json_o_none(testo) if stato == 200 else None
    return stato, dati, testo


def riassumi_tabella(tipo, stato, dati, testo):
    scrivi(f"### {tipo.capitalize()}")
    if not isinstance(dati, list):
        scrivi(f"- NON riuscito. Stato: {stato}. Risposta (primi 300 caratteri): {str(testo)[:300]}")
        return []
    scrivi(f"- Treni nella tabella: **{len(dati)}**")
    if not dati:
        return dati
    scrivi(f"- Campi disponibili: `{', '.join(sorted(dati[0].keys()))}`")
    cat = Counter(str(d.get("categoriaDescrizione") or d.get("categoria")) for d in dati)
    scrivi(f"- Categorie: {dict(cat)}")
    ritardi = [d.get("ritardo") for d in dati]
    con = [r for r in ritardi if r is not None]
    scrivi(f"- Treni con ritardo valorizzato: {len(con)} su {len(dati)}; "
           f"ritardo max {max(con) if con else 'n/d'}")
    prov = Counter(str(d.get("provvedimento")) for d in dati)
    scrivi(f"- Valori del campo `provvedimento` (segnala soppressioni?): {dict(prov)}")
    for chiave in ("nonPartito", "circolante", "inStazione"):
        if chiave in dati[0]:
            scrivi(f"- Campo `{chiave}`: {dict(Counter(str(d.get(chiave)) for d in dati))}")
    scrivi("- Esempi (primi 3):")
    for d in dati[:3]:
        scrivi(f"  - treno {d.get('numeroTreno')} | {d.get('origine')} -> {d.get('destinazione')} | "
               f"ritardo {d.get('ritardo')} | provvedimento {d.get('provvedimento')}")
    return dati


def dettaglio_treno(treno, mezzanotte_ms, indice):
    cod_orig = treno.get("codOrigine") or treno.get("idOrigine")
    num = treno.get("numeroTreno")
    scrivi(f"#### Treno {num} (origine {treno.get('origine')}, codice {cod_orig})")
    if not cod_orig or not num:
        scrivi("- codice origine o numero mancante: non posso chiedere il dettaglio")
        return
    stato, testo, _ = chiedi(f"andamentoTreno/{cod_orig}/{num}/{mezzanotte_ms}")
    salva_raw(f"raw_andamento_{indice}_{num}.json", testo or "")
    d = json_o_none(testo) if stato == 200 else None
    if not isinstance(d, dict):
        scrivi(f"- dettaglio non riuscito. Stato {stato}: {str(testo)[:200]}")
        return
    scrivi(f"- Campi principali: `{', '.join(sorted(d.keys()))}`")
    scrivi(f"- ritardo {d.get('ritardo')} | provvedimento {d.get('provvedimento')} | "
           f"tipoTreno {d.get('tipoTreno')} | stazioneUltimoRilevamento {d.get('stazioneUltimoRilevamento')}")
    fermate = d.get("fermate") or []
    scrivi(f"- Fermate elencate: {len(fermate)}")
    for f in fermate:
        if "BERGAMO" in str(f.get("stazione", "")).upper():
            scrivi(f"- Fermata Bergamo: programmato arrivo/partenza "
                   f"{f.get('arrivo_teorico')}/{f.get('partenza_teorica')}, "
                   f"reale {f.get('arrivoReale')}/{f.get('partenzaReale')}, "
                   f"ritardo arr {f.get('ritardoArrivo')} part {f.get('ritardoPartenza')}, "
                   f"stato {f.get('actualFermataType')}")


def main():
    ora = datetime.now(RM)
    scrivi("# Test fonte ViaggiaTreno - stazione di Bergamo")
    scrivi(f"Eseguito il {ora:%d/%m/%Y alle %H:%M} (ora italiana)")
    scrivi()

    # 1) codice stazione
    scrivi("## 1. Codice della stazione")
    stato, testo, base = chiedi("autocompletaStazione/BERGAMO")
    salva_raw("raw_autocompleta.json", testo or "")
    scrivi(f"- Stato risposta: {stato}")
    codice = CODICE_BERGAMO_IPOTIZZATO
    if stato != 200:
        scrivi(f"- LA FONTE NON RISPONDE da questo server. Dettaglio: {str(testo)[:300]}")
        scrivi("- Gli altri passaggi verranno tentati comunque con il codice ipotizzato.")
    else:
        scrivi(f"- Elenco grezzo (primi 300 caratteri): {testo[:300].strip()}")
        trovati = [r.split("|") for r in testo.strip().splitlines() if "|" in r]
        for nome, cod in trovati:
            if nome.strip().upper() == "BERGAMO":
                codice = cod.strip()
        scrivi(f"- Codice usato per Bergamo: **{codice}**"
               + ("" if codice == CODICE_BERGAMO_IPOTIZZATO else " (diverso da quello ipotizzato)"))
    scrivi()

    # 2) partenze e arrivi
    scrivi("## 2. Tabella di partenze e arrivi (fotografia di adesso)")
    elenchi = {}
    for tipo in ("partenze", "arrivi"):
        stato, dati, testo = tabella(codice, tipo, ora)
        elenchi[tipo] = riassumi_tabella(tipo, stato, dati, testo)
        scrivi()

    # 3) dettaglio di alcuni treni
    scrivi("## 3. Dettaglio di alcuni treni")
    mezzanotte = ora.replace(hour=0, minute=0, second=0, microsecond=0)
    mezzanotte_ms = int(mezzanotte.timestamp() * 1000)
    campione = (elenchi.get("partenze") or [])[:2] + (elenchi.get("arrivi") or [])[:2]
    if not campione:
        scrivi("- Nessun treno da dettagliare (le tabelle sono vuote o non riuscite).")
    for i, t in enumerate(campione, 1):
        dettaglio_treno(t, mezzanotte_ms, i)
        time.sleep(1)
    scrivi()
    scrivi("## Fine test")

    USCITA.mkdir(exist_ok=True)
    (USCITA / "report.md").write_text("\n".join(righe), encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # il test non deve mai "rompersi" senza lasciare un report
        USCITA.mkdir(exist_ok=True)
        (USCITA / "report.md").write_text(
            "\n".join(righe) + f"\n\nERRORE IMPREVISTO: {type(e).__name__}: {e}\n", encoding="utf-8")
        print("ERRORE:", e)
        sys.exit(1)  # il report resta salvato, ma l'esecuzione risulta fallita e GitHub lo segnala
