import schedule
import time
import requests

def job():
    print("Triggering daily email...")
    try:
        response = requests.get("http://127.0.0.1:8080/send_daily_email")
        print("Response:", response.json())
    except Exception as e:
        print("Error:", e)

# Run every 1 minute (TEST MODE)
schedule.every(1).minutes.do(job)

print("Scheduler running every 1 minute...")

while True:
    schedule.run_pending()
    time.sleep(1)


