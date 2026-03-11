import eventlet
eventlet.monkey_patch()

import os
import sys
import time
import json
import requests
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

# INIT
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get("SECRET_KEY", "herosms_stateless_v11")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# CONFIG
API_BASE = "https://hero-sms.com/stubs/handler_api.php"
# Ambil password dari ENV, default 'admin123'
MASTER_PASS = str(os.environ.get("ACCESS_PASSWORD", "admin123")).strip()

print(f"\n[BOOT] HERO-SMS WEB V11 (STATELESS) ONLINE")
print(f"[BOOT] PASSWORD AKTIF: {MASTER_PASS}")
sys.stdout.flush()

COUNTRIES = {
    "vietnam": {"name": "Vietnam", "flag": "🇻🇳", "id": "10", "code": "84", "max": "0.25"},
    "philipina": {"name": "Philipina", "flag": "🇵🇭", "id": "3", "code": "63", "max": "0.25"},
    "colombia": {"name": "Colombia", "flag": "🇨🇴", "id": "33", "code": "57", "max": None},
    "mexico": {"name": "Mexico", "flag": "🇲🇽", "id": "54", "code": "52", "max": None},
    "brazil": {"name": "Brazil", "flag": "🇧🇷", "id": "73", "code": "55", "max": "1.50"},
}

# Menyimpan status autobuy (hanya ini yang butuh state sementara)
autobuy_status = {}

def api_req(key, action, **kwargs):
    if not key: return "ERR_NO_KEY"
    p = {'api_key': key, 'action': action}
    p.update(kwargs)
    try:
        r = requests.get(API_BASE, params=p, timeout=15)
        return r.text.strip()
    except: return "ERR_HTTP"

@app.route('/')
def home(): return render_template('index.html', countries=COUNTRIES)

@socketio.on('check_auth')
def on_check(data):
    pw = str(data.get('password', '')).strip()
    if pw == MASTER_PASS:
        emit('auth_result', {'success': True})
    else:
        emit('auth_result', {'success': False})

@socketio.on('get_balance')
def on_bal(data):
    pw, key = str(data.get('password', '')).strip(), data.get('api_key')
    if pw != MASTER_PASS: return
    res = api_req(key, 'getBalance')
    if 'ACCESS_BALANCE' in res:
        emit('balance_update', {'balance': res.split(':')[-1]})
    else:
        emit('error_msg', {'message': 'API Key Invalid / Error'})

def otp_worker(sid, key, aid, st):
    # Worker mandiri untuk setiap nomor
    while True:
        if (time.time() - st) > 1200: # 20 Menit
            api_req(key, 'setStatus', status='8', id=aid)
            socketio.emit('order_update', {'id': aid, 'status': 'timeout'}, room=sid)
            break
        r = api_req(key, 'getStatus', id=aid)
        if r.startswith('STATUS_OK'):
            code = r.split(':')[-1]
            api_req(key, 'setStatus', status='6', id=aid)
            socketio.emit('order_update', {'id': aid, 'status': 'got_otp', 'code': code}, room=sid)
            break
        elif r == 'STATUS_CANCEL':
            socketio.emit('order_update', {'id': aid, 'status': 'cancelled'}, room=sid)
            break
        socketio.sleep(4)

@socketio.on('buy_number')
def on_buy(data):
    pw, key = str(data.get('password', '')).strip(), data.get('api_key')
    if pw != MASTER_PASS: return
    ck, count = data.get('country'), int(data.get('count', 1))
    sid = request.sid
    
    def run():
        cnt = COUNTRIES[ck]
        done = 0
        for _ in range(count * 50):
            if done >= count: break
            res = api_req(key, 'getNumber', service='wa', country=cnt['id'], maxPrice=cnt['max'] if cnt['max'] else None)
            if 'ACCESS_NUMBER' in res:
                parts = res.split(':')
                if len(parts) >= 3:
                    aid, num = parts[1], parts[2]
                    order = {'id': aid, 'number': num, 'status': 'waiting', 'order_time': time.time(), 'price': cnt['max'] or "0.00", 'country': ck, 'index': done+1, 'country_code': cnt['code']}
                    socketio.emit('new_number', order, room=sid)
                    socketio.start_background_task(otp_worker, sid, key, aid, order['order_time'])
                    done += 1
                socketio.sleep(0.3)
            elif 'NO_BALANCE' in res: 
                socketio.emit('error_msg', {'message': 'Saldo Habis!'}, room=sid)
                break
            socketio.sleep(0.001) # Brutal
        socketio.emit('buy_complete', {'count': done}, room=sid)
    socketio.start_background_task(run)

@socketio.on('start_autobuy')
def on_auto(data):
    pw, key = str(data.get('password', '')).strip(), data.get('api_key')
    if pw != MASTER_PASS: return
    ck = data.get('country')
    sid = request.sid
    autobuy_status[sid] = True
    cnt = COUNTRIES[ck]
    
    def run():
        att, found, last_ui, st = 0, 0, 0, time.time()
        socketio.emit('autobuy_started', {'country_name': cnt['name']}, room=sid)
        while sid in autobuy_status and autobuy_status[sid]:
            att += 1
            if (time.time() - last_ui) > 0.8:
                el = int(time.time() - st)
                socketio.emit('autobuy_stats', {'attempts': att, 'found': found, 'elapsed': el, 'speed': round(att/max(el,1), 1)}, room=sid)
                last_ui = time.time()
            res = api_req(key, 'getNumber', service='wa', country=cnt['id'], maxPrice=cnt['max'] if cnt['max'] else None)
            if 'ACCESS_NUMBER' in res:
                parts = res.split(':')
                if len(parts) >= 3:
                    aid, num = parts[1], parts[2]
                    found += 1
                    order = {'id': aid, 'number': num, 'status': 'waiting', 'order_time': time.time(), 'price': cnt['max'] or "0.00", 'country': ck, 'index': found, 'country_code': cnt['code']}
                    socketio.emit('new_number', order, room=sid)
                    socketio.start_background_task(otp_worker, sid, key, aid, order['order_time'])
                    socketio.sleep(0.4)
            elif 'NO_BALANCE' in res: break
            else: socketio.sleep(0.001)
        socketio.emit('autobuy_stopped', {'total': found}, room=sid)
    socketio.start_background_task(run)

@socketio.on('stop_autobuy')
def on_stop():
    autobuy_status[request.sid] = False

@socketio.on('cancel_order')
def on_cancel(data):
    pw, key, aid = str(data.get('password', '')).strip(), data.get('api_key'), data.get('id')
    if pw != MASTER_PASS: return
    api_req(key, 'setStatus', status='8', id=aid)
    emit('order_update', {'id': aid, 'status': 'cancelled'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)
