from flask import Blueprint, request, jsonify, session
from supabase import create_client
import os, random, smtplib, secrets, json
from dotenv import load_dotenv
from datetime import datetime, timedelta
from email.mime.text import MIMEText

load_dotenv()

api_bp = Blueprint('api', __name__, url_prefix='/api')

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

_tokens = {}  # token -> {'email': ..., 'role': ...}
_otp_store = {}  # email -> {'otp': ..., 'role': ..., 'expiry': ...}
_TOKENS_FILE = os.path.join(os.path.dirname(__file__), '.tokens.json')

def _load_tokens():
    if os.path.exists(_TOKENS_FILE):
        try:
            with open(_TOKENS_FILE) as f:
                _tokens.update(json.load(f))
        except:
            pass

def _save_tokens():
    with open(_TOKENS_FILE, 'w') as f:
        json.dump(_tokens, f)

_load_tokens()

def get_current_user():
    token = request.headers.get('X-Auth-Token')
    if token and token in _tokens:
        return _tokens[token]
    return None

def mask_name(full_name):
    parts = full_name.split()
    if len(parts) == 1:
        return parts[0][:2] + "***"
    first = parts[0][:2] + "***"
    last = parts[1][0] + "**"
    return f"{first} {last}"

def log_activity(user_email, action, case_id=None):
    supabase.table("activity_logs").insert({
        "user_email": user_email, "action": action, "case_id": case_id
    }).execute()

def send_otp_email(to_email, otp):
    msg = MIMEText(f"Your OTP login code is:\n\n    {otp}\n\nExpires in 5 minutes.\n\n— Prosecutor's Office Case Tracking System")
    msg['Subject'] = 'Your OTP Login Code'
    msg['From'] = os.getenv('MAIL_EMAIL')
    msg['To'] = to_email
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(os.getenv('MAIL_EMAIL'), os.getenv('MAIL_PASSWORD'))
        smtp.send_message(msg)

# ----------------------------
# Auth
# ----------------------------

@api_bp.route('/login', methods=['POST'])
def api_login():
    data = request.json
    email = data.get('email', '')
    password = data.get('password', '')

    response = supabase.table("users").select("*").eq("email", email).execute()
    if not response.data:
        return jsonify({"error": "Email not found"}), 401
    user = response.data[0]
    if user['password'] != password:
        return jsonify({"error": "Incorrect password"}), 401

    otp = str(random.randint(100000, 999999))
    _otp_store[email] = {
        'otp': otp,
        'role': user['role'],
        'expiry': (datetime.now() + timedelta(minutes=5)).isoformat()
    }
    send_otp_email(email, otp)
    return jsonify({"message": "OTP sent", "email": email})

@api_bp.route('/verify-otp', methods=['POST'])
def api_verify_otp():
    data = request.json
    entered = data.get('otp', '').strip()
    email = data.get('email', '').strip()

    if email not in _otp_store:
        return jsonify({"error": "No OTP session"}), 400

    record = _otp_store[email]
    expiry = datetime.fromisoformat(record['expiry'])
    if datetime.now() > expiry:
        del _otp_store[email]
        return jsonify({"error": "OTP expired"}), 401

    if entered != record['otp']:
        return jsonify({"error": "Invalid OTP"}), 401

    role = record['role']
    del _otp_store[email]

    token = secrets.token_hex(32)
    _tokens[token] = {'email': email, 'role': role}
    _save_tokens()
    return jsonify({"message": "Login successful", "email": email, "role": role, "token": token})

@api_bp.route('/logout', methods=['POST'])
def api_logout():
    token = request.headers.get('X-Auth-Token')
    if token and token in _tokens:
        del _tokens[token]
        _save_tokens()
    session.clear()
    return jsonify({"message": "Logged out"})

# ----------------------------
# Cases
# ----------------------------

@api_bp.route('/cases', methods=['GET'])
def api_cases():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    search = request.args.get('search', '')
    case_type = request.args.get('case_type', '')
    query = supabase.table("cases").select("*").order("id", desc=True)

    if case_type:
        query = query.eq("case_type", case_type)
    if search:
        query = query.ilike("case_number", f"%{search}%")

    cases = query.execute().data or []

    for case in cases:
        case["signed_url"] = None
        case["borrow_info"] = None
        if case.get("document_url"):
            try:
                signed = supabase.storage.from_('case-documents').create_signed_url(case['document_url'], 3600)
                case["signed_url"] = signed["signedURL"]
            except:
                pass
        tx = supabase.table("file_transactions").select("*").eq("case_id", case["id"]).eq("action", "borrowed").order("created_at", desc=True).limit(1).execute()
        if tx.data:
            borrow = tx.data[0]
            ret = supabase.table("file_transactions").select("return_date").eq("case_id", case["id"]).eq("action", "returned").order("created_at", desc=True).limit(1).execute()
            borrow["return_date"] = ret.data[0]["return_date"] if ret.data else None
            case["borrow_info"] = borrow

    return jsonify(cases)

@api_bp.route('/cases', methods=['POST'])
def api_add_case():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json
    new_case = {
        "case_number": data['case_number'],
        "case_title": data['case_category'],
        "case_type": data['case_type'],
        "complainant": mask_name(data['complainant']),
        "respondent": mask_name(data['respondent']),
        "status": "Open",
        "uploaded_by": user['email']
    }
    result = supabase.table("cases").insert(new_case).execute()
    if result.data:
        log_activity(user['email'], "Added a case", result.data[0]['id'])
        return jsonify(result.data[0]), 201
    return jsonify({"error": "Failed to add case"}), 500

@api_bp.route('/cases/<case_id>', methods=['PUT'])
def api_edit_case(case_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    response = supabase.table("cases").select("*").eq("id", case_id).execute()
    if not response.data:
        return jsonify({"error": "Case not found"}), 404
    case = response.data[0]
    if user['role'] != 'admin' and case.get('uploaded_by') != user['email']:
        return jsonify({"error": "Access denied"}), 403

    data = request.json
    updated = {
        "case_number": data['case_number'],
        "case_title": data['case_title'],
        "case_type": data['case_type'],
        "complainant": mask_name(data['complainant']),
        "respondent": mask_name(data['respondent']),
        "status": data['status']
    }
    supabase.table("cases").update(updated).eq("id", case_id).execute()
    log_activity(user['email'], "Edited a case", case_id)
    return jsonify({"message": "Case updated"})

@api_bp.route('/cases/<case_id>', methods=['DELETE'])
def api_delete_case(case_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    response = supabase.table("cases").select("*").eq("id", case_id).execute()
    if not response.data:
        return jsonify({"error": "Case not found"}), 404
    case = response.data[0]
    if user['role'] != 'admin' and case.get('uploaded_by') != user['email']:
        return jsonify({"error": "Access denied"}), 403

    if case.get("document_url"):
        supabase.storage.from_('case-documents').remove([case['document_url']])
    supabase.table("cases").delete().eq("id", case_id).execute()
    log_activity(user['email'], "Deleted a case", case_id)
    return jsonify({"message": "Case deleted"})

# ----------------------------
# Upload Document
# ----------------------------

@api_bp.route('/cases/<case_id>/upload', methods=['POST'])
def api_upload_document(case_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    file = request.files.get('document')
    if not file or file.filename == '':
        return jsonify({"error": "No file provided"}), 400

    filename = f"{case_id}_{file.filename}"
    file_bytes = file.read()
    try:
        supabase.storage.from_('case-documents').remove([filename])
    except:
        pass
    supabase.storage.from_('case-documents').upload(filename, file_bytes)
    supabase.table("cases").update({"document_url": filename}).eq("id", case_id).execute()
    log_activity(user['email'], "Uploaded document", case_id)
    return jsonify({"message": "Document uploaded", "filename": filename})

# ----------------------------
# File Transactions
# ----------------------------

@api_bp.route('/cases/<case_id>/borrow', methods=['POST'])
def api_borrow(case_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    borrowed_by = data.get('borrowed_by', user['email'])
    notes = data.get('notes', '')

    supabase.table("cases").update({"file_status": "borrowed"}).eq("id", case_id).execute()
    supabase.table("file_transactions").insert({
        "case_id": case_id, "action": "borrowed",
        "performed_by": borrowed_by, "case_status_after": "Open", "notes": notes
    }).execute()
    log_activity(user['email'], "Borrowed file", case_id)
    return jsonify({"message": "File borrowed"})

@api_bp.route('/cases/<case_id>/return', methods=['POST'])
def api_return(case_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    case_status = data.get('case_status', 'Open')
    notes = data.get('notes', '')
    return_date = datetime.now().isoformat()

    supabase.table("cases").update({"file_status": "in_storage", "status": case_status}).eq("id", case_id).execute()
    supabase.table("file_transactions").insert({
        "case_id": case_id, "action": "returned",
        "performed_by": user['email'], "case_status_after": case_status,
        "notes": notes, "return_date": return_date
    }).execute()
    log_activity(user['email'], "Returned file", case_id)
    return jsonify({"message": "File returned"})

@api_bp.route('/cases/<case_id>/dispose', methods=['POST'])
def api_dispose(case_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    notes = request.json.get('notes', '') if request.json else ''
    supabase.table("cases").update({"file_status": "disposed", "status": "Closed"}).eq("id", case_id).execute()
    supabase.table("file_transactions").insert({
        "case_id": case_id, "action": "disposed",
        "performed_by": user['email'], "case_status_after": "Closed", "notes": notes
    }).execute()
    log_activity(user['email'], "Disposed file", case_id)
    return jsonify({"message": "File disposed"})

@api_bp.route('/cases/<case_id>/undispose', methods=['POST'])
def api_undispose(case_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    supabase.table("cases").update({"file_status": "in_storage", "status": "Open"}).eq("id", case_id).execute()
    log_activity(user['email'], "Undisposed file", case_id)
    return jsonify({"message": "File undisposed"})

# ----------------------------
# Stats
# ----------------------------

@api_bp.route('/stats', methods=['GET'])
def api_stats():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    cases = supabase.table("cases").select("*").execute().data or []
    users = supabase.table("users").select("id").eq("role", "staff").execute().data or []
    return jsonify({
        "total_cases": len(cases),
        "open_cases": sum(1 for c in cases if c.get('status') == 'Open'),
        "pending_cases": sum(1 for c in cases if c.get('status') == 'Pending'),
        "closed_cases": sum(1 for c in cases if c.get('status') == 'Closed'),
        "borrowed_files": sum(1 for c in cases if c.get('file_status') == 'borrowed'),
        "total_users": len(users),
    })

# ----------------------------
# Activity Logs
# ----------------------------

@api_bp.route('/activity-logs', methods=['GET'])
def api_activity_logs():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    logs = supabase.table("activity_logs").select("*").order("created_at", desc=True).execute().data or []
    return jsonify(logs)
