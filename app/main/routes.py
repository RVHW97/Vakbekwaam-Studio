import os
from flask import render_template, redirect, url_for, abort, send_from_directory, current_app
from flask_login import login_required, current_user
from app import db
from app.main import bp
from app.models import Kaart, QRCode, QRKlik


@bp.route('/')
@login_required
def dashboard():
    recente_kaarten = Kaart.query.order_by(Kaart.bijgewerkt_op.desc()).limit(5).all()
    return render_template('main/dashboard.html', recente_kaarten=recente_kaarten)


@bp.route('/q/<slug>')
def qr_redirect(slug):
    """Publieke short-link: log klik en stuur door naar doel.

    Deze route staat bewust NIET achter @login_required:
    iedereen die een QR scant moet kunnen doorklikken.
    """
    qr = QRCode.query.filter_by(slug=slug).first()
    if not qr:
        abort(404)
    if qr.status != 'actief':
        return render_template('main/qr_inactief.html', qr=qr), 410

    soort, doel = qr.effectief_doel
    if not soort:
        abort(404)

    db.session.add(QRKlik(qr_code_id=qr.id))
    db.session.commit()

    if soort == 'pdf':
        return redirect(url_for('qr.pdf', bestand=doel))
    return redirect(doel)


# --- Tijdelijk: schets voor themakaart (Fase 5 ontwerp) ---

@bp.route('/themakaart-schets')
@login_required
def themakaart_schets():
    qrs = QRCode.query.order_by(QRCode.id.asc()).limit(6).all()
    gekoppelde_kaarten = Kaart.query.filter(Kaart.type != 'thema').order_by(Kaart.bijgewerkt_op.desc()).limit(5).all()
    return render_template('main/themakaart_schets.html', qrs=qrs, gekoppelde_kaarten=gekoppelde_kaarten)


@bp.route('/huisstijl-asset/<path:bestand>')
@login_required
def huisstijl_asset(bestand):
    """Serveert tijdelijk bestanden uit /huisstijl voor de themakaart-schets."""
    project_root = os.path.dirname(current_app.root_path)
    huisstijl_dir = os.path.join(project_root, 'huisstijl')
    return send_from_directory(huisstijl_dir, bestand)
