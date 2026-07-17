import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

def get_weather(location='Hagen'):
    """Wetter für Location"""
    try:
        r = requests.get(f'https://wttr.in/{location}?format=j1', timeout=10)
        data = r.json()
        curr = data['current_condition'][0]
        return {
            'location': location,
            'temp': f"{curr['temp_C']}°C",
            'description': curr['weatherDesc'][0]['value'],
            'humidity': f"{curr['humidity']}%",
            'wind': f"{curr['windspeedKmph']} km/h"
        }
    except Exception as e:
        return {'error': f'Wetter nicht verfügbar: {str(e)[:50]}'}

def get_news(topic='Deutschland', lang='de'):
    """News/Nachrichten"""
    try:
        r = requests.get(f'https://news.google.com/rss/search?q={topic}&hl={lang}&gl=DE&ceid=DE:de',
                        headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, 'xml')
        items = soup.find_all('item')[:5]
        news = []
        for item in items:
            title = item.find('title')
            link = item.find('link')
            pubdate = item.find('pubDate')
            if title:
                news.append({
                    'title': title.text[:100],
                    'link': link.text if link else '',
                    'date': pubdate.text if pubdate else ''
                })
        return {'topic': topic, 'news': news}
    except Exception as e:
        return {'error': f'News nicht verfügbar: {str(e)[:50]}'}

def get_facts(category='wissenschaft'):
    """Interessante Fakten"""
    facts = {
        'wissenschaft': [
            '🔬 Die menschliche Nase kann über 1 Billion verschiedene Gerüche unterscheiden.',
            '🧬 Deine DNA enthält genau dieselben Elemente wie die Sterne.',
            '🌍 Die Erde dreht sich langsamer - ein Tag ist heute 1,7ms länger als vor 100 Jahren.',
        ],
        'weltpolitik': [
            '🌏 Es gibt 195 Länder auf der Welt (193 UN-Mitglieder + 2 Beobachter).',
            '🏛️ Die EU hat 27 Mitgliedsstaaten seit dem Brexit 2020.',
            '📊 Deutschland ist die größte Wirtschaft in Europa.',
        ],
        'tech': [
            '💻 Das erste Smartphone wurde 1992 erfunden (IBM Simon).',
            '📱 Es gibt über 6 Milliarden aktive Smartphones weltweit.',
            '🤖 KI wird 2024 zur Standard-Technologie in fast allen Branchen.',
        ]
    }
    return facts.get(category, facts['wissenschaft'])

def google_search(query, num_results=3):
    """Google-ähnliche Suche"""
    try:
        r = requests.get(f'https://www.google.com/search?q={requests.utils.quote(query)}',
                        headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, 'html.parser')
        results = []
        for g in soup.find_all('div', class_='g')[:num_results]:
            link = g.find('a', href=True)
            if link:
                results.append({'url': link['href'], 'title': link.text})
        return {'query': query, 'results': results}
    except Exception as e:
        return {'error': f'Suche fehlgeschlagen: {str(e)[:50]}'}

