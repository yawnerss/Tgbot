import re
import json
import requests
import urllib3
import hmac
import hashlib
from flask import Flask, request

app = Flask(__name__)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

VERIFY_TOKEN = "verify-me"

# 🔥 REPLACE THIS WITH YOUR PAGE ACCESS TOKEN FROM THE TOKEN FIXER SCRIPT
PAGE_ACCESS_TOKEN = "EAAKSSCUQjUIBPPJcxi3CsHznFdxwQ160air0G2mP0oqMezlniiKZBhcd2ZCnyBnZBwtmKpflYCtZA9Ku5OVzd4tO6FUYLi1W5UDYysZCN8yP3Fm7ElMIlCok03FvZB3weT6XFZCmNLpHaKiBE74fnfFucb5ZAaEqZBJB7xltvc7JKTCfzxKkDrZCKMAIg8oTk0LEIkADI41M8nZC1LoGkGOlgb0BcDOTV5j2co5ZCstOhB6i65cZD"

APP_SECRET = "07f1df1bf9c213eb6a618908fab18189"

def generate_appsecret_proof(access_token, app_secret):
    """Generate the appsecret_proof required by Facebook"""
    try:
        token_bytes = access_token.encode('utf-8')
        secret_bytes = app_secret.encode('utf-8')
        proof = hmac.new(secret_bytes, token_bytes, hashlib.sha256).hexdigest()
        return proof
    except Exception as e:
        print(f"Error generating appsecret_proof: {e}")
        return None

@app.route('/', methods=['GET'])
def verify():
    """Webhook verification endpoint"""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    
    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("✅ Webhook verified successfully!")
        return challenge, 200
    elif mode is None and token is None:
        return "Bot is running", 200
    else:
        return "Verification failed", 403

@app.route('/', methods=['POST'])
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
        
    except Exception as e:
        print(f"Error processing webhook: {str(e)}")
        return "ERROR", 500

def handle_message(sender_id, message):
    """Process incoming messages and send appropriate responses"""
    try:
        msg_lower = message.lower().strip()
        
        if msg_lower.startswith("weather ") or msg_lower.startswith("update "):
            parts = message.split(" ", 1)
            if len(parts) > 1:
                location = parts[1].strip()
                reply = get_weather(location)
            else:
                reply = "⛅ Please specify a city. Example: `weather Manila`"
        elif msg_lower in ["help", "start", "hi", "hello"]:
            reply = "👋 Hello! I'm your Philippine Weather Bot!\n\n⛅ Type `weather <city>` to get weather information.\n\nExample: `weather Manila`"
        else:
            reply = "⛅ Type `weather <city>` to get Philippine weather.\n\nExample: `weather Cebu`"
        
        # Send message using Page Access Token
        success = send_message(sender_id, reply)
        
        if success:
            print("✅ Message sent successfully!")
        else:
            print("❌ Failed to send message")
        
    except Exception as e:
        print(f"Error handling message: {str(e)}")

def get_weather(place):
    """Fetch weather data for the specified location"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': f'https://openweathermap.org/find?q={place}',
    }
    
    params = {
        'callback': 'jQuery',
        'q': place,
        'type': 'like',
        'sort': 'population',
        'cnt': '1',
        'appid': '439d4b804bc8187953eb36d2a8c26a02',
    }
    
    try:
        response = requests.get(
            'https://openweathermap.org/data/2.5/find', 
            headers=headers, 
            params=params, 
            verify=False,
            timeout=10
        )
        
        if response.status_code != 200:
            return "❌ Weather service is currently unavailable."
        
        # Extract JSON from JSONP response
        match = re.search(r'\((\{.*\})\)', response.text)
        if not match:
            return "❌ Couldn't fetch weather info."
        
        data = json.loads(match.group(1))
        
        if not data.get('list') or len(data['list']) == 0:
            return f"❌ City '{place}' not found in the Philippines."
        
        weather_data = data['list'][0]
        
        # Check if it's a Philippine city
        if weather_data.get('sys', {}).get('country') != 'PH':
            return f"🌏 '{place}' is not in the Philippines. Only Philippine cities are supported."
        
        # Extract weather information
        main = weather_data['main']
        weather = weather_data['weather'][0]
        wind = weather_data.get('wind', {})
        clouds = weather_data.get('clouds', {})
        
        # Convert temperature from Kelvin to Celsius
        temp_c = round(main["temp"] - 273.15, 1)
        feels_c = round(main["feels_like"] - 273.15, 1)
        
        # Build weather response
        weather_response = (
            f"📍 {weather_data['name']}, Philippines\n"
            f"🌡️ Temperature: {temp_c}°C\n"
            f"🥶 Feels like: {feels_c}°C\n"
            f"💧 Humidity: {main['humidity']}%\n"
        )
        
        if wind.get('speed'):
            wind_speed = round(wind['speed'] * 3.6, 1)
            weather_response += f"🌬️ Wind: {wind_speed} km/h\n"
        
        if clouds.get('all') is not None:
            weather_response += f"☁️ Clouds: {clouds['all']}%\n"
        
        # Custom weather description handling
        weather_desc = weather['description'].lower()
        if 'overcast' in weather_desc and 'cloud' in weather_desc:
            display_desc = "Overcast cloud baka uulan"
        else:
            display_desc = weather['description'].capitalize()
        
        weather_response += f"🌈 {display_desc}"
        
        return weather_response
        
    except Exception as e:
        return f"⚠️ Error fetching weather: {str(e)}"

def send_message(recipient_id, text):
    """Send message using Page Access Token"""
    try:
        print(f"📤 Sending: '{text[:50]}...' to {recipient_id}")
        
        url = "https://graph.facebook.com/v18.0/me/messages"
        
        payload = {
            "messaging_type": "RESPONSE",
            "recipient": {"id": recipient_id},
            "message": {"text": text}
        }
        
        # Generate appsecret_proof
        appsecret_proof = generate_appsecret_proof(PAGE_ACCESS_TOKEN, APP_SECRET)
        
        params = {
            "access_token": PAGE_ACCESS_TOKEN,
            "appsecret_proof": appsecret_proof
        }
        
        headers = {'Content-Type': 'application/json'}
        
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

def validate_setup():
    """Validate that we have the correct Page Access Token"""
    if PAGE_ACCESS_TOKEN == "REPLACE_WITH_PAGE_TOKEN_FROM_FIXER_SCRIPT":
        print("❌ CRITICAL: You need to replace PAGE_ACCESS_TOKEN!")
        print("❌ Run the token_fixer.py script first to get your Page Access Token")
        return False
    
    print("✅ Page Access Token has been set")
    print(f"📝 Token length: {len(PAGE_ACCESS_TOKEN)}")
    return True

if __name__ == '__main__':
    print("🤖 Starting Corrected Facebook Weather Bot...")
    print("="*50)
    
    if not validate_setup():
        print("\n🔧 STEPS TO FIX:")
        print("1. Run the token_fixer.py script")
        print("2. Copy the Page Access Token it gives you")
        print("3. Replace PAGE_ACCESS_TOKEN in this file")
        print("4. Restart the bot")
        exit(1)
    
    print("🚀 Starting Flask server...")
    app.run(debug=True, host='0.0.0.0', port=5000)
