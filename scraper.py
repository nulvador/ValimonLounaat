#!/usr/bin/env python3
"""
Lounaslista-scraper – 6 ravintolaa.
pip install requests beautifulsoup4
python3 scraper.py
"""

import json, re
from datetime import date, datetime
import requests
from bs4 import BeautifulSoup

TODAY     = date.today()
TODAY_STR = f"{TODAY.day}.{TODAY.month}.{TODAY.year}"  # "12.5.2026"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fi-FI,fi;q=0.9",
}
TIMEOUT = 20


def fetch_html(url):
    s = requests.Session()
    s.headers.update(HEADERS)
    r = s.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def fetch_json(url):
    s = requests.Session()
    s.headers.update(HEADERS)
    r = s.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def clean(text):
    return re.sub(r"\s+", " ", text).strip()


def base(nimi, url, auki, hinta):
    return {"nimi": nimi, "url": url, "auki": auki, "hinta": hinta,
            "paiva": TODAY_STR, "ruoat": [], "virhe": None}


# ── 1. Talin Kartano – kuvalista ─────────────────────────────────────────────
def scrape_tali():
    r = base("Talin Kartano", "https://www.ravintolatali.fi/lounas", "11:00–14:00", "15,00 €")
    r["ruoat"] = ["(Ruokalista julkaistaan kuvana – katso ravintolan sivu)"]
    r["kuvalista"] = True
    return r


# ── 2. Factory Pitäjänmäki ───────────────────────────────────────────────────
# Rakenne: h3 "Tiistai 12.5.2026" → h3 (tyhjä) → p (kaikki ruoat yhdessä)
def scrape_factory():
    url = "https://ravintolafactory.com/lounasravintolat/ravintolat/helsinki-pitajanmaki/"
    r = base("Factory Pitäjänmäki", url, "10:00–14:00", "13,20 €")
    try:
        soup = fetch_html(url)
        paiva_h3 = None
        for h3 in soup.find_all("h3"):
            if TODAY_STR in h3.get_text():
                paiva_h3 = h3
                break
        if not paiva_h3:
            r["virhe"] = "Päivän listaa ei löydy"
            return r

        ruoat = []
        for sib in paiva_h3.find_next_siblings():
            if sib.name == "h3" and sib.get_text(strip=True):
                break
            if sib.name == "p":
                # Kaikki ruoat yhdessä p-elementissä, erotettu \n:llä
                for rivi in sib.get_text(separator="\n").split("\n"):
                    rivi = clean(rivi)
                    if not rivi or len(rivi) < 5:
                        continue
                    if any(x in rivi.lower() for x in ["kiinni", "suljettu", "helatorstai"]):
                        r["virhe"] = "Ravintola suljettu"
                        return r
                    # Poista allergeenimerkinnät lopusta: "(L+G+VS)"
                    rivi = re.sub(r"\s*\([A-Z+? ]+\)\s*$", "", rivi).strip()
                    if rivi and len(rivi) > 4:
                        ruoat.append(rivi)
        r["ruoat"] = ruoat
    except Exception as e:
        r["virhe"] = str(e)
    return r


# ── 3 & 4. ISS-ravintolat (Fero & Fucina) ───────────────────────────────────
# Käytetään Playwrightia koska sivu lataa datan JavaScriptillä.
# Ilman ?date-parametria sivu näyttää automaattisesti tänään oikean päivän.
def scrape_iss(nimi, url, auki, hinta):
    from playwright.sync_api import sync_playwright
    r = base(nimi, url, auki, hinta)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(url)
            page.wait_for_timeout(4000)
            teksti = page.inner_text("body")
            browser.close()

        # Parsitaan teksti riveittäin
        KATEGORIAT = {"Lounas", "Kasvislounas", "Keitto", "Grilli",
                      "Jälkiruoka", "Salaatti", "Burgeri", "A La Carte"}
        OHITA = {"L = Laktoositon", "Dieettimerkinnät", "Valitse tulostusnäkymä",
                 "Viikon ruokalista", "Päivän ruokalista", "Tulosta ruokalista",
                 "Edellinen viikko", "Seuraava viikko", "Maanantai", "Tiistai",
                 "Keskiviikko", "Torstai", "Perjantai", "Tänään"}

        if "Ruokalistaa ei löytynyt" in teksti:
            r["virhe"] = "Päivän listaa ei löydy"
            return r

        # Etsi tänään-osio: teksti alkaa "Lounas"-kategoriasta
        # ja päättyy kun tulee "L = Laktoositon" tai vastaava
        rivit = [rivi.strip() for rivi in teksti.split("\n") if rivi.strip()]
        
        current_kat = None
        ruoat = []
        keraysta = False

        for rivi in rivit:
            # Aloita kerays kun löydetään ensimmäinen kategoria
            if rivi in KATEGORIAT:
                keraysta = True
                current_kat = rivi
                continue
            # Lopeta kun tullaan footer-teksteihin
            if any(rivi.startswith(o) for o in OHITA):
                if keraysta:
                    break
                continue
            if not keraysta:
                continue
            # Suodata hinnat kuten "13,60 €"
            if re.match(r"^\d+[,\.]\d+", rivi.replace(" ", " ")):
                continue
            # Suodata suljettu
            if any(x in rivi.lower() for x in ["suljettu", "helatorstai", "kiinni"]):
                r["virhe"] = "Ravintola suljettu"
                return r
            if current_kat and len(rivi) > 4:
                ruoat.append(f"{current_kat}: {rivi}")

        r["ruoat"] = ruoat
    except Exception as e:
        r["virhe"] = str(e)
    return r


# ── 5. Lasihelmi (Compass Group) ─────────────────────────────────────────────
# Rakenne: h3 "Tiistai 12.5.2026" → section > h4 (buffet-otsikko) + ul > li (ruoat)
def scrape_lasihelmi():
    url = "https://www.compass-group.fi/ravintolat-ja-ruokalistat/foodco/kaupungit/helsinki/lasihelmi/"
    r = base("Lasihelmi", url, "10:30–13:00", "13,00 €")
    try:
        soup = fetch_html(url)
        paiva_h3 = None
        for h3 in soup.find_all("h3"):
            if TODAY_STR in h3.get_text():
                paiva_h3 = h3
                break
        if not paiva_h3:
            r["virhe"] = "Päivän listaa ei löydy"
            return r

        ruoat = []
        for sib in paiva_h3.find_next_siblings():
            if sib.name == "h3":
                break
            # Ruoat ovat section > ul > li -rakenteessa
            for li in sib.find_all("li"):
                t = clean(li.get_text())
                if not t:
                    continue
                if "suljettu" in t.lower():
                    r["virhe"] = "Ravintola suljettu"
                    return r
                # Poista allergeenimerkinnät sulkeissa lopusta
                t = re.sub(r"\s*\([^)]*\)\s*$", "", t).strip()
                if t:
                    ruoat.append(t)
        r["ruoat"] = ruoat
    except Exception as e:
        r["virhe"] = str(e)
    return r


# ── 6. Ravintola Valaja (Sodexo JSON API) ────────────────────────────────────
def scrape_valaja():
    url = "https://www.sodexo.fi/ravintolat/ravintola-valaja"
    r = base("Ravintola Valaja", url, "10:30–13:00", "14,00 €")
    try:
        api = f"https://www.sodexo.fi/ruokalistat/output/daily_json/190/{TODAY.strftime('%Y-%m-%d')}"
        data = fetch_json(api)
        courses = data.get("courses", {})
        if not courses:
            r["virhe"] = "Päivän listaa ei löydy"
            return r
        ruoat = []
        for c in courses.values():
            nimi = c.get("title_fi", "")
            kat  = c.get("category", "")
            if not nimi:
                continue
            ruoat.append(f"{kat}: {nimi}" if kat else nimi)
        r["ruoat"] = ruoat
    except Exception as e:
        r["virhe"] = str(e)
    return r


# ── Pääohjelma ────────────────────────────────────────────────────────────────
def main():
    print(f"Haetaan lounaslistat {TODAY_STR}...\n")
    ravintolat = [
        scrape_tali(),
        scrape_factory(),
        scrape_iss("Ravintola Fero",   "https://ravintolapalvelut.iss.fi/fero/",             "10:30–13:30", "13,40 €"),
        scrape_iss("Ravintola Fucina", "https://ravintolapalvelut.iss.fi/ravintola-fucina/", "10:30–13:30", "13,90 €"),
        scrape_lasihelmi(),
        scrape_valaja(),
    ]
    with open("lounaat.json", "w", encoding="utf-8") as f:
        json.dump({"paivitetty": datetime.now().isoformat(), "ravintolat": ravintolat},
                  f, ensure_ascii=False, indent=2)
    print("Valmis! Tallennettu: lounaat.json\n")
    for rv in ravintolat:
        if rv.get("kuvalista"):
            s = "→ kuvalista, linkki sivulle"
        elif rv["virhe"]:
            s = f"✗ {rv['virhe']}"
        else:
            s = f"✓ {len(rv['ruoat'])} ruokaa"
        print(f"  {rv['nimi']:28} {s}")

if __name__ == "__main__":
    main()
