from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import event
from sqlalchemy.engine import Engine
from config import Config


# Zonder deze hook negeert SQLite ALLE foreign-key-constraints (cascade werkt niet,
# koppel-tabellen kunnen dangling rijen krijgen). Enabled per connectie.
@event.listens_for(Engine, 'connect')
def _enable_sqlite_fk(dbapi_conn, _connection_record):
    driver = type(dbapi_conn).__module__
    if 'sqlite' in driver:
        cursor = dbapi_conn.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()

# Versie van de applicatie — toont in de footer van elke pagina.
# Bumpen volgens semver: patch bij bugfix, minor bij afgeronde fase.
__version__ = '0.7.13'
__version_date__ = '14 augustus 2026'

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    @app.context_processor
    def inject_versie():
        return {'app_versie': __version__, 'app_versie_datum': __version_date__}

    @app.context_processor
    def inject_kaart_constantes():
        # Constanten die in meerdere templates (formulier + PDF) gebruikt worden.
        from app.kaarten.forms import VEILIGHEID_MAX_ZINNEN, VEILIGHEID_ZIN_MAX
        return {
            'VEILIGHEID_MAX_ZINNEN': VEILIGHEID_MAX_ZINNEN,
            'VEILIGHEID_ZIN_MAX': VEILIGHEID_ZIN_MAX,
        }

    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Je moet eerst inloggen.'
    login_manager.login_message_category = 'info'
    # 'strong' invalideert de sessie zodra IP/user-agent verandert — beperkt session-hijacking.
    login_manager.session_protection = 'strong'

    from app.auth import bp as auth_bp
    app.register_blueprint(auth_bp)

    from app.main import bp as main_bp
    app.register_blueprint(main_bp)

    from app.kaarten import bp as kaarten_bp
    app.register_blueprint(kaarten_bp)

    from app.qr import bp as qr_bp
    app.register_blueprint(qr_bp)

    from app.beheer import bp as beheer_bp
    app.register_blueprint(beheer_bp)

    with app.app_context():
        from app import models
        db.create_all()
        models.migreer_schema()
        models.seed_admin()
        models.seed_kerntaken_en_subcategorieen()

    return app
