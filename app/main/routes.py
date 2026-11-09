import os
from flask import render_template, redirect, url_for, abort, send_from_directory, current_app
from flask_login import login_required, current_user
from app import db
from app.main import bp
from app.models import Kaart, QRCode, QRKlik, Review, KaartWijziging, OverdraagVerzoek


@bp.route('/')
@login_required
def dashboard():
    # Openstaande review-taken: direct toegewezen aan de gebruiker.
    openstaande_reviews = (Review.query
                            .filter_by(reviewer_id=current_user.id, status='open')
                            .order_by(Review.aangemaakt_op.asc())
                            .all())

    # Rechten-verzoeken voor de specialist uit de kerngroep van de kaart:
    # elke open OverdraagVerzoek waarvan de kaart in zijn kerngroep valt.
    rechten_verzoeken = []
    if current_user.is_specialist and current_user.kerngroep:
        rechten_verzoeken = (OverdraagVerzoek.query
                              .join(Kaart, OverdraagVerzoek.kaart_id == Kaart.id)
                              .filter(OverdraagVerzoek.status == 'open')
                              .filter(db.or_(
                                  Kaart.kerntaak == current_user.kerngroep,
                                  db.and_(Kaart.kerntaak == 'algemeen',
                                           Kaart.peer_kerngroep == current_user.kerngroep)
                              ))
                              .order_by(OverdraagVerzoek.aangemaakt_op.asc())
                              .all())
    elif current_user.is_admin:
        # Admin ziet álle open verzoeken (vangnet).
        rechten_verzoeken = (OverdraagVerzoek.query
                              .filter_by(status='open')
                              .order_by(OverdraagVerzoek.aangemaakt_op.asc())
                              .all())

    # Afkeur-meldingen voor de maker: reviews die zijn afgekeurd op MIJN kaarten
    # en waar de kaart nu teruggezet is naar concept (nog niet opnieuw ingediend).
    # Zo ziet de maker direct dat er iets te corrigeren is, wie afkeurde en waarom.
    afkeur_meldingen = (Review.query
                        .join(Kaart, Review.kaart_id == Kaart.id)
                        .filter(Kaart.auteur_id == current_user.id)
                        .filter(Kaart.status == 'concept')
                        .filter(Review.status == 'afgekeurd')
                        .order_by(Review.voltooid_op.desc())
                        .limit(10)
                        .all())

    # "Mijn kaarten" — kaarten waar je zelf aan hebt gewerkt (als maker of als
    # bijdrager via de wijzigingen-log). Geeft een persoonlijke invalshoek op het
    # dashboard: eerst zien waar jij betrokken bent.
    mijn_wijziging_ids = (db.session.query(KaartWijziging.kaart_id)
                          .filter(KaartWijziging.gebruiker_id == current_user.id)
                          .distinct()
                          .subquery())
    mijn_kaarten = (Kaart.query
                     .filter(db.or_(Kaart.auteur_id == current_user.id,
                                     Kaart.bijgewerkt_door_id == current_user.id,
                                     Kaart.id.in_(mijn_wijziging_ids)))
                     .order_by(Kaart.bijgewerkt_op.desc())
                     .limit(10)
                     .all())
    mijn_ids = {k.id for k in mijn_kaarten}

    # "Mijn kerngroep" — kaarten uit de kerngroep van de gebruiker. Effectieve
    # kerngroep = kaart.kerntaak behalve wanneer die 'algemeen' is, dan
    # kaart.peer_kerngroep (door maker gekozen bij indienen). Admin en medewerker
    # O&O hebben geen kerngroep — die sectie blijft dan leeg.
    kerngroep_kaarten = []
    if current_user.kerngroep:
        kg = current_user.kerngroep
        kerngroep_kaarten = (Kaart.query
                              .filter(db.or_(Kaart.kerntaak == kg,
                                              db.and_(Kaart.kerntaak == 'algemeen',
                                                       Kaart.peer_kerngroep == kg)))
                              .filter(~Kaart.id.in_(mijn_ids) if mijn_ids else db.text('1=1'))
                              .order_by(Kaart.bijgewerkt_op.desc())
                              .limit(10)
                              .all())

    return render_template('main/dashboard.html',
                           mijn_kaarten=mijn_kaarten,
                           kerngroep_kaarten=kerngroep_kaarten,
                           openstaande_reviews=openstaande_reviews,
                           afkeur_meldingen=afkeur_meldingen,
                           rechten_verzoeken=rechten_verzoeken)


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


@bp.route('/media/<path:pad>')
@login_required
def media(pad):
    """Serveert kaart-uploads (foto's) alleen aan ingelogde gebruikers.

    UPLOAD_FOLDER staat bewust buiten /app/static/ zodat Flask-static ze niet
    publiek kan serveren. Alle kaart-foto's gaan hierdoor. QR-PDF's blijven via
    de aparte publieke route qr.pdf lopen (nodig voor QR-scan van buitenaf).
    """
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], pad)
