from flask import Blueprint, render_template, request, redirect, session
from supabase import create_client
import os
from dotenv import load_dotenv

load_dotenv()

staff_bp = Blueprint('staff', __name__)

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)

def log_activity(user_email, action, case_id=None):
    supabase.table("activity_logs").insert({
        "user_email": user_email,
        "action": action,
        "case_id": case_id
    }).execute()

# ----------------------------
# Staff Dashboard
# ----------------------------
@staff_bp.route('/staff-cases')
def staff_cases():
    if 'user' not in session or session.get('role') != 'staff':
        return redirect('/login')

    search = request.args.get('search', '')
    case_type = request.args.get('case_type')
    query = supabase.table("cases").select("*").order("id", desc=True)

    if case_type:
        query = query.eq("case_type", case_type)
    if search:
        query = query.ilike("case_title", f"%{search}%")

    cases = query.execute().data or []

    for case in cases:
        case["signed_url"] = None
        if case.get("document_url"):
            try:
                signed = supabase.storage.from_('case-documents').create_signed_url(case['document_url'], 3600)
                case["signed_url"] = signed["signedURL"]
            except:
                pass

    return render_template("staff_case_list.html", user=session['user'], cases=cases, selected_type=case_type, search=search)

# ----------------------------
# View Case Details
# ----------------------------
@staff_bp.route('/staff-case/<case_id>')
def staff_case_detail(case_id):
    if 'user' not in session or session.get('role') != 'staff':
        return redirect('/login')

    response = supabase.table("cases").select("*").eq("id", case_id).execute()
    if not response.data:
        return "Case not found ❌"
    case = response.data[0]

    signed_url = None
    if case.get("document_url"):
        try:
            signed_url = supabase.storage.from_('case-documents').create_signed_url(case['document_url'], 3600)["signedURL"]
        except:
            signed_url = None

    transactions = supabase.table("file_transactions").select("*").eq("case_id", case_id).order("created_at", desc=True).execute().data or []

    return render_template("staff_case_detail.html", user=session['user'], case=case, signed_url=signed_url, transactions=transactions)

# ----------------------------
# Delete Case (Staff)
# ----------------------------
@staff_bp.route('/staff-delete/<case_id>')
def staff_delete(case_id):
    if 'user' not in session or session.get('role') != 'staff':
        return redirect('/login')

    response = supabase.table("cases").select("*").eq("id", case_id).execute()
    if not response.data:
        return "Case not found ❌"
    case = response.data[0]

    if case.get("document_url"):
        supabase.storage.from_('case-documents').remove([case['document_url']])

    supabase.table("cases").delete().eq("id", case_id).execute()
    log_activity(session['user'], "Deleted a case", case_id)
    return redirect('/staff-cases')

# ----------------------------
# Upload Document
# ----------------------------
@staff_bp.route('/staff-upload/<case_id>', methods=['GET', 'POST'])
def staff_upload(case_id):
    if 'user' not in session or session.get('role') != 'staff':
        return redirect('/login')

    response = supabase.table("cases").select("*").eq("id", case_id).execute()
    if not response.data:
        return "Case not found ❌"
    case = response.data[0]

    if request.method == 'POST':
        file = request.files.get('document')
        if file and file.filename != '':
            filename = f"{case_id}_{file.filename}"
            file_bytes = file.read()
            try:
                supabase.storage.from_('case-documents').remove([filename])
            except:
                pass
            supabase.storage.from_('case-documents').upload(filename, file_bytes)
            supabase.table("cases").update({"document_url": filename}).eq("id", case_id).execute()
            log_activity(session['user'], "Uploaded document", case_id)
            return redirect('/staff-cases')

    return render_template("staff_upload.html", user=session['user'], case=case)

# ----------------------------
# Borrow File
# ----------------------------
@staff_bp.route('/staff-borrow/<case_id>', methods=['POST'])
def staff_borrow(case_id):
    if 'user' not in session:
        return redirect('/login')

    notes = request.form.get('notes', '')
    redirect_url = '/dashboard' if session.get('role') == 'admin' else f'/staff-case/{case_id}'

    supabase.table("cases").update({
        "file_status": "borrowed"
    }).eq("id", case_id).execute()

    supabase.table("file_transactions").insert({
        "case_id": case_id,
        "action": "borrowed",
        "performed_by": session['user'],
        "case_status_after": "Open",
        "notes": notes
    }).execute()

    log_activity(session['user'], "Borrowed file", case_id)
    return redirect(redirect_url)

# ----------------------------
# Return File
# ----------------------------
@staff_bp.route('/staff-return/<case_id>', methods=['POST'])
def staff_return(case_id):
    if 'user' not in session:
        return redirect('/login')

    case_status = request.form.get('case_status', 'Open')
    notes = request.form.get('notes', '')
    redirect_url = '/dashboard' if session.get('role') == 'admin' else f'/staff-case/{case_id}'

    supabase.table("cases").update({
        "file_status": "in_storage",
        "status": case_status
    }).eq("id", case_id).execute()

    supabase.table("file_transactions").insert({
        "case_id": case_id,
        "action": "returned",
        "performed_by": session['user'],
        "case_status_after": case_status,
        "notes": notes
    }).execute()

    log_activity(session['user'], "Returned file", case_id)
    return redirect(redirect_url)

# ----------------------------
# Disposed File
# ----------------------------
@staff_bp.route('/staff-disposed/<case_id>', methods=['POST'])
def staff_disposed(case_id):
    if 'user' not in session:
        return redirect('/login')

    notes = request.form.get('notes', '')
    redirect_url = '/dashboard' if session.get('role') == 'admin' else f'/staff-case/{case_id}'

    supabase.table("cases").update({
        "file_status": "disposed",
        "status": "Closed"
    }).eq("id", case_id).execute()

    supabase.table("file_transactions").insert({
        "case_id": case_id,
        "action": "disposed",
        "performed_by": session['user'],
        "case_status_after": "Closed",
        "notes": notes
    }).execute()

    log_activity(session['user'], "Disposed file", case_id)
    return redirect(redirect_url)
