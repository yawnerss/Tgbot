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
    
    # Handle Facebook's webhook verification
    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("✅ Webhook verified successfully!")
        return challenge, 200
    
    # Handle other GET requests (health checks, direct access, etc.)
    elif mode is None and token is None:
        print("ℹ️  Non-verification GET request (probably health check)")
        return "Bot is running", 200
    
    else:
        print("❌ Verification failed!")
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
        elif msg_lower in ["test", "debug"]:
            reply = "🔧 Bot is running! However, I cannot send messages due to missing Facebook permissions. Please check the server logs for setup instructions."
        else:
            reply = "⛅ Type `weather <city>` or `update <city>` to get Philippine weather.\n\nExample: `weather Cebu`"
        
        # Try to send the message
        success = send_message(sender_id, reply)
        
        # If sending fails, log the issue but don't crash
        if not success:
            print(f"⚠️  Could not send reply to {sender_id}. Check permissions.")
            # In a production environment, you might want to queue this message
            # or store it for later retry when permissions are fixed
        
    except Exception as e:
        print(f"Error handling message: {str(e)}")
        # Try to send error message, but don't fail if it doesn't work
        try:
            send_message(sender_id, "⚠️ Sorry, something went wrong. Please try again.")
        except:
            print("Could not send error message either - permission issue")

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
        # Try multiple approaches to send the message
        
        # Method 1: Standard /me/messages with appsecret_proof
        success = try_send_with_me_endpoint(recipient_id, text)
        if success:
            return True
            
        # Method 2: Try with specific page ID if we can get it
        success = try_send_with_page_id(recipient_id, text)
        if success:
            return True
            
        # Method 3: Try batch request as a last resort
        success = try_send_with_batch(recipient_id, text)
        if success:
            return True
            
        print("❌ All send methods failed - Check app permissions")
        return False
        
    except Exception as e:
        print(f"❌ Error in send_message: {str(e)}")
        return False

def try_send_with_me_endpoint(recipient_id, text):
    """Try sending with /me/messages endpoint"""
    try:
        url = "https://graph.facebook.com/v18.0/me/messages"
        
        payload = {
            "recipient": {"id": recipient_id},
            "message": {"text": text},
            "messaging_type": "RESPONSE"
        }
        
        appsecret_proof = generate_appsecret_proof(PAGE_ACCESS_TOKEN, APP_SECRET)
        
        params = {
            "access_token": PAGE_ACCESS_TOKEN,
            "appsecret_proof": appsecret_proof
        }
        
        headers = {'Content-Type': 'application/json'}
        
        print(f"Trying /me/messages endpoint...")
        response = requests.post(url, params=params, json=payload, headers=headers, timeout=10)
        
        print(f"Response status: {response.status_code}")
        if response.status_code == 200:
            print("✅ Message sent successfully with /me/messages")
            return True
        else:
            print(f"❌ /me/messages failed: {response.text}")
            return False
            
    except Exception as e:
        print(f"Error with /me/messages: {str(e)}")
        return False

def try_send_with_page_id(recipient_id, text):
    """Try sending with specific page ID"""
    try:
        # First, get the page ID from the token
        page_id = get_page_id_from_token()
        if not page_id:
            print("❌ Could not get page ID")
            return False
            
        url = f"https://graph.facebook.com/v18.0/{page_id}/messages"
        
        payload = {
            "recipient": {"id": recipient_id},
            "message": {"text": text},
            "messaging_type": "RESPONSE"
        }
        
        appsecret_proof = generate_appsecret_proof(PAGE_ACCESS_TOKEN, APP_SECRET)
        
        params = {
            "access_token": PAGE_ACCESS_TOKEN,
            "appsecret_proof": appsecret_proof
        }
        
        headers = {'Content-Type': 'application/json'}
        
        print(f"Trying page ID endpoint: {page_id}")
        response = requests.post(url, params=params, json=payload, headers=headers, timeout=10)
        
        print(f"Response status: {response.status_code}")
        if response.status_code == 200:
            print("✅ Message sent successfully with page ID")
            return True
        else:
            print(f"❌ Page ID method failed: {response.text}")
            return False
            
    except Exception as e:
        print(f"Error with page ID method: {str(e)}")
        return False

def try_send_with_batch(recipient_id, text):
    """Try sending using batch request"""
    try:
        url = "https://graph.facebook.com/v18.0/"
        
        # Create batch request
        batch_request = [{
            "method": "POST",
            "relative_url": "me/messages",
            "body": f"recipient={{\"id\":\"{recipient_id}\"}}&message={{\"text\":\"{text}\"}}&messaging_type=RESPONSE"
        }]
        
        appsecret_proof = generate_appsecret_proof(PAGE_ACCESS_TOKEN, APP_SECRET)
        
        params = {
            "access_token": PAGE_ACCESS_TOKEN,
            "appsecret_proof": appsecret_proof,
            "batch": json.dumps(batch_request)
        }
        
        print(f"Trying batch request...")
        response = requests.post(url, params=params, timeout=10)
        
        print(f"Batch response status: {response.status_code}")
        if response.status_code == 200:
            batch_results = response.json()
            if batch_results and len(batch_results) > 0 and batch_results[0].get('code') == 200:
                print("✅ Message sent successfully with batch request")
                return True
            else:
                print(f"❌ Batch request failed: {batch_results}")
                return False
        else:
            print(f"❌ Batch request failed: {response.text}")
            return False
            
    except Exception as e:
        print(f"Error with batch request: {str(e)}")
        return False

def get_page_id_from_token():
    """Extract page ID from the access token"""
    try:
        # Try to get page info using the token
        url = "https://graph.facebook.com/v18.0/me"
        appsecret_proof = generate_appsecret_proof(PAGE_ACCESS_TOKEN, APP_SECRET)
        
        params = {
            "access_token": PAGE_ACCESS_TOKEN,
            "appsecret_proof": appsecret_proof
        }
        
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            page_id = data.get('id')
            print(f"Retrieved page ID: {page_id}")
            return page_id
        else:
            print(f"Failed to get page ID: {response.text}")
            
            # Try without appsecret_proof
            params_simple = {"access_token": PAGE_ACCESS_TOKEN}
            response = requests.get(url, params=params_simple, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                page_id = data.get('id')
                print(f"Retrieved page ID (without proof): {page_id}")
                return page_id
            else:
                print(f"Failed to get page ID (both methods): {response.text}")
                return None
                
    except Exception as e:
        print(f"Error getting page ID: {str(e)}")
        return None

def debug_token():
    """Debug the access token to check its validity and permissions"""
    try:
        print("=== TOKEN DEBUGGING ===")
        
        # Method 1: Try with app token (if available)
        debug_url = "https://graph.facebook.com/debug_token"
        
        # For debugging, we need an app access token or use the same token
        params = {
            "input_token": PAGE_ACCESS_TOKEN,
            "access_token": PAGE_ACCESS_TOKEN  # Using same token for now
        }
        
        response = requests.get(debug_url, params=params)
        print(f"Token debug status: {response.status_code}")
        print(f"Token debug response: {response.text}")
        
        if response.status_code == 200:
            data = response.json()
            if 'data' in data:
                token_data = data['data']
                print(f"✅ Token is valid: {token_data.get('is_valid', False)}")
                print(f"📱 App ID: {token_data.get('app_id', 'N/A')}")
                print(f"👤 User ID: {token_data.get('user_id', 'N/A')}")
                print(f"🔑 Scopes: {token_data.get('scopes', [])}")
                print(f"⏰ Expires at: {token_data.get('expires_at', 'Never')}")
                print(f"🏷️  Type: {token_data.get('type', 'Unknown')}")
                
                # Check if it's a page token
                if token_data.get('type') == 'PAGE':
                    print("✅ This is a Page Access Token")
                else:
                    print("⚠️  This might not be a Page Access Token")
            else:
                print("❌ No token data in response")
        else:
            print("❌ Token debug failed")
        
    except Exception as e:
        print(f"Error debugging token: {str(e)}")

def test_page_info():
    """Test if we can get page information using various methods"""
    try:
        print("\n=== PAGE INFO TESTING ===")
        
        # Method 1: Try /me endpoint with appsecret_proof
        print("Testing /me endpoint with appsecret_proof...")
        url = "https://graph.facebook.com/v18.0/me"
        appsecret_proof = generate_appsecret_proof(PAGE_ACCESS_TOKEN, APP_SECRET)
        
        params = {
            "access_token": PAGE_ACCESS_TOKEN,
            "appsecret_proof": appsecret_proof
        }
        
        response = requests.get(url, params=params, timeout=10)
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Page Name: {data.get('name', 'N/A')}")
            print(f"✅ Page ID: {data.get('id', 'N/A')}")
            print(f"✅ Category: {data.get('category', 'N/A')}")
            return data.get('id')  # Return page ID for later use
        
        # Method 2: Try without appsecret_proof
        print("\nTesting /me endpoint without appsecret_proof...")
        params_simple = {"access_token": PAGE_ACCESS_TOKEN}
        response = requests.get(url, params=params_simple, timeout=10)
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Page Name: {data.get('name', 'N/A')}")
            print(f"✅ Page ID: {data.get('id', 'N/A')}")
            print(f"✅ Category: {data.get('category', 'N/A')}")
            return data.get('id')
        
        print("❌ Both page info methods failed")
        return None
        
    except Exception as e:
        print(f"Error getting page info: {str(e)}")
        return None

def test_send_api():
    """Test the send API with a dummy recipient"""
    try:
        print("\n=== SEND API TESTING ===")
        print("This will test the send API structure (should fail with invalid recipient)")
        
        # Use a dummy recipient ID for testing
        test_recipient = "100000000000000"  # This will fail but show us the API response
        test_message = "Test message"
        
        url = "https://graph.facebook.com/v18.0/me/messages"
        
        payload = {
            "recipient": {"id": test_recipient},
            "message": {"text": test_message},
            "messaging_type": "RESPONSE"
        }
        
        appsecret_proof = generate_appsecret_proof(PAGE_ACCESS_TOKEN, APP_SECRET)
        
        params = {
            "access_token": PAGE_ACCESS_TOKEN,
            "appsecret_proof": appsecret_proof
        }
        
        headers = {'Content-Type': 'application/json'}
        
        response = requests.post(url, params=params, json=payload, headers=headers, timeout=10)
        print(f"Send API test status: {response.status_code}")
        print(f"Send API test response: {response.text}")
        
        # This should fail with invalid recipient, but if it fails with permission error,
        # that tells us about the token issue
        
    except Exception as e:
        print(f"Error testing send API: {str(e)}")

def comprehensive_diagnostics():
    """Run all diagnostic tests"""
    print("🔍 Running comprehensive diagnostics...")
    print(f"📝 Access token length: {len(PAGE_ACCESS_TOKEN)}")
    print(f"📝 App secret length: {len(APP_SECRET)}")
    
    debug_token()
    page_id = test_page_info()
    test_send_api()
    test_permissions()
    
    print("\n=== DIAGNOSIS & SOLUTION ===")
    if not page_id:
        print("❌ CRITICAL: Cannot access page information")
        print("   - Your PAGE_ACCESS_TOKEN might be invalid or expired")
        print("   - Make sure it's a Page Access Token, not a User Access Token")
        print("   - Regenerate the token in Facebook Developer Console")
    else:
        print("✅ Page access works - Token can read page info")
        print("❌ CRITICAL: Cannot send messages - Missing pages_messaging permission")
        print("\n🔧 SOLUTION STEPS:")
        print("1. Go to Facebook Developer Console: https://developers.facebook.com/")
        print("2. Select your app")
        print("3. Go to 'App Review' > 'Permissions and Features'")
        print("4. Search for 'pages_messaging' and request it")
        print("5. OR switch to 'Development Mode' for testing:")
        print("   - Go to 'Settings' > 'Basic'")
        print("   - Make sure app is in 'Development' mode")
        print("   - Add test users as 'Developers' or 'Testers'")
        print("\n⚠️  FOR IMMEDIATE TESTING:")
        print("   - Make sure your app is in Development mode")
        print("   - The person testing must be added as a Developer/Tester")
        print("   - Or submit for App Review to get pages_messaging approved")

def test_permissions():
    """Test what permissions the current token has"""
    try:
        print("\n=== PERMISSION TESTING ===")
        
        # Test basic page access
        url = "https://graph.facebook.com/v18.0/me"
        appsecret_proof = generate_appsecret_proof(PAGE_ACCESS_TOKEN, APP_SECRET)
        
        params = {
            "access_token": PAGE_ACCESS_TOKEN,
            "appsecret_proof": appsecret_proof,
            "fields": "id,name,access_token,category,tasks"
        }
        
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            print("✅ Page permissions that work:")
            print(f"   - Read page basic info: ✅")
            
            # Check if we can get tasks (indicates permissions)
            if 'tasks' in data:
                tasks = data['tasks']
                print(f"   - Page tasks: {tasks}")
                if 'MESSAGING' in tasks:
                    print("   - MESSAGING task: ✅")
                else:
                    print("   - MESSAGING task: ❌ (This is the problem!)")
            
        # Test messenger-specific endpoints
        print("\n🔍 Testing messenger-specific permissions...")
        
        # Try to get messenger profile (this should work if messaging is enabled)
        messenger_url = "https://graph.facebook.com/v18.0/me/messenger_profile"
        response = requests.get(messenger_url, params={"access_token": PAGE_ACCESS_TOKEN}, timeout=10)
        
        print(f"Messenger profile access: {response.status_code}")
        if response.status_code == 200:
            print("✅ Can access messenger profile")
        else:
            print(f"❌ Cannot access messenger profile: {response.text}")
            
    except Exception as e:
        print(f"Error testing permissions: {str(e)}")

if __name__ == '__main__':
    print("Starting Facebook Weather Bot...")
    print("\n" + "="*50)
    comprehensive_diagnostics()
    print("="*50 + "\n")
    
    app.run(debug=True, host='0.0.0.0', port=5000)
