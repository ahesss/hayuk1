import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, session, jsonify
from flask_socketio import SocketIO, emit
import requests as http_requests
import time
import json
import os
import sys

# =============================================
# KONFIGURASI
# =============================================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "herosms_mega_secret_v4")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet', ping_timeout=120, ping_interval=25)

API_BASE = "https://hero-sms.com/stubs/handler_api.php"
SERVICE = "wa"
# Fix Password handling
RAW_PW = os.environ.get("ACCESS_PASSWORD", "admin123")
ACCESS_PASSWORD = str(RAW_PW).strip()

print(f"\n[BOOT] === STARTING V4.0 (CRASH-PROOF) ===")
print(f"[BOOT] Loaded Password: '{ACCESS_PASSWORD}'")
sys.stdout.flush()

COUNTRIES = {
    "vietnam": {"name": "Vietnam", "flag": "🇻🇳", "id": "10", "code": "84", "max": "0.25"},
    "philipina": {"name": "Philipina", "flag": "🇵🇭", "id": "3", "code": "63", "max": "0.25"},
    "colombia": {"name": "Colombia", "flag": "🇨🇴", "id": "33", "code": "57", "max": None},
    "mexico": {"name": "Mexico", "flag": "🇲🇽", "id": "54", "code": "52", "max": None},
    "brazil": {"name": "Brazil", "flag": "🇧🇷", "id": "73", "code": "55", "max": "1.50"},
}

user_data = {} # sid -> info

# =============================================
# API CORE
# =============================================
def call_api(api_key, action, **kwargs):
    p = {'api_key': api_key, 'action': action}
    p.update(kwargs)
    try:
        r = http_requests.get(API_BASE, params=p, timeout=15)
        return r.text.strip()
    except Exception as e:
        print(f"[API ERROR] {action}: {e}")
        return f"ERR_CONN"

def safe_price(api_key, act_id, country_key):
    """Ambil harga tanpa bikin crash meskipun API Hero telat"""
    try:
        res = call_api(api_key, 'getActiveActivations')
        if res.startswith("{"):
            d = json.loads(res)
            acts = d.get('activeActivations', d)
            if isinstance(acts, dict) and str(act_id) in acts:
                item = acts[str(act_id)]
                cost = item.get('activationCost') or item.get('cost') or item.get('sum')
                if cost: return float(cost)
    except: pass
    
    # Fallback to config
    return COUNTRIES.get(country_key, {}).get('max', '0.00')

# =============================================
# SOCKET EVENTS
# =============================================
@socketio.on('connect')
def on_connect():
    user_data[request.sid] = {'key': None, 'auto': False, 'auth': False}

@socketio.on('disconnect')
def on_disconnect():
    if request.sid in user_data:
        user_data[request.sid]['auto'] = False
        del user_data[request.sid]

@socketio.on('login')
def on_login(data):
    sid = request.sid
    entered = str(data.get('password', '')).strip()
    print(f"[LOGIN] Attempt: {entered} (Expect: {ACCESS_PASSWORD})")
    sys.stdout.flush()
    if entered == ACCESS_PASSWORD:
        if sid in user_data: user_data[sid]['auth'] = True
        emit('login_result', {'success': True})
    else:
        emit('login_result', {'success': False, 'message': 'Password Salah!'})

@socketio.on('set_api_key')
def on_set_api(data):
    sid = request.sid
    if sid not in user_data or not user_data[sid]['auth']: return
    key = str(data.get('api_key', '')).strip()
    user_data[sid]['key'] = key
    res = call_api(key, 'getBalance')
    if 'ACCESS_BALANCE' in res:
        bal = res.split(':')[-1]
        emit('api_result', {'success': True, 'balance': bal})
    else:
        emit('api_result', {'success': False, 'message': 'API Key Tidak Valid'})

@socketio.on('get_balance')
def on_bal():
    sid = request.sid
    if sid in user_data and user_data[sid]['key']:
        res = call_api(user_data[sid]['key'], 'getBalance')
        if 'ACCESS_BALANCE' in res:
            emit('balance_update', {'balance': res.split(':')[-1]})

# =============================================
# BACKGROUND WORKERS
# =============================================
def otp_worker(sid, key, act_id, start_time):
    print(f"[OTP] Checking {act_id}...")
    while sid in user_data:
        if (time.time() - start_time) > 1200:
            call_api(key, 'setStatus', status='8', id=act_id)
            socketio.emit('order_update', {'id': act_id, 'status': 'timeout'}, room=sid)
            break
        
        res = call_api(key, 'getStatus', id=act_id)
        if res.startswith('STATUS_OK'):
            code = res.split(':')[-1]
            call_api(key, 'setStatus', status='6', id=act_id)
            socketio.emit('order_update', {'id': act_id, 'status': 'got_otp', 'code': code}, room=sid)
            break
        elif res == 'STATUS_CANCEL':
            socketio.emit('order_update', {'id': act_id, 'status': 'cancelled'}, room=sid)
            break
        socketio.sleep(4)

@socketio.on('buy_number')
def on_buy(data):
    sid = request.sid
    if sid not in user_data or not user_data[request.sid]['auth']: return
    
    key = user_data[sid]['key']
    ck = data.get('country', 'vietnam')
    count = int(data.get('count', 1))
    
    def run_buy():
        cnt = COUNTRIES.get(ck)
        socketio.emit('buy_status', {'message': f"Membeli {count} nomor {cnt['name']}..."}, room=sid)
        done = 0
        for _ in range(count * 4): # retry loop
            if done >= count or sid not in user_data: break
            
            p = {'service': SERVICE, 'country': cnt['id']}
            if cnt['max']: p['maxPrice'] = cnt['max']
            
            res = call_api(key, 'getNumber', **p)
            # CRITICAL FIX: Safe split to avoid IndexError
            if 'ACCESS_NUMBER' in res:
                parts = res.split(':')
                if len(parts) >= 3:
                    aid, num = parts[1], parts[2]
                    price = safe_price(key, aid, ck)
                    order = {
                        'id': aid, 'number': num, 'country': ck, 'country_name': cnt['name'],
                        'country_code': cnt['code'], 'status': 'waiting',
                        'order_time': time.time(), 'price': price, 'index': done + 1
                    }
                    socketio.emit('new_number', order, room=sid)
                    socketio.start_background_task(otp_worker, sid, key, aid, order['order_time'])
                    done += 1
                    socketio.sleep(0.3)
            elif 'NO_BALANCE' in res: break
            socketio.sleep(0.1)
        socketio.emit('buy_complete', {'count': done, 'country': cnt['name']}, room=sid)

    socketio.start_background_task(run_buy)

@socketio.on('start_autobuy')
def on_auto(data):
    sid = request.sid
    if sid not in user_data or not user_data[sid]['auth']: return
    
    ck = data.get('country', 'vietnam')
    user_data[sid]['auto'] = True
    key = user_data[sid]['key']
    cnt = COUNTRIES.get(ck)
    
    def run_auto():
        att, found, st_time, last_ui = 0, 0, time.time(), 0
        socketio.emit('autobuy_started', {'country_name': cnt['name']}, room=sid)
        
        while sid in user_data and user_data[sid]['auto']:
            att += 1
            now = time.time()
            if now - last_ui > 1.2:
                el = int(now - st_time)
                socketio.emit('autobuy_stats', {'attempts': att, 'found': found, 'elapsed': el, 'speed': round(att/max(el,1), 1)}, room=sid)
                last_ui = now
            
            p = {'service': SERVICE, 'country': cnt['id']}
            if cnt['max']: p['maxPrice'] = cnt['max']
            
            res = call_api(key, 'getNumber', **p)
            if 'ACCESS_NUMBER' in res:
                parts = res.split(':')
                if len(parts) >= 3: # Safe check
                    aid, num = parts[1], parts[2]
                    found += 1
                    order = {
                        'id': aid, 'number': num, 'country': ck, 'status': 'waiting',
                        'order_time': time.time(), 'price': safe_price(key, aid, ck), 'index': found
                    }
                    socketio.emit('new_number', order, room=sid)
                    socketio.start_background_task(otp_worker, sid, key, aid, order['order_time'])
                    socketio.sleep(0.5)
            elif 'NO_BALANCE' in res: break
            else: socketio.sleep(0.04)
            
        socketio.emit('autobuy_stopped', {'total': found}, room=sid)

    socketio.start_background_task(run_auto)

@socketio.on('stop_autobuy')
def on_stop_auto():
    if request.sid in user_data: user_data[request.sid]['auto'] = False

@socketio.on('cancel_order')
def on_cancel(data):
    sid = request.sid
    if sid in user_data and user_data[sid]['key']:
        call_api(user_data[sid]['key'], 'setStatus', status='8', id=data.get('id'))
        socketio.emit('order_update', {'id': data.get('id'), 'status': 'cancelled'}, room=sid)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)
