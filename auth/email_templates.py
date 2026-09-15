"""Small, email-client-safe renderers for DAUNTRA transactional messages."""

from dataclasses import dataclass
from html import escape
from typing import Sequence

from brand import BRAND_NAME


BRAND_LINE = "THE WORK, UNDERSTOOD."
BRAND_LOGO_URL = "https://dauntra.com/brand/dauntra/dauntra-app-icon.png"


@dataclass(frozen=True)
class EmailBodies:
    """The two representations sent for every transactional message."""

    plain_text: str
    html: str


def render_waitlist_welcome_email(
    *,
    support_email: str | None = None,
) -> EmailBodies:
    """Render the DAUNTRA waitlist welcome email — same scaffold as the OTP
    emails but with the free-month benefit callout instead of a code block.
    """
    preheader = "Welcome to the DAUNTRA waitlist. Your first month of Premium is free."
    support_html = ""
    if support_email:
        safe_se = escape(support_email, quote=True)
        support_html = f"""
                    <tr>
                      <td style="padding:20px 40px 32px;">
                        <p style="margin:0 0 4px;color:#6B7280;font-size:13px;line-height:20px;">Questions? Contact us:</p>
                        <a href="mailto:{safe_se}" style="color:#2563EB;font-size:14px;font-weight:700;text-decoration:none;">{safe_se}</a>
                      </td>
                    </tr>"""

    plain_text = "\n".join([
        BRAND_NAME,
        BRAND_LINE,
        "",
        "YOU'RE IN.",
        "",
        "Welcome to the DAUNTRA waitlist.",
        "",
        "Thanks for joining us early.",
        "",
        "DAUNTRA is being built to bring your training, nutrition, progress, and",
        "performance insights into one place — so you can spend less time looking at",
        "disconnected numbers and more time understanding what they mean.",
        "",
        "Because you joined before launch:",
        "",
        "YOUR FIRST MONTH OF DAUNTRA PREMIUM IS FREE.",
        "",
        "You do not need to remember a code right now.",
        "When DAUNTRA launches, we'll send eligible waitlist members the instructions",
        "needed to activate their free month.",
        "",
        "TRACK.     Log your workouts, nutrition, and progress.",
        "UNDERSTAND. See how your data changes over time and identify meaningful patterns.",
        "IMPROVE.   Use your training data, analytics, and DAUNTRA Lab Insights to make",
        "            better-informed decisions.",
        "",
        "We're still putting the final pieces together.",
        "Waitlist members will be among the first to hear when access opens.",
        "",
        "Welcome to DAUNTRA.",
        "",
        BRAND_LINE,
        "",
        "— The DAUNTRA Team",
        "",
        "-----",
        "EARLY ACCESS BENEFIT",
        "1 MONTH OF DAUNTRA PREMIUM — FREE",
        "We'll send activation details when DAUNTRA launches.",
    ] + (["", "Questions? Contact us:", support_email] if support_email else []) + [
        "",
        BRAND_NAME,
        BRAND_LINE,
    ])

    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="x-apple-disable-message-reformatting">
    <title>Welcome to DAUNTRA | {BRAND_NAME}</title>
  </head>
  <body style="margin:0;padding:0;background-color:#F3F4F6;color:#0B1220;font-family:Arial,Helvetica,sans-serif;">
    <div style="display:none;max-height:0;max-width:0;overflow:hidden;opacity:0;color:transparent;line-height:1px;font-size:1px;">{escape(preheader)}</div>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;background-color:#F3F4F6;">
      <tr>
        <td align="center" style="padding:32px 16px;">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:600px;background-color:#FFFFFF;border:1px solid #E5E7EB;border-radius:12px;overflow:hidden;">
            <!-- HEADER -->
            <tr>
              <td style="padding:24px 40px;background-color:#0B1220;border-bottom:4px solid #3B82F6;">
                <table role="presentation" cellspacing="0" cellpadding="0" border="0">
                  <tr>
                    <td style="padding-right:14px;vertical-align:middle;">
                      <img src="{BRAND_LOGO_URL}" width="44" height="44" alt="DAUNTRA logo" style="display:block;width:44px;height:44px;border:0;border-radius:8px;">
                    </td>
                    <td style="vertical-align:middle;">
                      <div style="color:#FFFFFF;font-size:21px;font-weight:800;letter-spacing:2.5px;line-height:24px;">{BRAND_NAME}</div>
                      <div style="margin-top:3px;color:#9CA3AF;font-size:9px;font-weight:700;letter-spacing:1.5px;line-height:12px;">{BRAND_LINE}</div>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            <!-- HERO INTRO -->
            <tr>
              <td style="padding:38px 40px 14px;">
                <p style="margin:0 0 12px;color:#3B82F6;font-size:12px;font-weight:800;letter-spacing:1.8px;line-height:18px;">EARLY ACCESS</p>
                <h1 style="margin:0 0 18px;color:#0B1220;font-size:27px;font-weight:800;letter-spacing:-0.4px;line-height:34px;">YOU'RE IN.</h1>
                <p style="margin:0 0 14px;color:#374151;font-size:16px;line-height:25px;">Welcome to the DAUNTRA waitlist.</p>
                <p style="margin:0 0 14px;color:#374151;font-size:16px;line-height:25px;">Thanks for joining us early.</p>
                <p style="margin:0 0 14px;color:#374151;font-size:16px;line-height:25px;">DAUNTRA is being built to bring your training, nutrition, progress, and performance insights into one place&nbsp;&mdash; so you can spend less time looking at disconnected numbers and more time understanding what they mean.</p>
                <p style="margin:0 0 14px;color:#374151;font-size:16px;line-height:25px;">Because you joined before launch:</p>
              </td>
            </tr>
            <!-- BENEFIT CALLOUT -->
            <tr>
              <td style="padding:8px 40px 22px;">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;background-color:#EFF6FF;border:1px solid #BFDBFE;border-radius:10px;">
                  <tr>
                    <td align="center" style="padding:28px 24px;">
                      <div style="margin:0 0 8px;color:#2563EB;font-size:10px;font-weight:800;letter-spacing:1.6px;line-height:14px;">EARLY ACCESS BENEFIT</div>
                      <div style="color:#0B1220;font-family:Arial,Helvetica,sans-serif;font-size:22px;font-weight:800;letter-spacing:0.5px;line-height:30px;">1 MONTH OF DAUNTRA PREMIUM</div>
                      <div style="margin-top:4px;color:#2563EB;font-size:28px;font-weight:800;letter-spacing:1px;line-height:36px;">FREE</div>
                    </td>
                  </tr>
                </table>
                <p style="margin:13px 0 0;color:#6B7280;font-size:13px;line-height:20px;text-align:center;">You do not need a code right now. We'll send activation details when DAUNTRA launches.</p>
              </td>
            </tr>
            <!-- BODY COPY -->
            <tr>
              <td style="padding:4px 40px 28px;">
                <p style="margin:0 0 14px;color:#374151;font-size:16px;line-height:25px;">When DAUNTRA launches, we&rsquo;ll send eligible waitlist members the instructions needed to activate their free month.</p>
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;margin:20px 0;">
                  <tr><td style="padding:10px 0;border-top:1px solid #E5E7EB;">
                    <p style="margin:0;color:#0B1220;font-size:15px;font-weight:800;letter-spacing:0.5px;">TRACK.</p>
                    <p style="margin:4px 0 0;color:#4B5563;font-size:14px;line-height:22px;">Log your workouts, nutrition, and progress.</p>
                  </td></tr>
                  <tr><td style="padding:10px 0;border-top:1px solid #E5E7EB;">
                    <p style="margin:0;color:#0B1220;font-size:15px;font-weight:800;letter-spacing:0.5px;">UNDERSTAND.</p>
                    <p style="margin:4px 0 0;color:#4B5563;font-size:14px;line-height:22px;">See how your data changes over time and identify meaningful patterns.</p>
                  </td></tr>
                  <tr><td style="padding:10px 0;border-top:1px solid #E5E7EB;border-bottom:1px solid #E5E7EB;">
                    <p style="margin:0;color:#0B1220;font-size:15px;font-weight:800;letter-spacing:0.5px;">IMPROVE.</p>
                    <p style="margin:4px 0 0;color:#4B5563;font-size:14px;line-height:22px;">Use your training data, analytics, and DAUNTRA Lab Insights to make better-informed decisions.</p>
                  </td></tr>
                </table>
                <p style="margin:0 0 14px;color:#374151;font-size:16px;line-height:25px;">We&rsquo;re still putting the final pieces together.</p>
                <p style="margin:0 0 14px;color:#374151;font-size:16px;line-height:25px;">Waitlist members will be among the first to hear when access opens.</p>
                <p style="margin:0 0 14px;color:#374151;font-size:16px;line-height:25px;">Welcome to DAUNTRA.</p>
                <p style="margin:24px 0 4px;color:#9CA3AF;font-size:11px;font-weight:700;letter-spacing:1.4px;">{BRAND_LINE}</p>
                <p style="margin:0;color:#374151;font-size:15px;line-height:24px;">&mdash; The DAUNTRA Team</p>
              </td>
            </tr>
            <!-- SECURITY / FOOTER NOTES -->
            <tr>
              <td style="padding:18px 40px;background-color:#F9FAFB;border-top:1px solid #E5E7EB;">
                <p style="margin:0 0 10px;color:#4B5563;font-size:14px;line-height:22px;">Your free month of Premium is not yet activated. Do not pay for anything now. Activation instructions will be sent at launch to eligible waitlist members.</p>
              </td>
            </tr>
            {support_html}
            <!-- FOOTER -->
            <tr>
              <td style="padding:24px 40px;background-color:#111827;text-align:center;">
                <div style="color:#FFFFFF;font-size:14px;font-weight:800;letter-spacing:2px;line-height:20px;">{BRAND_NAME}</div>
                <div style="margin-top:5px;color:#9CA3AF;font-size:9px;font-weight:700;letter-spacing:1.4px;line-height:14px;">{BRAND_LINE}</div>
                <div style="margin-top:13px;color:#6B7280;font-size:11px;line-height:17px;">This is an automated transactional message.</div>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""
    return EmailBodies(plain_text=plain_text, html=html)


def render_code_email(
    *,
    preheader: str,
    title: str,
    intro: Sequence[str],
    code: str,
    expiration_text: str,
    security_notes: Sequence[str],
    support_email: str | None = None,
    support_intro: str = "Need help?",
) -> EmailBodies:
    """Render a branded one-time-code email with safely escaped HTML values."""

    plain_sections = [
        BRAND_NAME,
        BRAND_LINE,
        "",
        title,
        "",
        *intro,
        "",
        "YOUR SECURITY CODE",
        code,
        "",
        expiration_text,
        "",
        *security_notes,
    ]
    if support_email:
        plain_sections.extend(("", support_intro, support_email))
    plain_sections.extend(("", BRAND_NAME, BRAND_LINE))
    plain_text = "\n".join(plain_sections)

    safe_preheader = escape(preheader)
    safe_title = escape(title)
    safe_code = escape(code)
    intro_html = "".join(
        f'<p style="margin:0 0 14px;color:#374151;font-size:16px;line-height:25px;">{escape(paragraph)}</p>'
        for paragraph in intro
    )
    security_html = "".join(
        f'<p style="margin:0 0 10px;color:#4B5563;font-size:14px;line-height:22px;">{escape(note)}</p>'
        for note in security_notes
    )
    support_html = ""
    if support_email:
        safe_support_email = escape(support_email, quote=True)
        support_html = f"""
                    <tr>
                      <td style="padding:20px 40px 32px;">
                        <p style="margin:0 0 4px;color:#6B7280;font-size:13px;line-height:20px;">{escape(support_intro)}</p>
                        <a href="mailto:{safe_support_email}" style="color:#2563EB;font-size:14px;font-weight:700;text-decoration:none;">{safe_support_email}</a>
                      </td>
                    </tr>"""

    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="x-apple-disable-message-reformatting">
    <title>{safe_title} | {BRAND_NAME}</title>
  </head>
  <body style="margin:0;padding:0;background-color:#F3F4F6;color:#0B1220;font-family:Arial,Helvetica,sans-serif;">
    <div style="display:none;max-height:0;max-width:0;overflow:hidden;opacity:0;color:transparent;line-height:1px;font-size:1px;">{safe_preheader}</div>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;background-color:#F3F4F6;">
      <tr>
        <td align="center" style="padding:32px 16px;">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:600px;background-color:#FFFFFF;border:1px solid #E5E7EB;border-radius:12px;overflow:hidden;">
            <tr>
              <td style="padding:24px 40px;background-color:#0B1220;border-bottom:4px solid #3B82F6;">
                <table role="presentation" cellspacing="0" cellpadding="0" border="0">
                  <tr>
                    <td style="padding-right:14px;vertical-align:middle;">
                      <img src="{BRAND_LOGO_URL}" width="44" height="44" alt="DAUNTRA logo" style="display:block;width:44px;height:44px;border:0;border-radius:8px;">
                    </td>
                    <td style="vertical-align:middle;">
                      <div style="color:#FFFFFF;font-size:21px;font-weight:800;letter-spacing:2.5px;line-height:24px;">{BRAND_NAME}</div>
                      <div style="margin-top:3px;color:#9CA3AF;font-size:9px;font-weight:700;letter-spacing:1.5px;line-height:12px;">{BRAND_LINE}</div>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:38px 40px 14px;">
                <p style="margin:0 0 12px;color:#3B82F6;font-size:12px;font-weight:800;letter-spacing:1.8px;line-height:18px;">SECURE ACCOUNT ACTION</p>
                <h1 style="margin:0 0 18px;color:#0B1220;font-size:27px;font-weight:800;letter-spacing:-0.4px;line-height:34px;">{safe_title}</h1>
                {intro_html}
              </td>
            </tr>
            <tr>
              <td style="padding:8px 40px 22px;">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;background-color:#EFF6FF;border:1px solid #BFDBFE;border-radius:10px;">
                  <tr>
                    <td align="center" style="padding:23px 16px 22px;">
                      <div style="margin:0 0 8px;color:#2563EB;font-size:10px;font-weight:800;letter-spacing:1.6px;line-height:14px;">YOUR SECURITY CODE</div>
                      <div style="color:#0B1220;font-family:Arial,Helvetica,sans-serif;font-size:34px;font-weight:800;letter-spacing:8px;line-height:42px;white-space:nowrap;">{safe_code}</div>
                    </td>
                  </tr>
                </table>
                <p style="margin:13px 0 0;color:#6B7280;font-size:13px;line-height:20px;text-align:center;">{escape(expiration_text)}</p>
              </td>
            </tr>
            <tr>
              <td style="padding:18px 40px;background-color:#F9FAFB;border-top:1px solid #E5E7EB;">
                {security_html}
              </td>
            </tr>
            {support_html}
            <tr>
              <td style="padding:24px 40px;background-color:#111827;text-align:center;">
                <div style="color:#FFFFFF;font-size:14px;font-weight:800;letter-spacing:2px;line-height:20px;">{BRAND_NAME}</div>
                <div style="margin-top:5px;color:#9CA3AF;font-size:9px;font-weight:700;letter-spacing:1.4px;line-height:14px;">{BRAND_LINE}</div>
                <div style="margin-top:13px;color:#6B7280;font-size:11px;line-height:17px;">This is an automated transactional message.</div>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""
    return EmailBodies(plain_text=plain_text, html=html)
