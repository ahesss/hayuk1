import eventlet
eventlet.monkey_patch()

import os
import sys
import time
import json
import requests
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room

# INIT
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get("SECRET_KEY", "herosms_ultimate_v12")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# CONFIG
API_BASE = "https://hero-sms.com/stubs/handler_api.php"
MASTER_PASS = str(os.environ.get("ACCESS_PASSWORD", "admin123")).strip()

print(f"\n[BOOT] HERO-SMS WEB V12 (ULTRA STABLE) ONLINE")
sys.stdout.flush()

COUNTRIES = {
    "vietnam": {"name": "Vietnam", "flag": "🇻🇳", "id": "10", "code": "84", "max": "0.25"},
    "philipina": {"name": "Philipina", "flag": "🇵🇭", "id": "3", "code": "63", "max": "0.25"},
    "colombia": {"name": "Colombia", "flag": "🇨🇴", "id": "33", "code": "57", "max": None},
    "mexico": {"name": "Mexico", "flag": "🇲🇽", "id": "54", "code": "52", "max": None},
    "brazil": {"name": "Brazil", "flag": "🇧🇷", "id": "73", "code": "55", "max": "1.50"},
}

# State Management
autobuy_active = {} # key: bool

def api_req(key, action, **kwargs):
    if not key: return "ERR_NO_KEY"
    p = {'api_key': key, 'action': action}
    p.update(kwargs)
    try:
        r = requests.get(API_BASE, params=p, timeout=5)
        return r.text.strip()
    except: return "ERR_HTTP"

@app.route('/')
def home(): return render_template('index.html', countries=COUNTRIES)

@socketio.on('init_session')
def on_init(data):
    key = data.get('api_key')
    if key:
        join_room(key) # Ikat semua urusan ke API KEY ini
        print(f"[SESSION] User linked to API Key: {key[:8]}...")
        sys.stdout.flush()
        if autobuy_active.get(key):
            emit('autobuy_started', {'country_name': 'Berjalan'})

@socketio.on('check_auth')
def on_check(data):
    pw = str(data.get('password', '')).strip()
    if pw == MASTER_PASS: emit('auth_result', {'success': True})
    else: emit('auth_result', {'success': False})

@socketio.on('get_balance')
def on_bal(data):
    key = data.get('api_key')
    res = api_req(key, 'getBalance')
    if 'ACCESS_BALANCE' in res:
        emit('balance_update', {'balance': res.split(':')[-1]})
    else:
        emit('error_msg', {'message': 'API Key bermasalah!'})

def otp_worker(room_key, api_key, aid, st):
    # Loop OTP Kirim ke SEMUA browser yang pakai API KEY yang sama
    while True:
        if (time.time() - st) > 1200:
            api_req(api_key, 'setStatus', status='8', id=aid)
            socketio.emit('order_update', {'id': aid, 'status': 'timeout'}, room=room_key)
            break
        r = api_req(api_key, 'getStatus', id=aid)
        if r.startswith('STATUS_OK'):
            code = r.split(':')[-1]
            api_req(api_key, 'setStatus', status='6', id=aid)
            socketio.emit('order_update', {'id': aid, 'status': 'got_otp', 'code': code}, room=room_key)
            break
        elif r == 'STATUS_CANCEL':
            socketio.emit('order_update', {'id': aid, 'status': 'cancelled'}, room=room_key)
            break
        socketio.sleep(4)

@socketio.on('buy_number')
def on_buy(data):
    key, ck, count = data.get('api_key'), data.get('country'), int(data.get('count', 1))
    def run():
        cnt = COUNTRIES[ck]
        socketio.emit('buy_status', {'message': f"Nembak {count} nomor..."}, room=key)
        done = 0
        for _ in range(count * 50):
            if done >= count: break
            res = api_req(key, 'getNumber', service='wa', country=cnt['id'], maxPrice=cnt['max'] if cnt['max'] else None)
            if 'ACCESS_NUMBER' in res:
                parts = res.split(':')
                if len(parts) >= 3:
                    aid, num = parts[1], parts[2]
                    order = {'id': aid, 'number': num, 'status': 'waiting', 'order_time': time.time(), 'price': cnt['max'] or "0.00", 'country': ck, 'index': done+1, 'country_code': cnt['code']}
                    socketio.emit('new_number', order, room=key)
                    socketio.start_background_task(otp_worker, key, key, aid, order['order_time'])
                    done += 1
                socketio.sleep(0.3)
            elif 'NO_BALANCE' in res: break
            socketio.sleep(0.001)
        socketio.emit('buy_complete', {'count': done}, room=key)
    socketio.start_background_task(run)

@socketio.on('start_autobuy')
def on_auto(data):
    key, ck = data.get('api_key'), data.get('country')
    if autobuy_active.get(key): return # Mencegah duplicate thread berjalan!
    autobuy_active[key] = True
    cnt = COUNTRIES[ck]
    NUM_WORKERS = 3  # Jumlah penembak paralel

    def single_worker(worker_id, shared):
        """Satu worker brutal yang terus menembak API tanpa henti"""
        while autobuy_active.get(key):
            try:
                res = api_req(key, 'getNumber', service='wa', country=cnt['id'], maxPrice=cnt['max'] if cnt['max'] else None)
                shared['att'] += 1
                if 'ACCESS_NUMBER' in res:
                    parts = res.split(':')
                    if len(parts) >= 3:
                        aid, num = parts[1], parts[2]
                        shared['found'] += 1
                        order = {'id': aid, 'number': num, 'status': 'waiting', 'order_time': time.time(), 'price': cnt['max'] or "0.00", 'country': ck, 'index': shared['found'], 'country_code': cnt['code']}
                        socketio.emit('new_number', order, room=key)
                        socketio.start_background_task(otp_worker, key, key, aid, order['order_time'])
                    socketio.sleep(0.01)
                elif 'NO_BALANCE' in res:
                    autobuy_active[key] = False
                    socketio.emit('error_msg', {'message': '💸 SALDO HABIS!'}, room=key)
                    break
                elif 'NO_NUMBERS' in res:
                    socketio.sleep(0.01)  # Stok kosong, langsung retry
                else:
                    socketio.sleep(0.01)
            except:
                socketio.sleep(0.05)

    def run():
        shared = {'att': 0, 'found': 0}
        st = time.time()
        socketio.emit('autobuy_started', {'country_name': cnt['name']}, room=key)

        # Jalankan worker paralel
        workers = []
        for wid in range(NUM_WORKERS):
            w = socketio.start_background_task(single_worker, wid, shared)
            workers.append(w)

        # UI updater loop
        while autobuy_active.get(key):
            el = int(time.time() - st)
            socketio.emit('autobuy_stats', {
                'attempts': shared['att'],
                'found': shared['found'],
                'elapsed': el,
                'speed': round(shared['att']/max(el,1), 1)
            }, room=key)
            socketio.sleep(0.5)

        autobuy_active[key] = False
        el = int(time.time() - st)
        socketio.emit('autobuy_stats', {'attempts': shared['att'], 'found': shared['found'], 'elapsed': el, 'speed': round(shared['att']/max(el,1), 1)}, room=key)
        socketio.emit('autobuy_stopped', {'total': shared['found']}, room=key)
    socketio.start_background_task(run)

@socketio.on('stop_autobuy')
def on_stop(data):
    key = data.get('api_key')
    if key: autobuy_active[key] = False

@socketio.on('cancel_order')
def on_cancel(data):
    key, aid = data.get('api_key'), data.get('id')
    api_req(key, 'setStatus', status='8', id=aid)
    socketio.emit('order_update', {'id': aid, 'status': 'cancelled'}, room=key)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)
