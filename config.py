import secrets
import os

basedir = os.path.abspath(os.path.dirname(__file__))


class Config:
    # SECRET_KEY: in productie (Docker/VPS) zetten via environment-variable SECRET_KEY.
    # Lokaal/dev: als geen env-var aanwezig, fallback naar een at-runtime gegenereerde key
    # (sessies overleven dan geen herstart — prima voor dev, niet voor productie).
    SECRET_KEY = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(basedir, 'instance', 'vakbekwaam.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = True
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER') or \
        os.path.join(basedir, 'app', 'static', 'uploads')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB max upload
