from flask import Flask, request, jsonify, render_template_string, redirect, url_for
from openai import OpenAI
from google.cloud import firestore
import os
import smtplib
import time
from email.mime.text import MIMEText
from datetime import datetime
from dotenv import load_dotenv

# -------------------------
# INIT
# -------------------------

load_dotenv()

app = Flask(__name__)

# OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ASSISTANT_ID = os.getenv("ASSISTANT_ID")
THREAD_ID = os.getenv("THREAD_ID")

# Firestore (auto-auth on Cloud Run)
db = firestore.Client()

# -------------------------
# FIRESTORE HELPERS
# -------------------------

def get_latest_day():
    docs = (
        db.collection("days")
        .order_by("day_number", direction=firestore.Query.DESCENDING)
        .limit(1)
        .stream()
    )
    for doc in docs:
        return doc.to_dict()
    return None


def get_last_7_summaries():
    docs = (
        db.collection("days")
        .order_by("day_number", direction=firestore.Query.DESCENDING)
        .limit(7)
        .stream()
    )
    summaries = []
    for d in docs:
        data = d.to_dict()
        if data.get("summary"):
            summaries.append(data["summary"])
    return "\n".join(summaries)


def get_updates_for_day(day_number):
    docs = (
        db.collection("updates")
        .where("day_number", "==", day_number)
        .stream()
    )
    return [d.to_dict()["text"] for d in docs]


def create_day(day_number, plan):
    db.collection("days").document(f"day_{day_number}").set({
        "day_number": day_number,
        "date": str(datetime.now().date()),
        "targets": plan,
        "summary": None
    })


def save_summary(day_number, summary):
    db.collection("days").document(f"day_{day_number}").update({
        "summary": summary
    })


def add_update(day_number, text):
    db.collection("updates").add({
        "day_number": day_number,
        "text": text,
        "timestamp": firestore.SERVER_TIMESTAMP
    })


def add_note(text):
    db.collection("notes").add({
        "text": text,
        "timestamp": firestore.SERVER_TIMESTAMP
    })

# -------------------------
# ASSISTANT LOGIC
# -------------------------

def run_assistant(message):
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


def generate_summary(prev_data):
    prompt = f"""
Summarize the following day in 2–3 lines for internal tracking.
Focus on execution, consistency, and gaps.
No motivation. No advice.

Day data:
{prev_data}
"""
    return run_assistant(prompt)


def generate_plan(prev_data, overall_context):
   prompt = f"""
SYSTEM ROLE:
You are Abhijeet’s personal execution trainer.
Your job is to convert intent into daily execution.

PERSONALITY:
- Strict but calm
- Honest, not polite
- Practical, not theoretical
- You value consistency over intensity
- You never shame, but you do not excuse patterns

CONTEXT — PREVIOUS DAY:
{prev_data}

CONTEXT — LONG-TERM TREND (last 7 days):
{overall_context}

OBJECTIVE:
Design today so that Abhijeet makes *measurable progress* even if motivation is low.

You must assume:
- Time and energy are limited
- Over-planning causes failure
- Finishing matters more than expanding scope

OUTPUT FORMAT (FOLLOW EXACTLY):

1. Previous Day Feedback
- One sentence on what actually moved things forward
- One sentence on what was avoided or unfinished
- One clear corrective instruction (not advice)

2. Overall Feedback (Long-Term)
- Current trajectory: improving / flat / declining
- One pattern you notice across days
- One rule Abhijeet should follow today to fix that pattern

3. Today’s Plan (Time-Blocked)
- Max 3–4 blocks total
- Each block must include:
  • Time range (realistic)
  • Single concrete task
  • Clear “done” condition
- Carry forward unfinished *critical* tasks first
- If yesterday was weak, reduce ambition but protect momentum

4. Execution Guidance
- Identify the hardest task today
- Explain how to start it in the *first 10 minutes*
- Include friction-reduction steps (environment, sequencing, constraints)

5. Motivation Quote
- Calm, grounded, non-cheesy
- Focus on discipline, identity, or long-term self-respect
- One or two lines max

RULES (IMPORTANT):
- Do not hype.
- Do not over-encourage.
- Do not introduce new goals unless necessary.
- If consistency is breaking, prioritize showing up over optimizing.
- Assume this will be read once in the morning — make it actionable immediately.
"""
   return run_assistant(prompt)

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

    last_day = get_latest_day()

    if last_day:
        last_day_number = last_day["day_number"]
        updates = get_updates_for_day(last_day_number)
        prev_data = "\n".join(updates) if updates else "No update submitted."

        # Generate & store summary for previous day
        summary = generate_summary(prev_data)
        save_summary(last_day_number, summary)

    else:
        last_day_number = 0
        prev_data = "No previous data."

    # Long-term context
    overall_context = get_last_7_summaries()

    # Generate today's plan
    day_number = last_day_number + 1
    plan = generate_plan(prev_data, overall_context)

    create_day(day_number, plan)
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

    last_day = get_latest_day()
    if not last_day:
        return "Generate daily plan first."

    add_update(last_day["day_number"], text)
    return redirect(url_for("dashboard"))

# -------------------------
# STRATEGIC NOTE
# -------------------------

@app.route("/add_note", methods=["POST"])
def add_note_route():
    note = request.form.get("note")
    if not note:
        return redirect(url_for("dashboard"))

    add_note(note)

    client.beta.threads.messages.create(
        thread_id=THREAD_ID,
        role="user",
        content=f"Strategic Update:\n{note}"
    )

    return redirect(url_for("dashboard"))

# -------------------------
# RUN
# -------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
