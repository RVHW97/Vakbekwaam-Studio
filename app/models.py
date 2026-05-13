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
}

# Gebruikersrollen — volgorde = oplopend in rechten
ROLLEN = {
    'medewerker':  {'naam': 'Medewerker',  'niveau': 1},
    'coordinator': {'naam': 'Coördinator', 'niveau': 2},
    'specialist':  {'naam': 'Specialist',  'niveau': 3},
    'admin':       {'naam': 'Admin',       'niveau': 4},
}

ROL_KEUZES = [(k, v['naam']) for k, v in ROLLEN.items()]

# Kerntaken van de brandweer — bepaalt kleur van zijbalk en badge
KERNTAKEN = {
    'algemeen': {'naam': 'Algemeen',     'afkorting': 'ALG',  'kleur': '#CC9933'},
    'brand':    {'naam': 'Brand',        'afkorting': 'BR',   'kleur': '#B6463D'},
    'thv':      {'naam': 'Hulpverlening','afkorting': 'THV',  'kleur': '#4C7F52'},
    'ibgs':     {'naam': 'IBGS',         'afkorting': 'IBGS', 'kleur': '#DAB94F'},
    'water':    {'naam': 'Waterongevallen','afkorting': 'WO', 'kleur': '#4B70A6'},
}

# Dynamisch label voor het eerste kenmerkenveld op een scenariokaart,
# afhankelijk van de gekozen kerntaak.
KENMERKEN_KERNTAAK_LABELS = {
    'algemeen': 'Algemene kenmerken',
    'brand':    'Brandkenmerken',
    'thv':      'THV-kenmerken',
    'ibgs':     'IBGS-kenmerken',
    'water':    'WO-kenmerken',
}

def kenmerken_kerntaak_label(kerntaak):
    return KENMERKEN_KERNTAAK_LABELS.get(kerntaak or '', 'Kerntaak-kenmerken')


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    wachtwoord_hash = db.Column(db.String(256), nullable=False)
    naam = db.Column(db.String(100), nullable=False)
    rol = db.Column(db.String(20), nullable=False, default='medewerker')
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
    def is_admin(self):
        return self.rol == 'admin'

    @property
    def is_specialist(self):
        return self.rol == 'specialist'

    @property
    def is_coordinator(self):
        return self.rol == 'coordinator'

    @property
    def is_beheerder(self):
        # Backward-compat: ooit = admin; nu alias zodat admin_required blijft werken
        return self.rol == 'admin'

    def __repr__(self):
        return f'<User {self.email}>'


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class Kaart(db.Model):
    __tablename__ = 'kaarten'

    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.String(20), nullable=False)
    nummer = db.Column(db.String(20), unique=True, nullable=False)
    naam = db.Column(db.String(200), nullable=False)
    status = db.Column(db.String(20), default='concept')
    inhoud = db.Column(db.Text, default='{}')
    auteur_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    bijgewerkt_door_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    aangemaakt_op = db.Column(db.DateTime, default=datetime.utcnow)
    bijgewerkt_op = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    versie = db.Column(db.Integer, default=0, nullable=False)
    versie_datum = db.Column(db.DateTime, nullable=True)
    kerntaak = db.Column(db.String(10), nullable=True)
    header_foto = db.Column(db.String(255), nullable=True)
    ensceneringstips_foto = db.Column(db.String(255), nullable=True)
    productfoto = db.Column(db.String(255), nullable=True)  # instructiekaart-materiaal: foto met markers

    auteur = db.relationship('User', foreign_keys=[auteur_id],
                             backref=db.backref('kaarten', lazy='dynamic'))
    bijgewerkt_door = db.relationship('User', foreign_keys=[bijgewerkt_door_id])
    afbeeldingen = db.relationship('KaartAfbeelding', backref='kaart', lazy='dynamic',
                                   cascade='all, delete-orphan')
    wijzigingen = db.relationship('KaartWijziging', backref='kaart', lazy='dynamic',
                                  cascade='all, delete-orphan',
                                  order_by='KaartWijziging.datum.desc()')

    def get_inhoud(self):
        return json.loads(self.inhoud) if self.inhoud else {}

    def set_inhoud(self, data):
        self.inhoud = json.dumps(data, ensure_ascii=False)

    @property
    def type_naam(self):
        return KAART_TYPES.get(self.type, {}).get('naam', self.type)

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
        """Alle thema-kaart-koppelingen, gegroepeerd per tussentitel-index (0/1/2)."""
        rows = ThemaKaartLink.query.filter_by(kaart_id=self.id).order_by(
            ThemaKaartLink.groep_index, ThemaKaartLink.volgorde, ThemaKaartLink.id
        ).all()
        groepen = {0: [], 1: [], 2: []}
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
    def volgende_nummer(kaart_type):
        prefix = KAART_TYPES[kaart_type]['prefix']
        kaarten = Kaart.query.filter_by(type=kaart_type).all()
        max_nr = 0
        for k in kaarten:
            deel = (k.nummer or '').split('-', 1)
            if len(deel) == 2:
                try:
                    n = int(deel[1])
                    if n > max_nr:
                        max_nr = n
                except ValueError:
                    continue  # niet-numerieke achtervoegsels (bv. SK-TEST) overslaan
        return f'{prefix}-{max_nr + 1:03d}'

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

    groep_index: 0/1/2 — hoort bij tussentitel_1 / tussentitel_2 / tussentitel_3.
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
THEMA_MAX_GROEPEN = 3
THEMA_QR_LABEL_MAX = 25


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
        if 'productfoto' not in kolommen:
            with db.engine.begin() as conn:
                conn.execute(text('ALTER TABLE kaarten ADD COLUMN productfoto VARCHAR(255)'))

    # Koppeling-toelichting
    if 'kaart_koppelingen' in inspector.get_table_names():
        kolommen = [c['name'] for c in inspector.get_columns('kaart_koppelingen')]
        if 'toelichting' not in kolommen:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE kaart_koppelingen ADD COLUMN toelichting VARCHAR(60) DEFAULT ''"))

    # Rollen hernoemen: beheerder → admin, redacteur → medewerker
    if 'users' in inspector.get_table_names():
        with db.engine.begin() as conn:
            conn.execute(text("UPDATE users SET rol='admin' WHERE rol='beheerder'"))
            conn.execute(text("UPDATE users SET rol='medewerker' WHERE rol='redacteur'"))

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
