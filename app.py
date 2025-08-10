# -*- coding: utf-8 -*-
# Flask + Facebook Messenger Weather Bot with 5-minute Auto Updates
# Commands:
# - "weather <city>": one-off weather
# - "set <city>": subscribe to 5-min updates for city (PH only, per your API filter)
# - "stop": cancel subscription
# - "status": show current subscription
# - "help": show help
#
# Notes:
# - Secrets are read from environment when available; placeholders otherwise.
# - Subscriptions are in-memory. Optional local JSON persistence for dev only.
# - For production persistence, use a database or key-value store.
#
# Run:
#   pip install flask requests urllib3
#   python scripts/weather-bot.py
#
# (c) You

import re
import json
import requests
import urllib3
import hmac
import hashlib
import os
import threading
import time
import subprocess
import sys
from typing import Dict, Any, Optional
from flask import Flask, request, jsonify

app = Flask(__name__)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "verify-me")

# Strongly recommended: set these as env vars in production
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN", "EAAKSSCUQjUIBPHA6ZA99bpTwz2LVhaUgjtvJ7AnoIVZAaZBYnHZBEJZBZAicibGSkRSZAnQDtStjc2AqI149z6YZCrZCit4J9PcU3lqS9iNDyZCmNvUOthoK8E3SMCm8zkV0ur4xqDp2PhTlN0x68w5e3CLX6eF6DSj0tUdjdzQJ4k9zrmyprvr5rCWXGoqyAIJw2CXovmrUsW")
APP_SECRET = os.environ.get("APP_SECRET", "07f1df1bf9c213eb6a618908fab18189")

# Environment detection - Render sets PORT environment variable
PORT = int(os.environ.get("PORT", 5000))
IS_RENDER = os.environ.get("RENDER") == "true"
IS_LOCAL_DEV = not IS_RENDER

# Ngrok configuration (only for local development)
NGROK_AUTH_TOKEN = os.environ.get("NGROK_AUTH_TOKEN", "")
ngrok_url: Optional[str] = None

# Optional local JSON persistence (best-effort for local dev)
SUBSCRIPTIONS_FILE = os.environ.get("SUBSCRIPTIONS_FILE", "subscriptions.json")

# Default update interval: 5 minutes
DEFAULT_INTERVAL_SECS = 300

# -----------------------------------------------------------------------------
# Subscriptions storage (in-memory)
# subscriptions: { user_id: { "location": str, "interval": int, "last_sent": float } }
# -----------------------------------------------------------------------------
subscriptions: Dict[str, Dict[str, Any]] = {}
subscriptions_lock = threading.RLock()

# -----------------------------------------------------------------------------
# Utilities: ngrok (local dev)
# -----------------------------------------------------------------------------
def setup_ngrok():
    """Setup and start ngrok tunnel (local development only)"""
    global ngrok_url
    if IS_RENDER:
        print("🚀 Running on Render - Ngrok not needed")
        return None
    try:
        if NGROK_AUTH_TOKEN:
            print("🔧 Setting up ngrok for local development...")
            subprocess.run(["ngrok", "config", "add-authtoken", NGROK_AUTH_TOKEN], check=True, capture_output=True)
            print("✅ Ngrok auth token configured")
        else:
            print("ℹ️ NGROK_AUTH_TOKEN not set. Will try to run ngrok without auth.")

        print(f"🚀 Starting ngrok tunnel on port {PORT}...")
        ngrok_process = subprocess.Popen(["ngrok", "http", str(PORT), "--log=stdout"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        time.sleep(3)
        try:
            response = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=5)
            if response.status_code == 200:
                tunnels = response.json().get("tunnels") or []
                if tunnels:
                    ngrok_url = tunnels[0]["public_url"]
                    print(f"✅ Ngrok tunnel active: {ngrok_url}")
                    print("=" * 60)
                    print("🔗 LOCAL DEVELOPMENT WEBHOOK URL:")
                    print(f"   {ngrok_url}")
                    print("=" * 60)
                    return ngrok_process
                else:
                    print("❌ No ngrok tunnels found")
            else:
                print(f"❌ Failed to get ngrok status: {response.status_code}")
        except requests.exceptions.RequestException as e:
            print(f"❌ Error getting ngrok URL: {e}")
        return ngrok_process
    except subprocess.CalledProcessError as e:
        print(f"❌ Error setting up ngrok: {e}")
        print("💡 Make sure ngrok is installed: https://ngrok.com/download")
        return None
    except FileNotFoundError:
        print("❌ Ngrok not found! Please install ngrok for local development.")
        print("💡 Download from: https://ngrok.com/download")
        return None

# -----------------------------------------------------------------------------
# Utilities: Meta appsecret_proof
# -----------------------------------------------------------------------------
def generate_appsecret_proof(access_token: str, app_secret: str) -> Optional[str]:
    """Generate the appsecret_proof required by Facebook"""
    try:
        token_bytes = access_token.encode("utf-8")
        secret_bytes = app_secret.encode("utf-8")
        proof = hmac.new(secret_bytes, token_bytes, hashlib.sha256).hexdigest()
        return proof
    except Exception as e:
        print(f"Error generating appsecret_proof: {e}")
        return None

# -----------------------------------------------------------------------------
# Messenger Send API
# -----------------------------------------------------------------------------
def send_message(recipient_id: str, text: str) -> bool:
    """Send message using Page Access Token"""
    try:
        if not PAGE_ACCESS_TOKEN or "YOUR_PAGE_ACCESS_TOKEN" in PAGE_ACCESS_TOKEN:
            print("❌ PAGE_ACCESS_TOKEN is not set. Cannot send message.")
            return False

        print(f"📤 Sending: '{text[:120]}...' to {recipient_id}")
        url = "https://graph.facebook.com/v18.0/me/messages"

        payload = {
            "messaging_type": "RESPONSE",
            "recipient": {"id": recipient_id},
            "message": {"text": text},
        }

        appsecret_proof = generate_appsecret_proof(PAGE_ACCESS_TOKEN, APP_SECRET or "")
        params = {"access_token": PAGE_ACCESS_TOKEN}
        if appsecret_proof:
            params["appsecret_proof"] = appsecret_proof

        headers = {"Content-Type": "application/json"}
        response = requests.post(url, params=params, json=payload, headers=headers, timeout=15)

        print(f"📡 Status: {response.status_code}")
        print(f"📡 Response: {response.text}")

        if response.status_code == 200:
            print("✅ MESSAGE SENT SUCCESSFULLY!")
            return True
        else:
            print("❌ Failed to send message")
            return False
    except Exception as e:
        print(f"❌ Error sending message: {str(e)}")
        return False

# -----------------------------------------------------------------------------
# Weather Fetch
# -----------------------------------------------------------------------------
def get_weather(place: str) -> str:
    """Fetch weather data for the specified location from OpenWeather (public JSONP endpoint)."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": f"https://openweathermap.org/find?q={place}",
    }
    params = {
        "callback": "jQuery",
        "q": place,
        "type": "like",
        "sort": "population",
        "cnt": "1",
        "appid": "439d4b804bc8187953eb36d2a8c26a02",  # public test key
    }
    try:
        response = requests.get(
            "https://openweathermap.org/data/2.5/find",
            headers=headers,
            params=params,
            verify=False,
            timeout=10,
        )
        if response.status_code != 200:
            return "❌ Weather service is currently unavailable."

        match = re.search(r"$$(\{.*\})$$", response.text)
        if not match:
            return "❌ Couldn't fetch weather info."

        data = json.loads(match.group(1))
        if not data.get("list") or len(data["list"]) == 0:
            return f"❌ City '{place}' not found in the Philippines."

        weather_data = data["list"][0]
        if weather_data.get("sys", {}).get("country") != "PH":
            return f"🌏 '{place}' is not in the Philippines. Only Philippine cities are supported."

        main = weather_data["main"]
        weather = weather_data["weather"][0]
        wind = weather_data.get("wind", {})
        clouds = weather_data.get("clouds", {})

        temp_c = round(main["temp"] - 273.15, 1)
        feels_c = round(main["feels_like"] - 273.15, 1)

        weather_response = (
            f"📍 {weather_data['name']}, Philippines\n"
            f"🌡️ Temperature: {temp_c}°C\n"
            f"🥶 Feels like: {feels_c}°C\n"
            f"💧 Humidity: {main['humidity']}%\n"
        )

        if wind.get("speed") is not None:
            wind_speed = round(wind["speed"] * 3.6, 1)
            weather_response += f"🌬️ Wind: {wind_speed} km/h\n"

        if clouds.get("all") is not None:
            weather_response += f"☁️ Clouds: {clouds['all']}%\n"

        weather_desc = (weather.get("description") or "").lower()
        if "overcast" in weather_desc and "cloud" in weather_desc:
            display_desc = "Overcast cloud baka uulan"
        else:
            display_desc = (weather.get("description") or "").capitalize()

        weather_response += f"🌈 {display_desc}"
        return weather_response

    except Exception as e:
        return f"⚠️ Error fetching weather: {str(e)}"

# -----------------------------------------------------------------------------
# Subscription Management
# -----------------------------------------------------------------------------
def load_subscriptions():
    if IS_LOCAL_DEV:
        try:
            if os.path.exists(SUBSCRIPTIONS_FILE):
                with open(SUBSCRIPTIONS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    with subscriptions_lock:
                        subscriptions.clear()
                        subscriptions.update(data)
                print(f"📥 Loaded {len(subscriptions)} subscriptions from {SUBSCRIPTIONS_FILE}")
        except Exception as e:
            print(f"⚠️ Could not load subscriptions: {e}")

def save_subscriptions():
    if IS_LOCAL_DEV:
        try:
            with subscriptions_lock:
                to_save = subscriptions.copy()
            with open(SUBSCRIPTIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(to_save, f, indent=2)
            print(f"💾 Saved {len(to_save)} subscriptions")
        except Exception as e:
            print(f"⚠️ Could not save subscriptions: {e}")

def set_subscription(user_id: str, location: str, interval: int = DEFAULT_INTERVAL_SECS) -> None:
    with subscriptions_lock:
        subscriptions[user_id] = {
            "location": location,
            "interval": int(interval),
            "last_sent": 0.0,
        }
    save_subscriptions()

def remove_subscription(user_id: str) -> bool:
    removed = False
    with subscriptions_lock:
        if user_id in subscriptions:
            subscriptions.pop(user_id, None)
            removed = True
    save_subscriptions()
    return removed

def get_subscription(user_id: str) -> Optional[Dict[str, Any]]:
    with subscriptions_lock:
        return subscriptions.get(user_id)

# -----------------------------------------------------------------------------
# Background Scheduler
# -----------------------------------------------------------------------------
def scheduler_loop():
    print("⏱️ Scheduler thread started (5-min default interval)")
    while True:
        now = time.time()
        # take a snapshot to iterate without holding the lock during network calls
        with subscriptions_lock:
            items = list(subscriptions.items())

        for user_id, sub in items:
            interval = int(sub.get("interval", DEFAULT_INTERVAL_SECS))
            last_sent = float(sub.get("last_sent", 0.0))
            location = sub.get("location", "")

            if not location:
                continue

            if now - last_sent >= interval:
                # Fetch and send without holding lock
                weather = get_weather(location)
                ok = send_message(user_id, f"⏰ 5-min update for {location}:\n\n{weather}")
                # Update last_sent only on attempt; even if fail, move forward to avoid spamming retries
                with subscriptions_lock:
                    if user_id in subscriptions:
                        subscriptions[user_id]["last_sent"] = now
        time.sleep(30)  # check every 30 seconds

def start_scheduler():
    t = threading.Thread(target=scheduler_loop, name="weather-scheduler", daemon=True)
    t.start()

# -----------------------------------------------------------------------------
# Flask Routes
# -----------------------------------------------------------------------------
@app.route("/", methods=["GET"])
def verify():
    """Webhook verification endpoint"""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("✅ Webhook verified successfully!")
        return challenge, 200
    elif mode is None and token is None:
        status_msg = "🤖 Philippine Weather Bot is running!"
        if IS_RENDER:
            status_msg += f"<br>🌐 Hosted on Render 24/7 (Port: {PORT})"
        else:
            status_msg += f"<br>📡 Local dev with ngrok: {ngrok_url or 'Starting...'}"
        return status_msg, 200
    else:
        return "Verification failed", 403

@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint for Render"""
    return jsonify({"status": "healthy", "bot": "Philippine Weather Bot", "port": PORT}), 200

@app.route("/", methods=["POST"])
def webhook():
    """Main webhook endpoint for receiving messages"""
    try:
        data = request.get_json()
        print(f"Received: {json.dumps(data, indent=2)}")

        if data and data.get("object") == "page":
            for entry in data.get("entry", []):
                for event in entry.get("messaging", []):
                    if "message" in event and "text" in event["message"]:
                        sender_id = event["sender"]["id"]
                        message_text = event["message"]["text"]
                        print(f"📨 Message from {sender_id}: {message_text}")
                        handle_message(sender_id, message_text)
            return "EVENT_RECEIVED", 200
        return "IGNORED", 200
    except Exception as e:
        print(f"Error processing webhook: {str(e)}")
        return "ERROR", 500

# -----------------------------------------------------------------------------
# Command Handling
# -----------------------------------------------------------------------------
HELP_TEXT = (
    "👋 Hello! I'm your Philippine Weather Bot!\n\n"
    "Commands:\n"
    "• weather <city> – one-time weather info (PH only)\n"
    "• set <city> – get updates every 5 minutes for <city>\n"
    "• status – show your current subscription\n"
    "• stop – stop auto updates\n"
    "• help – show this help\n\n"
    "Examples:\n"
    "• weather Manila\n"
    "• set Enrile\n"
)

def handle_message(sender_id: str, message: str):
    try:
        msg = (message or "").strip()
        msg_lower = msg.lower()

        # Auto-subscribe commands: "set <city>" / "subscribe <city>" / "track <city>"
        if msg_lower.startswith("set ") or msg_lower.startswith("subscribe ") or msg_lower.startswith("track "):
            parts = msg.split(" ", 1)
            if len(parts) > 1 and parts[1].strip():
                location = parts[1].strip()
                set_subscription(sender_id, location, DEFAULT_INTERVAL_SECS)
                send_message(
                    sender_id,
                    f"✅ Location set to '{location}'.\n"
                    f"🔔 You'll receive weather updates every 5 minutes.\n"
                    f"ℹ️ Send 'status' to check, 'stop' to unsubscribe."
                )
            else:
                send_message(sender_id, "⛅ Please specify a city. Example: set Manila")
            return

        # Stop
        if msg_lower in ["stop", "unsubscribe", "cancel"]:
            if remove_subscription(sender_id):
                send_message(sender_id, "🛑 Auto-updates stopped. Send 'set <city>' anytime to re-subscribe.")
            else:
                send_message(sender_id, "ℹ️ You are not subscribed. Send 'set <city>' to subscribe.")
            return

        # Status
        if msg_lower in ["status", "where", "subscription"]:
            sub = get_subscription(sender_id)
            if sub:
                next_in = max(0, sub["interval"] - int(time.time() - sub.get("last_sent", 0)))
                send_message(
                    sender_id,
                    f"📌 You are subscribed to: {sub['location']}\n"
                    f"⏱️ Interval: {sub['interval']//60} minutes\n"
                    f"⏭️ Next update in ~{next_in} seconds\n"
                    f"🔕 Send 'stop' to unsubscribe."
                )
            else:
                send_message(sender_id, "ℹ️ You are not subscribed. Send 'set <city>' to enable 5-min updates.")
            return

        # One-off weather
        if msg_lower.startswith("weather ") or msg_lower.startswith("update "):
            parts = msg.split(" ", 1)
            if len(parts) > 1 and parts[1].strip():
                location = parts[1].strip()
                reply = get_weather(location)
            else:
                reply = "⛅ Please specify a city. Example: weather Manila"
            send_message(sender_id, reply)
            return

        # Greetings/help
        if msg_lower in ["help", "start", "hi", "hello"]:
            send_message(sender_id, HELP_TEXT)
            return

        # Fallback
        send_message(sender_id, "⛅ Type 'weather <city>' or 'set <city>' for auto-updates.\nExample: set Enrile")

    except Exception as e:
        print(f"Error handling message: {str(e)}")

# -----------------------------------------------------------------------------
# Startup
# -----------------------------------------------------------------------------
def validate_setup() -> bool:
    if not PAGE_ACCESS_TOKEN or "YOUR_PAGE_ACCESS_TOKEN" in PAGE_ACCESS_TOKEN:
        print("❌ CRITICAL: You need to set PAGE_ACCESS_TOKEN!")
        return False
    print("✅ Page Access Token set")
    print(f"📝 Token length: {len(PAGE_ACCESS_TOKEN)}")
    return True

if __name__ == "__main__":
    print(f"🌐 Philippine Weather Bot starting on port {PORT}...")

    # Load subs (local dev)
    load_subscriptions()

    # Start scheduler
    start_scheduler()

    if IS_RENDER:
        print("🔥 RENDER DEPLOYMENT MODE")
        print(f"🚀 Binding to PORT: {PORT}")
    else:
        print("🔧 LOCAL DEVELOPMENT MODE")
        print(f"💻 Local server will run on port {PORT}")

    if not validate_setup():
        print("\n🔧 STEPS TO FIX:")
        print("1. Set PAGE_ACCESS_TOKEN as an environment variable")
        print("2. Set APP_SECRET as an environment variable")
        print("3. Restart the bot")
        sys.exit(1)

    ngrok_process = None
    try:
        if IS_LOCAL_DEV:
            ngrok_process = setup_ngrok()
            print(f"\n🎉 Starting local development server on port {PORT}...")
            print(f"📡 Local server: http://localhost:{PORT}")
            if ngrok_url:
                print(f"🌐 Public URL: {ngrok_url}")
                print(f"\n🔧 Use this webhook URL: {ngrok_url}")
            print("\n⏹️  Press Ctrl+C to stop the bot")

        # Run Flask server
        app.run(debug=False, host="0.0.0.0", port=PORT, use_reloader=False)

    except KeyboardInterrupt:
        print("\n🛑 Stopping local bot...")
        if ngrok_process:
            ngrok_process.terminate()
        print("✅ Bot stopped successfully!")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        if ngrok_process:
            ngrok_process.terminate()
