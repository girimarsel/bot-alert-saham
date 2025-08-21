# bot/main.py — News Movers (ID) single-run for GitHub Actions
# Tanpa library eksternal. Python 3.11+ disarankan.

import json, re, os, hashlib, urllib.request, urllib.parse, ssl
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

# ====== CONFIG via ENV ======
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
STATE_FILE = "sent_items.json"                 # dipersist lewat commit
MAX_MSG_PER_CYCLE = int(os.environ.get("MAX_MSG_PER_CYCLE", "12"))

# ====== FEEDS via Google News RSS (stabil) ======
# Format: https://news.google.com/rss/search?q=<QUERY>&hl=id&gl=ID&ceid=ID:id
GOOGLE_NEWS_SITES = [
    "emitennews.com", "kontan.co.id", "bisnis.com",
    "cnbcindonesia.com", "antaranews.com", "investor.id",
    "idxchannel.com", "kompas.com", "detik.com"
]

GOOGLE_NEWS_QUERIES = [
    # Corporate action & movers
    'akuisisi OR "mandatory tender offer" OR mto OR merger OR takeover',
    '"rights issue" OR hmetd OR pmthmetd OR "private placement" OR put',
    'ipo OR "listing perdana" OR delisting OR "stock split" OR "reverse stock split"',
    'buyback',
    'dividen OR deviden',
    # Fundamental & laporan
    '"laporan keuangan" OR "laba bersih" OR rugi OR ebitda OR eps OR pendapatan',
    # Regulasi & makro
    'pajak OR subsidi OR "larangan ekspor" OR "izin ekspor" OR ojk OR "bi rate" OR inflasi',
    # Komoditas
    'batubara OR coal OR hba OR cpo OR minyak OR oil OR nikel OR timah OR emas OR tembaga',
    # Ekspansi
    'ekspansi OR pabrik OR "joint venture" OR "investor strategis" OR "mitra strategis"',
]

def build_google_news_feeds():
    feeds = []
    site_filter = " OR ".join([f"site:{d}" for d in GOOGLE_NEWS_SITES])
    for q in GOOGLE_NEWS_QUERIES:
        full_q = f"({q}) ({site_filter})"
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
            {"q": full_q, "hl": "id", "gl": "ID", "ceid": "ID:id"}
        )
        feeds.append(url)
    return feeds

FEEDS = build_google_news_feeds()

# ====== Klasifikasi & Hint ======
KEYMAP = {
    "Akuisisi / MTO / Merger": [
        "akuisisi","mto","mandatory tender offer","merger","takeover",
        "akuisisi saham","akuisisi mayoritas","mitra strategis","investor strategis"
    ],
    "Rights Issue / Private Placement": [
        "rights issue","right issue","hmetd","put","pmthmetd","private placement","penambahan modal"
    ],
    "IPO / Delisting / Stock Action": [
        "ipo","listing perdana","delisting","reverse stock split","stock split","dual listing"
    ],
    "Buyback": ["buyback","pembelian kembali saham"],
    "Dividen": ["dividen","deviden","dividend payout"],
    "Laporan Keuangan": [
        "laba","rugi","laba bersih","pendapatan","penjualan","ebitda","eps","kinerja keuangan","laporan keuangan"
    ],
    "Regulasi / Kebijakan": [
        "pajak","subsidi","bea","royalti","larangan ekspor","izin ekspor","kuota ekspor",
        "peraturan","bi rate","suku bunga","inflasi","ojk","idx mengumumkan"
    ],
    "Komoditas / Makro": [
        "batubara","coal","hba","cpo","minyak","oil","nikel","timah","emas","tembaga"
    ],
    "Ekspansi / Proyek": [
        "ekspansi","pabrik","kapasitas","investasi","joint venture","kerja sama","kemitraan","perluasan","proyek baru"
    ],
}

IMPACT_HINT = {
    "Akuisisi / MTO / Merger":"Spekulatif naik, volatilitas tinggi.",
    "Rights Issue / Private Placement":"Tergantung harga & rasio; potensi dilusi.",
    "IPO / Delisting / Stock Action":"Perubahan struktur/float; minat spekulatif.",
    "Buyback":"Biasanya positif (dukungan harga).",
    "Dividen":"Positif bila yield besar & cumdate dekat.",
    "Laporan Keuangan":"Naik/turun tergantung surprise hasil.",
    "Regulasi / Kebijakan":"Sektor terkait bisa bergerak serempak.",
    "Komoditas / Makro":"Emitmen komoditas sensitif harga acuan.",
    "Ekspansi / Proyek":"Positif jika pendanaan sehat & prospek jelas.",
    "Lainnya":"Berpotensi menggerakkan harga.",
}

# ====== Utils HTTP/RSS ======
UA = "Mozilla/5.0 (compatible; NewsAkusisiBot/1.0)"
ssl_ctx = ssl.create_default_context()

def http_get(url, timeout=20):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "*/*", "Referer": "https://news.google.com/"}
    )
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as r:
        return r.read()

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
            le = e.find("a:link" if ns else "link"); link = ""
            if le is not None: link = le.attrib.get("href") or (le.text or "")
            summary = (e.findtext("a:summary" if ns else "summary") or
                       e.findtext("a:content" if ns else "content") or "")
            date_txt = (e.findtext("a:updated" if ns else "updated") or
                        e.findtext("a:published" if ns else "published") or "")
            try: dt = parsedate_to_datetime(date_txt) if date_txt else datetime.now(timezone.utc)
            except: dt = datetime.now(timezone.utc)
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
        try: dt = parsedate_to_datetime(date_txt) if date_txt else datetime.now(timezone.utc)
        except: dt = datetime.now(timezone.utc)
        items.append({"title": title, "link": link, "summary": desc, "published": dt})
    return items

# ====== Helpers ======
def clean_html(s): return re.sub(r"<[^>]*>", "", s or "").strip()

def classify(text):
    t = (text or "").lower()
    t = re.sub(r"\s+", " ", t)
    for cat, kws in KEYMAP.items():
        for kw in kws:
            if kw in t:
                return cat
    if any(x in t for x in ["saham","emiten","idx","bei","ihsg"]): return "Lainnya"
    return ""

def norm_url(u):
    if not u: return ""
    p = urllib.parse.urlsplit(u)
    q = urllib.parse.parse_qsl(p.query, keep_blank_values=False)
    q = [(k,v) for (k,v) in q if not k.lower().startswith("utm_")]
    return urllib.parse.urlunsplit((p.scheme,p.netloc,p.path,urllib.parse.urlencode(q),p.fragment))

def item_key(title, link):
    base = (title or "").strip().lower() + "|" + norm_url(link)
    return hashlib.sha1(base.encode("utf-8")).hexdigest()

def load_state():
    try:
        with open(STATE_FILE,"r",encoding="utf-8") as f: return set(json.load(f))
    except: return set()

def save_state(keys:set):
    with open(STATE_FILE,"w",encoding="utf-8") as f:
        json.dump(sorted(list(keys))[-5000:], f)

def as_jakarta(dt):
    try:
        from zoneinfo import ZoneInfo
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo("Asia/Jakarta"))
    except:
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def domain_of(url):
    try: return urllib.parse.urlsplit(url).netloc.replace("www.","")
    except: return "sumber"

def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Missing TELEGRAM envs; skip send."); return
    api=f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data={"chat_id":TELEGRAM_CHAT_ID,"text":text,"parse_mode":"HTML","disable_web_page_preview":True}
    req=urllib.request.Request(api, data=urllib.parse.urlencode(data).encode("utf-8"))
    try: urllib.request.urlopen(req, timeout=20, context=ssl_ctx).read()
    except Exception as e: print("Telegram error:", e)

def format_msg(cat,title,url,published_dt):
    hint = IMPACT_HINT.get(cat, IMPACT_HINT["Lainnya"])
    tgl  = as_jakarta(published_dt).strftime("%d %b %Y %H:%M WIB")
    src  = domain_of(url)
    emoji = {
        "Akuisisi / MTO / Merger":"📣","Rights Issue / Private Placement":"🧾",
        "IPO / Delisting / Stock Action":"🆕","Buyback":"🔁","Dividen":"💰",
        "Laporan Keuangan":"📊","Regulasi / Kebijakan":"⚖️",
        "Komoditas / Makro":"🌐","Ekspansi / Proyek":"🏗️","Lainnya":"📢",
    }.get(cat,"📢")
    return (
        f"{emoji} <b>ALERT SAHAM – {cat}</b>\n\n"
        f"📰 <b>Judul:</b> {title}\n"
        f"📅 <b>Tanggal:</b> {tgl}\n"
        f"🗞️ <b>Sumber:</b> {src}\n"
        f"🔗 <a href=\"{url}\">Baca sumber</a>\n\n"
        f"📈 <b>Potensi Dampak:</b> {hint}"
    )

# ====== SINGLE RUN ======
def run_once():
    sent_keys = load_state()
    found = []
    for url in FEEDS:
        try:
            xml = http_get(url)
            items = parse_rss(xml)
            for it in items:
                title = (it["title"] or "").strip()
                link  = norm_url(it["link"])
                text  = (title + " " + clean_html(it.get("summary",""))).lower()
                cat   = classify(text)
                if not cat: continue
                key = item_key(title, link)
                if key in sent_keys: continue
                found.append((it["published"], cat, title, link, key))
        except Exception as e:
            print("Feed error:", url, e)

    found.sort(key=lambda x: x[0], reverse=True)
    sent = 0
    for pub, cat, title, link, key in found[:MAX_MSG_PER_CYCLE]:
        send_telegram(format_msg(cat, title, link, pub))
        sent_keys.add(key); sent += 1
    save_state(sent_keys)
    print(f"Done. Sent {sent} message(s).")

if __name__ == "__main__":
    run_once()
