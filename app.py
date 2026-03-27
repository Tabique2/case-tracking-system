from flask import Flask
from supabase import create_client
import os
from dotenv import load_dotenv

load_dotenv()  # loads .env file

app = Flask(__name__)

# Use environment variables here
supabase = create_client(
    os.getenv("SUPABASE_URL"),  
    os.getenv("SUPABASE_KEY")   
)

@app.route('/')
def home():
    return "run franky!"

@app.route('/test-db')
def test_db():
    data = supabase.table("cases").select("*").execute()
    return str(data.data)

if __name__ == '__main__':
    app.run(debug=True)