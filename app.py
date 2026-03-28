from flask import Flask, render_template, request, redirect, session
from supabase import create_client
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = "secret123"  # Session management

# Connect to Supabase
supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)

# ----------------------------
# Utility Functions
# ----------------------------
def mask_name(full_name):
    parts = full_name.split()
    if len(parts) == 1:
        return parts[0][:2] + "***"
    elif len(parts) >= 2:
        first = parts[0][:2] + "***"
        last = parts[1][0] + "**"
        return f"{first} {last}"

def log_activity(user_email, action, case_id=None):
    data = {
        "user_email": user_email,
        "action": action,
        "case_id": case_id
    }
    supabase.table("activity_logs").insert(data).execute()

# ----------------------------
# Routes
# ----------------------------

@app.route('/')
def home():
    return redirect('/login')

# Login
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        response = supabase.table("users").select("*").eq("email", email).execute()
        if response.data:
            user = response.data[0]
            if user['password'] == password:
                session['user'] = user['email']
                session['role'] = user['role']
                return redirect('/dashboard')
            else:
                return "Wrong password ❌"
        else:
            return "User not found ❌"
    return render_template("login.html")

# Logout
@app.route('/logout')
def logout():
    session.pop('user', None)
    session.pop('role', None)
    return redirect('/login')

# Dashboard
@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        return redirect('/login')

    case_type = request.args.get('case_type')
    query = supabase.table("cases").select("*").order("id", desc=True)
    if case_type:
        query = query.eq("case_type", case_type)

    # Non-admins only see their own uploaded cases/documents
    if session.get('role') != 'admin':
        query = query.eq("uploaded_by", session['user'])

    response = query.execute()
    cases = response.data if response.data else []

    # Generate signed URL for documents only if user is allowed
    for case in cases:
        case["signed_url"] = None  # default None
        if case.get("document_url"):
            if session.get('role') == 'admin' or case.get('uploaded_by') == session['user']:
                filename = case["document_url"]
                try:
                    signed_url = supabase.storage.from_('case-documents').create_signed_url(filename, 3600)
                    case["signed_url"] = signed_url["signedURL"]
                except Exception as e:
                    # File not found in bucket, just skip
                    print(f"Warning: File {filename} not found in bucket: {e}")
            else:
                # User not allowed to see this file
                case["signed_url"] = None

    return render_template(
        "dashboard.html",
        user=session['user'],
        cases=cases,
        selected_type=case_type
    )

# Add Case
@app.route('/add-case', methods=['GET', 'POST'])
def add_case():
    if 'user' not in session:
        return redirect('/login')

    if request.method == 'POST':
        data = {
            "case_number": request.form['case_number'],
            "case_title": request.form['case_title'],
            "case_type": request.form['case_type'],
            "complainant": mask_name(request.form['complainant']),
            "respondent": mask_name(request.form['respondent']),
            "status": "Open",
            "uploaded_by": session['user']
        }
        result = supabase.table("cases").insert(data).execute()

        if result.data:
            case_id = result.data[0]['id']
            log_activity(session['user'], "Added a case", case_id)

        return redirect('/dashboard')

    return render_template("add_case.html")

# Edit Case
@app.route('/edit-case/<case_id>', methods=['GET', 'POST'])
def edit_case(case_id):
    if 'user' not in session:
        return redirect('/login')

    # Get existing case
    response = supabase.table("cases").select("*").eq("id", case_id).execute()
    if not response.data:
        return "Case not found ❌"
    case = response.data[0]

    # Permission check
    if session.get('role') != 'admin' and case.get('uploaded_by') != session['user']:
        return "Access denied ❌"

    signed_url = None
    if case.get("document_url"):
        signed_url = supabase.storage.from_('case-documents').create_signed_url(case['document_url'], 3600)["signedURL"]

    if request.method == 'POST':
        updated_data = {
            "case_number": request.form['case_number'],
            "case_title": request.form['case_title'],
            "case_type": request.form['case_type'],
            "complainant": mask_name(request.form['complainant']),
            "respondent": mask_name(request.form['respondent']),
            "status": request.form['status']
        }

        # Delete document if requested
        if request.form.get('delete_document') == 'yes' and case.get("document_url"):
            supabase.storage.from_('case-documents').remove([case['document_url']])
            updated_data['document_url'] = None

        # Replace document if uploaded
        file = request.files.get('document')
        if file and file.filename != '':
            filename = f"{case_id}_{file.filename}"
            file_bytes = file.read()
            
            # Add upsert=True to avoid Duplicate error
            supabase.storage.from_('case-documents').upload(filename, file_bytes, upsert=True)
            updated_data['document_url'] = filename

        supabase.table("cases").update(updated_data).eq("id", case_id).execute()
        log_activity(session['user'], "Edited a case", case_id)

        return redirect('/dashboard')

    return render_template("edit_case.html", case=case, signed_url=signed_url)

# Delete Case
@app.route('/delete-case/<case_id>')
def delete_case(case_id):
    if 'user' not in session:
        return redirect('/login')

    # Get case to check permission
    response = supabase.table("cases").select("*").eq("id", case_id).execute()
    if not response.data:
        return "Case not found ❌"
    case = response.data[0]

    if session.get('role') != 'admin' and case.get('uploaded_by') != session['user']:
        return "Access denied ❌"

    # Delete document if exists
    if case.get("document_url"):
        supabase.storage.from_('case-documents').remove([case['document_url']])

    supabase.table("cases").delete().eq("id", case_id).execute()
    log_activity(session['user'], "Deleted a case", case_id)

    return redirect('/dashboard')

# Upload Document separately (admin only)
@app.route('/upload-document/<case_id>', methods=['POST'])
def upload_document(case_id):
    if 'user' not in session:
        return redirect('/login')

    file = request.files.get('document')
    if not file or file.filename == '':
        return redirect('/dashboard')

    response = supabase.table("cases").select("*").eq("id", case_id).execute()
    if not response.data:
        return "Case not found ❌"
    case = response.data[0]

    # Only admin or uploader can upload
    if session.get('role') != 'admin' and case.get('uploaded_by') != session['user']:
        return "Access denied ❌"

    filename = f"{case_id}_{file.filename}"
    file_bytes = file.read()
    # Add upsert=True to prevent duplicate error
    supabase.storage.from_('case-documents').upload(filename, file_bytes, upsert=True)
    supabase.table("cases").update({"document_url": filename}).eq("id", case_id).execute()
    log_activity(session['user'], "Uploaded document", case_id)

    return redirect('/dashboard')

# Activity Logs
@app.route('/activity-logs')
def activity_logs():
    if 'user' not in session:
        return redirect('/login')

    response = supabase.table("activity_logs").select("*").order("created_at", desc=True).execute()
    logs = response.data if response.data else []
    return render_template("activity_logs.html", logs=logs)

# Run app
if __name__ == '__main__':
    app.run(debug=True)