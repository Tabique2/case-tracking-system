import smtplib
from dotenv import load_dotenv
import os

load_dotenv()

email = os.getenv('MAIL_EMAIL')
password = os.getenv('MAIL_PASSWORD')

print(f"Email: {email}")
print(f"Password length: {len(password) if password else 'NOT SET'}")
print(f"Password: {password}")

try:
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(email, password)
        print("SUCCESS - Login worked!")
except Exception as e:
    print(f"FAILED - {e}")
