"""Optionaler E-Mail-Versand (Einladungscodes, Erinnerungen).

Aktiv nur, wenn die Umgebungsvariablen gesetzt sind:
  SMTP_HOST, SMTP_PORT (Standard 587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM, APP_URL
Ohne Konfiguration zeigt die App Codes nur dem Admin an, der sie selbst weitergibt.
"""
import smtplib
from email.message import EmailMessage

from . import config


def is_configured() -> bool:
    return all(config.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"))


def app_url() -> str:
    return config.get("APP_URL", "")


def send(to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = config.get("SMTP_FROM"), to, subject
    msg.set_content(body)
    with smtplib.SMTP(config.get("SMTP_HOST"), int(config.get("SMTP_PORT", "587")), timeout=20) as s:
        s.starttls()
        s.login(config.get("SMTP_USER"), config.get("SMTP_PASSWORD"))
        s.send_message(msg)
