import sqlite3, json, hashlib, random, requests
from flask import Flask, request, jsonify, render_template
from flask_socketio import SocketIO, emit, join_room
from werkzeug.security import generate_password_hash, check_password_hash
from cryptography.fernet import Fernet

app = Flask(__name__)
# 20MB limit for secure file handling
socketio = SocketIO(app, cors_allowed_origins="*", max_http_buffer_size=20971520)
DATABASE = 'secure_chat.db'

# --- CYBERSECURITY CONFIGURATION ---
MASTER_KEY = b'6_Wf8X8pXQ5G_hY8K-Z3p8j5B5u9V-0W8X8pXQ5G_hY=' 
cipher_suite = Fernet(MASTER_KEY)

otp_storage = {} 

def encrypt_phone(phone):
    return cipher_suite.encrypt(str(phone).encode()).decode()

def decrypt_phone(cipher_text):
    return cipher_suite.decrypt(cipher_text.encode()).decode()

def hash_id(phone):
    return hashlib.sha256((str(phone) + "SECRET_PEPPER").encode()).hexdigest()

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

# Database Initialization
with get_db() as db:
    db.execute('CREATE TABLE IF NOT EXISTS users (hashed_id TEXT PRIMARY KEY, phone_enc TEXT, username TEXT, password TEXT, publicKey TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, sender_hash TEXT, recipient_hash TEXT, payload TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)')
    db.execute('CREATE TABLE IF NOT EXISTS blocks (blocker_hash TEXT, blocked_hash TEXT, PRIMARY KEY (blocker_hash, blocked_hash))')
    db.execute('CREATE TABLE IF NOT EXISTS admins (admin_id TEXT PRIMARY KEY, password TEXT)')

@app.route('/')
def index(): return render_template('index.html')

@app.route('/admin_panel')
def admin_page(): return render_template('admin.html')

def log_to_admin(event_type, event_name, details, payload=None):
    socketio.emit('admin_log', {
        "type": event_type,
        "event": event_name,
        "details": details,
        "payload": payload
    }, room='admin_room')

# ====================== USER AUTH ROUTES ======================

@app.route('/signup', methods=['POST'])
def signup():
    data = request.get_json()
    raw_phone = data['phone']
    h_id = hash_id(raw_phone)
    enc_phone = encrypt_phone(raw_phone)
    hashed_pw = generate_password_hash(data['password'])
    db = get_db()
    try:
        db.execute('INSERT INTO users VALUES (?, ?, ?, ?, ?)', (h_id, enc_phone, data['username'], hashed_pw, data['publicKey']))
        db.commit()
        socketio.emit('update_user_list', get_user_list_data())
        log_to_admin("AUTH", "New User Registered", f"Phone: {raw_phone} | Username: {data['username']}")
        return jsonify({"message": "Success", "username": data['username']})
    except: return jsonify({"message": "Error: Account already exists"}), 400

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    h_id = hash_id(data['phone'])
    user = get_db().execute('SELECT * FROM users WHERE hashed_id = ?', (h_id,)).fetchone()
    if user and check_password_hash(user['password'], data['password']):
         log_to_admin("AUTH", "User Login", f"Phone: {data['phone']} authenticated.")
         return jsonify({"message": "Success", "username": user['username']})
    return jsonify({"message": "Check phone number and password"}), 401

@app.route('/list_users')
def list_users(): return jsonify({"users": get_user_list_data()})

def get_user_list_data():
    rows = get_db().execute('SELECT phone_enc, username, hashed_id FROM users').fetchall()
    return [{"phone": decrypt_phone(r['phone_enc']), "username": r['username']} for r in rows]

@app.route('/get_key/<phone>')
def get_key(phone):
    h_id = hash_id(phone)
    user = get_db().execute('SELECT publicKey FROM users WHERE hashed_id = ?', (h_id,)).fetchone()
    if user: return jsonify({"publicKey": user['publicKey']})
    return jsonify({"message": "Error"}), 404

@app.route('/fetch_messages/<phone>')
def fetch_messages(phone):
    h_id = hash_id(phone)
    db = get_db()
    rows = db.execute('SELECT * FROM messages WHERE recipient_hash = ? OR sender_hash = ? ORDER BY timestamp ASC', (h_id, h_id)).fetchall()
    output = []
    for r in rows:
        sender_user = db.execute('SELECT phone_enc FROM users WHERE hashed_id = ?', (r['sender_hash'],)).fetchone()
        if sender_user:
            output.append({"sender": decrypt_phone(sender_user['phone_enc']), "recipient": phone, "payload": json.loads(r['payload'])})
    return jsonify({"messages": output})

@app.route('/update_profile', methods=['POST'])
def update_profile():
    data = request.get_json()
    old_p, new_p, name = data.get('oldPhone'), data.get('newPhone'), data.get('username')
    old_pw_input, new_pw_input = data.get('oldPassword'), data.get('newPassword')
    db = get_db()
    old_h_id = hash_id(old_p); new_h_id = hash_id(new_p); new_enc_phone = encrypt_phone(new_p)
    user = db.execute('SELECT * FROM users WHERE hashed_id = ?', (old_h_id,)).fetchone()
    if not user: return jsonify({"message": "User not found"}), 404
    final_pw = user['password']
    if old_pw_input and new_pw_input:
        if not check_password_hash(user['password'], old_pw_input): return jsonify({"message": "Current password incorrect"}), 403
        final_pw = generate_password_hash(new_pw_input)
    try:
        db.execute('UPDATE users SET hashed_id=?, phone_enc=?, username=?, password=? WHERE hashed_id=?', (new_h_id, new_enc_phone, name, final_pw, old_h_id))
        db.execute('UPDATE messages SET sender_hash=? WHERE sender_hash=?', (new_h_id, old_h_id))
        db.execute('UPDATE messages SET recipient_hash=? WHERE recipient_hash=?', (new_h_id, old_h_id))
        db.commit()
        socketio.emit('user_updated', {"oldPhone": old_p, "newPhone": new_p, "username": name})
        socketio.emit('update_user_list', get_user_list_data())
        log_to_admin("AUTH", "Profile Updated", f"User {old_p} changed details.")
        return jsonify({"message": "Success"})
    except: return jsonify({"message": "Phone already taken"}), 400

@app.route('/deactivate_account', methods=['POST'])
def deactivate_account():
    p = request.get_json().get('phone'); h_id = hash_id(p); db = get_db()
    db.execute('DELETE FROM users WHERE hashed_id=?', (h_id,))
    db.execute('DELETE FROM messages WHERE sender_hash=? OR recipient_hash=?', (h_id, h_id))
    db.commit()
    socketio.emit('update_user_list', get_user_list_data())
    log_to_admin("AUTH", "Account Deleted", f"User {p} removed from system.")
    return jsonify({"message": "Deleted"})

@app.route('/toggle_block', methods=['POST'])
def toggle_block():
    data = request.get_json(); my_h = hash_id(data.get('myPhone')); target_h = hash_id(data.get('targetPhone')); db = get_db()
    blocked = db.execute('SELECT 1 FROM blocks WHERE blocker_hash=? AND blocked_hash=?', (my_h, target_h)).fetchone()
    if blocked: 
        db.execute('DELETE FROM blocks WHERE blocker_hash=? AND blocked_hash=?', (my_h, target_h))
        status = "unblocked"
    else: 
        db.execute('INSERT INTO blocks VALUES (?, ?)', (my_h, target_h))
        status = "blocked"
    db.commit()
    log_to_admin("AUTH", f"User {status.upper()}", f"{data.get('myPhone')} {status} {data.get('targetPhone')}")
    return jsonify({"status": status})

@app.route('/get_blocks/<phone>')
def get_blocks(phone):
    h_id = hash_id(phone); rows = get_db().execute('SELECT blocked_hash FROM blocks WHERE blocker_hash=?', (h_id,)).fetchall(); db = get_db(); phones = []
    for r in rows:
        u = db.execute('SELECT phone_enc FROM users WHERE hashed_id=?', (r['blocked_hash'],)).fetchone()
        if u: phones.append(decrypt_phone(u['phone_enc']))
    return jsonify({"blockedUsers": phones})

# ====================== ADMIN AUTH ROUTES ======================

@app.route('/admin_register', methods=['POST'])
def admin_register():
    data = request.get_json()
    admin_id = data.get('id')
    password = data.get('password')
    
    if not admin_id or not password:
        return jsonify({"message": "ID and Password are required"}), 400

    hashed_pw = generate_password_hash(password)
    db = get_db()
    try:
        db.execute('INSERT INTO admins (admin_id, password) VALUES (?, ?)', (admin_id, hashed_pw))
        db.commit()
        return jsonify({"message": "Admin created successfully"})
    except Exception as e:
        # IntegrityError happens if admin_id already exists
        return jsonify({"message": "Admin ID already exists"}), 400

@app.route('/admin_login', methods=['POST'])
def admin_auth():
    data = request.get_json()
    admin_id = data.get('id')
    password = data.get('password')
    
    db = get_db()
    admin = db.execute('SELECT * FROM admins WHERE admin_id = ?', (admin_id,)).fetchone()
    
    # Check if admin exists and verify the hashed password
    if admin and check_password_hash(admin['password'], password):
        # We use a success status code 200
        return jsonify({"status": "success", "message": "Welcome back Commander"}), 200
    
    return jsonify({"status": "error", "message": "Invalid Admin Account"}), 401

# ====================== FORGOT PASSWORD ROUTES ======================

@app.route('/forgot_password_send_otp', methods=['POST'])
def send_otp():
    phone = request.get_json().get('phone'); h_id = hash_id(phone); db = get_db()
    user = db.execute('SELECT 1 FROM users WHERE hashed_id=?', (h_id,)).fetchone()
    if not user: return jsonify({"message": "Phone not registered."}), 404
    otp = str(random.randint(100000, 999999)); otp_storage[h_id] = otp
    print("\n" + "⚡"*20 + f"\n🔑 [OTP] PHONE: {phone} | CODE: {otp}\n" + "⚡"*20 + "\n")
    log_to_admin("AUTH", "OTP Generated", f"Reset code created for {phone}")
    return jsonify({"message": "Check Console/SMS"})

@app.route('/forgot_password_verify_otp', methods=['POST'])
def verify_otp():
    data = request.get_json(); h_id = hash_id(data.get('phone'))
    if otp_storage.get(h_id) == str(data.get('otp')): return jsonify({"message": "Verified"})
    return jsonify({"message": "Invalid OTP"}), 400

@app.route('/forgot_password_reset', methods=['POST'])
def forgot_password_reset():
    data = request.get_json(); h_id = hash_id(data.get('phone'))
    if otp_storage.get(h_id) != str(data.get('otp')): return jsonify({"message": "Error"}), 403
    db = get_db(); hashed_pw = generate_password_hash(data.get('newPassword'))
    db.execute('UPDATE users SET password=? WHERE hashed_id=?', (hashed_pw, h_id))
    db.commit()
    if h_id in otp_storage: del otp_storage[h_id]
    log_to_admin("AUTH", "Password Reset", f"Account recovered for ID: {data.get('phone')}")
    return jsonify({"message": "Success"})

# ====================== SOCKETS ======================

@socketio.on('join')
def on_join(phone): join_room(hash_id(str(phone)))

@socketio.on('join_admin')
def on_join_admin():
    join_room('admin_room')
    print("🛡️  Admin connected to System Monitor")

@socketio.on('chat_message')
def handle_chat_message(data):
    s_phone = str(data.get('sender')); r_phone = str(data.get('recipient'))
    s_hash = hash_id(s_phone); r_hash = hash_id(r_phone); db = get_db()
    
    # Bi-directional block check
    b_rec = db.execute('SELECT 1 FROM blocks WHERE blocker_hash=? AND blocked_hash=?', (r_hash, s_hash)).fetchone()
    b_sen = db.execute('SELECT 1 FROM blocks WHERE blocker_hash=? AND blocked_hash=?', (s_hash, r_hash)).fetchone()
    if b_rec or b_sen: return 

    # Save to Database
    db.execute('INSERT INTO messages (sender_hash, recipient_hash, payload) VALUES (?, ?, ?)', (s_hash, r_hash, json.dumps(data['payload'])))
    db.commit()
    
    # Admin Log & Terminal Proof
    payload = data.get('payload', {})

    log_to_admin("MSG", "Encrypted Relay", {
    "from": s_phone,
    "to": r_phone,
    "mediaType": payload.get("mediaType", "text").upper(),
    "ciphertext": payload.get("data", ""),
    "wrappedKey": payload.get("aesKey", "")
})

    print("\n" + "═"*60)
    print("🔒 [E2EE PROTOCOL] SECURE MESSAGE RELAY")
    print(f"   DIRECTION    : {s_phone}  >>>  {r_phone}")
    print(f"   MEDIA TYPE   : {payload.get('mediaType', 'text').upper()}")
    print("-" * 60)
    print(f"   CIPHERTEXT   : {payload.get('data', '')[:100]}...")
    print(f"   WRAPPED KEY  : {payload.get('aesKey', '')[:100]}...")
    print("═"*60 + "\n")

    emit('receive_message', data, room=r_hash)

@socketio.on('edit_message')
def handle_edit(data):
    db = get_db(); r_hash = hash_id(data['recipient'])
    db.execute('UPDATE messages SET payload=? WHERE sender_hash=? AND payload LIKE ?', (json.dumps(data['newPayload']), hash_id(data['sender']), f'%"{data["msgId"]}"%'))
    db.commit()
    emit('message_edited', data, room=r_hash)

@socketio.on('delete_message')
def handle_delete(data):
    db = get_db(); msg_id = data['msgId']; r_hash = hash_id(data['recipient'])
    if data.get('mode') == "everyone":
        marker = {"isDeleted": True, "msgId": msg_id}
        db.execute('UPDATE messages SET payload=? WHERE payload LIKE ?', (json.dumps(marker), f'%"msgId": "{msg_id}"%'))
        db.commit()
        emit('message_deleted', {"msgId": msg_id, "sender": data['sender']}, room=r_hash)
    else:
        db.execute('DELETE FROM messages WHERE sender_hash=? AND payload LIKE ?', (hash_id(data['sender']), f'%"msgId": "{msg_id}"%'))
        db.commit()

if __name__ == '__main__':
    socketio.run(app, debug=True)