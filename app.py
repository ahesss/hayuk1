import eventlet
eventlet.monkey_patch()

import os
import sys
import time
import json
import string
import random
import requests
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO, emit, join_room

# INIT
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get("SECRET_KEY", "herosms_ultimate_v16_secret_key_persistent")
# SESSION CONFIG - PENTING untuk Railway HTTPS!
app.config['SESSION_COOKIE_SECURE'] = True      # Required for HTTPS
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = 86400 * 30  # 30 hari

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# CONFIG
API_BASE = "https://hero-sms.com/stubs/handler_api.php"
MASTER_PASS = str(os.environ.get("ACCESS_PASSWORD", "admin123")).strip()
ADMIN_SECRET = str(os.environ.get("ADMIN_SECRET", "panel8899")).strip()

print(f"\n[BOOT] HERO-SMS WEB V17 (SESSION FIX + SINGLE-USE) ONLINE")
print(f"[BOOT] Admin Panel: /admin/{ADMIN_SECRET}")
sys.stdout.flush()

COUNTRIES = {
    "vietnam": {"name": "Vietnam", "flag": "\U0001f1fb\U0001f1f3", "id": "10", "code": "84", "max": "0.25"},
    "philipina": {"name": "Philipina", "flag": "\U0001f1f5\U0001f1ed", "id": "3", "code": "63", "max": "0.25"},
    "colombia": {"name": "Colombia", "flag": "\U0001f1e8\U0001f1f4", "id": "33", "code": "57", "max": None},
    "mexico": {"name": "Mexico", "flag": "\U0001f1f2\U0001f1fd", "id": "54", "code": "52", "max": None},
    "brazil": {"name": "Brazil", "flag": "\U0001f1e7\U0001f1f7", "id": "73", "code": "55", "max": "1.50"},
}

autobuy_active = {}

# Connection pooling
http_session = requests.Session()
adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10)
http_session.mount('https://', adapter)
http_session.mount('http://', adapter)

# =============================================
# IN-MEMORY CODE STORE
# =============================================
access_codes = {}

def generate_code():
    chars = string.ascii_uppercase + string.digits
    part1 = ''.join(random.choices(chars, k=4))
    part2 = ''.join(random.choices(chars, k=4))
    return f"HERO-{part1}-{part2}"

def api_req(key, action, **kwargs):
    if not key: return "ERR_NO_KEY"
    p = {'api_key': key, 'action': action}
    for k, v in kwargs.items():
        if v is not None:
            p[k] = v
    try:
        r = http_session.get(API_BASE, params=p, timeout=5)
        return r.text.strip()
    except Exception as e:
        print(f"[API_ERR] {action}: {e}")
        return "ERR_HTTP"

# =============================================
# LOGIN VIA HTTP - SESSION PERSISTENT!
# =============================================
@app.route('/')
def home():
    # Cek session - jika sudah authenticated, langsung masuk dashboard
    if session.get('authenticated'):
        print(f"[HOME] User authenticated via session cookie (code: {session.get('access_code', '?')})")
        sys.stdout.flush()
        return render_template('index.html', countries=COUNTRIES, logged_in=True)
    return render_template('index.html', countries=COUNTRIES, logged_in=False)

@app.route('/login', methods=['POST'])
def login():
    global access_codes
    code = request.form.get('code', '').strip().upper()
    
    print(f"[AUTH] Login attempt: '{code}'")
    print(f"[AUTH] Codes in memory: {len(access_codes)} | Keys: {list(access_codes.keys())}")
    sys.stdout.flush()
    
    if code not in access_codes:
        print(f"[AUTH] ❌ Code '{code}' TIDAK DITEMUKAN!")
        sys.stdout.flush()
        return jsonify({'success': False, 'error': 'Kode tidak valid'})
    
    code_info = access_codes[code]
    
    if code_info['status'] == 'available':
        # Kode baru - PERTAMA KALI DIPAKAI
        access_codes[code]['status'] = 'used'
        access_codes[code]['used_at'] = time.time()
        access_codes[code]['used_str'] = time.strftime('%Y-%m-%d %H:%M:%S')
        
        # Set session PERMANENT agar survive refresh
        session.permanent = True
        session['authenticated'] = True
        session['access_code'] = code
        session['login_time'] = time.time()
        
        print(f"[AUTH] ✅ Code {code} PERTAMA KALI DIPAKAI - BERHASIL!")
        sys.stdout.flush()
        return jsonify({'success': True})
    
    elif code_info['status'] == 'used':
        # Kode SUDAH TERPAKAI - TOLAK! (sekali pakai)
        print(f"[AUTH] ❌ Code {code} SUDAH TERPAKAI oleh orang lain!")
        sys.stdout.flush()
        return jsonify({'success': False, 'error': 'Kode sudah dipakai! Minta kode baru ke admin.'})
    
    return jsonify({'success': False, 'error': 'Kode tidak valid'})

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# =============================================
# ADMIN
# =============================================
@app.route(f'/admin/{ADMIN_SECRET}')
def admin_page():
    return render_template('admin.html')

@app.route(f'/api/admin/{ADMIN_SECRET}/verify', methods=['POST'])
def admin_verify():
    data = request.get_json()
    if data and data.get('password') == MASTER_PASS:
        return jsonify({'success': True})
    return jsonify({'success': False})

@app.route(f'/api/admin/{ADMIN_SECRET}/codes', methods=['GET'])
def admin_list_codes():
    pw = request.args.get('pw', '')
    if pw != MASTER_PASS:
        return jsonify({'error': 'unauthorized'}), 401
    return jsonify(access_codes)

@app.route(f'/api/admin/{ADMIN_SECRET}/generate', methods=['POST'])
def admin_generate():
    global access_codes
    data = request.get_json()
    if not data or data.get('password') != MASTER_PASS:
        return jsonify({'error': 'unauthorized'}), 401
    count = min(int(data.get('count', 1)), 50)
    
    new_codes = []
    for _ in range(count):
        code = generate_code()
        while code in access_codes:
            code = generate_code()
        access_codes[code] = {
            'status': 'available',
            'created': time.time(),
            'created_str': time.strftime('%Y-%m-%d %H:%M:%S')
        }
        new_codes.append(code)
    
    print(f"[ADMIN] ✅ Generated {len(new_codes)} codes. Total: {len(access_codes)}")
    sys.stdout.flush()
    return jsonify({'codes': new_codes, 'total': len(access_codes)})

@app.route(f'/api/admin/{ADMIN_SECRET}/delete', methods=['POST'])
def admin_delete():
    global access_codes
    data = request.get_json()
    if not data or data.get('password') != MASTER_PASS:
        return jsonify({'error': 'unauthorized'}), 401
    code = data.get('code')
    if code in access_codes:
        del access_codes[code]
        return jsonify({'success': True})
    return jsonify({'error': 'not found'}), 404

@app.route(f'/api/admin/{ADMIN_SECRET}/reset', methods=['POST'])
def admin_reset_code():
    """Reset kode yang sudah terpakai jadi available lagi"""
    global access_codes
    data = request.get_json()
    if not data or data.get('password') != MASTER_PASS:
        return jsonify({'error': 'unauthorized'}), 401
    code = data.get('code')
    if code in access_codes:
        access_codes[code]['status'] = 'available'
        if 'used_at' in access_codes[code]:
            del access_codes[code]['used_at']
        if 'used_str' in access_codes[code]:
            del access_codes[code]['used_str']
        return jsonify({'success': True})
    return jsonify({'error': 'not found'}), 404

# =============================================
# DEBUG
# =============================================
@app.route(f'/api/admin/{ADMIN_SECRET}/debug', methods=['GET'])
def admin_debug():
    pw = request.args.get('pw', '')
    if pw != MASTER_PASS:
        return jsonify({'error': 'unauthorized'}), 401
    
    available = sum(1 for c in access_codes.values() if c.get('status') == 'available')
    used = sum(1 for c in access_codes.values() if c.get('status') == 'used')
    
    return jsonify({
        'version': 'V17',
        'storage': 'IN_MEMORY',
        'total_codes': len(access_codes),
        'available': available,
        'used': used,
        'codes_detail': {k: v.get('status') for k, v in access_codes.items()},
        'server_time': time.strftime('%Y-%m-%d %H:%M:%S'),
        'pid': os.getpid(),
        'session_config': {
            'cookie_secure': app.config.get('SESSION_COOKIE_SECURE'),
            'cookie_samesite': app.config.get('SESSION_COOKIE_SAMESITE'),
            'permanent_lifetime': app.config.get('PERMANENT_SESSION_LIFETIME'),
        }
    })

# =============================================
# SOCKET EVENTS
# =============================================
@socketio.on('init_session')
def on_init(data):
    key = data.get('api_key')
    if key:
        join_room(key)
        if autobuy_active.get(key):
            emit('autobuy_started', {'country_name': 'Berjalan'})

@socketio.on('get_balance')
def on_bal(data):
    key = data.get('api_key')
    res = api_req(key, 'getBalance')
    if 'ACCESS_BALANCE' in res:
        emit('balance_update', {'balance': res.split(':')[-1]})
    else:
        emit('error_msg', {'message': 'API Key bermasalah!'})

def otp_worker(room_key, api_key, aid, st):
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
            res = api_req(key, 'getNumber', service='wa', country=cnt['id'], maxPrice=cnt['max'])
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
    if autobuy_active.get(key): return
    autobuy_active[key] = True
    cnt = COUNTRIES[ck]
    NUM_WORKERS = 5

    def single_worker(worker_id, shared):
        while autobuy_active.get(key):
            try:
                res = api_req(key, 'getNumber', service='wa', country=cnt['id'], maxPrice=cnt['max'])
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
                    socketio.emit('error_msg', {'message': '\U0001f4b8 SALDO HABIS!'}, room=key)
                    break
                elif 'NO_NUMBERS' in res:
                    socketio.sleep(0.01)
                else:
                    socketio.sleep(0.01)
            except:
                socketio.sleep(0.05)

    def run():
        shared = {'att': 0, 'found': 0}
        st = time.time()
        socketio.emit('autobuy_started', {'country_name': cnt['name']}, room=key)
        workers = []
        for wid in range(NUM_WORKERS):
            w = socketio.start_background_task(single_worker, wid, shared)
            workers.append(w)
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
