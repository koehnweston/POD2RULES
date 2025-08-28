# -*- coding: utf-8 -*-
"""
Created on Thu Aug 28 07:46:50 2025

@author: wkoehn
"""

import smtplib
import ssl
import os
import datetime
from sqlalchemy import create_engine, text
import pandas as pd

# --- Configuration (Match this with your app) ---

# This should be the same dictionary you have for your app's users
USERS = {
    "Paul": "pass123", "Weston": "pass123", "Jared": "pass123",
    "Cole": "pass123", "Andy": "pass123", "Krystal": "pass123",
    "Rian": "pass123", "Tucker": "pass123", "Aaron": "pass123",
    "Brayson": "pass123"
}

# Add the email addresses for each user
USER_EMAILS = {
    "Paul": "pparrish@olsson.com", 
    "Weston": "wkoehn@olsson.com",
    "Jared": "jloomis@olsson.com",
    "Brayson": "bbenne@olsson.com", 
    "Andy": "wkoehn@olsson.com",
    "Krystal": "kmcclain@olsson.com",
    "Aaron": "akruse@olsson.com", 
    "Rian": "reddie@olsson.com",
    "Cole": "cham@olsson.com",
    "Tucker": "tross@olsson.com"
    
}

# --- Email Credentials (Use Environment Variables for security) ---
# In production, you'll set these in your hosting environment (like Streamlit Secrets)
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD") 
SMTP_SERVER = "smtp.gmail.com" # Example for Gmail
SMTP_PORT = 465

# --- Helper Function (Copied from your app) ---
def get_current_week():
    """Calculates the current week of the season."""
    season_start_date = datetime.date(2025, 8, 18)
    today = datetime.date.today()
    if today < season_start_date:
        return 1
    days_since_start = (today - season_start_date).days
    current_week = (days_since_start // 7) + 1
    return min(current_week, 15)

# --- Main Logic ---

def check_and_send_reminders():
    """
    Checks for users who haven't submitted picks for the current week
    and sends them a reminder email.
    """
    if not SENDER_EMAIL or not EMAIL_PASSWORD:
        print("Error: Email credentials are not set.")
        return

    current_week = get_current_week()
    print(f"Checking for missing picks for Week {current_week}...")

    try:
        # Assumes your database file is in a .streamlit subdirectory
        # Adjust the path if your db.toml points elsewhere
        db_path = os.path.join(os.path.dirname(__file__), '.streamlit', 'db.sqlite')
        engine = create_engine(f'sqlite:///{db_path}')
        
        with engine.connect() as connection:
            query = text('SELECT DISTINCT "user" FROM picks WHERE week = :week;')
            picks_df = pd.read_sql_query(query, connection, params={"week": current_week})
            
        submitted_users = set(picks_df["user"])
        all_users = set(USERS.keys())
        
        users_to_remind = all_users - submitted_users

        if not users_to_remind:
            print("All users have submitted their picks. No reminders needed.")
            return

        print(f"Users to remind: {', '.join(users_to_remind)}")

        # --- Send Emails ---
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context) as server:
            server.login(SENDER_EMAIL, EMAIL_PASSWORD)
            for user in users_to_remind:
                recipient_email = USER_EMAILS.get(user)
                if not recipient_email:
                    print(f"Warning: No email address found for {user}.")
                    continue

                subject = f"🏈 Reminder: Submit Your CFB Picks for Week {current_week}!"
                body = f"Hi {user},\n\nThis is an automated reminder that you haven't submitted your college football picks for Week {current_week}.\n\nPlease submit them before the games start!\n\nGood luck!"
                message = f"Subject: {subject}\n\n{body}"

                try:
                    server.sendmail(SENDER_EMAIL, recipient_email, message)
                    print(f"Successfully sent reminder to {user} at {recipient_email}.")
                except Exception as e:
                    print(f"Failed to send email to {user}: {e}")

    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    check_and_send_reminders()