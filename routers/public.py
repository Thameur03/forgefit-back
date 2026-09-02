"""Unauthenticated legal and account-deletion browser pages."""

import html
import os
import secrets
from email.utils import parseaddr

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from brand import BRAND_NAME

router = APIRouter(include_in_schema=False)

_CSS = """
:root{color-scheme:dark;--bg:#07110d;--panel:#101c16;--line:#274435;--text:#f2f7f4;
--muted:#b8c9bf;--green:#63e6a5;--red:#ff817a}*{box-sizing:border-box}body{margin:0;
background:radial-gradient(circle at top,#153425,var(--bg) 40%);color:var(--text);
font:16px/1.65 system-ui,sans-serif}.wrap{width:min(860px,calc(100% - 32px));margin:auto;
padding:36px 0 72px}.brand{font-weight:800;color:var(--text);text-decoration:none}.card{margin-top:28px;
background:rgba(16,28,22,.96);border:1px solid var(--line);border-radius:20px;
padding:clamp(22px,5vw,48px)}h1{font-size:clamp(2rem,6vw,3.3rem);line-height:1.08;margin:.2em 0}
h2{font-size:1.25rem;margin:32px 0 8px;color:#dff7e9}p,li{color:var(--muted)}a{color:var(--green)}
.notice{border-left:3px solid var(--red);padding:12px 16px;background:#2b1716;border-radius:6px}
label{display:block;font-weight:700;margin:18px 0 7px}input{width:100%;border:1px solid var(--line);
border-radius:10px;background:#08130e;color:var(--text);padding:13px;font:inherit}.button{border:0;
border-radius:10px;background:var(--green);color:#041009;font-weight:800;padding:13px 18px;
cursor:pointer;margin-top:18px}.danger{background:var(--red);color:#1b0504}.hidden{display:none}
.status{min-height:1.6em}.error{color:#ffaaa5}.ok{color:#8ff0bb}.meta{font-size:.9rem;color:#91a99b}
"""


def _support_html() -> str:
    configured = os.getenv("SUPPORT_EMAIL", "").strip()
    _, address = parseaddr(configured)
    if address:
        try:
            normalized = validate_email(address, check_deliverability=False).normalized
            safe = html.escape(normalized)
            return f'<a href="mailto:{safe}">{safe}</a>'
        except EmailNotValidError:
            pass
    website = os.getenv("PUBLIC_WEB_URL", "").strip()
    if website.startswith("https://"):
        safe_url = html.escape(website.rstrip("/"), quote=True)
        return f'<a href="{safe_url}">the official DAUNTRA website</a>'
    return "the support contact in the official DAUNTRA store listing"


def _page(title: str, body: str, script: str = "") -> HTMLResponse:
    nonce = secrets.token_urlsafe(18)
    script_tag = f'<script nonce="{nonce}">{script}</script>' if script else ""
    document = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<meta name="robots" content="index,follow"><style nonce="{nonce}">{_CSS}</style></head><body>
<main class="wrap"><a class="brand" href="/">{BRAND_NAME}</a>{body}</main>{script_tag}</body></html>"""
    return HTMLResponse(document, headers={
        "Cache-Control": "public, max-age=300",
        "Content-Security-Policy": "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
        f"style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; connect-src 'self'; form-action 'self'",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
    })


@router.get("/privacy", response_class=HTMLResponse)
def privacy_policy() -> HTMLResponse:
    support = _support_html()
    body = f"""<article class="card"><p class="meta">Last updated: August 27, 2026</p>
<h1>DAUNTRA Privacy Policy</h1><p>This policy covers the DAUNTRA mobile app,
website, API, and supporting services.</p><h2>Information you provide</h2><ul>
<li><strong>Account/profile:</strong> full name, email, one-way password hash, and optional
date of birth, gender, weight, height, and fitness level.</li><li><strong>Workout:</strong>
workouts, exercises, sets, reps, load, history, programs, schedules, and derived records.</li>
<li><strong>Nutrition:</strong> meals, foods, calories, macros, micronutrients, food searches,
targets, and history.</li><li><strong>Website/waitlist:</strong> an email address submitted
to request early-access communications.</li></ul><h2>Authentication and security</h2><p>Access and refresh
tokens maintain sessions. Passwords are stored as hashes, and the app stores session credentials
in operating-system secure storage. Email verification, password reset, token revocation, and
verified-email deletion codes are used where applicable.</p><h2>Technical information</h2>
<p>The API host may process IP address, timestamp, route, response status, and user-agent for
operation and security. If configured, PostHog receives limited app interaction/lifecycle events
and SDK device/app context; workout, nutrition, email, and profile fields are not intentionally
sent, and session replay is disabled. If configured, Sentry receives crash/performance diagnostics
and limited activity breadcrumbs such as event names and coarse counts or ranges; raw workout,
nutrition, profile, and authentication values are excluded. Default PII, screenshots, and replay
are disabled.</p><h2>Camera</h2><p>Camera permission scans food
barcodes with Google ML Kit on-device. DAUNTRA does not retain or upload images or video.
The detected barcode may be sent for a food lookup. ML Kit may collect limited SDK diagnostics,
including app/device configuration, performance/usage metrics, and an installation identifier.</p>
<h2>Service providers</h2><ul><li><strong>Render</strong> hosts the API/database and processes
stored app data and request metadata.</li><li><strong>Resend</strong> receives an email address and
transactional message content.</li><li><strong>USDA FoodData Central and Open Food Facts</strong>
receive food searches or barcode/product lookups without account profile data.</li><li>
<strong>ExerciseDB or a self-hosted compatible service</strong> supplies exercise catalogue content
without requiring profile data.</li><li><strong>PostHog and Sentry</strong> provide optional analytics
and diagnostics.</li><li><strong>Google ML Kit</strong> performs on-device barcode recognition and
limited SDK diagnostics.</li><li><strong>Vercel and Supabase</strong> host the public website and
store waitlist email submissions. Vercel Web Analytics processes aggregate website traffic and
performance data; website PostHog is opt-in, captures selected interactions and browser exception
diagnostics when enabled, and keeps session recording disabled.</li></ul>
<h2>Use and sharing</h2><p>Data provides authentication,
fitness and nutrition tracking, programs, statistics, Lab Insights, security, support, and reliability.
It is shared with processors only as needed for those functions. DAUNTRA does not sell personal
information.</p><h2>Deletion and retention</h2><p>Delete in Profile → Account → Delete
Account or use the <a href="/delete-account">public deletion page</a>. The app hard-deletes the
account/profile and associated schedules, programs/exercises, workouts/sets, nutrition history,
account-linked app analytics, and revoked-token records from the active database. Pre-auth anonymous
events that are not linked to an account may remain under a rotated anonymous identifier. Limited security logs or provider
backups may remain until routine expiry where needed for security, legal compliance, or disaster
recovery; they are not used to restore the deleted account. Waitlist removal is handled through
a verified request to the published privacy contact and may be subject to the same limited provider
backup or legal/security retention.</p><h2>Security</h2><p>Production
traffic uses HTTPS/TLS. Hashing, access controls, token expiry, and restricted credentials reduce
risk, but no internet service can promise absolute security.</p><h2>Contact</h2><p>For privacy
questions, contact {support}.</p></article>"""
    return _page(f"Privacy Policy | {BRAND_NAME}", body)


@router.get("/delete-account", response_class=HTMLResponse)
def delete_account_page() -> HTMLResponse:
    support = _support_html()
    body = f"""<section class="card"><p class="meta">Public account deletion</p>
<h1>Delete your DAUNTRA account</h1><div class="notice"><strong>This is permanent.</strong>
Deletion removes your account/profile, workouts and sets, programs and schedules, nutrition history,
statistics source data, account-linked app analytics, and session/revocation records.</div><p>Enter your account email.
We return the same response whether an account exists. If it does, a six-digit code is emailed to you.
Knowing an email address alone can never delete an account.</p><form id="request"><label for="email">
Account email</label><input id="email" type="email" autocomplete="email" maxlength="254" required>
<button class="button">Send confirmation code</button><p id="rs" class="status"></p></form>
<form id="confirm" class="hidden"><label for="code">Six-digit code</label><input id="code"
inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{{6}}" maxlength="6" required>
<label for="word">Type DELETE to confirm permanent deletion</label><input id="word" pattern="DELETE"
required><button class="button danger">Permanently delete account</button><p id="cs" class="status"></p>
</form><h2>Need help?</h2><p>Contact {support}. Support still requires safe ownership verification.
Read the <a href="/privacy">Privacy Policy</a>.</p></section>"""
    script = """const q=s=>document.querySelector(s),email=q('#email'),request=q('#request'),confirm=q('#confirm'),rs=q('#rs'),cs=q('#cs');async function post(path,data){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data),credentials:'omit',cache:'no-store'});let j={};try{j=await r.json()}catch(e){}if(!r.ok)throw Error(j.detail||'Request could not be completed.');return j}request.addEventListener('submit',async e=>{e.preventDefault();rs.textContent='Sending…';try{const j=await post('/account/deletion/request',{email:email.value});rs.className='status ok';rs.textContent=j.message;confirm.classList.remove('hidden');email.readOnly=true}catch(x){rs.className='status error';rs.textContent=x.message}});confirm.addEventListener('submit',async e=>{e.preventDefault();if(q('#word').value!=='DELETE'){cs.className='status error';cs.textContent='Type DELETE exactly.';return}cs.textContent='Deleting…';try{const j=await post('/account/deletion/confirm',{email:email.value,code:q('#code').value,confirmation:'DELETE'});cs.className='status ok';cs.textContent=j.message;confirm.querySelectorAll('input,button').forEach(x=>x.disabled=true)}catch(x){cs.className='status error';cs.textContent=x.message}});"""
    response = _page(f"Delete Account | {BRAND_NAME}", body, script)
    response.headers["Cache-Control"] = "no-store"
    return response
