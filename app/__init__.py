from flask import Flask, flash, redirect, request, jsonify, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, current_user
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
__version__ = '0.7.51'
__version_date__ = '11 november 2026'

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    # Jinja-filter: parse JSON-string veilig naar lijst/dict; bij fout → lege lijst.
    import json as _json

    @app.template_filter('from_json_safe')
    def _from_json_safe(value):
        if not value:
            return []
        if not isinstance(value, str):
            return value
        try:
            return _json.loads(value)
        except (ValueError, TypeError):
            return []

    @app.context_processor
    def inject_versie():
        return {'app_versie': __version__, 'app_versie_datum': __version_date__}

    @app.context_processor
    def inject_kaart_constantes():
        # Constanten die in meerdere templates (formulier + PDF) gebruikt worden.
        from app.kaarten.forms import (VEILIGHEID_MAX_ZINNEN, VEILIGHEID_ZIN_MAX,
                                        DOELGROEP_GROEPEN, DOELGROEP_LABELS,
                                        OEFENSTAF_OVERIG_KEUZES, OEFENSTAF_OVERIG_LABELS)
        return {
            'VEILIGHEID_MAX_ZINNEN': VEILIGHEID_MAX_ZINNEN,
            'VEILIGHEID_ZIN_MAX': VEILIGHEID_ZIN_MAX,
            'DOELGROEP_GROEPEN': DOELGROEP_GROEPEN,
            'DOELGROEP_LABELS': DOELGROEP_LABELS,
            'OEFENSTAF_OVERIG_KEUZES': OEFENSTAF_OVERIG_KEUZES,
            'OEFENSTAF_OVERIG_LABELS': OEFENSTAF_OVERIG_LABELS,
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

    # 403-handler: Flask's standaard "Forbidden"-pagina is te hard.
    # In plaats daarvan: toon een duidelijke modal (via flash-category
    # 'toegang_geweigerd' die base.html in een popup rendert) en redirect
    # naar het dashboard. AJAX/JSON-requests krijgen een JSON-403 terug zodat
    # client-side JS netjes kan afhandelen zonder redirect.
    @app.errorhandler(403)
    def _toegang_geweigerd(err):
        wants_json = (request.is_json
                      or 'application/json' in (request.accept_mimetypes.best or '')
                      or request.headers.get('X-Requested-With') == 'XMLHttpRequest')
        boodschap = 'Je hebt niet de juiste rol om deze actie uit te voeren.'
        if wants_json:
            return jsonify({'ok': False, 'fout': boodschap, 'code': 'toegang_geweigerd'}), 403
        flash(boodschap, 'toegang_geweigerd')
        # Ga terug naar waar de user vandaan kwam (bv. kaart-detail) i.p.v. hem
        # altijd naar dashboard te slingeren. Alleen in-app URLs vertrouwen we.
        from urllib.parse import urlparse
        referrer = request.referrer or ''
        parsed = urlparse(referrer)
        is_veilige_referrer = (referrer
                                and not parsed.scheme and not parsed.netloc
                                or (parsed.netloc and parsed.netloc == request.host))
        if current_user.is_authenticated and is_veilige_referrer and referrer != request.url:
            return redirect(referrer)
        if current_user.is_authenticated:
            return redirect(url_for('main.dashboard'))
        return redirect(url_for('auth.login'))

    with app.app_context():
        from app import models
        db.create_all()
        models.migreer_schema()
        models.seed_admin()
        models.seed_kerntaken_en_subcategorieen()

    return app
