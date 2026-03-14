import os
import sys
import time
import json
import string
import random
import uuid
import requests
import threading
from datetime import timedelta
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, make_response
from flask_socketio import SocketIO, emit, join_room

# INIT
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get("SECRET_KEY", "herosms_ultimate_v19_secret_key_reset_2026")
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

# Gunakan async_mode='threading' untuk kestabilan tinggi di shared server/cloud
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading', ping_timeout=60, ping_interval=25)

# CONFIG
API_BASE = "https://hero-sms.com/stubs/handler_api.php"
MASTER_PASS = str(os.environ.get("ACCESS_PASSWORD", "admin123")).strip()
ADMIN_SECRET = str(os.environ.get("ADMIN_SECRET", "panel8899")).strip()

COOKIE_MAX_AGE = 86400 * 30  # 30 hari dalam detik

print(f"\n[BOOT] HERO-SMS WEB V18 (PERSISTENT LOGIN) ONLINE")
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

# Persistent HTTP Sessions
# Satu untuk UI/Saldo (Selalu Cepat), Satu untuk Workers (Massal)
ui_session = requests.Session()
worker_session = requests.Session()

# Adapter untuk UI: Cepat dan Resilien
ui_adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=3)
ui_session.mount('https://', ui_adapter)

# Adapter untuk Workers: Kapasitas Besar
worker_adapter = requests.adapters.HTTPAdapter(
    pool_connections=50, 
    pool_maxsize=100, 
    max_retries=1,
    pool_block=False 
)
worker_session.mount('https://', worker_adapter)
ui_session.headers.update({'Connection': 'keep-alive'})
worker_session.headers.update({'Connection': 'keep-alive'})

# =============================================
# IN-MEMORY CODE STORE
# =============================================
access_codes = {}

def generate_code():
    chars = string.ascii_uppercase + string.digits
    part1 = ''.join(random.choices(chars, k=4))
    part2 = ''.join(random.choices(chars, k=4))
    return f"HERO-{part1}-{part2}"

def api_req(key, action, use_ui_session=False, **kwargs):
    if not key: return "ERR_NO_KEY"
    p = {'api_key': str(key).strip(), 'action': action}
    p.update(kwargs)
    
    session_to_use = ui_session if use_ui_session else worker_session
    timeout = 5.0 if use_ui_session else 3.0
    
    try:
        r = session_to_use.get(API_BASE, params=p, timeout=timeout)
        return r.text.strip()
    except Exception as e:
        return f"ERR_HTTP: {str(e)}"

def is_authenticated():
    """Cek apakah user authenticated via session ATAU persistent cookie."""
    # 1. Cek Flask session dulu
    if session.get('authenticated'):
        code = session.get('access_code')
        if code in access_codes and access_codes[code].get('status') == 'used':
            return True
        else:
            session.clear()
    
    # 2. Cek persistent cookie (untuk kasus browser ditutup lalu dibuka lagi)
    auth_token = request.cookies.get('hero_token')
    hero_code = request.cookies.get('hero_code')
    
    if auth_token and hero_code and hero_code in access_codes:
        stored_token = access_codes[hero_code].get('auth_token')
        if stored_token and stored_token == auth_token:
            # Cookie valid! Restore session
            session.permanent = True
            session['authenticated'] = True
            session['access_code'] = hero_code
            print(f"[AUTH] ✅ Auto-login via persistent cookie: {hero_code}")
            sys.stdout.flush()
            return True
    
    return False

# =============================================
# ROUTES
# =============================================
@app.route('/')
def home():
    if is_authenticated():
        return render_template('index.html', countries=COUNTRIES, logged_in=True)
    return render_template('index.html', countries=COUNTRIES, logged_in=False)

@app.route('/check_token', methods=['POST'])
def check_token():
    """Client cek apakah saved token masih valid (dari localStorage)."""
    data = request.get_json()
    if not data:
        return jsonify({'valid': False})
    code = data.get('code', '').strip().upper()
    token = data.get('token', '')
    
    if code in access_codes:
        stored_token = access_codes[code].get('auth_token')
        if stored_token and stored_token == token:
            session.permanent = True
            session['authenticated'] = True
            session['access_code'] = code
            print(f"[AUTH] ✅ Token check OK: {code}")
            sys.stdout.flush()
            return jsonify({'valid': True})
    
    print(f"[AUTH] ❌ Token check GAGAL: {code}")
    sys.stdout.flush()
    return jsonify({'valid': False})

@app.route('/login', methods=['POST'])
def login():
    global access_codes
    code = request.form.get('code', '').strip().upper()
    
    print(f"[AUTH] Login attempt: '{code}'")
    print(f"[AUTH] Codes in memory: {len(access_codes)}")
    sys.stdout.flush()
    
    # Juga terima JSON body (untuk auto-login dari localStorage)
    if request.is_json:
        json_data = request.get_json()
        code = json_data.get('code', '').strip().upper()
        sent_token = json_data.get('auth_token', '')
    else:
        sent_token = ''
    
    if code not in access_codes:
        print(f"[AUTH] ❌ Code '{code}' TIDAK DITEMUKAN!")
        sys.stdout.flush()
        return jsonify({'success': False, 'error': 'Kode tidak valid'})
    
    code_info = access_codes[code]
    
    if code_info['status'] == 'available':
        # === KODE BARU - PERTAMA KALI DIPAKAI ===
        auth_token = str(uuid.uuid4())
        access_codes[code]['status'] = 'used'
        access_codes[code]['used_at'] = time.time()
        access_codes[code]['used_str'] = time.strftime('%Y-%m-%d %H:%M:%S')
        access_codes[code]['auth_token'] = auth_token
        
        session.permanent = True
        session['authenticated'] = True
        session['access_code'] = code
        
        # Buat response dengan PERSISTENT COOKIE (survive browser close!)
        # Return token ke client agar bisa disimpan di localStorage
        resp = make_response(jsonify({'success': True, 'auth_token': auth_token}))
        resp.set_cookie('hero_token', auth_token, max_age=COOKIE_MAX_AGE, secure=True, httponly=True, samesite='Lax')
        resp.set_cookie('hero_code', code, max_age=COOKIE_MAX_AGE, secure=True, httponly=True, samesite='Lax')
        
        print(f"[AUTH] ✅ Code {code} PERTAMA KALI - token: {auth_token[:8]}...")
        sys.stdout.flush()
        return resp
    
    elif code_info['status'] == 'used':
        # === KODE SUDAH TERPAKAI ===
        # Cek via cookie ATAU via localStorage token yang dikirim
        browser_token = request.cookies.get('hero_token') or sent_token
        stored_token = code_info.get('auth_token')
        
        if browser_token and stored_token and browser_token == stored_token:
            # Token cocok - izinkan re-login
            session.permanent = True
            session['authenticated'] = True
            session['access_code'] = code
            
            resp = make_response(jsonify({'success': True, 'auth_token': stored_token}))
            # Refresh cookies
            resp.set_cookie('hero_token', stored_token, max_age=COOKIE_MAX_AGE, secure=True, httponly=True, samesite='Lax')
            resp.set_cookie('hero_code', code, max_age=COOKIE_MAX_AGE, secure=True, httponly=True, samesite='Lax')
            
            print(f"[AUTH] ✅ Code {code} RE-LOGIN (same browser) OK!")
            sys.stdout.flush()
            return resp
        else:
            # Browser BERBEDA - TOLAK!
            print(f"[AUTH] ❌ Code {code} DITOLAK - browser berbeda!")
            sys.stdout.flush()
            return jsonify({'success': False, 'error': 'Kode sudah dipakai di browser lain! Minta kode baru.'})
    
    return jsonify({'success': False, 'error': 'Kode tidak valid'})

@app.route('/logout')
def logout():
    session.clear()
    resp = make_response(redirect('/'))
    resp.delete_cookie('hero_token')
    resp.delete_cookie('hero_code')
    return resp

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
    # Return tanpa auth_token (security)
    safe_codes = {}
    for code, info in access_codes.items():
        safe_codes[code] = {k: v for k, v in info.items() if k != 'auth_token'}
    return jsonify(safe_codes)

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

@app.route(f'/api/admin/{ADMIN_SECRET}/delete_used', methods=['POST'])
def admin_delete_used():
    global access_codes
    data = request.get_json()
    if not data or data.get('password') != MASTER_PASS:
        return jsonify({'error': 'unauthorized'}), 401
    
    used_keys = [k for k, v in access_codes.items() if v.get('status') == 'used']
    for k in used_keys:
        del access_codes[k]
        
    return jsonify({'success': True, 'deleted_count': len(used_keys)})

@app.route(f'/api/admin/{ADMIN_SECRET}/reset', methods=['POST'])
def admin_reset_code():
    global access_codes
    data = request.get_json()
    if not data or data.get('password') != MASTER_PASS:
        return jsonify({'error': 'unauthorized'}), 401
    code = data.get('code')
    if code in access_codes:
        access_codes[code]['status'] = 'available'
        access_codes[code].pop('used_at', None)
        access_codes[code].pop('used_str', None)
        access_codes[code].pop('auth_token', None)
        return jsonify({'success': True})
    return jsonify({'error': 'not found'}), 404

@app.route(f'/api/admin/{ADMIN_SECRET}/debug', methods=['GET'])
def admin_debug():
    pw = request.args.get('pw', '')
    if pw != MASTER_PASS:
        return jsonify({'error': 'unauthorized'}), 401
    available = sum(1 for c in access_codes.values() if c.get('status') == 'available')
    used = sum(1 for c in access_codes.values() if c.get('status') == 'used')
    return jsonify({
        'version': 'V18',
        'total_codes': len(access_codes),
        'available': available,
        'used': used,
        'codes_status': {k: v.get('status') for k, v in access_codes.items()},
        'server_time': time.strftime('%Y-%m-%d %H:%M:%S'),
        'pid': os.getpid(),
    })

# =============================================
# SOCKET EVENTS
# =============================================
@socketio.on('init_session')
def on_init(data):
    key = str(data.get('api_key', '')).strip()
    if key:
        join_room(key)
        # Verifikasi key via UI session (Prioritas)
        res = api_req(key, 'getBalance', use_ui_session=True)
        if 'ACCESS_BALANCE' in res:
            emit('balance_update', {'balance': res.split(':')[-1], 'valid': True})
        else:
            emit('error_msg', {'message': f"Key Check Error: {res}"})

@socketio.on('get_balance')
def on_bal(data):
    key = str(data.get('api_key', '')).strip()
    if not key:
        emit('error_msg', {'message': 'API Key Kosong!'})
        return
    res = api_req(key, 'getBalance', use_ui_session=True)
    if 'ACCESS_BALANCE' in res:
        emit('balance_update', {'balance': res.split(':')[-1]})
    else:
        emit('error_msg', {'message': f"Saldo Error: {res[:40]}"})

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
    # 40 Workers - Jauh lebih stabil untuk menghindari IP Block/Pool Error
    NUM_WORKERS = 40 

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
                    socketio.sleep(0.001)
                elif 'NO_BALANCE' in res:
                    autobuy_active[key] = False
                    socketio.emit('error_msg', {'message': '\U0001f4b8 SALDO HABIS!'}, room=key)
                    break
                elif 'NO_NUMBERS' in res:
                    socketio.sleep(0.01) # Ultra fast
                elif 'ERR_HTTP' in res or 'ERROR' in res:
                    socketio.sleep(0.1) 
                else:
                    socketio.sleep(0.01)
            except:
                socketio.sleep(0.1)

    def run():
        shared = {'att': 0, 'found': 0}
        st = time.time()
        socketio.emit('autobuy_started', {'country_name': cnt['name']}, room=key)
        workers = []
        for wid in range(NUM_WORKERS):
            if not autobuy_active.get(key): break
            socketio.start_background_task(single_worker, wid, shared)
            # Staggered start: Jeda 0.05 detik antar worker agar tidak banjir koneksi di awal
            socketio.sleep(0.05) 
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

@socketio.on('cancel_all')
def on_cancel_all(data):
    key, ids = data.get('api_key'), data.get('ids', [])
    def run_cancel():
        for aid in ids:
            api_req(key, 'setStatus', status='8', id=aid)
            socketio.emit('order_update', {'id': aid, 'status': 'cancelled'}, room=key)
            socketio.sleep(0.1) # Jeda sedikit biar gak kena rate limit
    socketio.start_background_task(run_cancel)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)
