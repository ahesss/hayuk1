import eventlet
eventlet.monkey_patch()

import os
import sys
import time
import json
import requests
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room

# INIT APP
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get("SECRET_KEY", "herosms_super_mega_brutal_v9_persistent")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# CONFIG
API_BASE = "https://hero-sms.com/stubs/handler_api.php"
ACCESS_PASSWORD = str(os.environ.get("ACCESS_PASSWORD", "admin123")).strip()

print(f"\n[BOOT] HERO-SMS WEB V9 (PERSISTENT OTP) STARTING...")
sys.stdout.flush()

COUNTRIES = {
    "vietnam": {"name": "Vietnam", "flag": "🇻🇳", "id": "10", "code": "84", "max": "0.25"},
    "philipina": {"name": "Philipina", "flag": "🇵🇭", "id": "3", "code": "63", "max": "0.25"},
    "colombia": {"name": "Colombia", "flag": "🇨🇴", "id": "33", "code": "57", "max": None},
    "mexico": {"name": "Mexico", "flag": "🇲🇽", "id": "54", "code": "52", "max": None},
    "brazil": {"name": "Brazil", "flag": "🇧🇷", "id": "73", "code": "55", "max": "1.50"},
}

# Menyimpan mapping user_id -> api_key & status
user_data = {}

def api_req(key, action, **kwargs):
    p = {'api_key': key, 'action': action}
    p.update(kwargs)
    try:
        r = requests.get(API_BASE, params=p, timeout=15)
        return r.text.strip()
    except: return "ERR_HTTP"

def get_price(key, aid, ck):
    try:
        res = api_req(key, 'getActiveActivations')
        if res.startswith("{"):
            d = json.loads(res)
            acts = d.get('activeActivations', d)
            if isinstance(acts, dict) and str(aid) in acts:
                item = acts[str(aid)]
                return item.get('activationCost', item.get('cost', 0))
    except: pass
    return COUNTRIES.get(ck, {}).get('max', 0)

@app.route('/')
def home(): return render_template('index.html', countries=COUNTRIES)

@socketio.on('join')
def on_join(data):
    uid = data.get('user_id')
    if uid:
        join_room(uid)
        if uid not in user_data:
            user_data[uid] = {'auth': False, 'key': None, 'auto': False}
        print(f"[ROOM] User {uid} joined their persistent room.")
        sys.stdout.flush()

@socketio.on('login')
def on_login(data):
    uid = data.get('user_id')
    entered = str(data.get('password', '')).strip()
    if entered == ACCESS_PASSWORD:
        if uid in user_data: user_data[uid]['auth'] = True
        emit('login_result', {'success': True}, room=uid)
    else:
        emit('login_result', {'success': False, 'message': 'Password Salah!'}, room=request.sid)

@socketio.on('set_api_key')
def on_set_api(data):
    uid = data.get('user_id')
    if not user_data.get(uid, {}).get('auth'): return
    key = str(data.get('api_key', '')).strip()
    user_data[uid]['key'] = key
    res = api_req(key, 'getBalance')
    if 'ACCESS_BALANCE' in res:
        emit('api_result', {'success': True, 'balance': res.split(':')[-1]}, room=uid)
    else:
        emit('api_result', {'success': False, 'message': 'API Key Invalid'}, room=uid)

@socketio.on('get_balance')
def handle_bal(data):
    uid = data.get('user_id')
    if uid in user_data and user_data[uid]['key']:
        res = api_req(user_data[uid]['key'], 'getBalance')
        if 'ACCESS_BALANCE' in res:
            emit('balance_update', {'balance': res.split(':')[-1]}, room=uid)

def otp_loop(uid, key, aid, st):
    # Loop tetap jalan biarpun sid ganti, selama uid (user_id) sama
    print(f"[OTP] Monitoring ID {aid} for user {uid}")
    sys.stdout.flush()
    while True:
        # Jika lewat 20 menit, timeout
        if (time.time() - st) > 1200:
            api_req(key, 'setStatus', status='8', id=aid)
            socketio.emit('order_update', {'id': aid, 'status': 'timeout'}, room=uid)
            break
            
        r = api_req(key, 'getStatus', id=aid)
        
        if r.startswith('STATUS_OK'):
            code = r.split(':')[-1]
            # Selesaikan order di API
            api_req(key, 'setStatus', status='6', id=aid)
            socketio.emit('order_update', {'id': aid, 'status': 'got_otp', 'code': code}, room=uid)
            print(f"[OTP] SUCCESS: ID {aid} Code {code}")
            sys.stdout.flush()
            break
        elif r == 'STATUS_CANCEL':
            socketio.emit('order_update', {'id': aid, 'status': 'cancelled'}, room=uid)
            break
        
        socketio.sleep(4)

@socketio.on('buy_number')
def on_buy(data):
    uid = data.get('user_id')
    if not user_data.get(uid, {}).get('auth'): return
    key, ck = user_data[uid]['key'], data.get('country')
    count = int(data.get('count', 1))
    
    def run():
        cnt = COUNTRIES[ck]
        socketio.emit('buy_status', {'message': f"Bursting {count} numbers..."}, room=uid)
        done = 0
        for _ in range(count * 50):
            if done >= count: break
            # Jika user mematikan auto buy atau diskonek, tetap lanjutkan buy ini sampai done
            p = {'service': 'wa', 'country': cnt['id']}
            if cnt['max']: p['maxPrice'] = cnt['max']
            res = api_req(key, 'getNumber', **p)
            if 'ACCESS_NUMBER' in res:
                parts = res.split(':')
                if len(parts) >= 3:
                    aid, num = parts[1], parts[2]
                    order = {'id': aid, 'number': num, 'status': 'waiting', 'order_time': time.time(), 'price': get_price(key, aid, ck), 'country': ck, 'index': done+1, 'country_code': cnt['code']}
                    socketio.emit('new_number', order, room=uid)
                    socketio.start_background_task(otp_loop, uid, key, aid, order['order_time'])
                    done += 1
                socketio.sleep(0.3)
            elif 'NO_BALANCE' in res: break
            socketio.sleep(0.01) # Brutalitas ditingkatkan kembali
        socketio.emit('buy_complete', {'count': done, 'country': cnt['name']}, room=uid)
    socketio.start_background_task(run)

@socketio.on('start_autobuy')
def on_auto(data):
    uid = data.get('user_id')
    if not user_data.get(uid, {}).get('auth'): return
    ck, key = data.get('country'), user_data[uid]['key']
    user_data[uid]['auto'] = True
    cnt = COUNTRIES[ck]
    def run():
        att, found, last_ui, st = 0, 0, 0, time.time()
        socketio.emit('autobuy_started', {'country_name': cnt['name'], 'country': ck}, room=uid)
        while uid in user_data and user_data[uid]['auto']:
            att += 1
            if (time.time() - last_ui) > 0.8:
                el = int(time.time() - st)
                socketio.emit('autobuy_stats', {'attempts': att, 'found': found, 'elapsed': el, 'speed': round(att/max(el,1), 1)}, room=uid)
                last_ui = time.time()
            p = {'service': 'wa', 'country': cnt['id']}
            if cnt['max']: p['maxPrice'] = cnt['max']
            res = api_req(key, 'getNumber', **p)
            if 'ACCESS_NUMBER' in res:
                parts = res.split(':')
                if len(parts) >= 3:
                    aid, num = parts[1], parts[2]
                    found += 1
                    order = {'id': aid, 'number': num, 'status': 'waiting', 'order_time': time.time(), 'price': get_price(key, aid, ck), 'country': ck, 'index': found, 'country_code': cnt['code']}
                    socketio.emit('new_number', order, room=uid)
                    socketio.start_background_task(otp_loop, uid, key, aid, order['order_time'])
                    socketio.sleep(0.4)
            elif 'NO_BALANCE' in res: break
            else:
                socketio.sleep(0.001) # Brutal 100x
        socketio.emit('autobuy_stopped', {'total': found}, room=uid)
    socketio.start_background_task(run)

@socketio.on('stop_autobuy')
def on_stop(data):
    uid = data.get('user_id')
    if uid in user_data: user_data[uid]['auto'] = False

@socketio.on('cancel_order')
def on_cancel(data):
    uid = data.get('user_id')
    if uid in user_data and user_data[uid]['key']:
        aid = data.get('id')
        api_req(user_data[uid]['key'], 'setStatus', status='8', id=aid)
        socketio.emit('order_update', {'id': aid, 'status': 'cancelled'}, room=uid)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)
