"""Tests must never spend API credit or reach the network, whatever is in backend/.env.

Imports now start AI summaries, so this blanks the keys before anything loads them.
load_dotenv does not override variables that are already set.
"""
import os

for key in ("ANTHROPIC_API_KEY", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY"):
    os.environ[key] = ""
