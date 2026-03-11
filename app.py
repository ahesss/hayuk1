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
app.config['SECRET_KEY'] = os.environ.get("SECRET_KEY", "herosms_super_mega_brutal_v10_fixed")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# CONFIG
API_BASE = "https://hero-sms.com/stubs/handler_api.php"
ACCESS_PASSWORD = str(os.environ.get("ACCESS_PASSWORD", "admin123")).strip()

print(f"\n[BOOT] HERO-SMS WEB V10 (BULLETPROOF) STARTING...")
sys.stdout.flush()

COUNTRIES = {
    "vietnam": {"name": "Vietnam", "flag": "🇻🇳", "id": "10", "code": "84", "max": "0.25"},
    "philipina": {"name": "Philipina", "flag": "🇵🇭", "id": "3", "code": "63", "max": "0.25"},
    "colombia": {"name": "Colombia", "flag": "🇨🇴", "id": "33", "code": "57", "max": None},
    "mexico": {"name": "Mexico", "flag": "🇲🇽", "id": "54", "code": "52", "max": None},
    "brazil": {"name": "Brazil", "flag": "🇧🇷", "id": "73", "code": "55", "max": "1.50"},
}

# Global Data Store
user_data = {}

def get_u(uid):
    if not uid: return None
    if uid not in user_data:
        user_data[uid] = {'auth': False, 'key': None, 'auto': False}
    return user_data[uid]

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
        get_u(uid)
        print(f"[ROOM] User {uid} joined room.")
        sys.stdout.flush()

@socketio.on('login')
def on_login(data):
    uid = data.get('user_id')
    entered = str(data.get('password', '')).strip()
    u = get_u(uid)
    if entered == ACCESS_PASSWORD:
        u['auth'] = True
        emit('login_result', {'success': True}, room=request.sid)
    else:
        emit('login_result', {'success': False, 'message': 'Password Salah!'}, room=request.sid)

@socketio.on('set_api_key')
def on_set_api(data):
    uid, key = data.get('user_id'), str(data.get('api_key', '')).strip()
    u = get_u(uid)
    if not u or not u['auth']: return emit('error_msg', {'message': 'Auth Required'}, room=request.sid)
    u['key'] = key
    res = api_req(key, 'getBalance')
    if 'ACCESS_BALANCE' in res:
        emit('api_result', {'success': True, 'balance': res.split(':')[-1]}, room=request.sid)
    else:
        emit('api_result', {'success': False, 'message': 'API Key Invalid'}, room=request.sid)

@socketio.on('get_balance')
def handle_bal(data):
    uid = data.get('user_id')
    u = get_u(uid)
    if u and u['key']:
        res = api_req(u['key'], 'getBalance')
        if 'ACCESS_BALANCE' in res:
            emit('balance_update', {'balance': res.split(':')[-1]}, room=request.sid)

def otp_loop(uid, key, aid, st):
    while True:
        if (time.time() - st) > 1200:
            api_req(key, 'setStatus', status='8', id=aid)
            socketio.emit('order_update', {'id': aid, 'status': 'timeout'}, room=uid)
            break
        r = api_req(key, 'getStatus', id=aid)
        if r.startswith('STATUS_OK'):
            code = r.split(':')[-1]
            api_req(key, 'setStatus', status='6', id=aid)
            socketio.emit('order_update', {'id': aid, 'status': 'got_otp', 'code': code}, room=uid)
            break
        elif r == 'STATUS_CANCEL':
            socketio.emit('order_update', {'id': aid, 'status': 'cancelled'}, room=uid)
            break
        socketio.sleep(4)

@socketio.on('buy_number')
def on_buy(data):
    uid, ck, count = data.get('user_id'), data.get('country'), int(data.get('count', 1))
    u = get_u(uid)
    if not u or not u['key']: return
    def run():
        cnt = COUNTRIES[ck]
        socketio.emit('buy_status', {'message': f"Bursting {count} numbers..."}, room=request.sid)
        done = 0
        for _ in range(count * 50):
            if done >= count: break
            res = api_req(u['key'], 'getNumber', service='wa', country=cnt['id'], maxPrice=cnt['max'] if cnt['max'] else None)
            if 'ACCESS_NUMBER' in res:
                parts = res.split(':')
                if len(parts) >= 3:
                    aid, num = parts[1], parts[2]
                    order = {'id': aid, 'number': num, 'status': 'waiting', 'order_time': time.time(), 'price': get_price(u['key'], aid, ck), 'country': ck, 'index': done+1, 'country_code': cnt['code']}
                    socketio.emit('new_number', order, room=uid)
                    socketio.start_background_task(otp_loop, uid, u['key'], aid, order['order_time'])
                    done += 1
                socketio.sleep(0.3)
            elif 'NO_BALANCE' in res: break
            socketio.sleep(0.001)
        socketio.emit('buy_complete', {'count': done}, room=request.sid)
    socketio.start_background_task(run)

@socketio.on('start_autobuy')
def on_auto(data):
    uid, ck = data.get('user_id'), data.get('country')
    u = get_u(uid)
    if not u or not u['key']: return
    u['auto'] = True
    cnt = COUNTRIES[ck]
    def run():
        att, found, last_ui, st = 0, 0, 0, time.time()
        socketio.emit('autobuy_started', {'country_name': cnt['name'], 'country': ck}, room=request.sid)
        while uid in user_data and user_data[uid]['auto']:
            att += 1
            if (time.time() - last_ui) > 0.8:
                el = int(time.time() - st)
                socketio.emit('autobuy_stats', {'attempts': att, 'found': found, 'elapsed': el, 'speed': round(att/max(el,1), 1)}, room=request.sid)
                last_ui = time.time()
            res = api_req(u['key'], 'getNumber', service='wa', country=cnt['id'], maxPrice=cnt['max'] if cnt['max'] else None)
            if 'ACCESS_NUMBER' in res:
                parts = res.split(':')
                if len(parts) >= 3:
                    aid, num = parts[1], parts[2]
                    found += 1
                    order = {'id': aid, 'number': num, 'status': 'waiting', 'order_time': time.time(), 'price': get_price(u['key'], aid, ck), 'country': ck, 'index': found, 'country_code': cnt['code']}
                    socketio.emit('new_number', order, room=uid)
                    socketio.start_background_task(otp_loop, uid, u['key'], aid, order['order_time'])
                    socketio.sleep(0.4)
            elif 'NO_BALANCE' in res: break
            else:
                socketio.sleep(0.001)
        socketio.emit('autobuy_stopped', {'total': found}, room=request.sid)
    socketio.start_background_task(run)

@socketio.on('stop_autobuy')
def on_stop(data):
    u = get_u(data.get('user_id'))
    if u: u['auto'] = False

@socketio.on('cancel_order')
def on_cancel(data):
    uid, aid = data.get('user_id'), data.get('id')
    u = get_u(uid)
    if u and u['key']:
        api_req(u['key'], 'setStatus', status='8', id=aid)
        socketio.emit('order_update', {'id': aid, 'status': 'cancelled'}, room=uid)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)
