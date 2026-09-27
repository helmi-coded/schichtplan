"""Konfiguration aus Umgebungsvariablen oder Streamlit-Secrets (.streamlit/secrets.toml
bzw. „Secrets" in der Streamlit Community Cloud). Umgebungsvariablen haben Vorrang."""
import os


def get(key: str, default: str | None = None) -> str | None:
    if os.environ.get(key):
        return os.environ[key]
    try:
        import streamlit as st
        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:  # noqa: BLE001 – keine secrets.toml vorhanden
        pass
    return default
