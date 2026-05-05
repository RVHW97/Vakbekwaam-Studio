from flask import Blueprint

bp = Blueprint('kaarten', __name__, url_prefix='/kaarten')

from app.kaarten import routes
