from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from config import Config

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Je moet eerst inloggen.'
    login_manager.login_message_category = 'info'

    from app.auth import bp as auth_bp
    app.register_blueprint(auth_bp)

    from app.main import bp as main_bp
    app.register_blueprint(main_bp)

    from app.kaarten import bp as kaarten_bp
    app.register_blueprint(kaarten_bp)

    from app.qr import bp as qr_bp
    app.register_blueprint(qr_bp)

    with app.app_context():
        from app import models
        db.create_all()
        models.migreer_schema()
        models.seed_admin()

    return app
