from flask import Blueprint

bp = Blueprint('beheer', __name__, url_prefix='/beheer')

from app.beheer import routes
