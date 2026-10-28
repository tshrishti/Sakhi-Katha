"""Optional: call the reminder endpoint from an external scheduler (Render Cron Job, cron-job.org) instead of the in-process loop.
Usage: python cron_ping.py   (needs PUBLIC_URL and CRON_SECRET in the environment)"""
import os, sys, urllib.request
url, sec = os.getenv("PUBLIC_URL", "").rstrip("/"), os.getenv("CRON_SECRET", "")
if not (url and sec): sys.exit("PUBLIC_URL and CRON_SECRET are required")
req = urllib.request.Request(url + "/api/cron/reminders", method="POST", headers={"X-Cron-Secret": sec})
print(urllib.request.urlopen(req, timeout=60).read().decode())
