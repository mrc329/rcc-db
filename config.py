"""
Secret lookup shared by the Census and Google Places modules.

On Streamlit Community Cloud, keys live in the app's Secrets settings
(read via st.secrets). Locally, put them in .streamlit/secrets.toml
(see secrets.toml.example) or export them as environment variables.
"""

import os


def get_secret(name):
    """Return st.secrets[name], falling back to the env var, else ""."""
    try:
        import streamlit as st
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        # No secrets.toml at all raises, not just a missing key.
        pass
    return os.environ.get(name, "")
