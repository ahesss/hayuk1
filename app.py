from flask import Flask, render_template, request, session, jsonify
from flask_socketio import SocketIO, emit
import requests as http_requests
import threading
import time
import json
import os

# =============================================
# KONFIGURASI
# =============================================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24).hex())
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet', ping_timeout=120, ping_interval=25)

API_BASE = "https://hero-sms.com/stubs/handler_api.php"
SERVICE = "wa"
ACCESS_PASSWORD = os.environ.get("ACCESS_PASSWORD", "admin123")
OTP_TIMEOUT = 1200  # 20 menit

COUNTRIES = {
    "vietnam": {"name": "Vietnam", "flag": "\ud83c\uddfb\ud83c\uddf3", "country_id": "10", "country_code": "84", "maxPrice": "0.25"},
    "philipina": {"name": "Philipina", "flag": "\ud83c\uddf5\ud83c\udded", "country_id": "3", "country_code": "63", "maxPrice": "0.25"},
    "colombia": {"name": "Colombia", "flag": "\ud83c\udde8\ud83c\uddf4", "country_id": "33", "country_code": "57"},
    "mexico": {"name": "Mexico", "flag": "\ud83c\uddf2\ud83c\uddfd", "country_id": "54", "country_code": "52"},
    "brazil": {"name": "Brazil", "flag": "\ud83c\udde7\ud83c\uddf7", "country_id": "73", "country_code": "55", "maxPrice": "1.50"},
}

# Per-session storage
user_sessions = {}  # sid -> {api_key, autobuy_active, autobuy_country}

# =============================================
# API HELPER
# =============================================
def req_api(api_key, action, **kwargs):
    params = {'api_key': api_key, 'action': action}
    params.update(kwargs)
    try:
        r = http_requests.get(API_BASE, params=params, timeout=12)
        return r.text.strip()
    except Exception as e:
        return f"ERROR: {str(e)}"

def get_balance(api_key):
    res = req_api(api_key, 'getBalance')
    if 'ACCESS_BALANCE' in res:
        return res.split(':')[1]
    return None

def fetch_price_by_activation(api_key, activation_id):
    try:
        res = req_api(api_key, 'getActiveActivations')
        if res.startswith("{"):
            d = json.loads(res)
            activations = d.get('activeActivations', d)
            if isinstance(activations, dict):
                act = activations.get(str(activation_id))
                if act and isinstance(act, dict):
                    cost = act.get('activationCost') or act.get('cost') or act.get('sum')
                    if cost:
                        return float(cost)
    except:
        pass
    return None

# =============================================
# ROUTES
# =============================================
@app.route('/')
def index():
    return render_template('index.html', countries=COUNTRIES)

# =============================================
# SOCKET.IO EVENTS
# =============================================
@socketio.on('connect')
def handle_connect():
    user_sessions[request.sid] = {
        'api_key': None,
        'autobuy_active': False,
        'autobuy_country': None,
    }

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    if sid in user_sessions:
        user_sessions[sid]['autobuy_active'] = False
        del user_sessions[sid]

@socketio.on('login')
def handle_login(data):
    password = data.get('password', '')
    if password != ACCESS_PASSWORD:
        emit('login_result', {'success': False, 'message': 'Password salah!'})
        return
    emit('login_result', {'success': True})

@socketio.on('set_api_key')
def handle_set_api_key(data):
    sid = request.sid
    api_key = data.get('api_key', '').strip()
    if sid in user_sessions:
        user_sessions[sid]['api_key'] = api_key
        bal = get_balance(api_key)
        if bal:
            emit('api_result', {'success': True, 'balance': bal})
        else:
            emit('api_result', {'success': False, 'message': 'API key tidak valid atau saldo 0'})

@socketio.on('get_balance')
def handle_get_balance():
    sid = request.sid
    if sid not in user_sessions or not user_sessions[sid]['api_key']:
        return
    bal = get_balance(user_sessions[sid]['api_key'])
    if bal:
        emit('balance_update', {'balance': bal})

@socketio.on('buy_number')
def handle_buy_number(data):
    sid = request.sid
    if sid not in user_sessions or not user_sessions[sid]['api_key']:
        emit('error_msg', {'message': 'Set API key dulu!'})
        return
    country_key = data.get('country', 'vietnam')
    count = min(int(data.get('count', 1)), 20)
    api_key = user_sessions[sid]['api_key']
    threading.Thread(target=buy_worker, args=(sid, api_key, country_key, count), daemon=True).start()

def buy_worker(sid, api_key, country_key, count):
    cntry = COUNTRIES.get(country_key)
    if not cntry:
        socketio.emit('error_msg', {'message': f'Negara tidak ditemukan'}, room=sid)
        return

    socketio.emit('buy_status', {'message': f'Membeli {count} nomor {cntry["name"]}...'}, room=sid)
    orders = []
    max_retries = count * 5
    attempts = 0

    while len(orders) < count and attempts < max_retries:
        if sid not in user_sessions:
            break
        attempts += 1
        kwargs = {'service': SERVICE, 'country': cntry['country_id']}
        if 'maxPrice' in cntry:
            kwargs['maxPrice'] = cntry['maxPrice']

        res = req_api(api_key, 'getNumber', **kwargs)

        if 'ACCESS_NUMBER' in res:
            p = res.split(':')
            act_id = p[1]
            number = p[2]
            pr = fetch_price_by_activation(api_key, act_id)
            order = {
                'id': act_id,
                'number': number,
                'country': country_key,
                'country_name': cntry['name'],
                'country_code': cntry['country_code'],
                'status': 'waiting',
                'order_time': time.time(),
                'price': pr,
                'index': len(orders) + 1
            }
            orders.append(order)
            socketio.emit('new_number', order, room=sid)
            threading.Thread(target=otp_checker, args=(sid, api_key, order), daemon=True).start()
            time.sleep(0.3)
        elif res == 'NO_BALANCE':
            socketio.emit('error_msg', {'message': 'Saldo habis!'}, room=sid)
            break
        elif res == 'NO_NUMBERS':
            if not orders and attempts >= 5:
                socketio.emit('error_msg', {'message': f'Tidak ada nomor {cntry["name"]} tersedia'}, room=sid)
                break
            time.sleep(0.1)
        else:
            time.sleep(0.2)

    socketio.emit('buy_complete', {'count': len(orders), 'country': cntry['name']}, room=sid)
    bal = get_balance(api_key)
    if bal:
        socketio.emit('balance_update', {'balance': bal}, room=sid)

def otp_checker(sid, api_key, order):
    while True:
        if sid not in user_sessions:
            break
        now = time.time()
        elapsed = now - order['order_time']

        if elapsed > OTP_TIMEOUT:
            order['status'] = 'timeout'
            req_api(api_key, 'setStatus', status='8', id=order['id'])
            socketio.emit('order_update', order, room=sid)
            break

        res = req_api(api_key, 'getStatus', id=order['id'])

        if res.startswith('STATUS_OK'):
            code = res.split(':')[1] if ':' in res else '???'
            order['status'] = 'got_otp'
            order['code'] = code
            req_api(api_key, 'setStatus', status='6', id=order['id'])
            socketio.emit('order_update', order, room=sid)
            break
        elif res == 'STATUS_CANCEL':
            order['status'] = 'cancelled'
            socketio.emit('order_update', order, room=sid)
            break

        time.sleep(3)

@socketio.on('cancel_order')
def handle_cancel(data):
    sid = request.sid
    if sid not in user_sessions or not user_sessions[sid]['api_key']:
        return
    act_id = data.get('id')
    if act_id:
        req_api(user_sessions[sid]['api_key'], 'setStatus', status='8', id=act_id)
        socketio.emit('order_update', {'id': act_id, 'status': 'cancelled'}, room=sid)

# =============================================
# AUTO BUY ULTRA BRUTAL (NO TELEGRAM LIMIT!)
# =============================================
@socketio.on('start_autobuy')
def handle_start_autobuy(data):
    sid = request.sid
    if sid not in user_sessions or not user_sessions[sid]['api_key']:
        emit('error_msg', {'message': 'Set API key dulu!'})
        return
    country_key = data.get('country', 'vietnam')
    user_sessions[sid]['autobuy_active'] = True
    user_sessions[sid]['autobuy_country'] = country_key
    threading.Thread(target=autobuy_worker, args=(sid, user_sessions[sid]['api_key'], country_key), daemon=True).start()

@socketio.on('stop_autobuy')
def handle_stop_autobuy():
    sid = request.sid
    if sid in user_sessions:
        user_sessions[sid]['autobuy_active'] = False

def autobuy_worker(sid, api_key, country_key):
    cntry = COUNTRIES.get(country_key)
    if not cntry:
        return

    att = 0
    count = 0
    st_time = time.time()
    no_number_streak = 0
    last_stats = 0

    socketio.emit('autobuy_started', {
        'country': country_key,
        'country_name': cntry['name'],
        'maxPrice': cntry.get('maxPrice', 'N/A')
    }, room=sid)

    while sid in user_sessions and user_sessions[sid].get('autobuy_active') and user_sessions[sid].get('autobuy_country') == country_key:
        att += 1
        now = time.time()

        # Update stats setiap 1 detik (BISA karena TANPA Telegram limit!)
        if now - last_stats >= 1:
            el = int(now - st_time)
            speed = att / max(el, 1)
            socketio.emit('autobuy_stats', {
                'attempts': att,
                'found': count,
                'elapsed': el,
                'speed': round(speed, 1),
                'streak': no_number_streak,
                'country': country_key,
                'country_name': cntry['name']
            }, room=sid)
            last_stats = now

        kwargs = {'service': SERVICE, 'country': cntry['country_id']}
        if 'maxPrice' in cntry:
            kwargs['maxPrice'] = cntry['maxPrice']

        res = req_api(api_key, 'getNumber', **kwargs)

        if 'ACCESS_NUMBER' in res:
            no_number_streak = 0
            p = res.split(':')
            act_id = p[1]
            number = p[2]
            pr = fetch_price_by_activation(api_key, act_id)
            count += 1

            order = {
                'id': act_id,
                'number': number,
                'country': country_key,
                'country_name': cntry['name'],
                'country_code': cntry['country_code'],
                'status': 'waiting',
                'order_time': time.time(),
                'price': pr,
                'index': count
            }

            socketio.emit('new_number', order, room=sid)
            threading.Thread(target=otp_checker, args=(sid, api_key, order), daemon=True).start()
            time.sleep(0.3)

        elif res == 'NO_BALANCE':
            socketio.emit('autobuy_stopped', {'reason': 'Saldo habis!', 'total': count, 'attempts': att}, room=sid)
            break

        elif res == 'NO_NUMBERS':
            no_number_streak += 1
            # ULTRA BRUTAL - jauh lebih cepat dari Telegram bot
            if no_number_streak > 100:
                time.sleep(0.08)
            elif no_number_streak > 50:
                time.sleep(0.05)
            else:
                time.sleep(0.02)  # 50x per detik!

        else:
            no_number_streak = 0
            time.sleep(0.05)

    if sid in user_sessions:
        user_sessions[sid]['autobuy_active'] = False

    el = int(time.time() - st_time)
    socketio.emit('autobuy_stopped', {
        'reason': 'Dihentikan',
        'total': count,
        'attempts': att,
        'elapsed': el
    }, room=sid)

    bal = get_balance(api_key)
    if bal:
        socketio.emit('balance_update', {'balance': bal}, room=sid)

# =============================================
# RUN
# =============================================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)
