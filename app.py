import re
import json
import requests
import urllib3
from flask import Flask, request

app = Flask(__name__)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

VERIFY_TOKEN = "verify-me"  # You set this in Facebook webhook config
PAGE_ACCESS_TOKEN = "EAAKSSCUQjUIBPAcbFb9XggBAgBXJKbN42L30MVqCQO1zhw5oJwDjzJgTaPHPYrNrC9LK3sgHVLmv2z9ZCHmfFwHCV96FwguZCEWGSMZBKTGtaqCKvtvK8lUJ16OZCHthKZBUSkm6pgTJOMlOYVomyTyOu63WnsJK3BgOxsF4VCGzogqKjq7lOVPHujuv2SK15kgZDZD"

@app.route('/', methods=['GET'])
def verify():
    # Webhook verification endpoint
    if request.args.get("hub.mode") == "subscribe" and request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge"), 200
    return "Verification failed", 403

@app.route('/', methods=['POST'])
def webhook():
    data = request.get_json()
    for entry in data.get("entry", []):
        for messaging_event in entry.get("messaging", []):
            if messaging_event.get("message"):
                sender_id = messaging_event["sender"]["id"]
                message_text = messaging_event["message"].get("text")
                if message_text:
                    handle_message(sender_id, message_text)
    return "ok", 200

def handle_message(sender_id, message):
    if message.lower().startswith("weather "):
        location = message.split(" ", 1)[1]
        reply = get_weather(location)
    else:
        reply = "Send: 'Weather Manila' or any city to get a forecast 🌦️"
    send_message(sender_id, reply)

def get_weather(place):
    headers = {
        'User-Agent': 'Mozilla/5.0',
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
        res = requests.get('https://openweathermap.org/data/2.5/find', headers=headers, params=params, verify=False)
        match = re.search(r'\((\{.*\})\)', res.text)
        if not match:
            return "❌ Unable to fetch weather data."
        data = json.loads(match.group(1))
        if not data['list']:
            return "❌ City not found."
        weather_data = data['list'][0]
        main = weather_data['main']
        weather = weather_data['weather'][0]
        wind = weather_data['wind']
        clouds = weather_data['clouds']
        temp_c = round(main["temp"] - 273.15, 1)
        feels_c = round(main["feels_like"] - 273.15, 1)
        return (
            f"📍 {weather_data['name']}\n"
            f"🌡️ Temp: {temp_c}°C\n"
            f"🥶 Feels like: {feels_c}°C\n"
            f"💧 Humidity: {main['humidity']}%\n"
            f"🌬️ Wind: {round(wind['speed'] * 3.6)} km/h\n"
            f"☁️ Clouds: {clouds['all']}%\n"
            f"🌈 {weather['description'].capitalize()}"
        )
    except Exception as e:
        return f"⚠️ Error: {str(e)}"

def send_message(recipient_id, text):
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text}
    }
    auth = {"access_token": PAGE_ACCESS_TOKEN}
    requests.post('https://graph.facebook.com/v18.0/me/messages', params=auth, json=payload)
