# bot/main.py — News movers single-run for GitHub Actions
import json, time, hashlib, re, urllib.request, urllib.parse, ssl, os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

# ====== CONFIG via ENV ======
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
STATE_FILE = "sent_items.json"  # dibaca & di-commit agar persist antar-run
MAX_MSG_PER_CYCLE = int(os.environ.get("MAX_MSG_PER_CYCLE", "12"))

# ====== FEEDS ======
FEEDS = [
    "https://www.cnbcindonesia.com/rss",
    "https://www.cnbcindonesia.com/market/rss",
    "https://www.bisnis.com/rss",
    "https://market.bisnis.com/rss",
    "https://investasi.kontan.co.id/rss",
    "https://keuangan.kontan.co.id/rss",
    "https://www.antaranews.com/rss/keuangan",
    "https://www.emitennews.com/feed",
]

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

UA="Mozilla/5.0 (compatible; NewsAkusisiBot/1.0)"
ssl_ctx = ssl.create_default_context()

def http_get(url, timeout=15):
    req=urllib.request.Request(url, headers={"User-Agent":UA})
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as r:
        return r.read()

def norm_url(u):
    if not u: return ""
    p=urllib.parse.urlsplit(u); q=urllib.parse.parse_qsl(p.query)
    q=[(k,v) for k,v in q if not k.lower().startswith("utm_")]
    return urllib.parse.urlunsplit((p.scheme,p.netloc,p.path,urllib.parse.urlencode(q),p.fragment))

def parse_rss(xml_bytes):
    items=[]; 
    try: root=ET.fromstring(xml_bytes)
    except: return items
    if root.tag.lower().endswith("feed"): # Atom
        ns={"a":root.tag.split('}')[0].strip('{')} if '}' in root.tag else {}
        for e in root.findall(".//a:entry" if ns else ".//entry", ns):
            title=(e.findtext("a:title" if ns else "title") or "").strip()
            ln=e.find("a:link" if ns else "link"); link=""
            if ln is not None: link=ln.attrib.get("href") or (ln.text or "")
            summary=(e.findtext("a:summary" if ns else "summary") or
                     e.findtext("a:content" if ns else "content") or "")
            date_txt=(e.findtext("a:updated" if ns else "updated") or
                      e.findtext("a:published" if ns else "published") or "")
            try: dt=parsedate_to_datetime(date_txt) if date_txt else datetime.now(timezone.utc)
            except: dt=datetime.now(timezone.utc)
            items.append({"title":title,"link":link,"summary":summary,"published":dt}); 
        return items
    chan=root.find("channel"); 
    if chan is None: return items
    for it in chan.findall("item"):
        title=(it.findtext("title") or "").strip()
        link=(it.findtext("link") or "").strip()
        desc=(it.findtext("description") or "")
        date_txt=(it.findtext("pubDate") or it.findtext("date") or "")
        try: dt=parsedate_to_datetime(date_txt) if date_txt else datetime.now(timezone.utc)
        except: dt=datetime.now(timezone.utc)
        items.append({"title":title,"link":link,"summary":desc,"published":dt})
    return items

def clean_html(s): 
    return re.sub(r"<[^>]*>", "", s or "").strip()

def classify(text):
    t=(text or "").lower(); t=re.sub(r"\s+"," ",t)
    for cat,kws in KEYMAP.items():
        for kw in kws:
            if kw in t: return cat
    if any(x in t for x in ["saham","emiten","idx","bei","ihsg"]): return "Lainnya"
    return ""

def item_key(title, link):
    base=(title or "").strip().lower()+"|"+norm_url(link)
    import hashlib; return hashlib.sha1(base.encode("utf-8")).hexdigest()

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
        tz=ZoneInfo("Asia/Jakarta")
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(tz)
    except: 
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def domain_of(url):
    try: return urllib.parse.urlsplit(url).netloc.replace("www.","")
    except: return "sumber"

def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: 
        print("Missing TELEGRAM envs; skip send.")
        return
    api=f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data={"chat_id":TELEGRAM_CHAT_ID,"text":text,"parse_mode":"HTML","disable_web_page_preview":True}
    req=urllib.request.Request(api, data=urllib.parse.urlencode(data).encode("utf-8"))
    try: urllib.request.urlopen(req, timeout=15, context=ssl_ctx).read()
    except Exception as e: print("Telegram error:", e)

def format_msg(cat,title,url,published_dt):
    hint=IMPACT_HINT.get(cat, IMPACT_HINT["Lainnya"])
    tgl=as_jakarta(published_dt).strftime("%d %b %Y %H:%M WIB")
    src=domain_of(url)
    emoji={
        "Akuisisi / MTO / Merger":"📣",
        "Rights Issue / Private Placement":"🧾",
        "IPO / Delisting / Stock Action":"🆕",
        "Buyback":"🔁",
        "Dividen":"💰",
        "Laporan Keuangan":"📊",
        "Regulasi / Kebijakan":"⚖️",
        "Komoditas / Makro":"🌐",
        "Ekspansi / Proyek":"🏗️",
        "Lainnya":"📢",
    }.get(cat,"📢")
    return (
        f"{emoji} <b>ALERT SAHAM – {cat}</b>\n\n"
        f"📰 <b>Judul:</b> {title}\n"
        f"📅 <b>Tanggal:</b> {tgl}\n"
        f"🗞️ <b>Sumber:</b> {src}\n"
        f"🔗 <a href=\"{url}\">Baca sumber</a>\n\n"
        f"📈 <b>Potensi Dampak:</b> {hint}"
    )

def run_once():
    sent_keys=load_state()
    found=[]
    for url in FEEDS:
        try:
            xml=http_get(url); items=parse_rss(xml)
            for it in items:
                title=(it["title"] or "").strip()
                link=norm_url(it["link"])
                text=(title+" "+clean_html(it.get("summary",""))).lower()
                cat=classify(text)
                if not cat: continue
                key=item_key(title,link)
                if key in sent_keys: continue
                found.append((it["published"], cat, title, link, key))
        except Exception as e:
            print("Feed error:", url, e)
    found.sort(key=lambda x:x[0], reverse=True)
    sent=0
    for pub,cat,title,link,key in found[:MAX_MSG_PER_CYCLE]:
        send_telegram(format_msg(cat,title,link,pub))
        sent_keys.add(key); sent+=1
    save_state(sent_keys)
    print(f"Done. Sent {sent} message(s).")

if __name__=="__main__":
    run_once()
