import os
import json
import random
import asyncio
import sys
import time
import warnings

# --- 🔇 SUPPRESS WARNINGS ---
os.environ["GRPC_VERBOSITY"] = "ERROR"
os.environ["GLOG_minloglevel"] = "2"
warnings.filterwarnings("ignore")

import google.generativeai as genai
from telegram import Bot

# --- 🔐 SECRETS MANAGEMENT ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
KEYS_STRING = os.environ.get("GEMINI_KEYS")

if not TELEGRAM_TOKEN or not KEYS_STRING:
    try:
        import toml
        script_dir = os.path.dirname(os.path.abspath(__file__))
        secrets_path = os.path.join(script_dir, ".streamlit", "secrets.toml")
        with open(secrets_path, "r") as f:
            local_secrets = toml.load(f)
            TELEGRAM_TOKEN = TELEGRAM_TOKEN or local_secrets.get("TELEGRAM_TOKEN")
            raw_keys = local_secrets.get("GEMINI_KEYS")
            if isinstance(raw_keys, list):
                GEMINI_API_KEYS = raw_keys
            elif isinstance(raw_keys, str):
                GEMINI_API_KEYS = raw_keys.split(",")
            else:
                GEMINI_API_KEYS = []
    except Exception:
        pass
else:
    GEMINI_API_KEYS = KEYS_STRING.split(",") if KEYS_STRING else []

GEMINI_API_KEYS = [k.strip() for k in GEMINI_API_KEYS if k.strip()]

if not TELEGRAM_TOKEN or not GEMINI_API_KEYS:
    print("❌ FATAL ERROR: Secrets not found.")
    sys.exit(1)

# --- 🎯 TARGET CONFIGURATION ---
TARGET_IDS = [
    "6882899041",            # Your Personal ID
    "-1003540692903"         # The Community Channel
]

CURRENT_KEY_INDEX = 0

# --- CONFIGURATION & ROTATION ---
def configure_genai():
    global CURRENT_KEY_INDEX
    if not GEMINI_API_KEYS: return
    key = GEMINI_API_KEYS[CURRENT_KEY_INDEX]
    try:
        genai.configure(api_key=key)
    except Exception as e:
        print(f"⚠️ Config Error on Key #{CURRENT_KEY_INDEX+1}: {e}")

def rotate_key():
    global CURRENT_KEY_INDEX
    if len(GEMINI_API_KEYS) > 1:
        CURRENT_KEY_INDEX = (CURRENT_KEY_INDEX + 1) % len(GEMINI_API_KEYS)
        print(f"🔄 Rotating to Backup Key #{CURRENT_KEY_INDEX + 1}...")
        configure_genai()
        global model
        model = get_valid_model() 
        return True
    return False

# 📡 SONAR SCANNER
def get_valid_model():
    print("🔍 Sonar Scanning for valid models...")
    try:
        models = list(genai.list_models())
        valid_models = [m.name for m in models if 'generateContent' in m.supported_generation_methods]
        
        # 1. Look for standard 1.5 flash
        for m in valid_models:
            if 'gemini-1.5-flash' in m and 'latest' not in m and 'exp' not in m:
                print(f"✅ Locked on target: {m}")
                return genai.GenerativeModel(m.replace("models/", ""))
        
        # 2. Look for ANY flash
        for m in valid_models:
             if 'flash' in m and 'gemini-2' not in m and 'exp' not in m:
                print(f"⚠️ Flash Fallback: {m}")
                return genai.GenerativeModel(m.replace("models/", ""))

        if valid_models:
            return genai.GenerativeModel(valid_models[0].replace("models/", ""))
            
    except Exception as e:
        print(f"⚠️ Scan failed: {e}")
    
    print("🤞 Sonar failed. Forcing 'gemini-1.5-flash'...")
    return genai.GenerativeModel('gemini-1.5-flash')

configure_genai()
model = get_valid_model()

# 🛡️ SAFE GENERATOR
def generate_content_safe(prompt_text):
    global model
    max_retries = 3
    for attempt in range(max_retries):
        try:
            return model.generate_content(prompt_text)
        except Exception as e:
            err_msg = str(e)
            if "404" in err_msg:
                print("⚠️ Model 404. Re-scanning...")
                model = get_valid_model()
                time.sleep(1)
                continue
            elif "429" in err_msg or "403" in err_msg:
                print(f"⏳ API Issue ({err_msg}). Rotating...")
                if rotate_key():
                    time.sleep(2)
                    continue
                else:
                    time.sleep(10)
            else:
                print(f"❌ API Error: {err_msg}")
                return None
    return None

# 🛡️ ROBUST MESSAGE SENDER (Splits Long Texts)
async def send_safe_message(bot, chat_id, text):
    # Telegram hard limit is 4096. We use 4000 to be safe.
    MAX_LENGTH = 4000 

    # Helper to send a single chunk safely
    async def send_chunk(chunk):
        try:
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode='HTML')
        except Exception as e:
            # If HTML fails (e.g. we sliced a <b> tag in half), send raw text
            print(f"⚠️ HTML formatting failed for chunk, sending raw: {e}")
            await bot.send_message(chat_id=chat_id, text=chunk)

    if len(text) <= MAX_LENGTH:
        await send_chunk(text)
    else:
        # ✂️ It's too big. Split it.
        lines = text.split('\n')
        current_chunk = ""
        
        for line in lines:
            if len(current_chunk) + len(line) + 1 > MAX_LENGTH:
                # Send what we have so far
                await send_chunk(current_chunk)
                current_chunk = ""
            
            current_chunk += line + "\n"
        
        # Send the leftovers
        if current_chunk:
            await send_chunk(current_chunk)

# 📡 BROADCAST HELPER
async def broadcast_message(bot, text):
    """Sends a text message to all targets in TARGET_IDS"""
    for chat_id in TARGET_IDS:
        if "REPLACE" in chat_id: continue
        try:
            print(f"📤 Sending to {chat_id}...")
            await send_safe_message(bot, chat_id, text)
        except Exception as e:
            print(f"⚠️ Broadcast failed for {chat_id}: {e}")

# --- 💾 STATE MANAGEMENT ---
STATE_FILE = "orbit_state.json"

def load_state():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(script_dir, STATE_FILE)
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_state(state):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(script_dir, STATE_FILE)
    with open(path, 'w') as f:
        json.dump(state, f)

def load_config():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, 'config.json')
    try:
        with open(config_path, 'r') as f: return json.load(f)
    except FileNotFoundError: return None

# 🚀 MAIN CHAOS ENGINE
async def send_chaos():
    bot = Bot(token=TELEGRAM_TOKEN)
    config = load_config()
    if not config: return 

    # ---------------------------------------------------------
    # 1. THE REVEAL (Checking for Cliffhangers)
    # ---------------------------------------------------------
    state = load_state()
    if state.get("pending_diagnosis"):
        print("🕵️‍♂️ Found a pending diagnosis. Revealing now...")
        diagnosis_text = state["pending_diagnosis"]
        
        reveal_msg = f"🧬 <b>DIAGNOSIS REVEALED (From previous case)</b>\n\n{diagnosis_text}"
        await broadcast_message(bot, reveal_msg)
        
        # Clear the state but DON'T RETURN. Continue to the roll.
        state["pending_diagnosis"] = None
        save_state(state)
        print("✅ Diagnosis revealed. Proceeding to roll...")
        
        # Add a small delay so messages don't stack up instantly
        time.sleep(3)

    # ---------------------------------------------------------
    # 2. THE ROLL
    # ---------------------------------------------------------
    # DEBUG OVERRIDES
    if "--quiz" in sys.argv: roll = 90
    elif "--god" in sys.argv: roll = 100
    elif "--fact" in sys.argv: roll = 60
    else: roll = random.randint(1, 100)
    
    print(f"🎲 Rolled a {roll}")

    if roll <= 20:
        print("Silence is golden.")
        return

    # --- FACT MODE (21-45) ---
    elif 21 <= roll <= 45:
        topic = random.choice(config['interests'])
        prompt = f"Tell me a mind-blowing, short random fact about {topic}. Keep it under 2 sentences."
        response = generate_content_safe(prompt)
        if response and response.text:
            msg = f"🎱 <b>Magic-∞ Fact:</b>\n\n{response.text}"
            await broadcast_message(bot, msg)

    # --- MULTI-QUIZ MODE (46-80) ---
    elif 46 <= roll <= 80:
        quotes = [
            "Your coffee dependency is clinical at this point. ☕🩺",
            "Palpate the hustle. Percuss the procrastination. 🔨",
            "Don't be the reason the attending sighs today. 😤",
            "Reviewing notes > Doomscrolling. 📱🚫",
            "You didn't survive Anatomy to die in Clinicals. Lock in. 💀",
            "Sleep is for the weak... but also for memory consolidation. Go to bed. 🛌, but first ...",
            "A's get degrees, but knowledge saves lives. 🏥",
            "Is it imposter syndrome, or do you just need to study more? 📖👀",
            "Treat your goals like a critical patient: Constant monitoring required. 📉",
            "Future Dr. in the making. Act like it. 🥼"
        ]
        
        unit = random.choice(config['current_units'])
        quote = random.choice(quotes)
        num_q = random.randint(1, 7) 
        
        await broadcast_message(bot, f"🚨 <b>{quote}</b>\n\nIncoming Rapid Fire: <b>{num_q} Questions on {unit}</b>")
        
        prompt = f"""
        Generate {num_q} multiple-choice questions about {unit} for a 2nd Year Student, make the questions relevant to the course BSC in Clinical Medicine and Surgery.
        Strict JSON format: Return a LIST of objects.
        [ {{"question": "...", "options": ["A","B","C","D"], "correct_id": 0, "explanation": "..."}} ]
        Limits: Question < 250 chars, Options < 100 chars.
        """.replace("{num_questions}", str(num_q))

        response = generate_content_safe(prompt)
        
        if response and response.text:
            try:
                text = response.text.replace('```json', '').replace('```', '').strip()
                data = json.loads(text)
                if isinstance(data, dict): data = [data]
                
                for i, q in enumerate(data):
                    for chat_id in TARGET_IDS:
                        if "REPLACE" in chat_id: continue
                        try:
                            await bot.send_poll(
                                chat_id=chat_id,
                                question=f"[{i+1}/{len(data)}] {q['question'][:290]}",
                                options=[o[:97] for o in q['options']],
                                type="quiz",
                                correct_option_id=q['correct_id'],
                                explanation=q['explanation'][:190]
                            )
                        except Exception as e:
                            print(f"⚠️ Poll failed for {chat_id}: {e}")
                    time.sleep(2) 
            except Exception as e:
                print(f"Quiz Parse Error: {e}")

    # --- 👑 GOD MODE: THE CLIFFHANGER (81-100) ---
    else:
        await broadcast_message(bot, "👑 <b>GOD MODE ACTIVATED: THE MYSTERY CASE</b> 👑\n\n<i>Searching global medical archives...</i>")
        
        god_prompt = """
        ACT AS: A Senior Consultant at a top-tier research hospital.
        TASK: Present a "Medical Mystery" case study for a final year student.
        TOPIC: A rare, baffling, or catastrophic condition (Any field: Toxicology, Neuro, ID, Genetics).
        
        STRICT FORMATTING RULES:
        1. Do NOT use Markdown (no ##, no **, no __).
        2. Use only these HTML tags: <b>bold</b>, <i>italic</i>, <u>underline</u>, <span class="tg-spoiler">hidden</span>.
        3. Split the response into two distinct parts separated by the text "||REVEAL||".
        
        PART 1 (The Presentation):
        - Start with <b>PATIENT DEMOGRAPHICS:</b>
        - <b>VITALS & LABS:</b> Use <u>underline</u> for abnormalities.
        - <b>THE DETERIORATION:</b>
        - End with: <i>"WHAT IS YOUR DIAGNOSIS? (Discuss below 👇)"</i>
        
        PART 2 (The Solution - The Reveal):
        - <b>DIAGNOSIS:</b> You MUST wrap the diagnosis name in <span class="tg-spoiler">TAGS</span>.
        - <b>THE SMOKING GUN:</b> You MUST wrap the key clue in <span class="tg-spoiler">TAGS</span>.
        - <b>PATHOPHYSIOLOGY:</b> Brief explanation.
        """
        
        response = generate_content_safe(god_prompt)
        
        if response and response.text:
            parts = response.text.split("||REVEAL||")
            
            def scrub(t):
                # 1. Strip Markdown
                t = t.replace("## ", "").replace("### ", "").replace("**", "").replace("__", "")
                # 2. Strip Illegal HTML (but keep spoilers/bold/italic)
                t = t.replace("<p>", "").replace("</p>", "\n\n") 
                t = t.replace("<ul>", "").replace("</ul>", "")
                t = t.replace("<li>", "• ").replace("</li>", "\n") 
                t = t.replace("<h1>", "<b>").replace("</h1>", "</b>\n") 
                t = t.replace("<h2>", "<b>").replace("</h2>", "</b>\n")
                return t.strip()

            part1_clean = scrub(parts[0])
            
            # Send Case Only
            case_text = f"📋 <b>CASE FILE #{random.randint(1000,9999)}: THE UNEXPLAINED</b>\n\n{part1_clean}"
            await broadcast_message(bot, case_text)
            
            # Save Diagnosis for NEXT run
            if len(parts) > 1:
                part2_clean = scrub(parts[1])
                state["pending_diagnosis"] = part2_clean
                save_state(state)
                print("🔒 Diagnosis saved for next run.")
                
                await broadcast_message(bot, "<i>🔮 Diagnosis will be revealed in the next transmission... discussing is advised.</i>")
            else:
                await broadcast_message(bot, "⚠️ <b>Error:</b> AI failed to generate a diagnosis.")
        else:
            await broadcast_message(bot, "⚠️ <b>System Failure:</b> API Error.")

if __name__ == "__main__":
    asyncio.run(send_chaos())

