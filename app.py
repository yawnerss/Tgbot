import re
import json
import requests
import urllib3
import hmac
import hashlib
from flask import Flask, request

app = Flask(__name__)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

VERIFY_TOKEN = "verify-me"  # Must match the one in your FB App
PAGE_ACCESS_TOKEN = "EAAKSSCUQjUIBPPaByGZAt7irvSaOUmqdVqb5w6EpOiLivl5tx9FLtZCn8V3ncJyYA7fo4OwNVMgKzBZABJbPuLoPn3yaxrVDsPXbMDMRZAZC4saxPzr6hD3vxOUQ6hTx3Km23ASMp3FMmdcrHOjia0HVmdAHw1uQBB9b2lZAhtZBuRg94CikZAKZBmxkwZCuXfzEIyuxIgG6JkXwOhfNdVZAXuiiG7qVQxnj8ZCze1T2gx2LXkXYGwZDZD"
APP_SECRET = "4abeeaa775731c09f6b78a4000668a45"  # Your actual App Secret

def generate_appsecret_proof(access_token, app_secret):
    """Generate the appsecret_proof required by Facebook"""
    try:
        # Ensure both are strings and encode properly
        token_bytes = access_token.encode('utf-8')
        secret_bytes = app_secret.encode('utf-8')
        
        # Create HMAC-SHA256 hash
        proof = hmac.new(secret_bytes, token_bytes, hashlib.sha256).hexdigest()
        print(f"Generated appsecret_proof: {proof[:20]}...") # Show first 20 chars for debugging
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
    
    print(f"Verification attempt - Mode: {mode}, Token: {token}")
    
    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("Webhook verified successfully!")
        return challenge, 200
    else:
        print("Verification failed!")
        return "Verification failed", 403

@app.route('/', methods=['POST'])
def webhook():
    """Main webhook endpoint for receiving messages"""
    try:
        data = request.get_json()
        print(f"Received webhook data: {json.dumps(data, indent=2)}")
        
        if data and data.get("object") == "page":
            for entry in data.get("entry", []):
                for event in entry.get("messaging", []):
                    print(f"Processing event: {event}")
                    
                    # Handle regular messages
                    if "message" in event and "text" in event["message"]:
                        sender_id = event["sender"]["id"]
                        message_text = event["message"]["text"]
                        print(f"Message from {sender_id}: {message_text}")
                        handle_message(sender_id, message_text)
                    
                    # Handle postbacks (optional)
                    elif "postback" in event:
                        sender_id = event["sender"]["id"]
                        payload = event["postback"]["payload"]
                        print(f"Postback from {sender_id}: {payload}")
                        handle_message(sender_id, "help")
        
        return "EVENT_RECEIVED", 200
        
    except Exception as e:
        print(f"Error processing webhook: {str(e)}")
        return "ERROR", 500

def handle_message(sender_id, message):
    """Process incoming messages and send appropriate responses"""
    try:
        print(f"Handling message: '{message}' from {sender_id}")
        
        msg_lower = message.lower().strip()
        
        if msg_lower.startswith("weather ") or msg_lower.startswith("update "):
            # Extract location from message
            parts = message.split(" ", 1)
            if len(parts) > 1:
                location = parts[1].strip()
                print(f"Getting weather for: {location}")
                reply = get_weather(location)
            else:
                reply = "⛅ Please specify a city. Example: `weather Manila`"
        elif msg_lower in ["help", "start", "hi", "hello"]:
            reply = "👋 Hello! I'm your Philippine Weather Bot!\n\n⛅ Type `weather <city>` or `update <city>` to get weather information for Philippine cities.\n\nExample: `weather Manila`"
        else:
            reply = "⛅ Type `weather <city>` or `update <city>` to get Philippine weather.\n\nExample: `weather Cebu`"
        
        send_message(sender_id, reply)
        
    except Exception as e:
        print(f"Error handling message: {str(e)}")
        send_message(sender_id, "⚠️ Sorry, something went wrong. Please try again.")

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
        print(f"Fetching weather data for: {place}")
        
        response = requests.get(
            'https://openweathermap.org/data/2.5/find', 
            headers=headers, 
            params=params, 
            verify=False,
            timeout=10
        )
        
        print(f"Weather API response status: {response.status_code}")
        
        if response.status_code != 200:
            return "❌ Weather service is currently unavailable."
        
        # Extract JSON from JSONP response
        match = re.search(r'\((\{.*\})\)', response.text)
        if not match:
            print("No JSON match found in response")
            return "❌ Couldn't fetch weather info."
        
        data = json.loads(match.group(1))
        print(f"Weather data: {data}")
        
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
        
        # Add wind information if available
        if wind.get('speed'):
            wind_speed = round(wind['speed'] * 3.6, 1)  # Convert m/s to km/h
            weather_response += f"🌬️ Wind: {wind_speed} km/h\n"
        
        # Add cloud information if available
        if clouds.get('all') is not None:
            weather_response += f"☁️ Clouds: {clouds['all']}%\n"
        
        # Add weather description
        weather_response += f"🌈 {weather['description'].capitalize()}"
        
        return weather_response
        
    except requests.exceptions.Timeout:
        return "⏰ Request timed out. Please try again."
    except requests.exceptions.RequestException as e:
        print(f"Request error: {str(e)}")
        return "❌ Network error. Please try again later."
    except json.JSONDecodeError as e:
        print(f"JSON decode error: {str(e)}")
        return "❌ Error processing weather data."
    except Exception as e:
        print(f"Weather fetch error: {str(e)}")
        return f"⚠️ Error fetching weather: {str(e)}"

def send_message(recipient_id, text):
    """Send a message to the user via Facebook Messenger API"""
    try:
        # Use the correct page ID from your debug results
        PAGE_ID = "122123589554894945"  # Your actual page ID
        
        url = f"https://graph.facebook.com/v18.0/{PAGE_ID}/messages"
        
        payload = {
            "recipient": {"id": recipient_id},
            "message": {"text": text}
        }
        
        # Generate appsecret_proof for enhanced security
        appsecret_proof = generate_appsecret_proof(PAGE_ACCESS_TOKEN, APP_SECRET)
        
        if not appsecret_proof:
            print("❌ Failed to generate appsecret_proof")
            return
        
        params = {
            "access_token": PAGE_ACCESS_TOKEN,
            "appsecret_proof": appsecret_proof
        }
        
        headers = {
            'Content-Type': 'application/json'
        }
        
        print(f"Sending message to {recipient_id}: {text}")
        print(f"Using URL: {url}")
        print(f"Access token length: {len(PAGE_ACCESS_TOKEN)}")
        print(f"App secret length: {len(APP_SECRET)}")
        
        response = requests.post(
            url,
            params=params,
            json=payload,
            headers=headers,
            timeout=10
        )
        
        print(f"Send message response status: {response.status_code}")
        
        if response.status_code == 200:
            print("✅ Message sent successfully")
            response_data = response.json()
            print(f"Message ID: {response_data.get('message_id', 'N/A')}")
        else:
            print(f"❌ Failed to send message. Status: {response.status_code}")
            print(f"Response: {response.text}")
            
            # Try fallback method
            print("Trying fallback method...")
            alternative_response = send_message_fallback(recipient_id, text)
            if alternative_response:
                print("✅ Fallback method succeeded")
            
    except Exception as e:
        print(f"❌ Error sending message: {str(e)}")

def send_message_fallback(recipient_id, text):
    """Fallback method using 'me' endpoint with appsecret_proof"""
    try:
        url = "https://graph.facebook.com/v18.0/me/messages"
        
        payload = {
            "recipient": {"id": recipient_id},
            "message": {"text": text},
            "messaging_type": "RESPONSE"
        }
        
        # Generate appsecret_proof for enhanced security
        appsecret_proof = generate_appsecret_proof(PAGE_ACCESS_TOKEN, APP_SECRET)
        
        params = {
            "access_token": PAGE_ACCESS_TOKEN,
            "appsecret_proof": appsecret_proof
        }
        
        headers = {
            'Content-Type': 'application/json'
        }
        
        response = requests.post(url, params=params, json=payload, headers=headers, timeout=10)
        
        if response.status_code == 200:
            return True
        else:
            print(f"Fallback also failed: {response.text}")
            return False
            
    except Exception as e:
        print(f"Fallback error: {str(e)}")
        return False

if __name__ == '__main__':
    print("Starting Facebook Weather Bot...")
    app.run(debug=True, host='0.0.0.0', port=5000)
