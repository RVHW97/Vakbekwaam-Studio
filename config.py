import secrets
import os
from datetime import timedelta

basedir = os.path.abspath(os.path.dirname(__file__))


def _is_production():
    return (os.environ.get('FLASK_ENV') or '').lower() == 'production'


def _resolve_secret_key():
    key = os.environ.get('SECRET_KEY')
    if key:
        return key
    if _is_production():
        raise RuntimeError(
            'SECRET_KEY-omgevingsvariabele ontbreekt in productie. '
            'Zet SECRET_KEY in de deployment-config (Docker/VPS) — anders '
            'werken sessies en CSRF niet consistent over meerdere workers.'
        )
    return secrets.token_hex(32)


class Config:
    SECRET_KEY = _resolve_secret_key()
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(basedir, 'instance', 'vakbekwaam.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = True
    # UPLOAD_FOLDER staat bewust BUITEN app/static/ — anders serveert Flask alle uploads
    # publiek (zonder login-check). Bestanden gaan via `main.media` en `qr.pdf`.
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER') or \
        os.path.join(basedir, 'instance', 'uploads')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB max upload
    LMRA_QR_URL = os.environ.get('LMRA_QR_URL') or ''

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = _is_production()
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_SECURE = _is_production()
    REMEMBER_COOKIE_DURATION = timedelta(days=14)
