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
PAGE_ACCESS_TOKEN = "EAAKSSCUQjUIBPPaByGZAt7irvSaOUmqdVqb5w6EpOiLivl5tx9FLtZCn8V3ncJyYA7fo4OwNVMgKzBZABJbPuLoPn3yaxrVDsPXbMDMRZAZC4saxPzr6hD3vxOUQ6hTx3Km23ASMp3FMmdcrHOjia0HVmdAHw1uQBB9b2lZAhtZBuRg94CikZAKZBmxkwZCuXfzEIyuxIgG6JkXwOhfNdVZAXuiiG7qVQxnj8ZCze1T2gx2LXkXYGwZDZD"
APP_SECRET = "4abeeaa775731c09f6b78a4000668a45"

# Your actual page ID that has messaging permissions
PAGE_ID = "715906884939884"

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
        
        # Simple send - just try once with the correct setup
        send_message_simple(sender_id, reply)
        
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
        
        weather_response += f"🌈 {weather['description'].capitalize()}"
        
        return weather_response
        
    except Exception as e:
        return f"⚠️ Error fetching weather: {str(e)}"

def send_message_simple(recipient_id, text):
    """Simple message sending - one method, clear logging"""
    try:
        print(f"📤 Attempting to send: '{text[:50]}...' to {recipient_id}")
        
        # Use the page ID directly with v23.0 API
        url = f"https://graph.facebook.com/v23.0/{PAGE_ID}/messages"
        
        payload = {
            "recipient": {"id": recipient_id},
            "message": {"text": text},
            "messaging_type": "RESPONSE"
        }
        
        # Generate appsecret_proof
        appsecret_proof = generate_appsecret_proof(PAGE_ACCESS_TOKEN, APP_SECRET)
        if not appsecret_proof:
            print("❌ Failed to generate appsecret_proof")
            return False
        
        params = {
            "access_token": PAGE_ACCESS_TOKEN,
            "appsecret_proof": appsecret_proof
        }
        
        headers = {'Content-Type': 'application/json'}
        
        # Make the request
        response = requests.post(url, params=params, json=payload, headers=headers, timeout=15)
        
        print(f"📡 Response Status: {response.status_code}")
        print(f"📡 Response Body: {response.text}")
        
        if response.status_code == 200:
            print("✅ MESSAGE SENT SUCCESSFULLY!")
            result = response.json()
            print(f"✅ Message ID: {result.get('message_id', 'N/A')}")
            return True
        else:
            print(f"❌ FAILED TO SEND MESSAGE")
            print(f"❌ Status: {response.status_code}")
            print(f"❌ Error: {response.text}")
            return False
        
    except requests.exceptions.Timeout:
        print("❌ Request timed out")
        return False
    except Exception as e:
        print(f"❌ Exception occurred: {str(e)}")
        return False

def test_send():
    """Test function to verify sending works"""
    print("🧪 Testing message sending...")
    
    # Test with a dummy recipient (this will fail but show us if the API structure is correct)
    test_recipient = "100000000000000"
    test_message = "Test message from bot"
    
    result = send_message_simple(test_recipient, test_message)
    
    if result:
        print("✅ Test passed - API structure is correct")
    else:
        print("❌ Test failed - Check the logs above for details")

if __name__ == '__main__':
    print("🤖 Starting SIMPLE Facebook Weather Bot...")
    print(f"📝 Using Page ID: {PAGE_ID}")
    print(f"📝 Token length: {len(PAGE_ACCESS_TOKEN)}")
    print(f"📝 App Secret length: {len(APP_SECRET)}")
    
    print("\n" + "="*50)
    test_send()
    print("="*50 + "\n")
    
    print("🚀 Starting Flask server...")
    app.run(debug=True, host='0.0.0.0', port=5000)
