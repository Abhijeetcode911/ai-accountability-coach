from flask import Flask, request, jsonify, render_template_string, redirect, url_for
from openai import OpenAI
import os
import sqlite3
import smtplib
import time
from email.mime.text import MIMEText
from datetime import datetime
from dotenv import load_dotenv
from db import init_db

# -------------------------
# INIT
# -------------------------

load_dotenv()
init_db()

app = Flask(__name__)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

ASSISTANT_ID = os.getenv("ASSISTANT_ID")
THREAD_ID = os.getenv("THREAD_ID")
DB_NAME = "accountability.db"

# -------------------------
# ASSISTANT PLAN GENERATION
# -------------------------

def generate_plan(prev_data, overall_context):

    message = f"""
SYSTEM CONTEXT:
You are Abhijeet's high-performance but calm execution coach.

PREVIOUS DAY UPDATES:
{prev_data}

LONG-TERM TREND (last 7 days):
{overall_context}

TASK:
Generate today's response in EXACTLY this structure:

1. Previous Day Feedback
2. Overall Feedback (Long-Term)
3. Today's Plan (Time-Blocked, max 4 blocks)
4. Adjustment Logic
5. Motivation Quote

Tone rules:
- Calm
- Direct
- No shaming
- No fluff
"""

    client.beta.threads.messages.create(
        thread_id=THREAD_ID,
        role="user",
        content=message
    )

    run = client.beta.threads.runs.create(
        thread_id=THREAD_ID,
        assistant_id=ASSISTANT_ID
    )

    while True:
        status = client.beta.threads.runs.retrieve(
            thread_id=THREAD_ID,
            run_id=run.id
        )
        if status.status == "completed":
            break
        if status.status in ["failed", "cancelled", "expired"]:
            return "Assistant run failed."
        time.sleep(1)

    messages = client.beta.threads.messages.list(thread_id=THREAD_ID)
    return messages.data[0].content[0].text.value.strip()

# -------------------------
# EMAIL
# -------------------------

def send_email(subject, body):
    sender = os.getenv("SENDER_EMAIL")
    password = os.getenv("SENDER_PASSWORD")
    receiver = os.getenv("RECEIVER_EMAIL")

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = receiver

    server = smtplib.SMTP("smtp.gmail.com", 587)
    server.starttls()
    server.login(sender, password)
    server.sendmail(sender, receiver, msg.as_string())
    server.quit()

# -------------------------
# DAILY EMAIL ENDPOINT
# -------------------------

@app.route("/send_daily_email")
def send_daily_email():

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # Determine next day number
    c.execute("SELECT day_number FROM daily_logs ORDER BY id DESC LIMIT 1")
    last_day = c.fetchone()
    last_day_number = last_day[0] if last_day else None
    day_number = (last_day_number or 0) + 1

    # Collect previous day updates
    if last_day_number:
        c.execute("""
            SELECT update_text FROM daily_updates
            WHERE day_number=?
        """, (last_day_number,))
        updates = c.fetchall()
        prev_data = "\n".join(u[0] for u in updates) if updates else "No update submitted."
    else:
        prev_data = "No previous data."

    # -------- Generate & store summary for previous day --------
    if last_day_number:
        summary_prompt = f"""
Summarize the following day in 2–3 lines.
Focus on execution, consistency, and gaps.
No motivation.

Day data:
{prev_data}
"""
        client.beta.threads.messages.create(
            thread_id=THREAD_ID,
            role="user",
            content=summary_prompt
        )

        summary_run = client.beta.threads.runs.create(
            thread_id=THREAD_ID,
            assistant_id=ASSISTANT_ID
        )

        while True:
            status = client.beta.threads.runs.retrieve(
                thread_id=THREAD_ID,
                run_id=summary_run.id
            )
            if status.status == "completed":
                break
            if status.status in ["failed", "cancelled", "expired"]:
                break
            time.sleep(1)

        messages = client.beta.threads.messages.list(thread_id=THREAD_ID)
        summary_text = messages.data[0].content[0].text.value.strip()

        c.execute("""
            UPDATE daily_logs
            SET summary=?
            WHERE day_number=?
        """, (summary_text, last_day_number))

    # -------- Long-term context --------
    c.execute("""
        SELECT summary FROM daily_logs
        ORDER BY id DESC LIMIT 7
    """)
    summaries = c.fetchall()
    overall_context = "\n".join(s[0] for s in summaries if s[0])

    # Generate today's plan
    plan = generate_plan(prev_data, overall_context)

    # Insert new day
    c.execute("""
        INSERT INTO daily_logs (day_number, date, targets)
        VALUES (?, ?, ?)
    """, (day_number, str(datetime.now().date()), plan))

    conn.commit()
    conn.close()

    send_email(f"Day {day_number}", plan)
    return jsonify({"status": "email_sent", "day": day_number})

# -------------------------
# DASHBOARD
# -------------------------

@app.route("/dashboard")
def dashboard():
    return render_template_string("""
<html>
<head>
<style>
body {
    display:flex; justify-content:center; align-items:center;
    height:100vh; margin:0; font-family:Arial; background:#f4f4f4;
}
.container {
    background:white; padding:40px; border-radius:10px;
    width:450px; box-shadow:0 4px 12px rgba(0,0,0,.1);
}
textarea { width:100%; height:100px; margin:10px 0; padding:10px; }
button { padding:8px 16px; cursor:pointer; margin-bottom:20px; }
</style>
</head>
<body>
<div class="container">
<h2>Execution Dashboard</h2>

<form method="POST" action="/daily_checkin">
<h3>Progress Update</h3>
<textarea name="completed"></textarea>
<button type="submit">Log Progress</button>
</form>

<form method="POST" action="/add_note">
<h3>Goals / Direction</h3>
<textarea name="note"></textarea>
<button type="submit">Update Goals</button>
</form>

</div>
</body>
</html>
""")

# -------------------------
# PROGRESS UPDATE
# -------------------------

@app.route("/daily_checkin", methods=["POST"])
def daily_checkin():
    text = request.form.get("completed")
    if not text:
        return redirect(url_for("dashboard"))

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("SELECT day_number FROM daily_logs ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    if not row:
        return "Generate daily plan first."

    c.execute("""
        INSERT INTO daily_updates (day_number, update_text, timestamp)
        VALUES (?, ?, ?)
    """, (row[0], text, str(datetime.now())))

    conn.commit()
    conn.close()
    return redirect(url_for("dashboard"))

# -------------------------
# STRATEGIC NOTE
# -------------------------

@app.route("/add_note", methods=["POST"])
def add_note():
    note = request.form.get("note")
    if not note:
        return redirect(url_for("dashboard"))

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        INSERT INTO strategic_notes (note, timestamp)
        VALUES (?, ?)
    """, (note, str(datetime.now())))
    conn.commit()
    conn.close()

    client.beta.threads.messages.create(
        thread_id=THREAD_ID,
        role="user",
        content=f"Strategic Update:\n{note}"
    )

    return redirect(url_for("dashboard"))

# -------------------------
# RUN LOCAL
# -------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
