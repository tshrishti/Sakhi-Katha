#!/bin/sh
# Starts Sakhi Khata on http://localhost:8000 . Keys are read from .env
cd "$(dirname "$0")/backend" && pip install -q -r requirements.txt && python -m uvicorn main:app --host 0.0.0.0 --port 8000
