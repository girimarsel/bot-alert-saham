import os
import re
import json
import hashlib
import requests
from html import escape as html_escape
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

# ================== Secrets (dari GitHub Actions) ==================
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# ================== Kata kunci ==================
KEYWORDS = [
    # Akuisisi / MTO / Pengendali
    "akuisisi", "tender offer", "mto", "mandatory tender offer",
    "pengambilalihan", "perubahan pengendali", "takeover",

    # Rights Issue (variasi umum)
    "rights issue", "right issue", "hmetd", "pmhmetd",
    "penambahan modal dengan hmetd", "penambahan modal melalui hmetd",
    "penawaran umum terbatas", "put i", "put ii", "put iii",
]

# ================== Sumber berita ==================
SOURCES = [
    "https://investasi.kontan.co.id/news/saham",
    "https://investor.id/market-and-corporate",
    "https://market.bisnis.com/market",
    "https://www.cnbcindonesia.com/market/indeks/idx",
    "https://www.idnfinancials.com/id/news",
    "https://www.emitennews.com/",
    "https://www.indopremier.com/ipotnews/newsList.php?group_news=RESEARCHNEWS",
]

# ================== Cache anti-duplikat ==================
CACHE_PATH = "data/sent.json"


# ------------------ Util ------------------
def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def key_for(title: str, url: str) -> str:
    raw = norm(title).lower() + "|" + url
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def load_cache():
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data.get("sent", []))
    except Exception:
        return set()


def save_cache(sent_set):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump({"sent": sorted(list(sent_set))}, f, ensure_ascii=False, indent=2)


def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def send_telegram(text: str):
    """Kirim pakai HTML agar aman dari karakter spesial."""
    api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    r = requests.post(api, json=payload, timeout=25)
    print("TG status:", r.status_code, r.text[:200])
    r.raise_for_status()


# ------------------ Scraper ------------------
def scrape():
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.google.com/",
    }
    found = []

    for src in SOURCES:
        try:
            r = requests.get(src, headers=headers, timeout=30)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            for a in soup.find_all("a"):
                title = norm(a.get_text() or "")
                href = (a.get("href") or "").strip()
                if not title or not href:
                    continue

                tnorm = title.lower()
                if any(k in tnorm for k in KEYWORDS):
                    # Normalisasi SEMUA href non-absolute:
                    # contoh "/path" atau "newsDetail.php?..." → jadi full URL
                    if not href.lower().startswith(("http://", "https://")):
                        href = urljoin(src, href)

                    found.append((title, href))

        except Exception as e:
            print("Scrape error:", src, "-", e)

    return found
# bot.py — NewsAkusisi & Market Movers (ID)
# Single-file, no external packages. Python 3.10+ recommended.

import json, time, hashlib, re, urllib.request, urllib.parse, ssl
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

# ========= CONFIG =========
TELEGRAM_BOT_TOKEN = "PASTE_BOT_TOKEN_KAMU"
TELEGRAM_CHAT_ID   = -1001234567890   # ganti dengan chat/channel ID (negatif untuk channel)
CHECK_INTERVAL_MINUTES = 60           # interval kirim (menit)
MAX_MSG_PER_CYCLE = 12                # batasi jumlah pesan per siklus
STATE_FILE = "sent_items.json"        # penyimpanan ID berita yang sudah dikirim

# Sumber RSS (boleh tambah sendiri; kalau ada yang error bot akan skip)
FEEDS = [
    # CNBC Indonesia
    "https://www.cnbcindonesia.com/rss",                 # umum
    "https://www.cnbcindonesia.com/market/rss",          # market

    # Bisnis Indonesia
    "https://www.bisnis.com/rss",                        # umum
    "https://market.bisnis.com/rss",                     # market

    # Kontan
    "https://investasi.kontan.co.id/rss",                # investasi
    "https://keuangan.kontan.co.id/rss",

    # Antara (Keuangan)
    "https://www.antaranews.com/rss/keuangan",

    # EmitenNews (WordPress RSS biasanya /feed)
    "https://www.emitennews.com/feed",
]

# Kata kunci yang biasanya menggerakkan harga
KEYMAP = {
    "Akuisisi / MTO / Merger": [
        "akuisisi", "mto", "mandatory tender offer", "merger",
        "takeover", "akuisisi saham", "akuisisi mayoritas",
        "mitra strategis", "investor strategis"
    ],
    "Rights Issue / Private Placement": [
        "rights issue", "right issue", "hmetd", "put",
        "pmthmetd", "private placement", "penambahan modal"
    ],
    "IPO / Delisting / Stock Action": [
        "ipo", "listing perdana", "delisting", "reverse stock split",
        "stock split", "dual listing"
    ],
    "Buyback": ["buyback", "pembelian kembali saham"],
    "Dividen": ["dividen", "deviden", "dividend payout"],
    "Laporan Keuangan": [
        "laba", "rugi", "laba bersih", "pendapatan", "penjualan",
        "ebitda", "eps", "kinerja keuangan", "laporan keuangan"
    ],
    "Regulasi / Kebijakan": [
        "pajak", "subsidi", "bea", "royalti", "larangan ekspor",
        "izin ekspor", "kuota ekspor", "peraturan", "bi rate",
        "suku bunga", "inflasi", "ojk", "idx mengumumkan"
    ],
    "Komoditas / Makro": [
        "batubara", "coal", "hba", "cpo", "minyak", "oil",
        "nikel", "timah", "emas", "tembaga"
    ],
    "Ekspansi / Proyek": [
        "ekspansi", "pabrik", "kapasitas", "investasi", "joint venture",
        "kerja sama", "kemitraan", "perluasan", "proyek baru"
    ],
}

# Dampak ringkas per kategori
IMPACT_HINT = {
    "Akuisisi / MTO / Merger": "Spekulatif naik, volatilitas tinggi.",
    "Rights Issue / Private Placement": "Tergantung harga & rasio; bisa dilusi.",
    "IPO / Delisting / Stock Action": "Perubahan struktur/float; minat spekulatif.",
    "Buyback": "Biasanya positif (dukungan harga).",
    "Dividen": "Positif bila yield besar & cumdate dekat.",
    "Laporan Keuangan": "Naik/turun sesuai surprise hasil.",
    "Regulasi / Kebijakan": "Sektor terkait bisa bergerak serempak.",
    "Komoditas / Makro": "Emitmen komoditas sensitif terhadap harga acuan.",
    "Ekspansi / Proyek": "Positif jika pendanaan sehat & prospek jelas.",
    "Lainnya": "Berpotensi gerakkan harga.",
}

# ========= UTIL =========
UA = "Mozilla/5.0 (compatible; NewsAkusisiBot/1.0)"
ssl_ctx = ssl.create_default_context()

def http_get(url: str, timeout=15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
        return resp.read()

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except:
        return set()

def save_state(keys: set):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(keys))[-5000:], f)  # simpan jejak terakhir saja

def norm_url(u: str) -> str:
    # buang parameter tracking umum
    if not u: return ""
    parts = urllib.parse.urlsplit(u)
    q = urllib.parse.parse_qsl(parts.query, keep_blank_values=False)
    q = [(k,v) for (k,v) in q if not k.lower().startswith("utm_")]
    new = urllib.parse.urlunsplit((
        parts.scheme, parts.netloc, parts.path,
        urllib.parse.urlencode(q), parts.fragment
    ))
    return new

def item_key(title, link):
    base = (title or "").strip().lower() + "|" + norm_url(link)
    return hashlib.sha1(base.encode("utf-8")).hexdigest()

def as_jakarta(dt: datetime) -> datetime:
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Jakarta")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(tz)
    except Exception:
        return dt.astimezone() if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def domain_of(url):
    try:
        return urllib.parse.urlsplit(url).netloc.replace("www.","")
    except:
        return "sumber"

# ========= RSS PARSER (RSS 2.0 / Atom) =========
def parse_rss(xml_bytes):
    items = []
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return items

    # Atom?
    if root.tag.lower().endswith("feed"):
        ns = {"a": root.tag.split("}")[0].strip("{")} if "}" in root.tag else {}
        for e in root.findall(".//a:entry" if ns else ".//entry", ns):
            title = (e.findtext("a:title" if ns else "title") or "").strip()
            link_el = e.find("a:link" if ns else "link")
            link = ""
            if link_el is not None:
                link = link_el.attrib.get("href") or link_el.text or ""
            summary = (e.findtext("a:summary" if ns else "summary") or
                       e.findtext("a:content"  if ns else "content") or "")
            date_txt = (e.findtext("a:updated" if ns else "updated")
                        or e.findtext("a:published" if ns else "published") or "")
            try:
                dt = parsedate_to_datetime(date_txt) if date_txt else datetime.now(timezone.utc)
            except Exception:
                dt = datetime.now(timezone.utc)
            items.append({"title": title, "link": link, "summary": summary, "published": dt})
        return items

    # RSS 2.0
    chan = root.find("channel")
    if chan is None: return items
    for it in chan.findall("item"):
        title = (it.findtext("title") or "").strip()
        link  = (it.findtext("link") or "").strip()
        desc  = (it.findtext("description") or "")
        date_txt = (it.findtext("pubDate") or it.findtext("date") or "")
        try:
            dt = parsedate_to_datetime(date_txt) if date_txt else datetime.now(timezone.utc)
        except Exception:
            dt = datetime.now(timezone.utc)
        items.append({"title": title, "link": link, "summary": desc, "published": dt})
    return items

# ========= CLASSIFIER =========
def classify(text: str) -> str:
    t = (text or "").lower()
    t = re.sub(r"\s+", " ", t)
    for cat, kws in KEYMAP.items():
        for kw in kws:
            if kw in t:
                return cat
    # fallback: kalau judul mengandung “saham”, “emiten”, dll anggap relevan
    if any(x in t for x in ["saham", "emiten", "idx", "bei", "ihsg"]):
        return "Lainnya"
    return ""

def clean_html(s: str) -> str:
    # sederhana: buang tag HTML untuk ringkasan
    return re.sub(r"<[^>]*>", "", s or "").strip()

# ========= TELEGRAM =========
def send_telegram(text: str):
    api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": str(TELEGRAM_CHAT_ID),
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        req = urllib.request.Request(api, data=urllib.parse.urlencode(data).encode("utf-8"))
        urllib.request.urlopen(req, timeout=15, context=ssl_ctx).read()
    except Exception as e:
        print("Telegram error:", e)

def format_msg(cat, title, url, published_dt):
    hint = IMPACT_HINT.get(cat, IMPACT_HINT["Lainnya"])
    dtj = as_jakarta(published_dt)
    tgl = dtj.strftime("%d %b %Y %H:%M WIB")
    src = domain_of(url)
    emoji = {
        "Akuisisi / MTO / Merger": "📣",
        "Rights Issue / Private Placement": "🧾",
        "IPO / Delisting / Stock Action": "🆕",
        "Buyback": "🔁",
        "Dividen": "💰",
        "Laporan Keuangan": "📊",
        "Regulasi / Kebijakan": "⚖️",
        "Komoditas / Makro": "🌐",
        "Ekspansi / Proyek": "🏗️",
        "Lainnya": "📢",
    }.get(cat, "📢")

    msg = (
        f"{emoji} <b>ALERT SAHAM – {cat}</b>\n\n"
        f"📰 <b>Judul:</b> {title}\n"
        f"📅 <b>Tanggal:</b> {tgl}\n"
        f"🗞️ <b>Sumber:</b> {src}\n"
        f"🔗 <a href=\"{url}\">Baca sumber</a>\n\n"
        f"📈 <b>Potensi Dampak:</b> {hint}"
    )
    return msg

# ========= MAIN LOOP =========
def run_once(sent_keys: set):
    found = []
    for url in FEEDS:
        try:
            xml = http_get(url)
            items = parse_rss(xml)
            for it in items:
                title = (it["title"] or "").strip()
                link  = norm_url(it["link"])
                summary = clean_html(it.get("summary", ""))
                text = f"{title} {summary}".lower()
                cat = classify(text)
                if not cat:
                    continue
                key = item_key(title, link)
                if key in sent_keys:
                    continue
                found.append((it["published"], cat, title, link))
        except Exception as e:
            print("Feed error:", url, e)

    # Urutkan paling baru
    found.sort(key=lambda x: x[0], reverse=True)

    sent_count = 0
    for pub, cat, title, link in found[:MAX_MSG_PER_CYCLE]:
        msg = format_msg(cat, title, link, pub)
        send_telegram(msg)
        sent_keys.add(item_key(title, link))
        sent_count += 1

    return sent_count

def main():
    print("NewsAkusisiBot started. Interval:", CHECK_INTERVAL_MINUTES, "minutes")
    sent_keys = load_state()
    while True:
        try:
            sent = run_once(sent_keys)
            if sent:
                save_state(sent_keys)
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Sent {sent} messages.")
            else:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] No new impactful news.")
        except Exception as e:
            print("Loop error:", e)
        time.sleep(CHECK_INTERVAL_MINUTES * 60)

if __name__ == "__main__":
    main()
