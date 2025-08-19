import os, time, re, html
import requests
from bs4 import BeautifulSoup

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

KEYWORDS = [
    "akuisisi", "tender offer", "mto", "pengambilalihan", "perubahan pengendali",
    "rights issue", "penawaran tender", "mandatory tender offer"
]

# Sumber berita (boleh tambah)
SOURCES = [
    "https://www.emitennews.com/",
    "https://www.indopremier.com/ipotnews/newsList.php?group_news=RESEARCHNEWS",
    "https://www.cnbcindonesia.com/market/indeks/idx",
    "https://investasi.kontan.co.id/news/saham"
]

# Simpan jejak berita yang sudah dikirim (biar ga spam)
STATE_FILE = "/tmp/sent.cache"

def norm(s):
    return re.sub(r"\s+", " ", s).strip().lower()

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True}
    requests.post(url, json=payload, timeout=15)

def load_sent():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(x.strip() for x in f if x.strip())
    except:
        return set()

def save_sent(sent_ids):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        for x in sent_ids:
            f.write(x + "\n")

def scrape():
    found = []
    headers = {"User-Agent": "Mozilla/5.0"}
    for src in SOURCES:
        try:
            r = requests.get(src, headers=headers, timeout=20)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            # Ambil semua link yang punya teks
            for a in soup.find_all("a"):
                title = (a.get_text() or "").strip()
                href = (a.get("href") or "").strip()
                if not title or not href: 
                    continue
                tnorm = norm(title)
                if any(k in tnorm for k in KEYWORDS):
                    # Normalisasi URL relatif
                    if href.startswith("/"):
                        # gabung domain
                        from urllib.parse import urljoin
                        href = urljoin(src, href)
                    found.append((title, href))
        except Exception as e:
            # Lewatin error sumber, lanjut yang lain
            continue
    return found

def main():
    sent = load_sent()
    new_items = []
    for title, url in scrape():
        key = norm(title) + "|" + url
        if key not in sent:
            new_items.append((title, url))
            sent.add(key)

    if new_items:
        # Gabung jadi satu pesan (maks 5 item biar ringkas)
        chunk = new_items[:5]
        msg_lines = ["[ALERT SAHAM – Akuisisi/MTO]"]
        for t, u in chunk:
            msg_lines.append(f"• {t}\n{u}")
        send_telegram("\n\n".join(msg_lines))
        save_sent(sent)

if __name__ == "__main__":
    main()
