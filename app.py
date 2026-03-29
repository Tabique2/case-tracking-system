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
                session['role'] = user['role']  # store role in session

                # Redirect based on role
                if user['role'] == 'admin':
                    return redirect('/home')
                elif user['role'] == 'staff':
                    return redirect('/staff-home')
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
        case["signed_url"] = None
        case["borrow_info"] = None
        if case.get("document_url"):
            if session.get('role') == 'admin' or case.get('uploaded_by') == session['user']:
                filename = case["document_url"]
                try:
                    signed_url = supabase.storage.from_('case-documents').create_signed_url(filename, 3600)
                    case["signed_url"] = signed_url["signedURL"]
                except Exception as e:
                    print(f"Warning: File {filename} not found in bucket: {e}")
            else:
                case["signed_url"] = None
        # Fetch latest borrow transaction
        tx = supabase.table("file_transactions").select("*").eq("case_id", case["id"]).eq("action", "borrowed").order("created_at", desc=True).limit(1).execute()
        if tx.data:
            borrow = tx.data[0]
            # Find matching return
            ret = supabase.table("file_transactions").select("return_date").eq("case_id", case["id"]).eq("action", "returned").order("created_at", desc=True).limit(1).execute()
            borrow["return_date"] = ret.data[0]["return_date"] if ret.data else None
            case["borrow_info"] = borrow

    return render_template(
        "dashboard.html",
        user=session['user'],
        cases=cases,
        selected_type=case_type,
        active_page='cases'
    )

# Add Case
@app.route('/add-case', methods=['GET', 'POST'])
def add_case():
    if 'user' not in session:
        return redirect('/login')

    if request.method == 'POST':
        data = {
            "case_number": request.form['case_number'],
            "case_title": request.form['case_category'],
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

#Edit Case
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

        # Show current document
        signed_url = None
        if case.get("document_url"):
            signed_url = supabase.storage.from_('case-documents') \
                .create_signed_url(case['document_url'], 3600)["signedURL"]

        # ✅ IMPORTANT: EVERYTHING BELOW MUST BE INSIDE POST
        if request.method == 'POST':

            updated_data = {
                "case_number": request.form['case_number'],
                "case_title": request.form['case_title'],
                "case_type": request.form['case_type'],
                "complainant": mask_name(request.form['complainant']),
                "respondent": mask_name(request.form['respondent']),
                "status": request.form['status']
            }

            # Delete document if checked
            if request.form.get('delete_document') == 'yes' and case.get("document_url"):
                supabase.storage.from_('case-documents').remove([case['document_url']])
                updated_data['document_url'] = None

            # Replace document if new file uploaded
            file = request.files.get('document')

            if file and file.filename != '':
                filename = f"{case_id}_{file.filename}"
                file_bytes = file.read()

                # Delete old file first
                try:
                    supabase.storage.from_('case-documents').remove([filename])
                except:
                    pass

                # Upload new file
                supabase.storage.from_('case-documents').upload(filename, file_bytes)

                # Save filename
                updated_data['document_url'] = filename

            # ✅ MUST BE INSIDE POST
            supabase.table("cases").update(updated_data).eq("id", case_id).execute()
            log_activity(session['user'], "Edited a case", case_id)

            return redirect('/dashboard')

        # GET request (show form)
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

    # ✅ ADD YOUR CODE HERE (THIS IS THE CORRECT PLACE)
    filename = f"{case_id}_{file.filename}"
    file_bytes = file.read()

    # Delete old file first
    try:
        supabase.storage.from_('case-documents').remove([filename])
    except:
        pass

    # Upload new file
    supabase.storage.from_('case-documents').upload(filename, file_bytes)

    # Save to database
    supabase.table("cases").update({"document_url": filename}).eq("id", case_id).execute()

    # Log activity
    log_activity(session['user'], "Uploaded document", case_id)

    return redirect('/dashboard')

# Delete Picture
@app.route('/delete-picture/<case_id>', methods=['POST'])
def delete_picture(case_id):
    if 'user' not in session:
        return redirect('/login')

    response = supabase.table("cases").select("*").eq("id", case_id).execute()
    if not response.data:
        return "Case not found ❌"
    case = response.data[0]

    if case.get("document_url"):
        supabase.storage.from_('case-documents').remove([case['document_url']])
        supabase.table("cases").update({"document_url": None}).eq("id", case_id).execute()
        log_activity(session['user'], "Deleted picture", case_id)

    return redirect(request.referrer or '/dashboard')

# Manage Users (Admin Only)
@app.route('/manage-users')
def manage_users():
    if 'user' not in session or session.get('role') != 'admin':
        return redirect('/login')
    users = supabase.table("users").select("*").order("id", desc=True).execute().data or []
    return render_template("manage_users.html", users=users, current_user=session['user'], active_page='users')

@app.route('/create-user', methods=['POST'])
def create_user():
    if 'user' not in session or session.get('role') != 'admin':
        return redirect('/login')

    email = request.form['email']
    password = request.form['password']

    existing = supabase.table("users").select("id").eq("email", email).execute()
    if existing.data:
        return redirect('/manage-users?error=Email already exists')

    supabase.table("users").insert({
        "email": email,
        "password": password,
        "role": "staff"
    }).execute()
    log_activity(session['user'], f"Created staff account: {email}")
    return redirect('/manage-users')

@app.route('/delete-user/<user_id>')
def delete_user(user_id):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect('/login')

    response = supabase.table("users").select("*").eq("id", user_id).execute()
    if not response.data:
        return "User not found ❌"
    user = response.data[0]

    if user['email'] == session['user']:
        return redirect('/manage-users?error=Cannot delete your own account')

    supabase.table("users").delete().eq("id", user_id).execute()
    log_activity(session['user'], f"Deleted user: {user['email']}")
    return redirect('/manage-users')

# Home
@app.route('/home')
def home_page():
    if 'user' not in session or session.get('role') != 'admin':
        return redirect('/login')
    cases = supabase.table("cases").select("*").execute().data or []
    users = supabase.table("users").select("*").eq("role", "staff").execute().data or []

    categories = [
        "PHYSICAL INJURY",
        "GAMBLING/RA 9287",
        "MURDER/FRUS. MURDER",
        "HOMICIDE/FRUSTRATED HOMICIDE",
        "RECKLESS IMPRUDENCE",
        "FORESTRY LAW/RA 9262",
        "DRUGS/RA9165",
        "LEGAL POSSESSION OF FIREARMS/RA 10591",
        "RTC APPEALED CASES",
        "RTC ARCHIVED",
        "OTHER CRIMES",
        "SPECIAL PROCEEDING",
        "CIVIL CASE",
        "SEXUAL CRIMES RA8353",
        "ABUSES/RA9262/RA7610"
    ]
    category_counts = {cat: sum(1 for c in cases if c.get('case_title', '').upper().startswith(cat.split('/')[0].strip())) for cat in categories}

    return render_template("home.html",
        total_cases=len(cases),
        open_cases=sum(1 for c in cases if c.get('status') == 'Open'),
        pending_cases=sum(1 for c in cases if c.get('status') == 'Pending'),
        closed_cases=sum(1 for c in cases if c.get('status') == 'Closed'),
        borrowed_files=sum(1 for c in cases if c.get('file_status') == 'borrowed'),
        total_users=len(users),
        categories=categories,
        category_counts=category_counts,
        active_page='home'
    )

# Cases by Category
@app.route('/cases-by-category')
def cases_by_category():
    if 'user' not in session:
        return redirect('/login')
    category = request.args.get('category', '')
    cases = supabase.table("cases").select("*").execute().data or []
    filtered = [c for c in cases if c.get('case_title', '').upper().startswith(category.split('/')[0].strip())]
    for case in filtered:
        case["signed_url"] = None
        if case.get("document_url"):
            try:
                signed = supabase.storage.from_('case-documents').create_signed_url(case['document_url'], 3600)
                case["signed_url"] = signed["signedURL"]
            except:
                pass
    active_page = 'home'
    return render_template("cases_by_category.html", cases=filtered, category=category, active_page=active_page)

# Profile
@app.route('/profile')
def profile():
    if 'user' not in session:
        return redirect('/login')
    response = supabase.table("users").select("*").eq("email", session['user']).execute()
    user = response.data[0] if response.data else {}
    return render_template("profile.html", user=user, active_page='profile')

# Activity Logs
@app.route('/activity-logs')
def activity_logs():
    if 'user' not in session:
        return redirect('/login')

    response = supabase.table("activity_logs").select("*").order("created_at", desc=True).execute()
    logs = response.data if response.data else []
    return render_template("activity_logs.html", logs=logs, active_page='logs')

from staff_routes import staff_bp
app.register_blueprint(staff_bp)

# Run app
if __name__ == '__main__':
    app.run(debug=True)