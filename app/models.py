import json
import os
from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app import db, login_manager

KAART_TYPES = {
    'thema': {'prefix': 'TK', 'naam': 'Themakaart'},
    'instructie': {'prefix': 'IK', 'naam': 'Instructiekaart'},
    'scenario': {'prefix': 'SK', 'naam': 'Scenariokaart'},
    'opdracht': {'prefix': 'OK', 'naam': 'Opdrachtkaart'},
    # Checkkaart-letter is gereserveerd in het nummer-systeem (v0.7.8) —
    # het kaarttype zelf (formulier + PDF) volgt in een latere fase.
    'check':    {'prefix': 'CK', 'naam': 'Checkkaart'},
}

# Gebruikersrollen — volgorde = oplopend in rechten.
# 'medewerker_oo' = Medewerker Opleiden & Oefenen (geen kerngroep; mag enkel
# instructiekaart-materieel aanmaken + ensceneringstips reviewen op scenariokaart).
# 'oefencoordinator' en 'specialist' krijgen een kerngroep (zie KERNGROEPEN).
# 'oefencoordinator_roi' = extra review-laag voor Repressieve Oefen Inzet; krijgt
#   GEEN kerngroep maar een kerntaak-mapping (zie ROI_KERNTAKEN). Mag ook zelf
#   kaarten maken en indienen.
ROLLEN = {
    'medewerker_oo':        {'naam': 'Medewerker O&O',        'niveau': 1},
    'oefencoordinator':     {'naam': 'Oefencoördinator',      'niveau': 2},
    'oefencoordinator_roi': {'naam': 'Oefencoördinator ROI',  'niveau': 2},
    'specialist':           {'naam': 'Specialist',            'niveau': 3},
    'admin':                {'naam': 'Admin',                 'niveau': 4},
}

ROL_KEUZES = [(k, v['naam']) for k, v in ROLLEN.items()]

# Kerngroepen voor rol-onderscheiding. Oefencoördinatoren en specialisten kennen
# dezelfde 5. 'Algemeen' is géén kerngroep hier — algemene kaarten krijgen bij
# indienen door de maker een kerngroep met meeste affiniteit.
KERNGROEPEN = {
    'brand':       'Brand',
    'natuurbrand': 'Natuurbrand',
    'water':       'Waterongevallen',
    'thv':         'THV',
    'ibgs':        'IBGS',
}
KERNGROEP_KEUZES = [(k, v) for k, v in KERNGROEPEN.items()]

# Specialisten kennen dezelfde 5 kerngroepen.
SPECIALIST_KERNGROEPEN = dict(KERNGROEPEN)
SPECIALIST_KERNGROEP_KEUZES = [(k, v) for k, v in SPECIALIST_KERNGROEPEN.items()]

# Rollen die een kerngroep MOETEN hebben.
ROLLEN_MET_KERNGROEP = {'oefencoordinator', 'specialist'}

# ROI-kerntaak-koppeling. Een oefencoordinator_roi is verantwoordelijk voor ÉÉN
# ROI-groep. 'specialistisch' bundelt redvoertuig / bijzondere_blusmiddelen /
# overig / algemeen / leiding_coordinatie — daarvoor is één ROI-oefencoord.
ROI_KERNTAKEN = {
    'brand':          'Brand',
    'ibgs':           'IBGS',
    'thv':            'THV',
    'water':          'Waterongevallen',
    'natuurbrand':    'Natuurbrand',
    'specialistisch': 'Specialistisch materieel',
}
ROI_KERNTAAK_KEUZES = [(k, v) for k, v in ROI_KERNTAKEN.items()]

# Welke kerntaak (bron-sleutel van kaart) valt onder welke ROI-groep?
# Niet-genoemde kerntaken vallen onder 'specialistisch'.
KERNTAAK_NAAR_ROI = {
    'brand':          'brand',
    'ibgs':           'ibgs',
    'thv':            'thv',
    'water':          'water',
    'natuurbrand':    'natuurbrand',
    # Alles hieronder (redvoertuig, bijzondere_blusmiddelen, overig, algemeen,
    # leiding_coordinatie, …) valt onder 'specialistisch'.
}


# Kaart-types waarop GEEN ROI-review wordt gedaan. Scenariokaart heeft al een
# ensceneringstips-review door medewerker O&O. Themakaart bundelt bestaande
# kaarten en krijgt daarom zelf geen ROI-controle.
TYPES_ZONDER_ROI = {'scenario', 'thema'}


def roi_groep_voor_kaart(kaart):
    """Welke ROI-groep hoort bij deze kaart? None als het type GEEN ROI-review
    krijgt (scenario, thema) of als er geen kerntaak is — dan wordt de ROI-
    fase overgeslagen.
    """
    if kaart.type in TYPES_ZONDER_ROI:
        return None
    if not kaart.kerntaak:
        return None
    return KERNTAAK_NAAR_ROI.get(kaart.kerntaak, 'specialistisch')

# Kaart-status-waarden (voorheen alleen 'concept' / 'gepubliceerd').
KAART_STATUS = {
    'concept':            'Concept',
    'peer_review':        'Peer-review',
    'specialist_review':  'Specialist-review',
    'gepubliceerd':       'Gepubliceerd',
    # Archief: kaart is uit actief gebruik maar blijft raadpleegbaar — gezet
    # door specialist van de kerntaak of admin. Niet bewerkbaar, niet opnieuw
    # in te dienen. Kan terug naar 'gepubliceerd' door dezelfde rollen.
    'archief':            'Archief',
}

# Kerntaken van de brandweer — bepaalt kleur van zijbalk en badge.
# Afgeleid van KERNTAKEN_SEED (bron van waarheid, verderop in dit bestand).
# Sleutel = string (bv. 'brand', 'water'), waarde = dict met naam/afkorting/kleur.
# Wordt ook naar de DB gemigreerd door seed_kerntaken_en_subcategorieen().
def _kerntaken_map():
    """Build the KERNTAKEN-dict from KERNTAKEN_SEED (zelfde vorm als de oude constant)."""
    return {kt['sleutel']: {'naam': kt['naam'],
                              'afkorting': kt['afkorting'],
                              'kleur': kt['kleur']}
             for kt in KERNTAKEN_SEED}


# Dynamisch label voor het eerste kenmerkenveld op een scenariokaart,
# afhankelijk van de gekozen kerntaak. Afgeleid van dezelfde seed, met
# uitzonderingen voor natuurlijker Nederlandse labels bij enkele kerntaken.
def _kenmerken_labels_map():
    LEESBAAR = {
        'algemeen':                'Algemene kenmerken',
        'water':                   'Waterongevallenkenmerken',
        'brand':                   'Brandkenmerken',
        'natuurbrand':             'Natuurbrandkenmerken',
        'redvoertuig':             'Redvoertuigkenmerken',
        'bijzondere_blusmiddelen': 'Bijzondere-blusmiddel-kenmerken',
        'overig':                  'Materieelkenmerken',
        'leiding_coordinatie':     'Leidingskenmerken',
    }
    labels = {}
    for kt in KERNTAKEN_SEED:
        labels[kt['sleutel']] = LEESBAAR.get(kt['sleutel'], f"{kt['afkorting']}-kenmerken")
    return labels


def kenmerken_kerntaak_label(kerntaak):
    return KENMERKEN_KERNTAAK_LABELS.get(kerntaak or '', 'Kerntaak-kenmerken')


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    wachtwoord_hash = db.Column(db.String(256), nullable=False)
    naam = db.Column(db.String(100), nullable=False)
    rol = db.Column(db.String(30), nullable=False, default='medewerker_oo')
    # Kerngroep is alleen gevuld voor oefencoordinator + specialist. Admin,
    # medewerker_oo en oefencoordinator_roi hebben geen kerngroep.
    kerngroep = db.Column(db.String(20), nullable=True)
    # ROI-kerntaak is alleen gevuld voor oefencoordinator_roi (brand / ibgs /
    # thv / water / natuurbrand / specialistisch).
    roi_kerntaak = db.Column(db.String(30), nullable=True)
    actief = db.Column(db.Boolean, default=True)
    aangemaakt_op = db.Column(db.DateTime, default=datetime.utcnow)

    def set_wachtwoord(self, wachtwoord):
        self.wachtwoord_hash = generate_password_hash(wachtwoord)

    def check_wachtwoord(self, wachtwoord):
        return check_password_hash(self.wachtwoord_hash, wachtwoord)

    @property
    def rol_naam(self):
        return ROLLEN.get(self.rol, {}).get('naam', self.rol.capitalize())

    @property
    def kerngroep_naam(self):
        return KERNGROEPEN.get(self.kerngroep) if self.kerngroep else None

    @property
    def roi_kerntaak_naam(self):
        return ROI_KERNTAKEN.get(self.roi_kerntaak) if self.roi_kerntaak else None

    @property
    def is_admin(self):
        return self.rol == 'admin'

    @property
    def is_specialist(self):
        return self.rol == 'specialist'

    @property
    def is_oefencoordinator(self):
        # Blijft alléén de "echte" oefencoord (met kerngroep). Een ROI-oefencoord
        # is een eigen rol — check via `is_oefencoordinator_roi` of de combinatie
        # `is_oefencoordinator_breed`.
        return self.rol == 'oefencoordinator'

    @property
    def is_oefencoordinator_roi(self):
        return self.rol == 'oefencoordinator_roi'

    @property
    def is_oefencoordinator_breed(self):
        """Beide soorten oefencoord samen — handig voor rechten/UI-checks die
        tussen 'gewone' en 'ROI' niet hoeven te onderscheiden (bv. 'mag kaart
        maken en indienen')."""
        return self.rol in ('oefencoordinator', 'oefencoordinator_roi')

    @property
    def is_medewerker_oo(self):
        return self.rol == 'medewerker_oo'

    @property
    def is_coordinator(self):
        # Backward-compat alias zodat oude code (coordinator-checks) blijft werken
        # als oefencoördinator-check. Nieuwe code gebruikt is_oefencoordinator.
        return self.rol == 'oefencoordinator'

    @property
    def is_beheerder(self):
        # Backward-compat: ooit = admin; nu alias zodat admin_required blijft werken
        return self.rol == 'admin'

    def mag_peer_review(self, kaart):
        """Mag deze gebruiker peer-review doen voor deze kaart?
        Regel: oefencoördinator uit kerngroep van de kaart, niet de maker zelf.
        Bij kerntaak algemeen gebruikt systeem kaart.peer_kerngroep (door maker gekozen).
        """
        if not self.is_oefencoordinator or not self.kerngroep:
            return False
        if kaart.auteur_id == self.id:
            return False
        doel = kaart.peer_kerngroep or kaart.kerntaak
        return self.kerngroep == doel

    def mag_specialist_review(self, kaart):
        """Mag deze gebruiker specialist-eindcheck doen voor deze kaart?
        Regel: specialist, NIET van dezelfde kerngroep als de kaart.
        """
        if not self.is_specialist or not self.kerngroep:
            return False
        doel = kaart.peer_kerngroep or kaart.kerntaak
        return self.kerngroep != doel

    def mag_roi_review(self, kaart):
        """Mag deze gebruiker een ROI-review doen voor deze kaart?
        Regel: oefencoordinator_roi wiens roi_kerntaak matcht met de ROI-groep
        van de kaart (via KERNTAAK_NAAR_ROI). Niet voor scenario- of
        themakaarten (zie TYPES_ZONDER_ROI).
        """
        if not self.is_oefencoordinator_roi or not self.roi_kerntaak:
            return False
        if kaart.auteur_id == self.id:
            return False
        groep = roi_groep_voor_kaart(kaart)
        return groep is not None and self.roi_kerntaak == groep

    def __repr__(self):
        return f'<User {self.email}>'


@login_manager.user_loader
def load_user(user_id):
    user = db.session.get(User, int(user_id))
    # Gedeactiveerde gebruikers blijven anders ingelogd tot hun cookie verloopt.
    if user is None or not user.actief:
        return None
    return user


class Kaart(db.Model):
    __tablename__ = 'kaarten'

    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.String(20), nullable=False)
    # v0.7.42 — Nummer wordt pas toegekend bij eerste indienen voor review.
    # Concept-kaarten krijgen tot dan een placeholder "CONCEPT-xxxxxxxx" zodat
    # de UNIQUE NOT NULL-constraint intact blijft; UI toont dan "Concept" i.p.v.
    # het echte XX-#### nummer.
    nummer = db.Column(db.String(20), unique=True, nullable=False)
    naam = db.Column(db.String(200), nullable=False)
    status = db.Column(db.String(20), default='concept')
    # Als kerntaak 'algemeen' is, kiest de maker bij indienen een kerngroep
    # met meeste affiniteit — die bepaalt wie peer- en specialist-review doet.
    # Null = volg kerntaak van de kaart (standaardgeval).
    peer_kerngroep = db.Column(db.String(20), nullable=True)
    inhoud = db.Column(db.Text, default='{}')
    auteur_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    bijgewerkt_door_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    aangemaakt_op = db.Column(db.DateTime, default=datetime.utcnow)
    bijgewerkt_op = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    versie = db.Column(db.Integer, default=0, nullable=False)
    versie_datum = db.Column(db.DateTime, nullable=True)
    kerntaak = db.Column(db.String(30), nullable=True)
    subcategorie_id = db.Column(db.Integer, db.ForeignKey('subcategorieen.id'), nullable=True)
    header_foto = db.Column(db.String(255), nullable=True)
    ensceneringstips_foto = db.Column(db.String(255), nullable=True)
    ensceneringstips_foto_2 = db.Column(db.String(255), nullable=True)
    productfoto = db.Column(db.String(255), nullable=True)  # instructiekaart-materiaal: foto met markers

    auteur = db.relationship('User', foreign_keys=[auteur_id],
                             backref=db.backref('kaarten', lazy='dynamic'))
    bijgewerkt_door = db.relationship('User', foreign_keys=[bijgewerkt_door_id])
    subcategorie = db.relationship('Subcategorie', foreign_keys=[subcategorie_id])
    afbeeldingen = db.relationship('KaartAfbeelding', backref='kaart', lazy='dynamic',
                                   cascade='all, delete-orphan')
    wijzigingen = db.relationship('KaartWijziging', backref='kaart', lazy='dynamic',
                                  cascade='all, delete-orphan',
                                  order_by='KaartWijziging.datum.desc()')
    # Koppeltabellen — cascade bij delete zodat een themakaart met koppelingen
    # of een instructiekaart met QR-links schoon kan verdwijnen. Zonder deze
    # cascades geeft SQLite een FOREIGN KEY-fout bij delete.
    thema_koppelingen_bron = db.relationship(
        'ThemaKaartLink', foreign_keys='ThemaKaartLink.kaart_id',
        backref='bron_kaart', lazy='dynamic', cascade='all, delete-orphan')
    thema_qr_koppelingen = db.relationship(
        'ThemaQRLink', foreign_keys='ThemaQRLink.kaart_id',
        backref='bron_kaart', lazy='dynamic', cascade='all, delete-orphan')
    instructie_qr_koppelingen = db.relationship(
        'InstructieQRLink', foreign_keys='InstructieQRLink.kaart_id',
        backref='bron_kaart', lazy='dynamic', cascade='all, delete-orphan')

    def get_inhoud(self):
        return json.loads(self.inhoud) if self.inhoud else {}

    def set_inhoud(self, data):
        self.inhoud = json.dumps(data, ensure_ascii=False)

    @property
    def type_naam(self):
        return KAART_TYPES.get(self.type, {}).get('naam', self.type)

    @property
    def status_naam(self):
        return KAART_STATUS.get(self.status, self.status.capitalize())

    @property
    def huidige_review_locatie(self):
        """Korte tekst die zegt bij wie de kaart NU in de flow ligt.
        Leeg als de kaart niet in review staat. Gebruikt in dashboard/overzicht
        zodat elke gebruiker ziet wie er aan zet is.
        """
        if self.status not in ('peer_review', 'specialist_review'):
            return ''
        regels = self.review_locatie_regels
        if not regels:
            return self.status_naam
        return ' + '.join(r['tekst'] for r in regels)

    @property
    def review_locatie_regels(self):
        """Gedetailleerde variant van huidige_review_locatie: per fase één dict
        met {icoon, status, tekst}. Zo kan de template per rij een vinkje
        (goedgekeurd), kruisje (afgekeurd) of klok (openstaand) renderen.
        Bevat de rondes die nu LOPEN of in de huidige flow-ronde al afgerond
        zijn — niet de volledige geschiedenis.
        """
        if self.status not in ('peer_review', 'specialist_review'):
            return []
        # Alleen open reviews horen hier in de "ligt nu bij"-weergave — die zijn
        # waar de kaart op wacht. Afgekeurde reviews sturen de kaart terug naar
        # concept dus komen hier nooit voor; eerder-goedgekeurde van dezelfde
        # ronde slaan we ook even over want die wachten niet meer.
        if not hasattr(self, 'reviews'):
            return []
        open_rs = (self.reviews.filter_by(status='open')
                   .order_by(db.case((Review.fase == 'specialist', 2), else_=1),
                             Review.fase.asc())
                   .all())
        regels = []
        for r in open_rs:
            naam = r.reviewer.naam if r.reviewer else '?'
            regels.append({
                'status': 'open',
                'icoon': '⏳',
                'tekst': f'{r.fase_naam} bij {naam}',
                'reviewer': r.reviewer,
                'fase': r.fase,
            })
        return regels

    @property
    def effectieve_kerngroep(self):
        """De kerngroep die peer- en specialist-review aanstuurt. Als de
        kerntaak zelf één van de 5 kerngroep-sleutels is (brand, natuurbrand,
        water, thv, ibgs) → gebruik die. Anders (algemeen, redvoertuig,
        bijzondere_blusmiddelen, overig, leiding_coordinatie) gebruikt-ie
        peer_kerngroep die de maker bij indienen koos.
        """
        if self.kerntaak and self.kerntaak in KERNGROEPEN:
            return self.kerntaak
        return self.peer_kerngroep

    @property
    def kerntaak_info(self):
        return KERNTAKEN.get(self.kerntaak)

    @property
    def kerntaak_naam(self):
        info = self.kerntaak_info
        return info['naam'] if info else 'Niet toegekend'

    @property
    def kerntaak_kleur(self):
        info = self.kerntaak_info
        return info['kleur'] if info else '#B8B2A4'

    @property
    def kerntaak_afkorting(self):
        info = self.kerntaak_info
        return info['afkorting'] if info else '—'

    def get_thema_kaart_links(self):
        """Alle thema-kaart-koppelingen, gegroepeerd per tussentitel-index (0/1/2/3)."""
        rows = ThemaKaartLink.query.filter_by(kaart_id=self.id).order_by(
            ThemaKaartLink.groep_index, ThemaKaartLink.volgorde, ThemaKaartLink.id
        ).all()
        groepen = {0: [], 1: [], 2: [], 3: []}
        for r in rows:
            if r.groep_index in groepen and r.gekoppelde_kaart is not None:
                groepen[r.groep_index].append(r)
        return groepen

    def get_thema_qr_links(self):
        """Alle thema-QR-koppelingen, gegroepeerd per rij ('top' / 'bottom').

        DEPRECATED: gebruik get_thema_qr_verdeling() voor de nieuwe auto-verdeling.
        Deze blijft bestaan voor backward compat — bij toevoegen via de nieuwe UI
        wordt rij='auto' opgeslagen en gegroepeerd onder 'bottom'.
        """
        rows = ThemaQRLink.query.filter_by(kaart_id=self.id).order_by(
            ThemaQRLink.volgorde, ThemaQRLink.id
        ).all()
        return {
            'top':    [r for r in rows if r.rij == 'top'],
            'bottom': [r for r in rows if r.rij != 'top'],
        }

    def get_thema_qr_links_op_volgorde(self):
        """Eén platte lijst van alle thema-QR-koppelingen, op volgorde."""
        return ThemaQRLink.query.filter_by(kaart_id=self.id).order_by(
            ThemaQRLink.volgorde, ThemaQRLink.id
        ).all()

    def get_thema_qr_verdeling(self):
        """Auto-verdeling van QR-codes over twee rijen op basis van het aantal.

        - 1 t/m 10 codes: alle in de onderste rij (volle breedte)
        - 11 t/m 15: bovenste rij krijgt het overschot (vanaf links),
          onderste rij krijgt altijd 10
        """
        lijst = self.get_thema_qr_links_op_volgorde()
        aantal = len(lijst)
        if aantal <= 10:
            return {'top': [], 'bottom': lijst}
        boven = aantal - 10
        return {'top': lijst[:boven], 'bottom': lijst[boven:]}

    def get_thema_aantal_kaarten(self):
        return ThemaKaartLink.query.filter_by(kaart_id=self.id).count()

    def get_instructie_qr_links(self):
        """Lijst van QR-koppelingen voor een instructiekaart, op volgorde."""
        return InstructieQRLink.query.filter_by(kaart_id=self.id).order_by(
            InstructieQRLink.volgorde, InstructieQRLink.id
        ).all()

    def get_gekoppelde_kaarten(self):
        """Bidirectionele koppelingen: alle kaarten waaraan deze kaart gekoppeld is."""
        bron = db.session.query(KaartKoppeling.gekoppelde_kaart_id).filter(
            KaartKoppeling.kaart_id == self.id
        )
        doel = db.session.query(KaartKoppeling.kaart_id).filter(
            KaartKoppeling.gekoppelde_kaart_id == self.id
        )
        ids = {r[0] for r in bron.all()} | {r[0] for r in doel.all()}
        if not ids:
            return []
        return Kaart.query.filter(Kaart.id.in_(ids)).order_by(Kaart.type, Kaart.nummer).all()

    @staticmethod
    def volgende_nummer(kaart_type, kerntaak_cijfer, subcat_cijfer):
        """Bouw het volgende XX-#### nummer voor deze combinatie.

        XX     = kaart-type-letters uit KAART_TYPES (TK/IK/SK/OK/CK).
        1e #   = kerntaak-cijfer (0-9).
        2e #   = subcategorie-cijfer (0-9) binnen die kerntaak.
        3e+4e# = volgnummer 01-99, één hoger dan het hoogste bestaande
                 OF gereserveerde nummer met dezelfde 4-cijfer-prefix. Een
                 gereserveerd nummer (afkomstig van een verwijderde kaart)
                 wordt nooit opnieuw toegekend.

        Voorbeeld: volgende_nummer('instructie', 1, 1) → 'IK-1101' (of hoger
        als er al IK-1101, IK-1102, … bestaan of gereserveerd zijn).
        """
        prefix_letters = KAART_TYPES[kaart_type]['prefix']
        prefix_cijfers = f'{int(kerntaak_cijfer)}{int(subcat_cijfer)}'
        zoek = f'{prefix_letters}-{prefix_cijfers}'
        max_nr = 0
        # Bestaande kaarten (placeholders CONCEPT-* beginnen met 'C' en matchen
        # nooit het XX-####-prefix — geen aparte filter nodig). Een eenmaal
        # toegekend nummer verdwijnt NOOIT (kaarten met definitief nummer
        # blijven voor de logging bewaard, ook na afkeur), dus de max over
        # bestaande kaarten is altijd de hoogste ooit uitgegeven.
        for k in Kaart.query.filter(
            Kaart.type == kaart_type,
            Kaart.nummer.like(f'{zoek}%'),
        ).all():
            suffix = (k.nummer or '')[len(zoek):]
            # Strikt: exact 2 cijfers als volgnummer. Zo raken oude 3-cijfer
            # nummers (TK-001, IK-042 uit v0.7.x) de nieuwe generator niet in
            # de war.
            if len(suffix) != 2 or not suffix.isdigit():
                continue
            n = int(suffix)
            if n > max_nr:
                max_nr = n
        return f'{zoek}{max_nr + 1:02d}'

    @property
    def heeft_definitief_nummer(self):
        """False zolang de kaart nog een placeholder (CONCEPT-xxxxxxxx) draagt
        — kaart is dan nog nooit ingediend geweest. Zodra het echte XX-####-
        nummer is toegekend bij eerste indienen, blijft True permanent."""
        return bool(self.nummer) and not self.nummer.startswith('CONCEPT-')

    @property
    def nummer_leesbaar(self):
        """Toont het officiële nummer; bij een placeholder de werkbenaming
        'Concept #<id>' (voor overzichten / dashboards)."""
        if self.heeft_definitief_nummer:
            return self.nummer
        return f'Concept #{self.id}'

    def __repr__(self):
        return f'<Kaart {self.nummer} - {self.naam}>'


class KaartAfbeelding(db.Model):
    __tablename__ = 'kaart_afbeeldingen'

    id = db.Column(db.Integer, primary_key=True)
    kaart_id = db.Column(db.Integer, db.ForeignKey('kaarten.id'), nullable=False)
    bestandsnaam = db.Column(db.String(255), nullable=False)
    originele_naam = db.Column(db.String(255), nullable=False)
    beschrijving = db.Column(db.String(500), default='')
    aangemaakt_op = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Afbeelding {self.originele_naam}>'


class KaartKoppeling(db.Model):
    __tablename__ = 'kaart_koppelingen'

    id = db.Column(db.Integer, primary_key=True)
    kaart_id = db.Column(db.Integer, db.ForeignKey('kaarten.id'), nullable=False)
    gekoppelde_kaart_id = db.Column(db.Integer, db.ForeignKey('kaarten.id'), nullable=False)
    toelichting = db.Column(db.String(60), default='')
    aangemaakt_op = db.Column(db.DateTime, default=datetime.utcnow)
    aangemaakt_door_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    __table_args__ = (
        db.UniqueConstraint('kaart_id', 'gekoppelde_kaart_id', name='uq_kaart_koppeling'),
    )

    def __repr__(self):
        return f'<Koppeling {self.kaart_id}<->{self.gekoppelde_kaart_id}>'


class ThemaKaartLink(db.Model):
    """Koppeling van een themakaart aan een onderliggende kaart, gegroepeerd per tussentitel.

    groep_index: 0/1/2/3 — hoort bij tussentitel_1 / tussentitel_2 / tussentitel_3 / tussentitel_4.
    """
    __tablename__ = 'thema_kaart_links'

    id = db.Column(db.Integer, primary_key=True)
    kaart_id = db.Column(db.Integer, db.ForeignKey('kaarten.id'), nullable=False, index=True)
    gekoppelde_kaart_id = db.Column(db.Integer, db.ForeignKey('kaarten.id'), nullable=False)
    groep_index = db.Column(db.Integer, default=0, nullable=False)
    volgorde = db.Column(db.Integer, default=0, nullable=False)
    aangemaakt_op = db.Column(db.DateTime, default=datetime.utcnow)

    gekoppelde_kaart = db.relationship('Kaart', foreign_keys=[gekoppelde_kaart_id])

    __table_args__ = (
        db.UniqueConstraint('kaart_id', 'gekoppelde_kaart_id', name='uq_thema_kaart_link'),
    )

    def __repr__(self):
        return f'<ThemaKaartLink {self.kaart_id}->{self.gekoppelde_kaart_id} g{self.groep_index}>'


class InstructieQRLink(db.Model):
    """Koppeling van een instructiekaart aan een QR-code (achtergrondinformatie).

    Eenvoudig: geen rij/positie zoals bij themakaart — gewoon een lijst op volgorde.
    """
    __tablename__ = 'instructie_qr_links'

    id = db.Column(db.Integer, primary_key=True)
    kaart_id = db.Column(db.Integer, db.ForeignKey('kaarten.id'), nullable=False, index=True)
    qr_code_id = db.Column(db.Integer, db.ForeignKey('qr_codes.id'), nullable=False)
    volgorde = db.Column(db.Integer, default=0, nullable=False)
    aangemaakt_op = db.Column(db.DateTime, default=datetime.utcnow)

    qr_code = db.relationship('QRCode')

    __table_args__ = (
        db.UniqueConstraint('kaart_id', 'qr_code_id', name='uq_instructie_qr_link'),
    )

    def __repr__(self):
        return f'<InstructieQRLink kaart={self.kaart_id} qr={self.qr_code_id}>'


class ThemaQRLink(db.Model):
    """Koppeling van een themakaart aan een QR-code, in 'top' of 'bottom' rij."""
    __tablename__ = 'thema_qr_links'

    id = db.Column(db.Integer, primary_key=True)
    kaart_id = db.Column(db.Integer, db.ForeignKey('kaarten.id'), nullable=False, index=True)
    qr_code_id = db.Column(db.Integer, db.ForeignKey('qr_codes.id'), nullable=False)
    rij = db.Column(db.String(10), nullable=False, default='top')   # 'top' / 'bottom'
    volgorde = db.Column(db.Integer, default=0, nullable=False)
    label = db.Column(db.String(25), default='')   # eigen label (max 25), valt terug op QR.naam
    aangemaakt_op = db.Column(db.DateTime, default=datetime.utcnow)

    qr_code = db.relationship('QRCode')

    def __repr__(self):
        return f'<ThemaQRLink kaart={self.kaart_id} qr={self.qr_code_id} rij={self.rij}>'


# Limieten voor de themakaart (één centrale plek)
THEMA_MAX_KAARTEN_TOTAAL = 20
THEMA_MAX_QR_TOP = 5
THEMA_MAX_QR_BOTTOM = 10
THEMA_MAX_GROEPEN = 4
THEMA_QR_LABEL_MAX = 25

# Limieten voor de instructiekaart-achtergrondinformatie
INSTRUCTIE_MAX_KAART_KOPPELINGEN = 10
INSTRUCTIE_MAX_QR_KOPPELINGEN = 6


class KaartWijziging(db.Model):
    __tablename__ = 'kaart_wijzigingen'

    id = db.Column(db.Integer, primary_key=True)
    kaart_id = db.Column(db.Integer, db.ForeignKey('kaarten.id'), nullable=False)
    gebruiker_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    actie = db.Column(db.String(30), nullable=False)
    omschrijving = db.Column(db.String(500), default='')
    datum = db.Column(db.DateTime, default=datetime.utcnow)

    gebruiker = db.relationship('User')

    def __repr__(self):
        return f'<Wijziging {self.actie} op {self.kaart_id}>'


# ---------- Reviews (peer / ensceneringstips / specialist) ----------

REVIEW_FASEN = {
    'peer':             'Peer-review',
    'ensceneringstips': 'Ensceneringstips-review',
    'roi':              'ROI-review',
    'specialist':       'Specialist-eindcheck',
}

REVIEW_STATUSSEN = {
    'open':         'Open',
    'goedgekeurd':  'Goedgekeurd',
    'afgekeurd':    'Afgekeurd',
    # 'ingetrokken' = maker heeft de kaart teruggetrokken uit de flow terwijl er
    # nog reviews open stonden. De review-taak verdwijnt dan van het dashboard;
    # de kaart gaat terug naar concept en moet opnieuw ingediend worden.
    'ingetrokken':  'Ingetrokken',
}


class Review(db.Model):
    """Een review-taak gekoppeld aan één kaart. Elke indieningsronde van een kaart
    maakt één nieuwe Review per fase aan: peer + (bij scenariokaart) ensceneringstips
    parallel; na beider GO één specialist-review. Afgekeurd = taak gesloten en kaart
    terug naar concept bij de maker. Bij afwezigheid mogen collega-reviewers de
    reviewer_id overnemen (dashboard: 'Taak overnemen').
    """
    __tablename__ = 'reviews'

    id = db.Column(db.Integer, primary_key=True)
    kaart_id = db.Column(db.Integer, db.ForeignKey('kaarten.id'), nullable=False)
    fase = db.Column(db.String(30), nullable=False)
    reviewer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='open')
    opmerking = db.Column(db.Text, nullable=True)
    # Checklist-antwoorden (JSON string) — invulling komt in latere fase.
    checklist_json = db.Column(db.Text, nullable=True)
    aangemaakt_op = db.Column(db.DateTime, default=datetime.utcnow)
    voltooid_op = db.Column(db.DateTime, nullable=True)

    kaart = db.relationship('Kaart', backref=db.backref('reviews', lazy='dynamic',
                                                        cascade='all, delete-orphan'))
    reviewer = db.relationship('User', foreign_keys=[reviewer_id])

    @property
    def fase_naam(self):
        return REVIEW_FASEN.get(self.fase, self.fase)

    @property
    def status_naam(self):
        return REVIEW_STATUSSEN.get(self.status, self.status.capitalize())

    def __repr__(self):
        return f'<Review {self.fase} {self.status} kaart={self.kaart_id}>'


# ---------- OverdraagVerzoek (rechten-aanvraag bij ziekte/afwezigheid) ----------
# Een collega-oefencoördinator in dezelfde kerngroep kan níet zomaar bewerken
# (alleen de maker mag). Bij ziekte vraagt hij via dit model de specialist uit
# zijn kerngroep om het makerschap over te dragen. De specialist handelt het af
# via zijn dashboard — door de kaart aan de aanvrager toe te wijzen (goedkeuren)
# of het verzoek te weigeren.
OVERDRAAG_STATUSSEN = {
    'open':         'Open',
    'goedgekeurd':  'Goedgekeurd',
    'afgewezen':    'Afgewezen',
    'ingetrokken':  'Ingetrokken',
}


class OverdraagVerzoek(db.Model):
    __tablename__ = 'overdraag_verzoeken'

    id = db.Column(db.Integer, primary_key=True)
    kaart_id = db.Column(db.Integer, db.ForeignKey('kaarten.id'), nullable=False, index=True)
    aanvrager_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    bericht = db.Column(db.String(500), default='')
    status = db.Column(db.String(20), nullable=False, default='open')
    aangemaakt_op = db.Column(db.DateTime, default=datetime.utcnow)
    afgehandeld_door_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    afgehandeld_op = db.Column(db.DateTime, nullable=True)

    kaart = db.relationship('Kaart',
                             backref=db.backref('overdraag_verzoeken', lazy='dynamic',
                                                cascade='all, delete-orphan'))
    aanvrager = db.relationship('User', foreign_keys=[aanvrager_id])
    afgehandeld_door = db.relationship('User', foreign_keys=[afgehandeld_door_id])

    @property
    def status_naam(self):
        return OVERDRAAG_STATUSSEN.get(self.status, self.status.capitalize())

    def __repr__(self):
        return f'<OverdraagVerzoek {self.status} kaart={self.kaart_id} aanvrager={self.aanvrager_id}>'


# ---------- QR-codes ----------

QR_CATEGORIEEN = {
    'oefening':   'Oefening',
    'opleiding':  'Opleiding / cursus',
    'drukwerk':   'Drukwerk',
    'evaluatie':  'Evaluatie / formulier',
    'document':   'Planning / document',
    'video':      'Video',
    'campagne':   'Campagne / communicatie',
    'anders':     'Anders',
}

QR_CATEGORIE_KEUZES = [(k, v) for k, v in QR_CATEGORIEEN.items()]


# Visuele opmaak van de QR-code. Bepaalt achtergrond, QR-kleur, schild-kleur en tekstkleur.
QR_STIJLEN = {
    'navy':        'Navy blauw (huisstijl)',
    'rood':        'Brandweer-rood',
    'goud':        'Brandweer-goud',
    'transparant': 'Transparant (om zelf te plakken)',
    'wit':         'Wit (intern voor themakaart)',
}
# Alleen door gebruiker zelf te kiezen via formulier: 'wit' is reserved voor themakaart-context.
QR_STIJL_KEUZES = [(k, v) for k, v in QR_STIJLEN.items() if k != 'wit']


class QRCode(db.Model):
    __tablename__ = 'qr_codes'

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(30), unique=True, nullable=False, index=True)
    naam = db.Column(db.String(80), nullable=False)
    omschrijving = db.Column(db.String(300), default='')
    doel_url = db.Column(db.String(500), nullable=True)
    pdf_bestand = db.Column(db.String(255), nullable=True)
    categorie = db.Column(db.String(20), nullable=False, default='oefening')
    categorie_anders = db.Column(db.String(40), default='')
    tekst_boven = db.Column(db.String(40), default='')  # niet meer in UI, behouden voor data
    tekst_onder = db.Column(db.String(40), default='')
    transparante_achtergrond = db.Column(db.Boolean, default=False, nullable=False)
    stijl = db.Column(db.String(20), nullable=False, default='navy')
    actief = db.Column(db.Boolean, default=True, nullable=False)
    vervaldatum = db.Column(db.Date, nullable=True)
    eigenaar_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    aangemaakt_op = db.Column(db.DateTime, default=datetime.utcnow)
    bijgewerkt_op = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    eigenaar = db.relationship('User', backref=db.backref('qr_codes', lazy='dynamic'))
    kliks = db.relationship('QRKlik', backref='qr_code', lazy='dynamic',
                            cascade='all, delete-orphan', order_by='QRKlik.datum.desc()')

    @property
    def categorie_naam(self):
        if self.categorie == 'anders' and self.categorie_anders:
            return self.categorie_anders
        return QR_CATEGORIEEN.get(self.categorie, self.categorie)

    @property
    def aantal_kliks(self):
        return self.kliks.count()

    @property
    def laatste_klik(self):
        k = self.kliks.first()
        return k.datum if k else None

    @property
    def is_verlopen(self):
        if not self.vervaldatum:
            return False
        from datetime import date
        return self.vervaldatum < date.today()

    @property
    def status(self):
        if not self.actief:
            return 'gepauzeerd'
        if self.is_verlopen:
            return 'verlopen'
        return 'actief'

    @property
    def effectief_doel(self):
        """Waar de scan-redirect naartoe gaat: doel_url of een upload-PDF."""
        if self.pdf_bestand:
            return ('pdf', self.pdf_bestand)
        if self.doel_url:
            return ('url', self.doel_url)
        return (None, None)

    def mag_bewerken(self, user):
        if not user or not user.is_authenticated:
            return False
        return user.id == self.eigenaar_id or user.is_admin or user.is_specialist

    def __repr__(self):
        return f'<QRCode {self.slug} -> {self.naam}>'


class QRKlik(db.Model):
    __tablename__ = 'qr_kliks'

    id = db.Column(db.Integer, primary_key=True)
    qr_code_id = db.Column(db.Integer, db.ForeignKey('qr_codes.id'), nullable=False)
    datum = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def __repr__(self):
        return f'<QRKlik qr={self.qr_code_id} {self.datum}>'


# =========================================================================
#   Nummering-schema (VGGM-stijl): XX-####
#   XX  = kaart-type-letters (TK/IK/SK/OK/CK)
#   #   = kerntaak-cijfer (0-9)
#   #   = subcategorie-cijfer (0-9) binnen die kerntaak
#   ## = volgnummer (01-99) binnen die combinatie
#
#   Kerntaken en subcategorieen leven in de DB (via /beheer/nummering) —
#   NIET meer in de KERNTAKEN-constante hierboven (die blijft nog even
#   staan als fallback / backward-compat tot fase 2/3 klaar is).
# =========================================================================

class Kerntaak(db.Model):
    __tablename__ = 'kerntaken'

    id = db.Column(db.Integer, primary_key=True)
    cijfer = db.Column(db.Integer, unique=True, nullable=False)
    sleutel = db.Column(db.String(30), unique=True, nullable=False)
    naam = db.Column(db.String(80), nullable=False)
    afkorting = db.Column(db.String(10), nullable=False, default='')
    kleur = db.Column(db.String(9), nullable=False, default='#B8B2A4')

    subcategorieen = db.relationship('Subcategorie', backref='kerntaak',
                                      lazy='dynamic', cascade='all, delete-orphan',
                                      order_by='Subcategorie.cijfer')

    def __repr__(self):
        return f'<Kerntaak {self.cijfer} {self.naam}>'


class Subcategorie(db.Model):
    __tablename__ = 'subcategorieen'

    id = db.Column(db.Integer, primary_key=True)
    kerntaak_id = db.Column(db.Integer, db.ForeignKey('kerntaken.id'), nullable=False, index=True)
    cijfer = db.Column(db.Integer, nullable=False)
    naam = db.Column(db.String(120), nullable=False)

    __table_args__ = (
        db.UniqueConstraint('kerntaak_id', 'cijfer', name='uq_subcategorie_kerntaak_cijfer'),
    )

    def __repr__(self):
        return f'<Subcategorie {self.kerntaak.cijfer if self.kerntaak else "?"}{self.cijfer} {self.naam}>'


# VASTE kerntaken — deze lijst is de bron van waarheid. Naam/afkorting/kleur/cijfer
# zijn niet via de UI te wijzigen (alleen subcategorieën zijn dat). Kleuren komen uit
# huisstijl/Primaire kleuren.txt; kerntaken zonder eigen huisstijl-kleur krijgen het
# Algemene Brandweergoud (#CC9933).
KLEUR_ALGEMEEN = '#CC9933'  # Brandweergoud — fallback voor kerntaken zonder eigen kleur

KERNTAKEN_SEED = [
    {'cijfer': 0, 'sleutel': 'algemeen',              'naam': 'Algemeen',                      'afkorting': 'ALG',  'kleur': KLEUR_ALGEMEEN},
    {'cijfer': 1, 'sleutel': 'water',                 'naam': 'Waterongevallen',               'afkorting': 'WO',   'kleur': '#4B70A6'},  # Water Blauw
    {'cijfer': 2, 'sleutel': 'ibgs',                  'naam': 'IBGS',                          'afkorting': 'IBGS', 'kleur': '#DAB94F'},  # IBGS Geel
    {'cijfer': 3, 'sleutel': 'brand',                 'naam': 'Brand',                         'afkorting': 'BR',   'kleur': '#B6463D'},  # Brand Rood
    {'cijfer': 4, 'sleutel': 'natuurbrand',           'naam': 'Natuurbrand',                   'afkorting': 'NB',   'kleur': '#2F5D3A'},  # Natuurbrand Groen
    {'cijfer': 5, 'sleutel': 'redvoertuig',           'naam': 'Redvoertuig',                   'afkorting': 'RV',   'kleur': KLEUR_ALGEMEEN},
    {'cijfer': 6, 'sleutel': 'bijzondere_blusmiddelen','naam': 'Bijzondere blusmiddelen',      'afkorting': 'BM',   'kleur': KLEUR_ALGEMEEN},
    {'cijfer': 7, 'sleutel': 'thv',                   'naam': 'Technische Hulpverlening',      'afkorting': 'THV',  'kleur': '#4C7F52'},  # THV Groen
    {'cijfer': 8, 'sleutel': 'overig',                'naam': 'Overig materieel',              'afkorting': 'OM',   'kleur': KLEUR_ALGEMEEN},
    {'cijfer': 9, 'sleutel': 'leiding_coordinatie',   'naam': 'Leiding & Coördinatie',         'afkorting': 'LC',   'kleur': KLEUR_ALGEMEEN},
]

# Subcategorieen per kerntaak-cijfer. Namen met "?" komen letterlijk uit de
# Excel — bewust zo bewaard totdat de admin ze via de beheerpagina hernoemt.
SUBCATEGORIEEN_SEED = {
    0: [  # Algemeen
        (0, 'Oefenmiddelen?'),
        (1, 'Dienstvoertuigen?'),
        (2, 'Communicatiemiddelen & Randapparatuur'),
    ],
    1: [  # Waterongevallen
        (0, 'Waterongevallen (basis brandweerzorg)'),
        (1, 'Schippers'),
        (2, 'ORT'),
        (3, 'Duikteam'),
        (4, 'ROV'),
    ],
    2: [  # IBGS / Gevaarlijke stoffen
        (0, 'Gevaarlijke stoffen (basis brandweerzorg)'),
        (1, 'VKE'),
        (2, 'GSE'),
        (3, 'BOE'),
    ],
    3: [  # Brand
        (0, 'Brand (basis brandweerzorg)'),
        (1, 'Nog te bepalen — 31'),
        (2, 'Nog te bepalen — 32'),
        (3, 'Nog te bepalen — 33'),
    ],
    4: [  # Natuurbrand
        (0, 'Natuurbrand (basis brandweerzorg)'),
        (1, 'NBH'),
        (2, 'Handcrew?'),
    ],
    5: [  # Redden van hoogte
        (1, 'Redvoertuig'),
    ],
    6: [  # Bijzondere blusmiddelen
        (1, 'Waterwagen'),
        (2, 'SIV'),
        (3, 'Grootwatertransport'),
        (4, 'Schuimblusvoertuig'),
        (5, 'SBE'),
        (6, 'Robot'),
    ],
    7: [  # Technische Hulpverlening
        (0, 'Technische Hulpverlening (basis brandweerzorg)'),
        (1, 'SHE'),
    ],
    8: [  # Overig materieel
        (0, 'Chauffeur TS?'),
        (1, 'Haakarmchassis'),
        (2, 'ABH'),
        (3, 'Droneteam (Team Digitale Verkenning)?'),
        (4, 'VZH'),
        (5, 'Veilige Energietransitie?'),
    ],
    9: [  # Leiding & Coördinatie
        (0, 'Bevelvoerders?'),
        (1, 'MCU'),
        (2, 'BCU'),
        (3, 'LAC'),
    ],
}


# Module-constanten opgebouwd uit KERNTAKEN_SEED — deze worden op module-import-tijd
# geëvalueerd (Kaart.kerntaak_info leest ze lazy op via `KERNTAKEN.get(...)`).
KERNTAKEN = _kerntaken_map()
KENMERKEN_KERNTAAK_LABELS = _kenmerken_labels_map()


def seed_kerntaken_en_subcategorieen():
    """Vulling van kerntaken (VAST) + subcategorieen uit de Excel-schema.

    Kerntaken zijn de bron van waarheid in de code — naam, afkorting én kleur
    worden bij elke start FORCE-gesynchroniseerd naar de KERNTAKEN_SEED-lijst.
    Zo blijft de UI-lijst altijd in lijn met huisstijl/Primaire kleuren.txt,
    ook als de DB al gevuld is met oudere waarden.

    Subcategorieën worden alleen toegevoegd als ze ontbreken — hun namen blijven
    door de admin bewerkbaar via /beheer/nummering.
    """
    for kt_seed in KERNTAKEN_SEED:
        bestaand = Kerntaak.query.filter_by(cijfer=kt_seed['cijfer']).first()
        if bestaand is None:
            kt = Kerntaak(**kt_seed)
            db.session.add(kt)
            db.session.flush()
        else:
            kt = bestaand
            kt.sleutel = kt_seed['sleutel']
            kt.naam = kt_seed['naam']
            kt.afkorting = kt_seed['afkorting']
            kt.kleur = kt_seed['kleur']
        for cijfer, naam in SUBCATEGORIEEN_SEED.get(kt.cijfer, []):
            sub_bestaand = Subcategorie.query.filter_by(
                kerntaak_id=kt.id, cijfer=cijfer
            ).first()
            if sub_bestaand is None:
                db.session.add(Subcategorie(
                    kerntaak_id=kt.id, cijfer=cijfer, naam=naam
                ))
    db.session.commit()


def migreer_schema():
    """Eenvoudige in-place migratie: voeg ontbrekende kolommen toe aan bestaande SQLite-tabellen."""
    from sqlalchemy import text, inspect
    inspector = inspect(db.engine)
    if 'kaarten' in inspector.get_table_names():
        kolommen = [c['name'] for c in inspector.get_columns('kaarten')]
        if 'bijgewerkt_door_id' not in kolommen:
            with db.engine.begin() as conn:
                conn.execute(text('ALTER TABLE kaarten ADD COLUMN bijgewerkt_door_id INTEGER'))
        kolommen = [c['name'] for c in inspector.get_columns('kaarten')]
        if 'versie' not in kolommen:
            with db.engine.begin() as conn:
                conn.execute(text('ALTER TABLE kaarten ADD COLUMN versie INTEGER DEFAULT 0'))
        if 'versie_datum' not in kolommen:
            with db.engine.begin() as conn:
                conn.execute(text('ALTER TABLE kaarten ADD COLUMN versie_datum DATETIME'))
        kolommen = [c['name'] for c in inspector.get_columns('kaarten')]
        if 'kerntaak' not in kolommen:
            with db.engine.begin() as conn:
                conn.execute(text('ALTER TABLE kaarten ADD COLUMN kerntaak VARCHAR(10)'))
        kolommen = [c['name'] for c in inspector.get_columns('kaarten')]
        if 'header_foto' not in kolommen:
            with db.engine.begin() as conn:
                conn.execute(text('ALTER TABLE kaarten ADD COLUMN header_foto VARCHAR(255)'))
        kolommen = [c['name'] for c in inspector.get_columns('kaarten')]
        if 'ensceneringstips_foto' not in kolommen:
            with db.engine.begin() as conn:
                conn.execute(text('ALTER TABLE kaarten ADD COLUMN ensceneringstips_foto VARCHAR(255)'))
        kolommen = [c['name'] for c in inspector.get_columns('kaarten')]
        if 'ensceneringstips_foto_2' not in kolommen:
            with db.engine.begin() as conn:
                conn.execute(text('ALTER TABLE kaarten ADD COLUMN ensceneringstips_foto_2 VARCHAR(255)'))
        kolommen = [c['name'] for c in inspector.get_columns('kaarten')]
        if 'productfoto' not in kolommen:
            with db.engine.begin() as conn:
                conn.execute(text('ALTER TABLE kaarten ADD COLUMN productfoto VARCHAR(255)'))
        kolommen = [c['name'] for c in inspector.get_columns('kaarten')]
        if 'subcategorie_id' not in kolommen:
            with db.engine.begin() as conn:
                conn.execute(text('ALTER TABLE kaarten ADD COLUMN subcategorie_id INTEGER'))

    # Koppeling-toelichting
    if 'kaart_koppelingen' in inspector.get_table_names():
        kolommen = [c['name'] for c in inspector.get_columns('kaart_koppelingen')]
        if 'toelichting' not in kolommen:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE kaart_koppelingen ADD COLUMN toelichting VARCHAR(60) DEFAULT ''"))

    # Rollen hernoemen (historische stappen behouden voor oude DB's):
    #   beheerder   → admin
    #   redacteur   → medewerker       (oude naam vóór v0.8-rolwijziging)
    # En vervolgens de v0.8-rolwijziging:
    #   coordinator → oefencoordinator
    #   medewerker  → medewerker_oo
    #   specialist blijft; admin blijft.
    # Nieuwe kolom `kerngroep` wordt leeg aangemaakt — admin vult 'm per gebruiker
    # in via /beheer. Oefencoördinatoren en specialisten zonder kerngroep worden
    # door de UI zichtbaar gemarkeerd ('kerngroep ontbreekt').
    if 'users' in inspector.get_table_names():
        kolommen = [c['name'] for c in inspector.get_columns('users')]
        if 'kerngroep' not in kolommen:
            with db.engine.begin() as conn:
                conn.execute(text('ALTER TABLE users ADD COLUMN kerngroep VARCHAR(20)'))
        if 'roi_kerntaak' not in kolommen:
            with db.engine.begin() as conn:
                conn.execute(text('ALTER TABLE users ADD COLUMN roi_kerntaak VARCHAR(30)'))
        with db.engine.begin() as conn:
            conn.execute(text("UPDATE users SET rol='admin' WHERE rol='beheerder'"))
            conn.execute(text("UPDATE users SET rol='medewerker' WHERE rol='redacteur'"))
            conn.execute(text("UPDATE users SET rol='oefencoordinator' WHERE rol='coordinator'"))
            conn.execute(text("UPDATE users SET rol='medewerker_oo' WHERE rol='medewerker'"))
            # v0.7.41 — kerngroep 'roi' bestaat niet meer; die rol is nu een
            # aparte oefencoordinator_roi met roi_kerntaak-koppeling. Oude
            # gebruikers met kerngroep='roi' zetten we tijdelijk zonder
            # kerngroep neer zodat admin ze kan omzetten — ze verliezen geen
            # inlog-mogelijkheid.
            conn.execute(text("UPDATE users SET kerngroep=NULL WHERE kerngroep='roi'"))

    # Kaart: peer_kerngroep-kolom toevoegen. Bij EERSTE toevoeging (= deze
    # migratie draait voor het eerst op een bestaande DB) zetten we meteen
    # ALLE bestaande kaarten op 'gepubliceerd' — zonder dat zouden ze allemaal
    # als 'concept' in de nieuwe workflow belanden en de QR's offline gaan.
    # De `peer_kerngroep`-check werkt als idempotentie-sentinel: na toevoegen
    # loopt deze UPDATE nooit opnieuw.
    if 'kaarten' in inspector.get_table_names():
        kolommen = [c['name'] for c in inspector.get_columns('kaarten')]
        if 'peer_kerngroep' not in kolommen:
            with db.engine.begin() as conn:
                conn.execute(text('ALTER TABLE kaarten ADD COLUMN peer_kerngroep VARCHAR(20)'))
                conn.execute(text("UPDATE kaarten SET status='gepubliceerd'"))


    # QR-codes: transparante_achtergrond kolom toevoegen als die ontbreekt
    if 'qr_codes' in inspector.get_table_names():
        kolommen = [c['name'] for c in inspector.get_columns('qr_codes')]
        if 'transparante_achtergrond' not in kolommen:
            with db.engine.begin() as conn:
                conn.execute(text(
                    'ALTER TABLE qr_codes ADD COLUMN transparante_achtergrond BOOLEAN DEFAULT 0 NOT NULL'
                ))
        kolommen = [c['name'] for c in inspector.get_columns('qr_codes')]
        if 'stijl' not in kolommen:
            with db.engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE qr_codes ADD COLUMN stijl VARCHAR(20) DEFAULT 'navy' NOT NULL"
                ))

    # Kaartnummering hernoemen naar VGGM-stijl: THEMA→TK, INS→IK, SCEN→SK, OPDR→OK
    if 'kaarten' in inspector.get_table_names():
        with db.engine.begin() as conn:
            conn.execute(text(
                "UPDATE kaarten SET nummer = 'TK-' || substr(nummer, 7) "
                "WHERE type='thema' AND nummer LIKE 'THEMA-%'"
            ))
            conn.execute(text(
                "UPDATE kaarten SET nummer = 'IK-' || substr(nummer, 5) "
                "WHERE type='instructie' AND nummer LIKE 'INS-%'"
            ))
            conn.execute(text(
                "UPDATE kaarten SET nummer = 'SK-' || substr(nummer, 6) "
                "WHERE type='scenario' AND nummer LIKE 'SCEN-%'"
            ))
            conn.execute(text(
                "UPDATE kaarten SET nummer = 'OK-' || substr(nummer, 6) "
                "WHERE type='opdracht' AND nummer LIKE 'OPDR-%'"
            ))


def seed_admin():
    # In productie: ADMIN_EMAIL en ADMIN_WACHTWOORD via env-var (Docker/VPS).
    # In dev/lokaal: fallback op de defaults — wijzig direct na eerste login.
    admin_email = os.environ.get('ADMIN_EMAIL', 'admin@vakbekwaam.nl')
    admin_wachtwoord = os.environ.get('ADMIN_WACHTWOORD', 'Wijzigen123!')
    if not User.query.filter_by(email=admin_email).first():
        admin = User(
            email=admin_email,
            naam='Beheerder',
            rol='admin'
        )
        admin.set_wachtwoord(admin_wachtwoord)
        db.session.add(admin)
        db.session.commit()
        print(f'Admin-account aangemaakt: {admin_email} — wijzig het wachtwoord direct na eerste login.')
