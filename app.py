import struct
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import os
import platform
import subprocess
import datetime
import webbrowser
import requests
import socket
from newsapi import NewsApiClient
import screen_brightness_control as sbc
from ctypes import cast, POINTER
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
import pythoncom
import cv2
from dotenv import load_dotenv
import google.generativeai as genai
import logging
import threading
import ssl
import re
import ctypes
import sqlite3
import schedule
import time
import pvporcupine
import pyaudio
from plyer import notification
from dateutil.parser import parse
from datetime import datetime, timedelta
from langdetect import detect
import warnings


# Initialize Flask app and enable CORS
app = Flask(__name__)
CORS(app)

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
# Suppress Werkzeug development server warning
warnings.filterwarnings('ignore', message='This is a development server')
logging.getLogger('werkzeug').setLevel(logging.ERROR)

# Load environment variables
load_dotenv()
app.secret_key = os.getenv('FLASK_SECRET_KEY', os.urandom(24).hex())
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
WEATHER_API_KEY = os.getenv('WEATHER_API_KEY')
NEWS_API_KEY = os.getenv('NEWS_API_KEY')
PICOVOICE_ACCESS_KEY = os.getenv('PICOVOICE_ACCESS_KEY')

# Log API key status
logger.debug(f"GEMINI_API_KEY set: {bool(GEMINI_API_KEY)}")
logger.debug(f"WEATHER_API_KEY set: {bool(WEATHER_API_KEY)}")
logger.debug(f"NEWS_API_KEY set: {bool(NEWS_API_KEY)}")
logger.debug(f"PICOVOICE_ACCESS_KEY set: {bool(PICOVOICE_ACCESS_KEY)}")

# Check for missing API keys
required_keys = ['GEMINI_API_KEY', 'WEATHER_API_KEY', 'NEWS_API_KEY', 'PICOVOICE_ACCESS_KEY']
for key in required_keys:
    if not os.getenv(key):
        logger.warning(f"Environment variable {key} is not set. Related features may not work.")

# Initialize SQLite for conversational memory and reminders
conn = sqlite3.connect('jarvis_sessions.db', check_same_thread=False)
c = conn.cursor()
c.execute('CREATE TABLE IF NOT EXISTS sessions (user_id TEXT, query TEXT, response TEXT, timestamp DATETIME)')
c.execute('CREATE TABLE IF NOT EXISTS reminders (user_id TEXT, task TEXT, reminder_time TEXT, created_at DATETIME)')
conn.commit()

# Initialize Gemini model
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    try:
        model = genai.GenerativeModel('gemini-2.0-flash')
        logger.debug("Gemini model initialized with gemini-2.0-flash.")
    except Exception as e:
        logger.warning(f"Failed to initialize gemini-2.0-flash: {str(e)}")
        try:
            models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            logger.debug(f"Available models: {models}")
            if models:
                model = genai.GenerativeModel(models[0])
                logger.debug(f"Falling back to model: {models[0]}")
            else:
                model = None
                logger.warning("No supported models found.")
        except Exception as e:
            model = None
            logger.error(f"Failed to list models: {str(e)}")
else:
    model = None
    logger.warning("GEMINI_API_KEY not set. General queries will not work.")

# Initialize NewsAPI client
newsapi = NewsApiClient(api_key=NEWS_API_KEY) if NEWS_API_KEY else None

# Application paths configuration
# To add a new website: add a new entry with format 'name': {'url': 'https://example.com'}
# To add a new app: add entry with 'win', 'mac', 'linux' keys for platform-specific commands
APPS = {
    'whatsapp': {
        'win': os.getenv('WHATSAPP_PATH', 'start whatsapp'),
        'mac': 'WhatsApp',
        'linux': 'whatsapp-desktop',
        'url': 'https://web.whatsapp.com'
    },
    'youtube': {'url': 'https://www.youtube.com'},
    'facebook': {'url': 'https://www.facebook.com'},
    'google': {'url': 'https://www.google.com'},
    'twitter': {'url': 'https://www.twitter.com'},
    'instagram': {'url': 'https://www.instagram.com'},
    'github': {'url': 'https://www.github.com'},
    'linkedin': {'url': 'https://www.linkedin.com'},
    'reddit': {'url': 'https://www.reddit.com'},
    'stackoverflow': {'url': 'https://stackoverflow.com'},
    'gmail': {'url': 'https://mail.google.com'},
    'netflix': {'url': 'https://www.netflix.com'},
    'calculator': {
        'win': 'calc',
        'mac': 'Calculator',
        'linux': 'gnome-calculator'
    },
    'vscode': {
        'win': os.getenv('VSCODE_PATH', 'code'),
        'mac': 'Visual Studio Code',
        'linux': 'code'
    },
    'chrome': {
        'win': 'chrome',
        'mac': 'Google Chrome',
        'linux': 'google-chrome'
    },
    'edge': {
        'win': 'msedge',
        'mac': 'Microsoft Edge',
        'linux': 'microsoft-edge'
    },
    'firefox': {
        'win': 'firefox',
        'mac': 'Firefox',
        'linux': 'firefox'
    },
    'notepad': {
        'win': 'notepad',
        'mac': 'TextEdit',
        'linux': 'gedit'
    },
    'wikipedia': {'url': 'https://en.wikipedia.org/wiki/Main_Page'}
}

# Camera state
camera_active = False

# Store server port for URL retrieval
JARVIS_PORT = None
JARVIS_HOST = 'localhost'

# Send desktop notification
def send_notification(task):
    notification.notify(
        title="Jarvis Reminder",
        message=f"Reminder: {task}",
        app_name="Jarvis",
        timeout=10
    )

# Set one-time reminder
def set_one_time_reminder(user_id, task, reminder_time_str):
    try:
        parsed_time = parse(reminder_time_str, fuzzy=True, default=datetime.now())
        now = datetime.now()
        # If time is in the past, assume next day
        if parsed_time < now:
            parsed_time = parsed_time + timedelta(days=1)
        seconds_until = (parsed_time - now).total_seconds()
        if seconds_until < 0:
            return "Reminder time is in the past."
        threading.Timer(seconds_until, lambda: send_notification(task)).start()
        formatted_time = parsed_time.strftime('%I:%M %p on %B %d, %Y')
        c.execute("INSERT INTO reminders VALUES (?, ?, ?, ?)", (user_id, task, formatted_time, datetime.now()))
        conn.commit()
        return f"One-time reminder set for '{task}' at {formatted_time}."
    except ValueError:
        return "Invalid time format. Use '3pm', '11:00 AM tomorrow', etc."

# Load reminders from SQLite on startup
def load_reminders():
    c.execute("SELECT user_id, task, reminder_time FROM reminders")
    reminders = c.fetchall()
    for user_id, task, reminder_time in reminders:
        try:
            parsed_time = parse(reminder_time, fuzzy=True, default=datetime.now())
            now = datetime.now()
            if parsed_time > now:
                seconds_until = (parsed_time - now).total_seconds()
                threading.Timer(seconds_until, lambda: send_notification(task)).start()
                logger.debug(f"Loaded reminder: {task} at {reminder_time} for user {user_id}")
        except ValueError:
            logger.warning(f"Invalid reminder time format: {reminder_time}")

# Task scheduling setup
def run_daily_news():
    if newsapi:
        try:
            top = newsapi.get_top_headlines(language='en', page_size=3)
            if top['status'] == 'ok' and top['articles']:
                headlines = [f"{article['title']} from {article['source']['name']}" for article in top['articles']]
                response = f"Daily news: {' | '.join(headlines)}"
                logger.debug(f"Scheduled news: {response}")
                send_notification(response)
                return response
        except Exception as e:
            logger.error(f"Scheduled news error: {str(e)}")
    return "News API unavailable."

schedule.every().day.at("08:00").do(run_daily_news)

def run_continuously():
    cease_continuous_run = threading.Event()
    class ScheduleThread(threading.Thread):
        @classmethod
        def run(cls):
            while not cease_continuous_run.is_set():
                schedule.run_pending()
                time.sleep(1)
    continuous_thread = ScheduleThread()
    continuous_thread.start()
    return cease_continuous_run

cease_run = run_continuously()

# Wake word detection setup
def listen_for_wake_word():
    if not PICOVOICE_ACCESS_KEY:
        logger.warning("PICOVOICE_ACCESS_KEY not set. Wake word detection disabled.")
        return False

    porcupine = None
    pa = None
    audio_stream = None
    try:
        porcupine = pvporcupine.create(access_key=PICOVOICE_ACCESS_KEY, keywords=['jarvis'])
        pa = pyaudio.PyAudio()
        audio_stream = pa.open(
            rate=porcupine.sample_rate,
            channels=1,
            format=pyaudio.paInt16,
            input=True,
            frames_per_buffer=porcupine.frame_length
        )
        logger.debug("Listening for wake word 'JARVIS'...")
        while True:
            pcm = audio_stream.read(porcupine.frame_length, exception_on_overflow=False)
            pcm = struct.unpack_from("h" * porcupine.frame_length, pcm)
            keyword_index = porcupine.process(pcm)
            if keyword_index >= 0:
                logger.debug("Wake word 'JARVIS' detected")
                return True
    except Exception as e:
        logger.error(f"Wake word detection error: {str(e)}")
        return False
    finally:
        if audio_stream:
            audio_stream.close()
        if pa:
            pa.terminate()
        if porcupine:
            porcupine.delete()

# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()
    if not data or 'query' not in data:
        logger.error("Invalid request: No query provided")
        return jsonify({'error': 'No query provided'}), 400

    query = data.get('query', '').lower().strip()
    user_id = data.get('user_id', 'user1')
    logger.debug(f"Raw query received: {query} (user: {user_id})")

    try:
        # Enhanced cleaning to handle "Jarvis," or "Hey Jarvis"
        clean_query = re.sub(r'^(how\s+can\s+i\s+help\s+you\s+today\??\s*)?(hey|ok|jarvis)\s*[,.\s]*(hey\s+jarvis[,.\s]*)*', '', query, flags=re.IGNORECASE).strip()
        commands = [cmd.strip() for cmd in re.split(r'[.!?]', clean_query) if cmd.strip()]
        clean_query = commands[-1] if commands else clean_query
        if not clean_query:
            last_attempt = re.findall(r'\b\w+\b$', query, re.IGNORECASE)
            clean_query = last_attempt[0] if last_attempt else query
        logger.debug(f"Cleaned query: {clean_query}")

        context = ""
        if 'what was' in clean_query or 'previous' in clean_query:
            c.execute("SELECT query, response FROM sessions WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1", (user_id,))
            prev = c.fetchone()
            if prev:
                context = f"Previous query: {prev[0]}\nPrevious response: {prev[1]}"
                logger.debug(f"Retrieved context: {context[:100]}...")

        # Handle commands
        if any(k in clean_query for k in ['jarvis url', 'jarvis link', 'my url', 'my link', 'web url', 'server url', 'local url']):
            # Get the current port from the Flask app context or use stored port
            port = JARVIS_PORT or request.environ.get('SERVER_PORT', '5000')
            host = request.environ.get('SERVER_NAME', JARVIS_HOST)
            if host == '0.0.0.0':
                host = JARVIS_HOST
            jarvis_url = f"http://{host}:{port}"
            response = f"JARVIS is running at: {jarvis_url}\n\nTo access JARVIS, open this URL in your browser:\n{jarvis_url}"
            logger.info(f"🌐 JARVIS URL: {jarvis_url}")
        elif 'time' in clean_query:
            response = f"The current time is {datetime.now().strftime('%I:%M %p')} (IST, {datetime.now().strftime('%B %d, %Y')})."
            logger.debug(f"Time query response: {response}")
        elif 'date' in clean_query:
            response = f"Today's date is {datetime.now().strftime('%B %d, %Y')}."
            logger.debug(f"Date query response: {response}")
        elif 'toggle notebook' in clean_query:
            response = "Toggling notebook visibility."
            logger.debug("Notebook toggle command received")
        elif 'clear notebook' in clean_query:
            response = "Clearing notebook content."
            logger.debug("Notebook clear command received")
        elif 'show my reminders' in clean_query:
            c.execute("SELECT task, reminder_time FROM reminders WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
            reminders = c.fetchall()
            if not reminders:
                response = "No reminders found."
            else:
                reminder_list = [f"{task} at {time}" for task, time in reminders]
                response = "Your reminders:\n" + "\n".join(reminder_list)
            logger.debug(f"Show reminders response: {response}")
        elif 'delete my' in clean_query and 'reminder' in clean_query:
            task = re.sub(r'delete\s+my\s+reminder\s*', '', clean_query, flags=re.IGNORECASE).strip()
            if not task:
                response = "Please specify the task to delete (e.g., 'delete my lecture reminder')."
            else:
                c.execute("DELETE FROM reminders WHERE user_id = ? AND task = ?", (user_id, task))
                conn.commit()
                if c.rowcount > 0:
                    response = f"Reminder for '{task}' deleted."
                else:
                    response = f"No reminder found for '{task}'."
                logger.debug(f"Delete reminder response: {response}")
        elif any(re.search(pattern, clean_query, re.IGNORECASE) for pattern in [
            r'\b(remind\s+(?:me\s+)?|set\s+reminder|reminder\s+(?:for\s+)?)\b',
            r'\b(meeting|lecture|call|task|appointment)\s+(?:reminder|at)\b'
        ]):
            try:
                time_match = re.search(r'(?:at\s+)?(\d{1,2}(?::\d{2})?\s*(am|pm)?(?:\s+tomorrow)?)', clean_query, re.IGNORECASE)
                task_match = re.search(r'(?:remind\s+(?:me\s+)?|set\s+reminder|reminder\s+(?:for\s+)?)(.+?)(?:\s+at\s+\d|\s*$)', clean_query, re.IGNORECASE)
                task = task_match.group(1).strip() if task_match else re.sub(r'(remind\s+(?:me\s+)?|set\s+reminder|reminder\s+(?:for\s+)?).*', '', clean_query, flags=re.IGNORECASE).strip()
                
                if not time_match:
                    response = "Please specify a time (e.g., 'set reminder for meeting at 3pm')."
                else:
                    reminder_time_str = time_match.group(0).strip()
                    if 'tomorrow' in clean_query.lower():
                        parsed_time = parse(reminder_time_str, fuzzy=True, default=datetime.now() + timedelta(days=1))
                    else:
                        parsed_time = parse(reminder_time_str, fuzzy=True, default=datetime.now())
                        # If time is past today, assume tomorrow
                        if parsed_time < datetime.now():
                            parsed_time += timedelta(days=1)
                    formatted_time = parsed_time.strftime('%I:%M %p on %B %d, %Y')
                    response = set_one_time_reminder(user_id, task, formatted_time)
                    logger.debug(f"Reminder scheduled: {task} at {formatted_time}")
            except Exception as e:
                response = f"Error setting reminder: {str(e)}. Try: 'set reminder for meeting at 3pm tomorrow'."
                logger.error(f"Reminder parsing error: {str(e)}")
        elif re.search(r'\b(sleep|standby)\b', clean_query, re.IGNORECASE) and not re.search(r'\b(shutdown|power\s+off)\b', clean_query, re.IGNORECASE):
            system = platform.system().lower()
            if system == 'windows':
                try:
                    subprocess.run(['rundll32.exe', 'powrprof.dll,SetSuspendState', '0,1,0'], check=True)
                    response = "System is entering sleep mode."
                    logger.debug("Sleep command executed")
                except Exception as e:
                    response = f"Error putting system to sleep: {str(e)}"
                    logger.error(f"Sleep error: {str(e)}")
            elif system == 'darwin':
                try:
                    subprocess.run(['pmset', 'sleepnow'], check=True)
                    response = "System is entering sleep mode."
                    logger.debug("Sleep command executed")
                except Exception as e:
                    response = f"Error putting system to sleep: {str(e)}"
                    logger.error(f"Sleep error: {str(e)}")
            else:
                response = "Sleep command not supported on Linux."
                logger.warning("Sleep command attempted on Linux")
        elif 'restart' in clean_query or 'reboot' in clean_query:
            system = platform.system().lower()
            if system != 'windows':
                response = "Restart command only supported on Windows."
                logger.warning("Restart command attempted on non-Windows system")
            else:
                try:
                    subprocess.run(['shutdown', '/r', '/t', '0'], check=True)
                    response = "System is restarting."
                    logger.debug("Restart command executed")
                except Exception as e:
                    response = f"Error restarting system: {str(e)}"
                    logger.error(f"Restart error: {str(e)}")
        elif re.search(r'\b(shutdown|power\s+off)\b', clean_query, re.IGNORECASE):
            system = platform.system().lower()
            if system != 'windows':
                response = "Shutdown command only supported on Windows."
                logger.warning("Shutdown command attempted on non-Windows system")
            else:
                try:
                    subprocess.run(['shutdown', '/s', '/t', '0'], check=True)
                    response = "System is shutting down."
                    logger.debug("Shutdown command executed")
                except Exception as e:
                    response = f"Error shutting down system: {str(e)}"
                    logger.error(f"Shutdown error: {str(e)}")
        elif 'lock' in clean_query:
            system = platform.system().lower()
            if system != 'windows':
                response = "Lock command only supported on Windows."
                logger.warning("Lock command attempted on non-Windows system")
            else:
                try:
                    ctypes.windll.user32.LockWorkStation()
                    response = "System is locked."
                    logger.debug("Lock command executed")
                except Exception as e:
                    response = f"Error locking system: {str(e)}"
                    logger.error(f"Lock error: {str(e)}")
        elif re.search(r'\b(?:https?://|www\.)', clean_query, re.IGNORECASE):
            # Direct URL opening: "open https://example.com" or "open www.example.com"
            url_match = re.search(r'(?:https?://|www\.)[^\s]+', clean_query, re.IGNORECASE)
            if url_match:
                url = url_match.group(0)
                if not url.startswith('http://') and not url.startswith('https://'):
                    url = 'https://' + url
                try:
                    webbrowser.open(url)
                    logger.info(f"🌐 Opening direct URL: {url}")
                    response = f"Opening {url} in browser."
                except Exception as e:
                    response = f"Error opening URL: {str(e)}"
                    logger.error(f"Error opening URL: {str(e)}")
            else:
                response = "Invalid URL format. Use 'open https://example.com' or 'open www.example.com'."
        elif any(k in clean_query for k in ['open', 'kholo', 'khol', 'launch', 'start', 'kholna']):
            # Support variants like "open chrome", "chrome kholo", "kholo chrome", and similar
            app_name = None
            extra_query = ''

            # Try explicit pattern: (open|kholo|khol|launch|start) <app> [extra]
            match = re.search(r'(?:open|kholo|khol|launch|start|kholna)\s+(\w+)(?:\s+(.*))?', clean_query, re.IGNORECASE)
            if match:
                app_name = match.group(1).strip().lower()
                extra_query = match.group(2).strip() if match.group(2) else ''
            else:
                # Try pattern where app name appears first: e.g., "chrome kholo yarr" or "chrome kholo"
                for key in APPS.keys():
                    if re.search(r'\b' + re.escape(key) + r'\b', clean_query, re.IGNORECASE):
                        app_name = key
                        m = re.search(r'\b' + re.escape(key) + r'\b\s*(.*)', clean_query, re.IGNORECASE)
                        extra_query = m.group(1).strip() if m and m.group(1) else ''
                        break

            if not app_name:
                response = "Invalid open command format. Use 'open <app/website> [optional query]'."
                logger.warning(f"Invalid open command format: {clean_query}")
            else:
                logger.debug(f"App name parsed: {app_name}, Extra query: {extra_query}")
                if app_name in APPS:
                    try:
                        system = platform.system().lower()
                        logger.debug(f"Opening {app_name} on {system}")
                        if 'url' in APPS[app_name]:
                            url_to_open = None
                            if app_name == 'youtube' and extra_query:
                                search_query = extra_query.replace(' ', '+')
                                url_to_open = f"{APPS[app_name]['url']}/results?search_query={search_query}"
                                webbrowser.open(url_to_open)
                                response = f"Opening YouTube and searching for '{extra_query}'."
                            elif app_name == 'google' and extra_query:
                                search_query = extra_query.replace(' ', '+')
                                url_to_open = f"{APPS[app_name]['url']}/search?q={search_query}"
                                webbrowser.open(url_to_open)
                                response = f"Opening Google and searching for '{extra_query}'."
                            elif app_name == 'wikipedia' and extra_query:
                                search_query = extra_query.replace(' ', '+')
                                url_to_open = f"https://en.wikipedia.org/w/index.php?search={search_query}"
                                webbrowser.open(url_to_open)
                                response = f"Opening Wikipedia and searching for '{extra_query}'."
                            else:
                                # For websites, fall back to opening the configured URL
                                url_to_open = APPS[app_name]['url']
                                webbrowser.open(url_to_open)
                                response = f"Opening {app_name} in browser."
                            logger.info(f"🌐 Opening URL: {url_to_open}")
                            logger.debug(f"Successfully opened {app_name}")
                        else:
                            # Native app launch
                            app_command = None
                            jarvis_url_to_open = None
                            # If opening Chrome or Edge, optionally open JARVIS URL
                            if app_name in ['chrome', 'edge'] and JARVIS_PORT:
                                jarvis_url_to_open = f"http://localhost:{JARVIS_PORT}"
                            
                            if system == 'windows':
                                # Using shell=True to allow commands like 'start' or app names available in PATH
                                app_command = APPS[app_name]['win']
                                if jarvis_url_to_open:
                                    # Open browser with JARVIS URL
                                    if app_name == 'chrome':
                                        subprocess.Popen([app_command, jarvis_url_to_open], shell=True)
                                    elif app_name == 'edge':
                                        subprocess.Popen([app_command, jarvis_url_to_open], shell=True)
                                    logger.info(f"🚀 Opening {app_name} with JARVIS URL: {jarvis_url_to_open}")
                                    response = f"Opening {app_name} with JARVIS interface at {jarvis_url_to_open}."
                                else:
                                    subprocess.Popen(app_command, shell=True)
                                    logger.info(f"🚀 Opening app: {app_name} with command: {app_command}")
                                    response = f"Opening {app_name}."
                            elif system == 'darwin':
                                app_command = f"open -a {APPS[app_name]['mac']}"
                                if jarvis_url_to_open:
                                    subprocess.run(['open', '-a', APPS[app_name]['mac'], jarvis_url_to_open])
                                    logger.info(f"🚀 Opening {app_name} with JARVIS URL: {jarvis_url_to_open}")
                                    response = f"Opening {app_name} with JARVIS interface at {jarvis_url_to_open}."
                                else:
                                    subprocess.run(['open', '-a', APPS[app_name]['mac']])
                                    logger.info(f"🚀 Opening app: {app_name} with command: {app_command}")
                                    response = f"Opening {app_name}."
                            elif system == 'linux':
                                app_command = APPS[app_name]['linux']
                                if jarvis_url_to_open:
                                    subprocess.run([app_command, jarvis_url_to_open])
                                    logger.info(f"🚀 Opening {app_name} with JARVIS URL: {jarvis_url_to_open}")
                                    response = f"Opening {app_name} with JARVIS interface at {jarvis_url_to_open}."
                                else:
                                    subprocess.run([app_command])
                                    logger.info(f"🚀 Opening app: {app_name} with command: {app_command}")
                                    response = f"Opening {app_name}."
                            logger.debug(f"Successfully opened {app_name}")
                    except Exception as e:
                        response = f"Error opening {app_name}: {str(e)}"
                        logger.error(f"Error opening {app_name}: {str(e)}")
                else:
                    response = f"Application or website '{app_name}' not supported."
                    logger.warning(f"Unsupported app/website: {app_name}")
        elif 'whatsapp message' in clean_query:
            # Command: "send a whatsapp message to [number] saying [message]"
            match = re.search(r'whatsapp message to\s+([\d\s\+]+?)\s+saying\s+(.*)', clean_query, re.IGNORECASE)
            if match:
                phone_number = re.sub(r'\s+', '', match.group(1)) # Remove spaces from number
                message_text = match.group(2).strip()
                
                # Basic validation for phone number (e.g., starts with + and has digits)
                if not re.match(r'^\+?\d+$', phone_number):
                    response = f"Invalid phone number format: {phone_number}. Please include the country code."
                else:
                    url = f"https://web.whatsapp.com/send?phone={phone_number}&text={requests.utils.quote(message_text)}"
                    webbrowser.open(url)
                    response = f"Opening WhatsApp to send a message to {phone_number}. The message box will be focused; press Enter to send."
            else:
                response = "I couldn't understand the phone number or message. Please say, 'send a whatsapp message to [phone number with country code] saying [your message]'."
        elif 'weather' in clean_query:
            if not WEATHER_API_KEY:
                response = "Weather API key not configured."
                logger.warning("Weather API key missing")
            else:
                city_match = re.search(r'(?:weather|was the weather)\s*(?:in)?\s*([\w\s]+)', clean_query, re.IGNORECASE)
                city = city_match.group(1).strip() if city_match else 'Delhi'
                logger.debug(f"City parsed: {city}")
                try:
                    url = f'http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric'
                    res = requests.get(url)
                    data = res.json()
                    if data.get('cod') != 200:
                        response = f"Weather error: {data.get('message', 'City not found')} (cod: {data.get('cod')})"
                        logger.error(f"Weather API error for {city}: {data.get('message', 'No detail')} (cod: {data.get('cod')})")
                    else:
                        response = f"Weather in {data['name']}: {data['main']['temp']}°C, {data['weather'][0]['description']}."
                        logger.debug(f"Weather response for {city}: {response}")
                except Exception as e:
                    response = f"Error fetching weather: {str(e)}"
                    logger.error(f"Weather API error: {str(e)}")
        elif 'news' in clean_query or 'headlines' in clean_query:
            if not newsapi:
                response = "News API key not configured."
                logger.warning("News API key missing")
            else:
                try:
                    top = newsapi.get_top_headlines(language='en', page_size=3)
                    if top['status'] != 'ok' or not top['articles']:
                        response = "No news available."
                        logger.warning("No news articles found")
                    else:
                        headlines = [f"{article['title']} from {article['source']['name']}" for article in top['articles']]
                        response = f"Top headlines: {' | '.join(headlines)}"
                        logger.debug(f"News response: {response}")
                except Exception as e:
                    response = f"Error fetching news: {str(e)}"
                    logger.error(f"News API error: {str(e)}")
        elif 'volume' in clean_query:
            if platform.system() != 'Windows':
                response = "Volume control only supported on Windows."
                logger.warning("Volume control attempted on non-Windows system")
            else:
                try:
                    pythoncom.CoInitialize()
                    level_str = re.search(r'\d+', clean_query)
                    level = int(level_str.group()) / 100 if level_str else 0.5
                    logger.debug(f"Volume level parsed: {level}")
                    devices = AudioUtilities.GetSpeakers()
                    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                    volume = cast(interface, POINTER(IAudioEndpointVolume))
                    volume.SetMasterVolumeLevelScalar(min(max(level, 0.0), 1.0), None)
                    response = f"Volume set to {int(level * 100)}%."
                    logger.debug(f"Volume set to {level}")
                except Exception as e:
                    response = f"Error setting volume: {str(e)}. Ensure you're on Windows and try again."
                    logger.error(f"Volume control error: {str(e)}")
                finally:
                    pythoncom.CoUninitialize()
        elif 'brightness' in clean_query:
            try:
                level_str = re.search(r'\d+', clean_query)
                level = int(level_str.group()) if level_str else 50
                logger.debug(f"Brightness level parsed: {level}")
                sbc.set_brightness(level)
                response = f"Brightness set to {level}%."
                logger.debug(f"Brightness set to {level}")
            except Exception as e:
                response = f"Error setting brightness: {str(e)}"
                logger.error(f"Brightness control error: {str(e)}")
        elif 'camera' in clean_query:
            global camera_active
            if camera_active:
                response = "Camera is already active."
                logger.warning("Camera already active")
            else:
                try:
                    camera_active = True
                    def open_camera():
                        global camera_active
                        cap = cv2.VideoCapture(0)
                        if not cap.isOpened():
                            logger.error("Could not open camera")
                            camera_active = False
                            return
                        while camera_active:
                            ret, frame = cap.read()
                            if not ret:
                                logger.error("Failed to capture frame")
                                break
                            cv2.imshow('JARVIS Camera', frame)
                            key = cv2.waitKey(1) & 0xFF
                            if key == ord('c'):
                                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                                cv2.imwrite(f"capture_{timestamp}.jpg", frame)
                                logger.debug(f"Picture captured")
                            elif key == ord('q'):
                                logger.debug("Camera feed closed by 'q' key")
                                break
                        cap.release()
                        cv2.destroyAllWindows()
                        camera_active = False
                    threading.Thread(target=open_camera, daemon=True).start()
                    response = "Camera opened. Press 'c' to capture, 'q' to quit."
                    logger.debug("Camera command initiated")
                except Exception as e:
                    camera_active = False
                    response = f"Error opening camera: {str(e)}"
                    logger.error(f"Camera error: {str(e)}")
        elif 'note' in clean_query or 'generate note' in clean_query:
            if not model:
                response = "Note generation unavailable without Gemini API key."
                logger.warning("Note generation attempted without Gemini API key")
            else:
                topic = re.sub(r'(generate\s+)?note\s*', '', clean_query, flags=re.IGNORECASE).strip() or 'general note'
                logger.debug(f"Note topic parsed: {topic}")
                try:
                    prompt = f"Summarize key information about {topic} in a concise note format."
                    if context:
                        prompt += f"\nContext: {context}"
                    gemini_response = model.generate_content(prompt)
                    if not gemini_response.text:
                        response = "No response received from Gemini for note generation."
                        logger.warning("Gemini returned an empty response for note.")
                    else:
                        response = gemini_response.text.strip()
                        logger.debug(f"Note generated: {response[:100]}...")
                except Exception as e:
                    response = f"Error generating note: {str(e)}. Check GEMINI_API_KEY or API quotas."
                    logger.error(f"Note generation error: {str(e)}")
        elif re.search(r'(write\s+a\s+program|code\s+in|give\s+me\s+a\s+code)\s+(java|python|javascript|c\+\+|c#)', clean_query, re.IGNORECASE):
            if not model:
                response = "Code generation unavailable without Gemini API key."
                logger.warning("Code generation attempted without Gemini API key")
            else:
                try:
                    match = re.search(r'(write\s+a\s+program|code\s+in|give\s+me\s+a\s+code)\s+(java|python|javascript|c\+\+|c#)\s*(.*)', clean_query, re.IGNORECASE)
                    language = match.group(2).lower() if match else 'python'
                    code_topic = match.group(3).strip() or 'hello world'
                    logger.debug(f"Code language: {language}, Topic: {code_topic}")
                    prompt = f"""
Provide a comprehensive and educational response for writing a {language} program for '{code_topic}'. Include:
1. At least three distinct code examples, showcasing different approaches or variations.
2. Detailed explanation for each example, covering syntax, key components, logic, and use cases.
3. Step-by-step instructions for compiling and running each example.
4. Relevant best practices for {language} programming.
5. Additional insights or tips.
Format code in ```{language}``` blocks.
"""
                    if context:
                        prompt += f"\nContext: {context}"
                    gemini_response = model.generate_content(prompt)
                    if not gemini_response.text:
                        response = f"No {language} code example received from Gemini."
                        logger.warning(f"Gemini returned an empty response for {language} code.")
                    else:
                        response = gemini_response.text.strip()
                        logger.debug(f"{language.capitalize()} code generated: {response[:100]}...")
                except Exception as e:
                    response = f"Error generating {language} code: {str(e)}."
                    logger.error(f"Code generation error: {str(e)}")
        else:
            if model:
                try:
                    logger.debug(f"Sending to Gemini: '{clean_query}'")
                    prompt = f"Summarize key information about {clean_query} in a concise format."
                    if context:
                        prompt += f"\nContext: {context}"
                    gemini_response = model.generate_content(prompt)
                    if not gemini_response.text:
                        response = "No response received from Gemini."
                        logger.warning("Gemini returned an empty response.")
                    else:
                        response = gemini_response.text.strip()
                        logger.debug(f"Gemini response: {response[:100]}...")
                except Exception as e:
                    response = f"Error processing query: {str(e)}."
                    logger.error(f"Gemini error: {str(e)}")
            else:
                response = "General query support unavailable. Please check GEMINI_API_KEY."
                logger.error("Gemini model unavailable.")

        c.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", (user_id, clean_query, response, datetime.now()))
        conn.commit()

        logger.debug(f"Response: {response[:100]}...")
        return jsonify({'response': response})

    except Exception as e:
        error_message = f"Error processing request: {str(e)}"
        logger.error(error_message)
        return jsonify({'error': error_message}), 500

@app.route('/close_camera', methods=['POST'])
def close_camera():
    global camera_active
    if not camera_active:
        return jsonify({'response': "No active camera to close."})
    try:
        camera_active = False
        cv2.destroyAllWindows()
        response = "Camera closed."
        logger.debug(response)
        return jsonify({'response': response})
    except Exception as e:
        error_message = f"Error closing camera: {str(e)}"
        logger.error(error_message)
        return jsonify({'error': error_message}), 500


# ---------- AI Friend Function (POST) ----------
def ai_friend_reply(text: str) -> str:
    """Return a short, friendly reply using Gemini.

    Raises RuntimeError when GEMINI_API_KEY is not configured or on API errors.
    """
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY not configured for ai_friend_reply")
        raise RuntimeError("GEMINI_API_KEY not configured")
    try:
        # Detect language from user input and include it in the prompt so
        # the model replies in the same language.
        detected_lang = 'en'
        try:
            if text and text.strip():
                detected_lang = detect(text)
                logger.debug(f"Detected language for ai_friend_reply: {detected_lang}")
        except Exception as le:
            logger.warning(f"Language detection failed, defaulting to 'en': {le}")

        # Instruct model to reply in detected language; provide explicit language code
        prompt = (
            "Act like a friendly human friend. "
            "Reply casually, short and natural. "
            f"Detected language: {detected_lang}. Reply in the same language as the user. "
            f"Message: {text}"
        )
        gemini_response = model.generate_content(prompt)
        # Some SDK responses place text on .text, ensure safe access
        reply_text = ""
        if gemini_response is not None and getattr(gemini_response, 'text', None):
            reply_text = gemini_response.text.strip()
        elif gemini_response is not None and getattr(gemini_response, 'content', None):
            # fallback if SDK uses different attribute name
            reply_text = str(gemini_response.content).strip()
        return reply_text
    except Exception as e:
        logger.error(f"ai_friend_reply error: {e}")
        raise


@app.route('/chat', methods=['POST'])
def chat_post():
    """POST endpoint that returns a friendly short reply from the AI friend.

    Accepts JSON: { "text": "..." }
    Returns JSON: { "user_message": "...", "friend_reply": "..." }
    """
    data = request.get_json()
    text = data.get('text', '').strip() if data else ''
    if not text:
        return jsonify({"error": "Empty message"}), 400
    if not GEMINI_API_KEY:
        return jsonify({"error": "GEMINI_API_KEY not configured"}), 503
    try:
        reply = ai_friend_reply(text)
        return jsonify({
            "user_message": text,
            "friend_reply": reply
        })
    except Exception as e:
        logger.error(f"AI friend chat error: {e}")
        return jsonify({"error": str(e)}), 500

def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0

if __name__ == '__main__':
    load_reminders()
    if PICOVOICE_ACCESS_KEY:
        threading.Thread(target=listen_for_wake_word, daemon=True).start()

    ports = [5000, 5001, 5002]
    selected_port = None
    for port in ports:
        if not is_port_in_use(port):
            selected_port = port
            JARVIS_PORT = port
            logger.info(f"Selected port: {port}")
            logger.info(f"🌐 JARVIS URL: http://localhost:{port}")
            break
    if not selected_port:
        logger.error("All attempted ports (5000, 5001, 5002) are in use.")
        exit(1)

    try:
        cert_path = os.getenv('SSL_CERT_PATH')
        key_path = os.getenv('SSL_KEY_PATH')
        if cert_path and key_path and os.path.exists(cert_path) and os.path.exists(key_path):
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certfile=cert_path, keyfile=key_path)
            logger.debug("SSL certificate loaded successfully.")
            app.run(host='0.0.0.0', port=selected_port, ssl_context=context, debug=True)
        else:
            logger.warning("SSL certificates not found or not configured. Falling back to HTTP...")
            app.run(host='0.0.0.0', port=selected_port, debug=True)
    except Exception as e:
        logger.error(f"Failed to start server on port {selected_port}: {str(e)}")
        exit(1)