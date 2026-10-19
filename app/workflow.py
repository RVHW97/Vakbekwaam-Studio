"""Workflow-helpers voor de review-flow van kaarten.

Centraliseert reviewer-selectie (round-robin), status-overgangen en logging.
Routes importeren deze helpers; de helpers raken `db` direct aan en roepen
`db.session.commit()` NIET aan — dat laat de caller doen samen met andere
wijzigingen in dezelfde transactie.
"""
from datetime import datetime
from sqlalchemy import func
from app import db
from app.models import (User, Kaart, Review, KaartWijziging,
                        SPECIALIST_KERNGROEPEN)


# -------------------- Reviewer-selectie (round-robin) --------------------

def _laatste_review_per_user(fase, user_ids):
    """Haalt per user_id de datum van de LAATSTE review van gegeven fase.
    Users zonder reviews krijgen datetime.min zodat ze eerst aan de beurt zijn.
    Retourneert dict {user_id: datetime}.
    """
    if not user_ids:
        return {}
    rows = (db.session.query(Review.reviewer_id, func.max(Review.aangemaakt_op))
            .filter(Review.fase == fase)
            .filter(Review.reviewer_id.in_(user_ids))
            .group_by(Review.reviewer_id)
            .all())
    laatste = {uid: dt for uid, dt in rows}
    for uid in user_ids:
        laatste.setdefault(uid, datetime.min)
    return laatste


def _kies_round_robin(fase, kandidaten):
    """Selecteer uit kandidaten (lijst User-objecten) de user die het langst
    geen review in gegeven fase heeft gedaan. Bij gelijke stand: laagste user-id
    (= deterministisch). Retourneert None als lijst leeg is.
    """
    if not kandidaten:
        return None
    ids = [u.id for u in kandidaten]
    laatste = _laatste_review_per_user(fase, ids)
    kandidaten_sorted = sorted(kandidaten, key=lambda u: (laatste.get(u.id, datetime.min), u.id))
    return kandidaten_sorted[0]


def _actieve_users(filter_fn):
    """Alle actieve users die aan filter_fn voldoen (filter krijgt User, returnt bool)."""
    return [u for u in User.query.filter_by(actief=True).all() if filter_fn(u)]


def kies_peer_reviewer(kaart):
    """Kies één oefencoördinator uit kerngroep-van-de-kaart (niet de maker zelf).
    Kerngroep wordt bepaald door kaart.effectieve_kerngroep (bij Algemeen = peer_kerngroep).
    Geeft None als er niemand is."""
    doel = kaart.effectieve_kerngroep
    if not doel:
        return None
    kandidaten = _actieve_users(lambda u: (u.is_oefencoordinator
                                            and u.kerngroep == doel
                                            and u.id != kaart.auteur_id))
    return _kies_round_robin('peer', kandidaten)


def kies_ensceneringstips_reviewer(kaart):
    """Round-robin uit alle medewerker_oo. Alleen voor scenariokaarten."""
    if kaart.type != 'scenario':
        return None
    kandidaten = _actieve_users(lambda u: u.is_medewerker_oo and u.id != kaart.auteur_id)
    return _kies_round_robin('ensceneringstips', kandidaten)


def kies_specialist_reviewer(kaart):
    """Round-robin uit specialisten BUITEN eigen kerngroep.
    Voor ROI-kaarten: alle 5 specialisten komen in aanmerking (specialist ROI bestaat niet).
    """
    doel = kaart.effectieve_kerngroep
    def _match(u):
        if not u.is_specialist or not u.kerngroep:
            return False
        if u.id == kaart.auteur_id:
            return False
        if doel == 'roi':
            return True
        return u.kerngroep != doel
    kandidaten = _actieve_users(_match)
    return _kies_round_robin('specialist', kandidaten)


# -------------------- Status-overgangen --------------------

def dien_in_voor_review(kaart, maker):
    """Zet kaart in peer-review en maakt de bijbehorende Review-taken aan.
    Caller MOET db.session.commit() aanroepen na succes.

    Returnt dict met:
        'ok': bool
        'fout': optionele foutmelding
        'reviews': lijst Review-objecten die aangemaakt zijn
    """
    if kaart.status not in ('concept',):
        return {'ok': False, 'fout': f'Kaart heeft status "{kaart.status_naam}" — '
                                      f'alleen een concept kan ingediend worden.',
                'reviews': []}
    if not kaart.effectieve_kerngroep:
        return {'ok': False,
                'fout': 'Kies eerst een kerngroep met meeste affiniteit '
                         '(bij kaarten met kerntaak Algemeen is dat verplicht).',
                'reviews': []}

    nieuwe_reviews = []

    peer = kies_peer_reviewer(kaart)
    if peer is None:
        return {'ok': False,
                'fout': f'Geen oefencoördinator gevonden in kerngroep '
                         f'"{kaart.effectieve_kerngroep}" (behalve jijzelf).',
                'reviews': []}
    nieuwe_reviews.append(Review(kaart_id=kaart.id, fase='peer',
                                   reviewer_id=peer.id, status='open'))

    if kaart.type == 'scenario':
        tips_rev = kies_ensceneringstips_reviewer(kaart)
        if tips_rev is not None:
            # Als er geen medewerker_oo bestaat, slaan we deze parallelle review
            # over — de hoofd-peer-flow blijft doorlopen zonder blokkade.
            nieuwe_reviews.append(Review(kaart_id=kaart.id, fase='ensceneringstips',
                                           reviewer_id=tips_rev.id, status='open'))

    for r in nieuwe_reviews:
        db.session.add(r)
    kaart.status = 'peer_review'
    db.session.add(KaartWijziging(
        kaart_id=kaart.id,
        gebruiker_id=maker.id if maker else None,
        actie='ingediend',
        omschrijving=f'Ingediend voor peer-review ({kaart.effectieve_kerngroep}).'
    ))
    return {'ok': True, 'reviews': nieuwe_reviews}


# -------------------- Review-besluit afhandelen --------------------

def verwerk_review_besluit(review, besluit, opmerking, uitvoerder):
    """Handelt een review-besluit af (goedgekeurd of afgekeurd).

    Afgekeurd (op elke fase) → kaart terug naar 'concept', maker kan opnieuw
      bewerken. Lopende parallelle open reviews op dezelfde kaart worden gesloten
      (afgekeurd + opmerking) zodat ze niet meer in dashboards blijven staan.

    Goedgekeurd op peer / ensceneringstips → check of alle parallelle reviews in
      die ronde klaar zijn; zo ja, start specialist-review (round-robin).

    Goedgekeurd op specialist → kaart naar 'gepubliceerd' (versie-bump).

    Caller moet db.session.commit() aanroepen.
    Returnt dict met ok/fout/boodschap + nieuwe_status voor UI-feedback.
    """
    if review.status != 'open':
        return {'ok': False, 'fout': 'Deze review-taak is al afgerond.'}
    if besluit not in ('goedgekeurd', 'afgekeurd'):
        return {'ok': False, 'fout': 'Ongeldig besluit.'}
    if besluit == 'afgekeurd' and not (opmerking or '').strip():
        return {'ok': False,
                'fout': 'Vul een opmerking in waarom je afkeurt — zonder uitleg '
                         'kan de maker niet corrigeren.'}

    kaart = review.kaart
    nu = datetime.utcnow()
    review.status = besluit
    review.opmerking = (opmerking or '').strip() or None
    review.voltooid_op = nu

    if besluit == 'afgekeurd':
        # Sluit andere parallelle reviews op deze kaart (bv. ensceneringstips
        # loopt nog) zodat de maker niet meer onder review-druk zit.
        andere = Review.query.filter(
            Review.kaart_id == kaart.id,
            Review.status == 'open',
            Review.id != review.id
        ).all()
        for r in andere:
            r.status = 'afgekeurd'
            r.opmerking = (r.opmerking or '') + \
                '[Automatisch gesloten: andere reviewer heeft kaart afgekeurd.]'
            r.voltooid_op = nu
        kaart.status = 'concept'
        db.session.add(KaartWijziging(
            kaart_id=kaart.id,
            gebruiker_id=uitvoerder.id if uitvoerder else None,
            actie='review_afgekeurd',
            omschrijving=f'{review.fase_naam} afgekeurd. '
                          f'Opmerking: {review.opmerking or "—"}'[:500]
        ))
        return {'ok': True,
                'boodschap': 'Kaart is afgekeurd en teruggestuurd naar de maker.',
                'nieuwe_status': 'concept'}

    # besluit == 'goedgekeurd'
    db.session.add(KaartWijziging(
        kaart_id=kaart.id,
        gebruiker_id=uitvoerder.id if uitvoerder else None,
        actie='review_go',
        omschrijving=f'{review.fase_naam} goedgekeurd door {uitvoerder.naam if uitvoerder else "?"}.'[:500]
    ))

    if review.fase == 'specialist':
        # Eindcheck → publiceren.
        kaart.status = 'gepubliceerd'
        kaart.versie = (kaart.versie or 0) + 1
        kaart.versie_datum = nu
        db.session.add(KaartWijziging(
            kaart_id=kaart.id,
            gebruiker_id=uitvoerder.id if uitvoerder else None,
            actie='gepubliceerd',
            omschrijving=f'Gepubliceerd als versie {kaart.versie}.'
        ))
        return {'ok': True,
                'boodschap': f'Kaart gepubliceerd als versie {kaart.versie}.',
                'nieuwe_status': 'gepubliceerd'}

    # peer of ensceneringstips → check of alle 1e-ronde-reviews klaar zijn
    open_eerste_ronde = Review.query.filter(
        Review.kaart_id == kaart.id,
        Review.fase.in_(['peer', 'ensceneringstips']),
        Review.status == 'open'
    ).count()
    if open_eerste_ronde > 0:
        return {'ok': True,
                'boodschap': 'Jouw review is opgeslagen. '
                             f'Nog {open_eerste_ronde} parallelle review(s) open.',
                'nieuwe_status': 'peer_review'}

    # Beide eerste-ronde-reviews GO → start specialist-fase
    specialist = kies_specialist_reviewer(kaart)
    if specialist is None:
        return {'ok': False,
                'fout': 'Geen beschikbare specialist gevonden voor eindcheck. '
                         'Vraag een admin om een specialist toe te wijzen.'}
    db.session.add(Review(kaart_id=kaart.id, fase='specialist',
                           reviewer_id=specialist.id, status='open'))
    kaart.status = 'specialist_review'
    return {'ok': True,
            'boodschap': f'Peer-review afgerond — kaart is doorgezet naar '
                          f'specialist {specialist.naam} voor eindcheck.',
            'nieuwe_status': 'specialist_review'}
