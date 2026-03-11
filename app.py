import eventlet
eventlet.monkey_patch()

import os
import sys
import time
import json
import requests
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

# INIT APP
app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# CONFIG
API_BASE = "https://hero-sms.com/stubs/handler_api.php"
ACCESS_PASSWORD = str(os.environ.get("ACCESS_PASSWORD", "admin123")).strip()

print(f"\n[SYSTEM] STARTING HERO-SMS WEB ULTRA")
print(f"[SYSTEM] PASSWORD: {ACCESS_PASSWORD}")
sys.stdout.flush()

COUNTRIES = {
    "vietnam": {"name": "Vietnam", "flag": "🇻🇳", "id": "10", "code": "84", "max": "0.25"},
    "philipina": {"name": "Philipina", "flag": "🇵🇭", "id": "3", "code": "63", "max": "0.25"},
    "colombia": {"name": "Colombia", "flag": "🇨🇴", "id": "33", "code": "57", "max": None},
    "mexico": {"name": "Mexico", "flag": "🇲🇽", "id": "54", "code": "52", "max": None},
    "brazil": {"name": "Brazil", "flag": "🇧🇷", "id": "73", "code": "55", "max": "1.50"},
}

user_sessions = {}

# API WRAPPER
def api_req(key, action, **kwargs):
    params = {'api_key': key, 'action': action}
    params.update(kwargs)
    try:
        r = requests.get(API_BASE, params=params, timeout=12)
        return r.text.strip()
    except: return "ERROR_CONN"

# GET REAL PRICE
def get_price(key, act_id, country_key):
    try:
        res = api_req(key, 'getActiveActivations')
        if res.startswith("{"):
            d = json.loads(res)
            acts = d.get('activeActivations', d)
            if isinstance(acts, dict) and str(act_id) in acts:
                item = acts[str(act_id)]
                return item.get('activationCost', item.get('cost', 0))
    except: pass
    return COUNTRIES.get(country_key, {}).get('max', 0)

# SOCKET EVENTS
@socketio.on('connect')
def connect():
    user_sessions[request.sid] = {'auth': False, 'key': None, 'auto': False}

@socketio.on('disconnect')
def disconnect():
    if request.sid in user_sessions:
        user_sessions[request.sid]['auto'] = False
        del user_sessions[request.sid]

@socketio.on('login')
def login(data):
    sid = request.sid
    entered = str(data.get('password', '')).strip()
    if entered == ACCESS_PASSWORD:
        user_sessions[sid]['auth'] = True
        emit('login_result', {'success': True})
    else:
        emit('login_result', {'success': False, 'message': 'Password Salah!'})

@socketio.on('set_api_key')
def set_api(data):
    sid = request.sid
    key = str(data.get('api_key', '')).strip()
    user_sessions[sid]['key'] = key
    res = api_req(key, 'getBalance')
    if 'ACCESS_BALANCE' in res:
        emit('api_result', {'success': True, 'balance': res.split(':')[-1]})
    else:
        emit('api_result', {'success': False, 'message': 'API Key Invalid'})

# BUY LOGIC
def otp_checker(sid, key, act_id, start_time):
    while sid in user_sessions:
        if (time.time() - start_time) > 1200:
            api_req(key, 'setStatus', status='8', id=act_id)
            socketio.emit('order_update', {'id': act_id, 'status': 'timeout'}, room=sid)
            break
        res = api_req(key, 'getStatus', id=act_id)
        if res.startswith('STATUS_OK'):
            code = res.split(':')[-1]
            api_req(key, 'setStatus', status='6', id=act_id)
            socketio.emit('order_update', {'id': act_id, 'status': 'got_otp', 'code': code}, room=sid)
            break
        elif res == 'STATUS_CANCEL':
            socketio.emit('order_update', {'id': act_id, 'status': 'cancelled'}, room=sid)
            break
        socketio.sleep(4)

@socketio.on('buy_number')
def buy(data):
    sid = request.sid
    if not user_sessions.get(sid, {}).get('auth'): return
    key = user_sessions[sid]['key']
    ck = data.get('country')
    count = int(data.get('count', 1))
    
    def task():
        cnt = COUNTRIES[ck]
        socketio.emit('buy_status', {'message': f"Membeli {count} nomor..."}, room=sid)
        done = 0
        for _ in range(count * 3):
            if done >= count or sid not in user_sessions: break
            p = {'service': 'wa', 'country': cnt['id']}
            if cnt['max']: p['maxPrice'] = cnt['max']
            res = api_req(key, 'getNumber', **p)
            if 'ACCESS_NUMBER' in res:
                parts = res.split(':')
                if len(parts) >= 3:
                    aid, num = parts[1], parts[2]
                    order = {'id': aid, 'number': num, 'status': 'waiting', 'order_time': time.time(), 'price': get_price(key, aid, ck), 'country': ck, 'index': done+1}
                    socketio.emit('new_number', order, room=sid)
                    socketio.start_background_task(otp_checker, sid, key, aid, order['order_time'])
                    done += 1
                socketio.sleep(0.3)
            elif 'NO_BALANCE' in res: break
            socketio.sleep(0.12)
        socketio.emit('buy_complete', {'count': done}, room=sid)
        
    socketio.start_background_task(task)

@socketio.on('start_autobuy')
def auto(data):
    sid = request.sid
    if not user_sessions.get(sid, {}).get('auth'): return
    ck = data.get('country')
    user_sessions[sid]['auto'] = True
    key = user_sessions[sid]['key']
    cnt = COUNTRIES[ck]
    
    def task():
        att, found, last_ui = 0, 0, 0
        st = time.time()
        socketio.emit('autobuy_started', {'country_name': cnt['name']}, room=sid)
        while sid in user_sessions and user_sessions[sid]['auto']:
            att += 1
            if (time.time() - last_ui) > 1.1:
                el = int(time.time() - st)
                socketio.emit('autobuy_stats', {'attempts': att, 'found': found, 'elapsed': el, 'speed': round(att/max(el,1), 1)}, room=sid)
                last_ui = time.time()
            p = {'service': 'wa', 'country': cnt['id']}
            if cnt['max']: p['maxPrice'] = cnt['max']
            res = api_req(key, 'getNumber', **p)
            if 'ACCESS_NUMBER' in res:
                parts = res.split(':')
                if len(parts) >= 3:
                    aid, num = parts[1], parts[2]
                    found += 1
                    order = {'id': aid, 'number': num, 'status': 'waiting', 'order_time': time.time(), 'price': get_price(key, aid, ck), 'country': ck, 'index': found}
                    socketio.emit('new_number', order, room=sid)
                    socketio.start_background_task(otp_checker, sid, key, aid, order['order_time'])
                    socketio.sleep(0.5)
            elif 'NO_BALANCE' in res: break
            else: socketio.sleep(0.04)
        socketio.emit('autobuy_stopped', {'total': found}, room=sid)
    socketio.start_background_task(task)

@socketio.on('stop_autobuy')
def stop_auto():
    if request.sid in user_sessions: user_sessions[request.sid]['auto'] = False

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)
