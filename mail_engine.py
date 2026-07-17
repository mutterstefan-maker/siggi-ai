# -*- coding: utf-8 -*-
import imaplib
import email
from email.header import decode_header
from email.message import EmailMessage
import smtplib
import sqlite3
import json
import os
import re
import requests
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'mails.db')
SETTINGS_PATH = os.path.join(BASE_DIR, 'settings.json')


_AI_DISCLAIMER_PATTERNS = [
    'Diese Nachricht wurde von SIGGI (KI) verfasst.',
    'Diese Nachricht wurde von SIGGI (KI) verfasst',
    'Diese Nachricht wurde von SIGGI verfasst.',
    'Stefan meldet sich persönlich wenn nötig.',
    'Stefan meldet sich persönlich wenn nötig',
]


def strip_ai_disclaimer(text):
    """Entfernt KI-Selbstauskunfts-Sätze, die das Modell manchmal trotz Anweisung an den Text
    anhängt - die echte, in den Einstellungen hinterlegte Signatur wird separat angehängt."""
    if not text:
        return text
    cleaned = text
    for phrase in _AI_DISCLAIMER_PATTERNS:
        cleaned = cleaned.replace(phrase, '')
    cleaned = '\n'.join(line for line in cleaned.split('\n') if line.strip())
    return cleaned.strip()


def load_settings():
    with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_settings(settings):
    with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=4, ensure_ascii=False)


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS mails (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid TEXT,
        account TEXT,
        from_addr TEXT,
        subject TEXT,
        body TEXT,
        date TEXT,
        category TEXT DEFAULT 'inbox',
        ai_reply TEXT,
        sent INTEGER DEFAULT 0,
        has_attachment INTEGER DEFAULT 0,
        attachment_names TEXT,
        deleted INTEGER DEFAULT 0,
        read INTEGER DEFAULT 0,
        is_spam INTEGER DEFAULT 0,
        is_auto_reply INTEGER DEFAULT 0,
        is_new_customer INTEGER DEFAULT 0,
        ticket_nr TEXT,
        followup_sent INTEGER DEFAULT 0,
        created_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS sent_mails (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account TEXT,
        to_addr TEXT,
        subject TEXT,
        body TEXT,
        sent_at TEXT
    )''')
    for col, typ in [
        ('is_spam', 'INTEGER DEFAULT 0'),
        ('is_auto_reply', 'INTEGER DEFAULT 0'),
        ('is_new_customer', 'INTEGER DEFAULT 0'),
        ('ticket_nr', 'TEXT'),
        ('followup_sent', 'INTEGER DEFAULT 0')
    ]:
        try:
            c.execute(f'ALTER TABLE mails ADD COLUMN {col} {typ}')
        except:
            pass
    # Contacts-Tabelle
    c.execute('''CREATE TABLE IF NOT EXISTS contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE,
        name TEXT,
        phone TEXT,
        company TEXT,
        notes TEXT,
        first_contact TEXT,
        last_contact TEXT,
        mail_count INTEGER DEFAULT 1
    )''')
    conn.commit()
    conn.close()


def upsert_contact(from_addr, created_at):
    """Kontakt aus E-Mail-Adresse anlegen oder aktualisieren."""
    email_match = re.search(r'<([^>]+)>', from_addr)
    clean_email = email_match.group(1) if email_match else from_addr.strip()
    # Name aus "Vorname Nachname <email>" extrahieren
    name_match = re.match(r'^(.+?)\s*<', from_addr)
    name = name_match.group(1).strip().strip('"') if name_match else ''
    if not clean_email or '@' not in clean_email:
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, mail_count FROM contacts WHERE email=?', (clean_email,))
    row = c.fetchone()
    if row:
        c.execute('UPDATE contacts SET last_contact=?, mail_count=?, name=CASE WHEN name=\'\' OR name IS NULL THEN ? ELSE name END WHERE email=?',
                  (created_at, row[1] + 1, name, clean_email))
    else:
        c.execute('INSERT INTO contacts (email, name, first_contact, last_contact) VALUES (?,?,?,?)',
                  (clean_email, name, created_at, created_at))
    conn.commit()
    conn.close()


def decode_str(s):
    if s is None:
        return ""
    decoded = decode_header(s)
    result = ""
    for part, enc in decoded:
        if isinstance(part, bytes):
            result += part.decode(enc or 'utf-8', errors='replace')
        else:
            result += str(part)
    return result


AUTO_REPLY_SUBJECT_KEYWORDS = [
    'out of office', 'automatische antwort', 'auto-reply:',
    'automatic reply:', 'delivery notification', 'undeliverable:',
    'mail delivery failed', 'returned mail:', 'failure notice'
]


def is_auto_reply_mail(msg, from_addr, subject):
    auto_submitted = msg.get('auto-submitted', '')
    if auto_submitted and auto_submitted.lower() not in ('no', 'none', 'false'):
        return True
    x_autoreply = msg.get('x-autoreply', '').lower()
    if x_autoreply in ('yes', 'true', '1'):
        return True
    precedence = msg.get('precedence', '').lower()
    if precedence in ('bulk', 'auto_reply', 'junk'):
        return True
    from_lower = from_addr.lower()
    strict_noreply = ['noreply@', 'no-reply@', 'donotreply@', 'mailer-daemon@', 'postmaster@',
        'account-alerts@', 'notification@', 'notifications@', 'alert@', 'alerts@',
        'brevo.com', 'sendgrid', 'mailchimp', 'stripe.com', 'paypal.com', 'newsletter@']
    if any(kw in from_lower for kw in strict_noreply):
        return True
    subject_lower = subject.lower()
    if any(kw in subject_lower for kw in AUTO_REPLY_SUBJECT_KEYWORDS):
        return True
    return False


def is_spam_mail(from_addr, subject, body, settings):
    spam_senders = settings.get('spam_senders', [])
    from_lower = from_addr.lower()
    if any(s.lower() in from_lower for s in spam_senders):
        return True
    return False


def reply_count_last_24h(from_addr, conn):
    since = (datetime.now() - timedelta(hours=24)).isoformat()
    email_match = re.search(r'<([^>]+)>', from_addr)
    clean_addr = email_match.group(1) if email_match else from_addr
    c = conn.cursor()
    c.execute(
        "SELECT COUNT(*) FROM sent_mails WHERE to_addr LIKE ? AND sent_at > ?",
        (f'%{clean_addr}%', since)
    )
    return c.fetchone()[0]


def has_invoice_attachment(msg):
    invoice_keywords = ['rechnung', 'invoice', 'beleg', 'quittung', 'faktura', 'billing']
    attachments = []
    for part in msg.walk():
        filename = part.get_filename()
        if filename:
            filename_decoded = decode_str(filename).lower()
            attachments.append(decode_str(part.get_filename()))
            if any(kw in filename_decoded for kw in invoice_keywords):
                return True, attachments
            if part.get_content_type() == 'application/pdf':
                return True, attachments
    return False, attachments


def extract_phone_number(text):
    pattern = r'(\+?\d[\d\s\-\/]{7,}\d)'
    matches = re.findall(pattern, text)
    return matches[0].strip() if matches else None


AUDIT_KEYWORDS = [
    'webseite analysieren', 'website analysieren', 'seite analysieren',
    'webseite prüfen', 'website prüfen', 'homepage prüfen',
    'webauftritt analysieren', 'seo analyse', 'seo check',
    'webseite bewerten', 'website bewerten', 'audit'
]


def check_audit_trigger(subject, body, from_addr):
    """Prüft ob die Mail einen Website-Audit anfragt."""
    combined = (subject + ' ' + body).lower()
    if any(kw in combined for kw in AUDIT_KEYWORDS):
        # URL aus Mail extrahieren
        url_match = re.search(r'https?://[^\s\]\)>]+', body)
        if not url_match:
            # Domain-Muster ohne Protokoll suchen
            domain_match = re.search(r'\b(www\.[a-zA-Z0-9\-]+\.[a-zA-Z]{2,})\b', body)
            if domain_match:
                return f'https://{domain_match.group(1)}'
        else:
            return url_match.group(0).rstrip('.,)')
    return None


def categorize_mail(subject, body, has_attachment, settings):
    subject_lower = subject.lower()
    body_lower = body.lower()
    if has_attachment:
        return 'invoices'
    callback_keywords = ['rueckruf', 'ruckruf', 'callback', 'rufen sie', 'bitte rufen',
                         'call back', 'zurueckrufen', 'anrufen', 'rückruf', 'zurückrufen']
    if any(kw in subject_lower or kw in body_lower for kw in callback_keywords):
        return 'callbacks'
    return 'inbox'


def generate_ticket_nr(conn):
    year = datetime.now().year
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM mails WHERE ticket_nr LIKE ?", (f'CB-{year}-%',))
    count = c.fetchone()[0] + 1
    return f"CB-{year}-{count:04d}"


def is_new_customer(from_addr, conn):
    email_match = re.search(r'<([^>]+)>', from_addr)
    clean_addr = email_match.group(1) if email_match else from_addr
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM mails WHERE from_addr LIKE ?", (f'%{clean_addr}%',))
    return c.fetchone()[0] == 0


def send_followups():
    settings = load_settings()
    if not settings.get('global_auto', False):
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=3)).isoformat()
    c.execute("""SELECT id, account, from_addr, subject, ticket_nr
                 FROM mails
                 WHERE sent=0 AND deleted=0 AND followup_sent=0
                 AND is_auto_reply=0 AND is_spam=0
                 AND category NOT IN ('knowledge_gap','spam','sent_archive')
                 AND created_at < ?""", (cutoff,))
    mails = c.fetchall()
    for mail_id, account, to_addr, subject, ticket_nr in mails:
        acc_config = settings.get('accounts', {}).get(account, {})
        if not acc_config.get('active', False):
            continue
        api_key = settings.get('anthropic_api_key', '')
        followup_text = f"Guten Tag,\n\nwir wollten nachfragen ob Sie unsere Antwort zu Ihrer Anfrage ({subject}) erhalten haben.\n\nFalls noch Fragen offen sind, melden Sie sich gerne!\n\nTicket: {ticket_nr or ''}"
        if api_key and api_key != 'HIER_API_KEY_EINTRAGEN':
            try:
                resp = requests.post(
                    'https://api.anthropic.com/v1/messages',
                    headers={'x-api-key': api_key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
                    json={'model': 'claude-haiku-4-5-20251001', 'max_tokens': 300,
                          'messages': [{'role': 'user', 'content': f'Schreib eine kurze freundliche Nachfass-Mail auf Deutsch fuer unbeantwortete Anfrage: {subject}. Ticket: {ticket_nr}. Max 4 Saetze.'}]},
                    timeout=20
                )
                followup_text = strip_ai_disclaimer(resp.json()['content'][0]['text'])
            except:
                pass
        signature = acc_config.get('signature', '')
        full_body = f"{followup_text}\n\n--\n{signature}" if signature else followup_text
        try:
            msg = EmailMessage()
            msg.set_content(full_body)
            msg['Subject'] = f"Nachfrage: {subject}"
            msg['From'] = account
            msg['To'] = to_addr
            msg['X-Autoreply'] = 'yes'
            with smtplib.SMTP(acc_config.get('smtp_server', 'smtp.ionos.de'), acc_config.get('smtp_port', 587)) as server:
                server.starttls()
                server.login(account, acc_config.get('password', ''))
                server.send_message(msg)
            c.execute('UPDATE mails SET followup_sent=1 WHERE id=?', (mail_id,))
            c.execute('INSERT INTO sent_mails (account, to_addr, subject, body, sent_at) VALUES (?,?,?,?,?)',
                      (account, to_addr, msg['Subject'], full_body, datetime.now().isoformat()))
            print(f"Follow-Up gesendet an {to_addr}")
        except Exception as e:
            print(f"Follow-Up Fehler: {e}")
    conn.commit()
    conn.close()


def ai_categorize_and_reply(from_addr, subject, body, category, settings, is_auto, is_spam, new_customer=False, ticket_nr=''):
    api_key = settings.get('anthropic_api_key', '')
    if not api_key or api_key == 'HIER_API_KEY_EINTRAGEN':
        return category, None, True

    if is_auto:
        return category, None, False

    ai_character = settings.get('email_ai_character') or settings.get('ai_character', 'Du bist ein hilfreicher E-Mail-Assistent.')
    knowledge_prompts = settings.get('knowledge_gap_prompts', [])

    knowledge_context = ""
    if knowledge_prompts:
        knowledge_context = "\n\nZusaetzliche Anweisungen:\n" + "\n".join(
            f"- {p['trigger']}: {p['response']}" for p in knowledge_prompts
        )

    new_customer_hint = ""
    if new_customer:
        new_customer_hint = "\n\nWICHTIG: Dies ist ein ERSTKONTAKT. Begruesse ihn besonders herzlich."

    spam_instruction = ""
    if is_spam:
        spam_instruction = "\n\nDIESE MAIL IST SPAM. Schreibe eine Abmelde-Anfrage."

    # Few-Shot aus gesendeten Mails
    few_shot_examples = ""
    try:
        _conn = sqlite3.connect(DB_PATH)
        _c = _conn.cursor()
        _c.execute('''SELECT m.body, s.body as reply
                     FROM mails m
                     JOIN sent_mails s ON s.to_addr LIKE '%' || substr(m.from_addr, instr(m.from_addr,'<')+1, instr(m.from_addr,'>')-instr(m.from_addr,'<')-1) || '%'
                     WHERE m.sent=1 AND m.category != 'spam'
                     ORDER BY m.created_at DESC LIMIT 3''')
        examples = _c.fetchall()
        _conn.close()
        if examples:
            few_shot_examples = "\n\nBEISPIELE:\n"
            for i, (body_ex, reply_ex) in enumerate(examples, 1):
                few_shot_examples += f"\nBeispiel {i}:\nAnfrage: {(body_ex or '')[:150]}\nAntwort: {(reply_ex or '')[:200]}\n"
    except:
        pass

    system_prompt = f"""{ai_character}{knowledge_context}{spam_instruction}{new_customer_hint}{few_shot_examples}

Antworte NUR mit JSON (kein Markdown):
{{
  "category": "inbox|callbacks|invoices|knowledge_gap|spam",
  "needs_phone": false,
  "ai_reply": "Deine Antwort ODER null",
  "knowledge_gap": false,
  "is_spam": false
}}

Kategorien: inbox=normal, callbacks=Rueckruf, invoices=Rechnung, knowledge_gap=unklar, spam=Werbung/irrelevant

WICHTIG - Inhaltliche Pruefung:
Pruefe ob die Mail inhaltlich etwas mit ChefBlick (Webdesign/Software-Agentur, deren Kunden, Auftraege, Rechnungen, Anfragen) zu tun hat.
is_spam=true wenn die Mail NICHTS mit ChefBlick-Geschaeft zu tun hat, z.B.:
- Automatische System-/Plattform-Benachrichtigungen (Google, IONOS, SendOwl, TikTok, Social-Media-Plattformen, etc.)
- Newsletter, Werbung, Gewinnspiele, Phishing
- Account-/Sicherheitsbenachrichtigungen die nicht direkt eine Kundenanfrage sind
is_spam=false wenn es eine echte Kundenanfrage, ein Auftrag, eine Rechnung oder sonstige ChefBlick-relevante Kommunikation ist - auch wenn Absender @gmail.com o.ae. ist.
ai_reply=null nur bei Systemnachrichten/Spam.
knowledge_gap=true NUR wenn du wirklich nicht weisst was zu tun ist."""

    try:
        response = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'x-api-key': api_key,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json'
            },
            json={
                'model': 'claude-haiku-4-5-20251001',
                'max_tokens': 1000,
                'system': system_prompt,
                'messages': [{'role': 'user', 'content': f"Von: {from_addr}\nBetreff: {subject}\nTicket: {ticket_nr}\n\n{body[:2000]}"}]
            },
            timeout=30
        )
        result = response.json()
        if 'content' not in result:
            print(f"API Fehler: {result}")
            return category, None, True
        text = result['content'][0]['text'].strip()
        text = re.sub(r'^```json\s*', '', text)
        text = re.sub(r'```$', '', text).strip()
        data = json.loads(text)

        final_category = data.get('category', category)
        ai_reply = strip_ai_disclaimer(data.get('ai_reply'))
        is_gap = data.get('knowledge_gap', False)
        ki_spam = data.get('is_spam', False)

        if is_spam:
            final_category = 'spam'
        elif ki_spam:
            final_category = 'spam'

        if is_gap and not ai_reply:
            final_category = 'knowledge_gap'
        elif is_gap and ai_reply:
            final_category = category

        return final_category, ai_reply, is_gap and not ai_reply

    except Exception as e:
        print(f"KI-Fehler: {e}")
        return category, None, True


def fetch_mails():
    init_db()
    settings = load_settings()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    new_count = 0

    for account_email, acc_config in settings.get('accounts', {}).items():
        if not acc_config.get('active', False):
            continue
        imap_server = acc_config.get('imap_server', 'imap.ionos.de')
        password = acc_config.get('password', '')

        try:
            mail = imaplib.IMAP4_SSL(imap_server, 993)
            mail.login(account_email, password)
            mail.select("inbox")

            since_date = (datetime.now() - timedelta(days=7)).strftime('%d-%b-%Y')
            status, messages = mail.search(None, f'SINCE {since_date}')

            if not messages[0]:
                mail.logout()
                continue

            for num in messages[0].split():
                status, data = mail.fetch(num, '(UID RFC822)')
                uid = None
                raw_email = None
                for part in data:
                    if isinstance(part, tuple):
                        if b'UID' in part[0]:
                            uid_match = re.search(rb'UID (\d+)', part[0])
                            if uid_match:
                                uid = uid_match.group(1).decode()
                        raw_email = part[1]

                if raw_email is None:
                    continue

                if uid:
                    c.execute('SELECT id FROM mails WHERE uid=? AND account=?', (uid, account_email))
                    if c.fetchone():
                        continue

                msg = email.message_from_bytes(raw_email)
                from_addr = decode_str(msg.get('From', ''))
                subject = decode_str(msg.get('Subject', '')) or '(Kein Betreff)'
                date_str = msg.get('Date', datetime.now().strftime('%a, %d %b %Y %H:%M:%S +0000'))

                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain" and not part.get_filename():
                            payload = part.get_payload(decode=True)
                            if payload:
                                body += payload.decode('utf-8', errors='replace')
                else:
                    payload = msg.get_payload(decode=True)
                    if payload:
                        body = payload.decode('utf-8', errors='replace')

                ticket_nr = generate_ticket_nr(conn)
                new_cust = is_new_customer(from_addr, conn)
                auto_reply = is_auto_reply_mail(msg, from_addr, subject)
                spam = is_spam_mail(from_addr, subject, body, settings)

                reply_count = reply_count_last_24h(from_addr, conn)
                if reply_count >= 3:
                    print(f"Endlosschutz: {from_addr}")
                    auto_reply = True

                is_invoice, attachment_names = has_invoice_attachment(msg)
                category = categorize_mail(subject, body, is_invoice, settings)

                final_category, ai_reply, is_gap = ai_categorize_and_reply(
                    from_addr, subject, body, category, settings, auto_reply, spam, new_cust, ticket_nr
                )

                c.execute('''INSERT INTO mails
                    (uid, account, from_addr, subject, body, date, category, ai_reply,
                     has_attachment, attachment_names, is_spam, is_auto_reply,
                     is_new_customer, ticket_nr, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (uid, account_email, from_addr, subject, body, date_str,
                     final_category, ai_reply, 1 if is_invoice else 0,
                     ', '.join(attachment_names), 1 if spam else 0,
                     1 if auto_reply else 0, 1 if new_cust else 0,
                     ticket_nr, datetime.now().isoformat()))
                new_count += 1
                # Kontakt anlegen/aktualisieren (nur echte Mails, kein Spam/Auto-Reply)
                if not auto_reply and not spam:
                    upsert_contact(from_addr, datetime.now().isoformat())
                    # Audit-Trigger: Wenn Kunde Analyse seiner Website anfragt
                    audit_url = check_audit_trigger(subject, body, from_addr)
                    if audit_url:
                        import threading as _t
                        def _run_audit(url, api_key):
                            try:
                                import audit_engine as _ae
                                _ae.RUNTIME_API_KEY = api_key
                                _ae.run_full_audit(url)
                                print(f"[Auto-Audit] Fertig: {url}")
                            except Exception as _e:
                                print(f"[Auto-Audit] Fehler: {_e}")
                        _api_key = settings.get('anthropic_api_key', '')
                        _t.Thread(target=_run_audit, args=(audit_url, _api_key), daemon=True).start()
                        print(f"[Auto-Audit] Gestartet für: {audit_url}")

            mail.logout()
            print(f"Abruf OK: {account_email}")

        except Exception as e:
            print(f"Fehler bei {account_email}: {e}")

    conn.commit()
    conn.close()
    print(f"Neue Mails verarbeitet: {new_count}")
    return new_count


def send_auto_replies():
    settings = load_settings()
    if not settings.get('global_auto', False):
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT id, account, from_addr, subject, ai_reply
                 FROM mails WHERE ai_reply IS NOT NULL AND sent=0 AND deleted=0
                 AND category != 'spam' AND is_auto_reply=0''')
    mails = c.fetchall()

    for mail_id, account, to_addr, subject, ai_reply in mails:
        if reply_count_last_24h(to_addr, conn) >= 3:
            print(f"Endlosschutz Senden: {to_addr}")
            c.execute('UPDATE mails SET sent=1 WHERE id=?', (mail_id,))
            continue

        acc_config = settings.get('accounts', {}).get(account, {})
        if not acc_config.get('active', False):
            continue

        smtp_server = acc_config.get('smtp_server', 'smtp.ionos.de')
        smtp_port = acc_config.get('smtp_port', 587)
        password = acc_config.get('password', '')
        signature = acc_config.get('signature', '')
        clean_reply = strip_ai_disclaimer(ai_reply)
        full_body = f"{clean_reply}\n\n--\n{signature}" if signature else clean_reply

        try:
            msg = EmailMessage()
            msg.set_content(full_body)
            msg['Subject'] = f"Re: {subject}" if subject else "Re: Ihre Nachricht"
            msg['From'] = account
            msg['To'] = to_addr
            msg['X-Autoreply'] = 'yes'

            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(account, password)
                server.send_message(msg)

            c.execute('UPDATE mails SET sent=1, read=1, category=\'sent_archive\' WHERE id=?', (mail_id,))
            c.execute('INSERT INTO sent_mails (account, to_addr, subject, body, sent_at) VALUES (?,?,?,?,?)',
                      (account, to_addr, msg['Subject'], full_body, datetime.now().isoformat()))
            print(f"Gesendet an {to_addr}")

        except Exception as e:
            print(f"Sendefehler an {to_addr}: {e}")

    conn.commit()
    conn.close()
