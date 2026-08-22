# -*- coding: utf-8 -*-
"""
Image Generation Engine — gpt-image-1 via OpenAI
ChefBlick Brand Standard — vollständige Umsetzung des GPT-Prompts
"""
import os
import json
import random
import requests
import base64
from datetime import datetime
from PIL import Image
import io

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE_DIR, 'settings.json')
STATE_PATH = os.path.join(BASE_DIR, 'imagegen_state.json')
LOGO_PATH = os.path.join(BASE_DIR, 'logo.png')


def load_settings():
    with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


# ── STATE TRACKING (letztes Thema/Layout/Einhorn-Zähler) ──────────────────────

TOPIC_HISTORY_SIZE = 8  # so viele zuletzt genutzte Themen werden bei der Auswahl ausgeschlossen


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, 'r', encoding='utf-8') as f:
            state = json.load(f)
            state.setdefault('recent_topics', [])
            return state
    return {'last_topic': '', 'last_layout': '', 'total_count': 0, 'last_unicorn': -20, 'recent_topics': []}


def save_state(state):
    with open(STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ── LOGO OVERLAY ──────────────────────────────────────────────────────────────

def _overlay_logo(img_bytes):
    """Blendet das ChefBlick-Logo unten links ins Bild ein."""
    if not os.path.exists(LOGO_PATH):
        return img_bytes
    try:
        main = Image.open(io.BytesIO(img_bytes)).convert('RGBA')
        w, h = main.size
        logo = Image.open(LOGO_PATH).convert('RGBA')
        logo_w = int(w * 0.28)
        ratio = logo_w / logo.width
        logo_h = int(logo.height * ratio)
        logo = logo.resize((logo_w, logo_h), Image.LANCZOS)

        # Hintergrundfarbe aus Ecken ermitteln und transparent machen
        ldata = logo.load()
        corners = [ldata[0,0], ldata[logo.width-1,0], ldata[0,logo.height-1], ldata[logo.width-1,logo.height-1]]
        bg_r = sum(c[0] for c in corners) // 4
        bg_g = sum(c[1] for c in corners) // 4
        bg_b = sum(c[2] for c in corners) // 4
        tolerance = 30
        for y in range(logo.height):
            for x in range(logo.width):
                r, g, b, a = ldata[x, y]
                if abs(r-bg_r) < tolerance and abs(g-bg_g) < tolerance and abs(b-bg_b) < tolerance:
                    ldata[x, y] = (r, g, b, 0)

        padding = 24
        pos = (padding, h - logo_h - padding)
        main.paste(logo, pos, logo)

        out = io.BytesIO()
        main.convert('RGB').save(out, format='PNG')
        return out.getvalue()
    except Exception as e:
        print(f"[ImageGen] Logo-Overlay Fehler: {e}")
        return img_bytes


# ── SLUGIFY ───────────────────────────────────────────────────────────────────

_UMLAUT_MAP = str.maketrans({'ä':'ae','ö':'oe','ü':'ue','Ä':'Ae','Ö':'Oe','Ü':'Ue','ß':'ss'})

def _slugify(text):
    t = text.translate(_UMLAUT_MAP).lower()
    t = ''.join(c if c.isalnum() else '_' for c in t)
    while '__' in t:
        t = t.replace('__', '_')
    return t.strip('_')[:35]


# ── PROMPT BUILDER ────────────────────────────────────────────────────────────

def build_dynamic_prompt(settings):
    """
    Baut einen vollständigen ChefBlick-Prompt mit Thema/Layout-Rotation,
    Einhorn-Regel (max jedes 10. Bild) und CTA.
    Returns: (prompt_str, filename_slug)
    """
    # Eigene Themen-Ideen (aus der App/dem Dashboard eingetragen) fließen mit in den
    # Themen-Pool ein, damit Stefan gezielt Themen vorgeben kann statt nur die feste
    # Rotation zu bekommen.
    custom_topics = [t['text'] for t in settings.get('custom_topics', []) if t.get('text')]
    topics   = settings.get('flyer_topics', ['Webdesign']) + custom_topics
    layouts  = settings.get('flyer_layouts', ['Smartphone Mockup'])
    texts    = settings.get('flyer_text_examples', [])
    ctas     = settings.get('flyer_ctas', ['Folge uns auf Instagram'])

    state = load_state()
    total = state.get('total_count', 0)
    recent_topics = state.get('recent_topics', [])
    last_layout  = state.get('last_layout', '')
    last_unicorn = state.get('last_unicorn', -20)

    # Thema wählen — keins der letzten TOPIC_HISTORY_SIZE Themen wiederholen, nicht nur
    # das allerletzte, damit sich Inhalte nicht in kurzen Zyklen wiederholen.
    available_topics = [t for t in topics if t not in recent_topics]
    topic = random.choice(available_topics) if available_topics else random.choice(topics)

    # Layout wählen — nie dasselbe wie zuletzt, Einhorn-Regel beachten
    unicorn_layout = 'Einhorn Charakter'
    since_last_unicorn = total - last_unicorn
    if since_last_unicorn < 10:
        available_layouts = [l for l in layouts if l != last_layout and l != unicorn_layout]
    else:
        available_layouts = [l for l in layouts if l != last_layout]

    layout = random.choice(available_layouts) if available_layouts else random.choice(layouts)

    # Einhorn-Zähler updaten
    if layout == unicorn_layout:
        state['last_unicorn'] = total

    # CTA und Text
    cta = random.choice(ctas) if ctas else ''
    example_text = random.choice(texts) if texts else ''

    # Einhorn-spezifischer Prompt
    if layout == unicorn_layout:
        visual = (
            "A white muscular unicorn character standing confidently. "
            "Rainbow mane and tail, black sunglasses, black polo shirt with 'ChefBlick' text on the chest, "
            "black shorts, sneakers. Human-like hands. Cheeky and self-confident expression. "
            "Pure black background with subtle blue glow. No cooking hat, no hooves showing, no suit. "
            "Photorealistic 3D render quality."
        )
    else:
        visual = f"Layout style: {layout}."

    prompt = f"""Create a professional Instagram marketing post for ChefBlick, a German web design and software agency.

FORMAT: Portrait 4:5 ratio (1080x1350px). All important content within safe area. No cropped text.

COLORS: Electric blue (#0057FF), pure black, white ONLY. No other dominant colors.

TOPIC: {topic}

VISUAL: {visual}

GERMAN HEADLINE: Bold, all-caps, provocative headline related to the topic. Key words highlighted in electric blue. Example style: '{example_text}'

CALL TO ACTION: Include at bottom: '{cta}'

BRAND IDENTITY: The word 'ChefBlick' appears as text brand in the image. Tagline: 'Webdesign. Hosting. Software.'

STYLE: Ultra-bold typography, high contrast, premium German tech marketing aesthetic. Every element must serve a purpose. No random decorations. Direct, modern, results-focused."""

    slug = _slugify(f"{topic}_{layout}")

    # State speichern
    state['last_topic'] = topic
    state['recent_topics'] = (recent_topics + [topic])[-TOPIC_HISTORY_SIZE:]
    state['last_layout'] = layout
    state['total_count'] = total + 1
    save_state(state)

    return prompt, slug


# ── POOL ──────────────────────────────────────────────────────────────────────

def get_pool_path():
    settings = load_settings()
    path = settings.get('flyer_pool_path', os.path.join(BASE_DIR, 'flyer_pool'))
    os.makedirs(path, exist_ok=True)
    return path


def get_pool_count():
    pool = get_pool_path()
    if not os.path.exists(pool):
        return 0
    return len([f for f in os.listdir(pool)
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))])


def pool_needs_refill():
    settings = load_settings()
    minimum = settings.get('flyer_pool_min', 5)
    return get_pool_count() < minimum


# ── GENERIERUNG ───────────────────────────────────────────────────────────────

def generate_image(custom_prompt=None):
    """
    Generiert ein Bild mit gpt-image-1 und speichert es im flyer_pool.
    Returns: {'success': True/False, 'filename': '...', 'error': '...'}
    """
    settings = load_settings()
    api_key = settings.get('openai_api_key', '')
    if not api_key:
        return {'success': False, 'error': 'Kein OpenAI API Key konfiguriert'}

    if custom_prompt:
        prompt = custom_prompt
        filename_slug = 'custom'
    else:
        prompt, filename_slug = build_dynamic_prompt(settings)

    print(f"[ImageGen] Prompt: {prompt[:120]}...")

    try:
        resp = requests.post(
            'https://api.openai.com/v1/images/generations',
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json'
            },
            json={
                'model': 'gpt-image-1',
                'prompt': prompt,
                'n': 1,
                'size': '1024x1536'
            },
            timeout=120
        )
        data = resp.json()
        if 'error' in data:
            return {'success': False, 'error': data['error'].get('message', 'Unbekannter Fehler')}

        item = data['data'][0]
        if 'b64_json' in item:
            img_bytes = base64.b64decode(item['b64_json'])
        else:
            img_bytes = requests.get(item['url'], timeout=30).content

        img_bytes = _overlay_logo(img_bytes)

        filename = f"chefblick_{filename_slug}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        filepath = os.path.join(get_pool_path(), filename)
        with open(filepath, 'wb') as f:
            f.write(img_bytes)

        print(f"[ImageGen] Bild generiert: {filename}")
        return {'success': True, 'filename': filename, 'path': filepath}

    except Exception as e:
        return {'success': False, 'error': str(e)}


def auto_refill_pool(target_count=None):
    """Füllt den Pool auf target_count auf."""
    settings = load_settings()
    minimum = settings.get('flyer_pool_min', 5)
    target = target_count or (minimum * 2)
    current = get_pool_count()
    needed = max(0, target - current)

    results = []
    for i in range(needed):
        print(f"[ImageGen] Generiere Bild {i+1}/{needed}...")
        result = generate_image()
        results.append(result)
        if not result['success']:
            print(f"[ImageGen] Fehler: {result['error']}")
            break

    return results
