"""Render ASGI entrypoint shim.

Keeps `uvicorn main:app` working when the service root directory is `backend`.
"""

from app.main import app
