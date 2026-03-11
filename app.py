import eventlet
eventlet.monkey_patch()

import os
import sys
import time
import json
import string
import random
import requests
import threading
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO, emit, join_room

# INIT
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get("SECRET_KEY", "herosms_ultimate_v13_secret")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# CONFIG
API_BASE = "https://hero-sms.com/stubs/handler_api.php"
MASTER_PASS = str(os.environ.get("ACCESS_PASSWORD", "admin123")).strip()
ADMIN_SECRET = str(os.environ.get("ADMIN_SECRET", "panel8899")).strip()
CODES_FILE = "codes.json"

print(f"\n[BOOT] HERO-SMS WEB V15 (SYNC FIX) ONLINE")
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
# THREAD-SAFE KODE AKSES MANAGER
# =============================================
_codes_lock = threading.Lock()

def load_codes():
    """Thread-safe load codes dari file."""
    with _codes_lock:
        if os.path.exists(CODES_FILE):
            try:
                with open(CODES_FILE, 'r') as f:
                    data = json.load(f)
                    print(f"[CODES] Loaded {len(data)} codes from {CODES_FILE}")
                    sys.stdout.flush()
                    return data
            except json.JSONDecodeError as e:
                print(f"[ERROR] codes.json corrupt: {e}")
                sys.stdout.flush()
            except Exception as e:
                print(f"[ERROR] Gagal baca codes.json: {e}")
                sys.stdout.flush()
        else:
            print(f"[CODES] {CODES_FILE} belum ada, return empty dict")
            sys.stdout.flush()
        return {}

def save_codes(codes):
    """Thread-safe save codes ke file."""
    with _codes_lock:
        try:
            # Write to temp file first, then rename (atomic write)
            tmp_file = CODES_FILE + ".tmp"
            with open(tmp_file, 'w') as f:
                json.dump(codes, f, indent=2)
            # Atomic rename
            if os.path.exists(CODES_FILE):
                os.remove(CODES_FILE)
            os.rename(tmp_file, CODES_FILE)
            print(f"[CODES] Saved {len(codes)} codes to {CODES_FILE}")
            sys.stdout.flush()
            return True
        except Exception as e:
            print(f"[ERROR] Gagal simpan codes.json: {e}")
            sys.stdout.flush()
            return False

def update_code_status(code, new_status, extra_fields=None):
    """Thread-safe update status satu kode. Returns True jika berhasil."""
    codes = load_codes()
    if code not in codes:
        return False, "Code not found"
    codes[code]['status'] = new_status
    if extra_fields:
        codes[code].update(extra_fields)
    if save_codes(codes):
        return True, "OK"
    return False, "Save failed"

def generate_code():
    chars = string.ascii_uppercase + string.digits
    part1 = ''.join(random.choices(chars, k=4))
    part2 = ''.join(random.choices(chars, k=4))
    return f"HERO-{part1}-{part2}"

def api_req(key, action, **kwargs):
    if not key: return "ERR_NO_KEY"
    p = {'api_key': key, 'action': action}
    # Filter out None values to avoid sending "None" as string
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
# LOGIN VIA HTTP (BUKAN SOCKETIO!)
# =============================================
@app.route('/')
def home():
    if session.get('authenticated'):
        return render_template('index.html', countries=COUNTRIES, logged_in=True)
    return render_template('index.html', countries=COUNTRIES, logged_in=False)

@app.route('/login', methods=['POST'])
def login():
    code = request.form.get('code', '').strip().upper()
    print(f"[AUTH] Login attempt: '{code}'")
    sys.stdout.flush()
    
    # SELALU baca fresh dari file - ini fix utama sinkronisasi
    codes = load_codes()
    print(f"[AUTH] Fresh load: {len(codes)} codes | Keys: {list(codes.keys())}")
    sys.stdout.flush()
    
    if code not in codes:
        print(f"[AUTH] ❌ Code '{code}' TIDAK DITEMUKAN di codes.json!")
        print(f"[AUTH] Available codes: {list(codes.keys())}")
        sys.stdout.flush()
        return jsonify({'success': False, 'error': 'Kode tidak valid'})
    
    code_info = codes[code]
    
    if code_info['status'] == 'available':
        # Kode baru, tandai sebagai digunakan
        success, msg = update_code_status(code, 'used', {
            'used_at': time.time(),
            'used_str': time.strftime('%Y-%m-%d %H:%M:%S')
        })
        if success:
            session['authenticated'] = True
            session['access_code'] = code
            print(f"[AUTH] ✅ Code {code} PERTAMA KALI DIPAKAI - BERHASIL!")
            sys.stdout.flush()
            return jsonify({'success': True})
        else:
            print(f"[AUTH] ❌ Code {code} gagal disimpan: {msg}")
            sys.stdout.flush()
            return jsonify({'success': False, 'error': 'Server error, coba lagi'})
    
    elif code_info['status'] == 'used':
        # Kode sudah terpakai - izinkan re-login
        session['authenticated'] = True
        session['access_code'] = code
        print(f"[AUTH] ✅ Code {code} RE-LOGIN OK!")
        sys.stdout.flush()
        return jsonify({'success': True})
    
    print(f"[AUTH] ❌ Code {code} status unknown: {code_info.get('status')}")
    sys.stdout.flush()
    return jsonify({'success': False, 'error': 'Kode tidak valid atau sudah dipakai orang lain'})

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
    # SELALU baca fresh dari file
    codes = load_codes()
    print(f"[ADMIN] List codes: {len(codes)} total")
    sys.stdout.flush()
    return jsonify(codes)

@app.route(f'/api/admin/{ADMIN_SECRET}/generate', methods=['POST'])
def admin_generate():
    data = request.get_json()
    if not data or data.get('password') != MASTER_PASS:
        return jsonify({'error': 'unauthorized'}), 401
    count = min(int(data.get('count', 1)), 50)
    
    # SELALU baca fresh dari file sebelum generate
    codes = load_codes()
    new_codes = []
    for _ in range(count):
        code = generate_code()
        while code in codes:
            code = generate_code()
        codes[code] = {
            'status': 'available',
            'created': time.time(),
            'created_str': time.strftime('%Y-%m-%d %H:%M:%S')
        }
        new_codes.append(code)
    
    if save_codes(codes):
        print(f"[ADMIN] ✅ Generated {len(new_codes)} codes. Total: {len(codes)}")
        sys.stdout.flush()
        
        # Verifikasi: baca ulang file untuk memastikan tersimpan
        verify = load_codes()
        saved_count = sum(1 for c in new_codes if c in verify)
        print(f"[ADMIN] ✅ Verifikasi: {saved_count}/{len(new_codes)} kode terkonfirmasi di file")
        sys.stdout.flush()
        
        return jsonify({'codes': new_codes, 'total': len(codes)})
    else:
        print(f"[ADMIN] ❌ GAGAL simpan kode!")
        sys.stdout.flush()
        return jsonify({'error': 'Failed to save codes'}), 500

@app.route(f'/api/admin/{ADMIN_SECRET}/delete', methods=['POST'])
def admin_delete():
    data = request.get_json()
    if not data or data.get('password') != MASTER_PASS:
        return jsonify({'error': 'unauthorized'}), 401
    code = data.get('code')
    
    # SELALU baca fresh dari file
    codes = load_codes()
    if code in codes:
        del codes[code]
        if save_codes(codes):
            print(f"[ADMIN] ✅ Deleted code: {code}")
            sys.stdout.flush()
            return jsonify({'success': True})
        else:
            return jsonify({'error': 'Failed to save'}), 500
    return jsonify({'error': 'not found'}), 404

# =============================================
# SOCKET EVENTS (TANPA AUTH - auth sudah via HTTP)
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

# =============================================
# DEBUG ENDPOINT - untuk cek sinkronisasi
# =============================================
@app.route(f'/api/admin/{ADMIN_SECRET}/debug', methods=['GET'])
def admin_debug():
    pw = request.args.get('pw', '')
    if pw != MASTER_PASS:
        return jsonify({'error': 'unauthorized'}), 401
    
    codes = load_codes()
    file_exists = os.path.exists(CODES_FILE)
    file_size = os.path.getsize(CODES_FILE) if file_exists else 0
    
    available = sum(1 for c in codes.values() if c.get('status') == 'available')
    used = sum(1 for c in codes.values() if c.get('status') == 'used')
    
    return jsonify({
        'file_exists': file_exists,
        'file_size': file_size,
        'total_codes': len(codes),
        'available': available,
        'used': used,
        'codes_list': list(codes.keys()),
        'server_time': time.strftime('%Y-%m-%d %H:%M:%S'),
        'cwd': os.getcwd(),
        'codes_file_path': os.path.abspath(CODES_FILE)
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)
