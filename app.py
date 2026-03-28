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
                session['role'] = user['role']  # store role
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
    response = query.execute()
    cases = response.data if response.data else []

    # Generate signed URL for each case document (1-hour expiry)
    for case in cases:
        if case.get("document_url"):
            filename = case["document_url"]  # filename stored in DB
            signed_url = supabase.storage.from_('case-documents').create_signed_url(filename, 3600)
            case["signed_url"] = signed_url["signedURL"]

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
            "status": "Open"
        }
        supabase.table("cases").insert(data).execute()
        return redirect('/dashboard')

    return render_template("add_case.html")

@app.route('/edit-case/<case_id>', methods=['GET', 'POST'])
def edit_case(case_id):
    if 'user' not in session:
        return redirect('/login')

    response = supabase.table("cases").select("*").eq("id", case_id).execute()
    if not response.data:
        return "Case not found ❌"

    case = response.data[0]

    # Generate signed URL for existing document
    signed_url = None
    if case.get("document_url"):
        result = supabase.storage.from_('case-documents').create_signed_url(case['document_url'], 3600)
        signed_url = result['signedURL']

    if request.method == 'POST':
        updated_data = {
            "case_number": request.form['case_number'],
            "case_title": request.form['case_title'],
            "case_type": request.form['case_type'],
            "complainant": mask_name(request.form['complainant']),
            "respondent": mask_name(request.form['respondent']),
            "status": request.form['status']
        }

        # Delete document if checkbox checked
        if request.form.get('delete_document') == 'yes' and case.get("document_url"):
            try:
                supabase.storage.from_('case-documents').remove([case['document_url']])
            except Exception as e:
                return f"Error deleting document: {e}"
            updated_data['document_url'] = None

        # Replace document if a new file is uploaded
        file = request.files.get('document')
        if file and file.filename != '':
            filename = f"{case_id}_{file.filename}"
            file_bytes = file.read()
            try:
                supabase.storage.from_('case-documents').upload(filename, file_bytes)
            except Exception as e:
                return f"Error uploading new document: {e}"
            updated_data['document_url'] = filename

        supabase.table("cases").update(updated_data).eq("id", case_id).execute()
        return redirect('/dashboard')

    return render_template("edit_case.html", case=case, signed_url=signed_url)

# Delete Case
@app.route('/delete-case/<case_id>')
def delete_case(case_id):
    if 'user' not in session:
        return redirect('/login')

    supabase.table("cases").delete().eq("id", case_id).execute()
    return redirect('/dashboard')

@app.route('/upload-document/<case_id>', methods=['POST'])
def upload_document(case_id):
    if 'user' not in session:
        return redirect('/login')
    if session.get('role') != 'admin':
        return "Access denied ❌"

    file = request.files.get('document')
    if file and file.filename != '':
        filename = f"{case_id}_{file.filename}"  # unique filename per case
        file_bytes = file.read()  # read file content as bytes
        try:
            # Upload bytes to private bucket
            supabase.storage.from_('case-documents').upload(filename, file_bytes)
        except Exception as e:
            return f"Upload error: {e}"

        # Save filename in the cases table
        supabase.table("cases").update({"document_url": filename}).eq("id", case_id).execute()

    return redirect('/dashboard')

# Run app
if __name__ == '__main__':
    app.run(debug=True)