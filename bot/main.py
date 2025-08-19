import os, re, html, requests
from bs4 import BeautifulSoup

# ========= Konfigurasi dari Secrets (JANGAN hardcode) =========
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# ========= Kata kunci & Sumber =========
KEYWORDS = [
    "akuisisi", "tender offer", "mto", "pengambilalihan",
    "perubahan pengendali", "rights issue", "penawaran tender",
    "mandatory tender offer"
]

SOURCES = [
    "https://www.emitennews.com/",
    "https://www.indopremier.com/ipotnews/newsList.php?group_news=RESEARCHNEWS",
    "https://www.cnbcindonesia.com/market/indeks/idx",
    "https://investasi.kontan.co.id/news/saham"
]

STATE_FILE = "/tmp/sent.cache"  # jejak berita yg sudah dikirim

# ========= Util =========
def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()

def send_telegram(message: str) -> None:
    """Kirim pesan + log respons ke Actions agar mudah debug."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "disable_web_page_preview": True}
    try:
        r = requests.post(url, json=payload, timeout=15)
        print("TG status:", r.status_code, r.text[:200])  # LOG penting
        r.raise_for_status()
    except Exception as e:
        print("Gagal kirim Telegram:", e)

def load_sent():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(x.strip() for x in f if x.strip())
    except Exception:
        return set()

def save_sent(sent_ids):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            for x in sent_ids:
                f.write(x + "\n")
    except Exception as e:
        print("Gagal simpan cache:", e)

def scrape():
    """Ambil judul+link dari sumber, filter by keyword."""
    found = []
    headers = {"User-Agent": "Mozilla/5.0"}
    for src in SOURCES:
        try:
            r = requests.get(src, headers=headers, timeout=20)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a"):
                title = (a.get_text() or "").strip()
                href = (a.get("href") or "").strip()
                if not title or not href:
                    continue
                tnorm = norm(title)
                if any(k in tnorm for k in KEYWORDS):
                    # Normalisasi URL relatif
                    if href.startswith("/"):
                        from urllib.parse import urljoin
                        href = urljoin(src, href)
                    found.append((title, href))
        except Exception as e:
            print("Scrape error pada:", src, "-", e)
            continue
    return found

# ========= Main =========
def main():
    # 1) Ping tiap run
    send_telegram("✅ Bot Alert Saham aktif. Cek berita akuisisi/MTO sekarang...")

    # 2) Scrape & filter
    sent = load_sent()
    new_items = []
    for title, url in scrape():
        key = norm(title) + "|" + url
        if key not in sent:
            new_items.append((title, url))
            sent.add(key)

    # 3) Kirim hasil baru / info belum ada
    if new_items:
        chunk = new_items[:5]  # batasi 5 item sekali kirim
        msg_lines = ["📢 [ALERT SAHAM – Akuisisi/MTO]"]
        for t, u in chunk:
            msg_lines.append(f"• {t}\n{u}")
        send_telegram("\n\n".join(msg_lines))
        save_sent(sent)
    else:
        send_telegram("ℹ️ Belum ada berita baru yang cocok keyword.")

# ========= Trigger saat dipanggil =========
if __name__ == "__main__":
    main()
