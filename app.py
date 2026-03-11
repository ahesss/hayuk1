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
# KONFIGURASI & LOGGING
# =============================================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "herosms_super_ultra_secret_999")
# Gunakan lock untuk thread safety
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet', ping_timeout=120, ping_interval=25, logger=False, engineio_logger=False)

API_BASE = "https://hero-sms.com/stubs/handler_api.php"
SERVICE = "wa"
ACCESS_PASSWORD = str(os.environ.get("ACCESS_PASSWORD", "admin123")).strip()

print(f"\n[SYSTEM] === RESTARTING HERO-SMS WEB ULTRA BRUTAL ===")
print(f"[SYSTEM] Access Password configured as: '{ACCESS_PASSWORD}'")
print(f"[SYSTEM] Python Version: {sys.version}")
sys.stdout.flush()

COUNTRIES = {
    "vietnam": {"name": "Vietnam", "flag": "🇻🇳", "country_id": "10", "country_code": "84", "maxPrice": "0.25"},
    "philipina": {"name": "Philipina", "flag": "🇵🇭", "country_id": "3", "country_code": "63", "maxPrice": "0.25"},
    "colombia": {"name": "Colombia", "flag": "🇨🇴", "country_id": "33", "country_code": "57"},
    "mexico": {"name": "Mexico", "flag": "🇲🇽", "country_id": "54", "country_code": "52"},
    "brazil": {"name": "Brazil", "flag": "🇧🇷", "country_id": "73", "country_code": "55", "maxPrice": "1.50"},
}

user_sessions = {}

# =============================================
# API HELPER (ULTRA ROBUST)
# =============================================
def req_api(api_key, action, **kwargs):
    params = {'api_key': api_key, 'action': action}
    params.update(kwargs)
    try:
        r = http_requests.get(API_BASE, params=params, timeout=15)
        text = r.text.strip()
        if not text: return "EMPTY_RESPONSE"
        return text
    except Exception as e:
        print(f"[ERROR] API {action} failed: {str(e)}")
        sys.stdout.flush()
        return f"CRASH_ERROR: {str(e)}"

def get_balance(api_key):
    res = req_api(api_key, 'getBalance')
    if 'ACCESS_BALANCE' in res:
        try: return res.split(':')[1]
        except: return None
    return None

def fetch_price(api_key, act_id, country_key):
    # Coba ambil harga asli dulu
    try:
        res = req_api(api_key, 'getActiveActivations')
        if res.startswith("{"):
            d = json.loads(res)
            acts = d.get('activeActivations', d)
            if isinstance(acts, dict) and str(act_id) in acts:
                info = acts[str(act_id)]
                cost = info.get('activationCost') or info.get('cost') or info.get('sum')
                if cost: return float(cost)
    except: pass
    
    # Fallback ke getPrices
    try:
        cid = COUNTRIES[country_key]['country_id']
        res_p = req_api(api_key, 'getPrices', service=SERVICE, country=cid)
        if res_p.startswith("{"):
            d = json.loads(res_p)
            inn = d.get(cid, {}).get(SERVICE) or d.get(SERVICE, {}).get(cid)
            if isinstance(inn, dict):
                prices = [float(k) for k in inn.keys() if k.replace('.','').isdigit()]
                if prices: return max(prices)
    except: pass
    return None

# =============================================
# SOCKET EVENTS
# =============================================
@socketio.on('connect')
def connect():
    sid = request.sid
    user_sessions[sid] = {'api_key': None, 'autobuy': False, 'auth': False}
    print(f"[CONN] New client: {sid}")
    sys.stdout.flush()

@socketio.on('disconnect')
def disconnect():
    sid = request.sid
    if sid in user_sessions:
        print(f"[DISC] Client left: {sid}")
        user_sessions[sid]['autobuy'] = False
        del user_sessions[sid]
    sys.stdout.flush()

@socketio.on('login')
def login(data):
    sid = request.sid
    entered = str(data.get('password', '')).strip()
    print(f"[AUTH] Login attempt SID {sid}: '{entered}' vs '{ACCESS_PASSWORD}'")
    sys.stdout.flush()
    
    if entered == ACCESS_PASSWORD:
        if sid in user_sessions: user_sessions[sid]['auth'] = True
        emit('login_result', {'success': True})
    else:
        emit('login_result', {'success': False, 'message': 'Password Salah!'})

@socketio.on('set_api_key')
def set_api(data):
    sid = request.sid
    if sid not in user_sessions or not user_sessions[sid]['auth']: return
    key = str(data.get('api_key', '')).strip()
    user_sessions[sid]['api_key'] = key
    bal = get_balance(key)
    if bal: emit('api_result', {'success': True, 'balance': bal})
    else: emit('api_result', {'success': False, 'message': 'API Key Invalid'})

@socketio.on('get_balance')
def handle_bal():
    sid = request.sid
    if sid in user_sessions and user_sessions[sid]['api_key']:
        bal = get_balance(user_sessions[sid]['api_key'])
        if bal: emit('balance_update', {'balance': bal})

# =============================================
# WORKERS (USING SOCKETIO BACKGROUND TASKS)
# =============================================
def otp_loop(sid, api_key, order_id, order_time):
    print(f"[OTP] Background checker started for {order_id}")
    sys.stdout.flush()
    while sid in user_sessions:
        if (time.time() - order_time) > 1200:
            req_api(api_key, 'setStatus', status='8', id=order_id)
            socketio.emit('order_update', {'id': order_id, 'status': 'timeout'}, room=sid)
            break
        
        res = req_api(api_key, 'getStatus', id=order_id)
        if res.startswith('STATUS_OK'):
            code = res.split(':')[1] if ':' in res else '???'
            req_api(api_key, 'setStatus', status='6', id=order_id)
            socketio.emit('order_update', {'id': order_id, 'status': 'got_otp', 'code': code}, room=sid)
            break
        elif res == 'STATUS_CANCEL':
            socketio.emit('order_update', {'id': order_id, 'status': 'cancelled'}, room=sid)
            break
        
        socketio.sleep(4)

@socketio.on('buy_number')
def buy(data):
    sid = request.sid
    if sid not in user_sessions or not user_sessions[sid]['auth'] or not user_sessions[sid]['api_key']: return
    
    ck = data.get('country', 'vietnam')
    count = min(int(data.get('count', 1)), 20)
    api_key = user_sessions[sid]['api_key']
    
    def task():
        cntry = COUNTRIES.get(ck)
        if not cntry: return
        socketio.emit('buy_status', {'message': f"Membeli {count} nomor {cntry['name']}..."}, room=sid)
        
        success_count = 0
        for i in range(count * 3): # retry limit
            if success_count >= count or sid not in user_sessions: break
            
            kwargs = {'service': SERVICE, 'country': cntry['country_id']}
            if 'maxPrice' in cntry: kwargs['maxPrice'] = cntry['maxPrice']
            
            res = req_api(api_key, 'getNumber', **kwargs)
            if 'ACCESS_NUMBER' in res:
                parts = res.split(':')
                act_id, num = parts[1], parts[2]
                pr = fetch_price(api_key, act_id, ck)
                order = {
                    'id': act_id, 'number': num, 'country': ck, 'country_name': cntry['name'],
                    'country_code': cntry['country_code'], 'status': 'waiting', 
                    'order_time': time.time(), 'price': pr, 'index': success_count + 1
                }
                socketio.emit('new_number', order, room=sid)
                socketio.start_background_task(otp_loop, sid, api_key, act_id, order['order_time'])
                success_count += 1
                socketio.sleep(0.3)
            elif 'NO_BALANCE' in res:
                socketio.emit('error_msg', {'message': 'Saldo Habis!'}, room=sid); break
            elif 'NO_NUMBERS' in res:
                socketio.sleep(0.15)
            else: socketio.sleep(0.5)
            
        socketio.emit('buy_complete', {'count': success_count, 'country': cntry['name']}, room=sid)
        bal = get_balance(api_key)
        if bal: socketio.emit('balance_update', {'balance': bal}, room=sid)

    socketio.start_background_task(task)

@socketio.on('start_autobuy')
def start_auto(data):
    sid = request.sid
    if sid not in user_sessions or not user_sessions[sid]['auth'] or not user_sessions[sid]['api_key']: return
    
    ck = data.get('country', 'vietnam')
    user_sessions[sid]['autobuy'] = True
    api_key = user_sessions[sid]['api_key']
    cntry = COUNTRIES.get(ck)
    
    def task():
        print(f"[AUTO] Start Brutal for {ck} on {sid}")
        att, count, st_time, last_stats = 0, 0, time.time(), 0
        socketio.emit('autobuy_started', {'country': ck, 'country_name': cntry['name'], 'maxPrice': cntry.get('maxPrice', 'N/A')}, room=sid)
        
        while sid in user_sessions and user_sessions[sid].get('autobuy'):
            att += 1
            now = time.time()
            if now - last_stats > 1.1:
                el = int(now - st_time)
                socketio.emit('autobuy_stats', {'attempts': att, 'found': count, 'elapsed': el, 'speed': round(att/max(el,1), 1), 'country': ck, 'country_name': cntry['name']}, room=sid)
                last_stats = now
            
            kwargs = {'service': SERVICE, 'country': cntry['country_id']}
            if 'maxPrice' in cntry: kwargs['maxPrice'] = cntry['maxPrice']
            
            res = req_api(api_key, 'getNumber', **kwargs)
            if 'ACCESS_NUMBER' in res:
                parts = res.split(':')
                act_id, num = parts[1], parts[2]
                pr = fetch_price(api_key, act_id, ck)
                count += 1
                order = {'id': act_id, 'number': num, 'country': ck, 'country_name': cntry['name'], 'country_code': cntry['country_code'], 'status': 'waiting', 'order_time': time.time(), 'price': pr, 'index': count}
                socketio.emit('new_number', order, room=sid)
                socketio.start_background_task(otp_loop, sid, api_key, act_id, order['order_time'])
                socketio.sleep(0.4)
            elif 'NO_BALANCE' in res: break
            elif 'NO_NUMBERS' in res: socketio.sleep(0.04)
            else: socketio.sleep(0.1)
            
        socketio.emit('autobuy_stopped', {'reason': 'Berhenti', 'total': count}, room=sid)
        bal = get_balance(api_key)
        if bal: socketio.emit('balance_update', {'balance': bal}, room=sid)

    socketio.start_background_task(task)

@socketio.on('stop_autobuy')
def stop_auto():
    sid = request.sid
    if sid in user_sessions: user_sessions[sid]['autobuy'] = False

@socketio.on('cancel_order')
def cancel(data):
    sid = request.sid
    if sid in user_sessions and user_sessions[sid]['api_key']:
        req_api(user_sessions[sid]['api_key'], 'setStatus', status='8', id=data.get('id'))
        socketio.emit('order_update', {'id': data.get('id'), 'status': 'cancelled'}, room=sid)

@app.route('/health')
def health(): return "OK", 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)
