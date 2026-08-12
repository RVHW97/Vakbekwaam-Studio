from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, MultipleFileField, FileField, SubmitField, SelectField, IntegerField, RadioField
from wtforms.validators import DataRequired, Optional, Length, NumberRange

NAAM_MAX = 40
NAAM_VALIDATORS = [
    DataRequired(message='Vul een naam in.'),
    Length(max=NAAM_MAX, message=f'De titel mag maximaal {NAAM_MAX} tekens lang zijn (anders past hij niet in de header van de PDF).'),
]

def _kerntaak_keuzes():
    """Bouw KERNTAAK_KEUZES uit KERNTAKEN_SEED — 10 keuzes op cijfer-volgorde."""
    from app.models import KERNTAKEN_SEED
    return [('', '— kies een kerntaak —')] + [(kt['sleutel'], kt['naam']) for kt in KERNTAKEN_SEED]


# Wordt in de routes opnieuw geset bij form-init (form.kerntaak.choices = ...),
# maar zetten we hier alvast zodat de default-choices niet leeg zijn tijdens tests.
KERNTAAK_KEUZES = _kerntaak_keuzes()
KERNTAAK_VALIDATORS = [DataRequired(message='Kies een kerntaak.')]

DOELGROEP_KEUZES = [
    ('', '— kies doelgroep —'),
    ('manschappen', 'Manschappen'),
    ('bevelvoerders', 'Bevelvoerders'),
    ('chauffeurs', 'Chauffeurs'),
    ('spec', 'SPEC'),
    ('anders', 'Anders (zelf invullen)'),
]

TIJDSDUUR_KEUZES = [
    ('', '— kies tijdsduur —'),
    ('1_oefenavond', '1 oefenavond'),
    ('1_dagdeel', '1 dagdeel'),
    ('2_dagdelen', '2 dagdelen'),
]

PAGER_PRIO_KEUZES = [
    ('', '— kies prioriteit —'),
    ('1', 'Prio 1'),
    ('2', 'Prio 2'),
    ('3', 'Prio 3'),
]

OEFENLEIDER_ROL_KEUZES = [
    ('', '— niet gespecificeerd —'),
    ('PKD-brand', 'PKD Brand'),
    ('PKD-ibgs', 'PKD IBGS'),
    ('PKD-water', 'PKD Waterongevallen'),
    ('PKD-thv', 'PKD THV'),
    ('PKD-moi', 'PKD MOI'),
    ('PKD-zagen', 'PKD Zagen'),
    ('anders', 'Anders (zelf invullen)'),
]

PAGER_SOORT_KEUZES = [
    ('', '— kies soort melding —'),
    ('BR-binnen', 'BR-binnen'),
    ('BR-buiten', 'BR-buiten'),
    ('BR-woning', 'BR-woning'),
    ('BR-auto', 'BR-auto'),
    ('BR-container', 'BR-container'),
    ('BR-industrie', 'BR-industrie'),
    ('BR-schoorsteen', 'BR-schoorsteen'),
    ('BR-natuur', 'BR-natuur'),
    ('HV-algemeen', 'HV-algemeen'),
    ('HV-beknelling', 'HV-beknelling'),
    ('HV-verkeersongeval', 'HV-verkeersongeval'),
    ('HV-liftopsluiting', 'HV-liftopsluiting'),
    ('HV-dier', 'HV-dier in nood'),
    ('HV-stormschade', 'HV-stormschade'),
    ('HV-water', 'HV-wateroverlast'),
    ('IBGS-lekkage', 'IBGS-lekkage'),
    ('IBGS-gas', 'IBGS-gasverspreiding'),
    ('WO-duik', 'WO-duikincident'),
    ('WO-boot', 'WO-vaartuig'),
    ('ASS-ambu', 'Assistentie ambulance'),
    ('OMS-loos', 'OMS-melding / loos'),
    ('anders', 'Anders (zelf invullen)'),
]


class ThemakaartForm(FlaskForm):
    # Themakaart heeft GEEN apart `naam` veld — de titel fungeert als naam intern
    # (voor overzichten en zoeken). Wordt server-side gekopieerd: kaart.naam = form.titel.data.
    kerntaak = SelectField('Kerntaak', choices=KERNTAAK_KEUZES, validators=KERNTAAK_VALIDATORS)
    subcategorie_id = SelectField('Subcategorie', choices=[('', '— kies eerst een kerntaak —')],
                                    validators=[Optional()], validate_choice=False)
    header_foto = FileField('Achtergrondfoto')

    titel = StringField('Titel',
                        validators=[DataRequired(message='Vul een titel in.'),
                                    Length(max=40, message='Max 40 tekens.')])
    ondertitel = StringField('Ondertitel',
                              validators=[Optional(), Length(max=50, message='Max 50 tekens.')])

    tussentitel_1 = StringField('Tussentitel 1', validators=[Optional(), Length(max=45, message='Max 45 tekens.')])
    tussentitel_2 = StringField('Tussentitel 2', validators=[Optional(), Length(max=45, message='Max 45 tekens.')])
    tussentitel_3 = StringField('Tussentitel 3', validators=[Optional(), Length(max=45, message='Max 45 tekens.')])

    submit = SubmitField('Opslaan als concept')


INSTRUCTIE_TYPE_KEUZES = [
    ('materiaal', 'Materiaal'),
    ('procedure', 'Procedure / werkwijze'),
]

# Werkwijze: maximaal aantal stappen op één instructiekaart (platte lijst).
WERKWIJZE_MAX_STAPPEN = 20
WERKWIJZE_TITEL_MAX = 35
# Per-layout max tekens voor de stap-uitleg. Vaste kaart-hoogte in de PDF
# vereist een voorspelbare hoeveelheid tekst — A/B hebben halve breedte
# (foto + tekst naast elkaar), C de volle breedte, D ook volledig (onder de foto).
WERKWIJZE_TEKST_MAX = {
    'B': 420,
    'C': 560,
    'D': 480,
}

# Veiligheid (fase 6g) — vrije opsomming van max 5 korte zinnen.
VEILIGHEID_MAX_ZINNEN = 5
VEILIGHEID_ZIN_MAX = 100


class InstructiekaartForm(FlaskForm):
    # De logische naam is óók de titel op de PDF (HOOFDLETTERS bovenaan).
    naam = StringField('Titel', validators=NAAM_VALIDATORS)
    kerntaak = SelectField('Kerntaak', choices=KERNTAAK_KEUZES, validators=KERNTAAK_VALIDATORS)
    subcategorie_id = SelectField('Subcategorie', choices=[('', '— kies eerst een kerntaak —')],
                                    validators=[Optional()], validate_choice=False)
    header_foto = FileField('Headerfoto')
    instructie_type = RadioField('Type instructiekaart',
                                  choices=INSTRUCTIE_TYPE_KEUZES,
                                  default='procedure',
                                  validators=[DataRequired(message='Kies of dit een materiaal- of procedurekaart is.')])
    omschrijving = TextAreaField('Omschrijving',
                                  validators=[Optional(), Length(max=2000, message='Maximaal 2000 tekens.')])
    # Markers op de productfoto worden als JSON-string in een hidden veld meegestuurd.
    # Lijst van {nummer, x, y, label} — x/y zijn fracties (0..1) van foto-breedte/hoogte.
    productfoto_markers_json = StringField('Marker-legenda', validators=[Optional()])
    # Veiligheid — 5 korte zinnen (max 100 tekens elk). LMRA wordt centraal beheerd, niet per kaart.
    veiligheid_zin_1 = StringField('Veiligheidspunt 1', validators=[Optional(), Length(max=VEILIGHEID_ZIN_MAX)])
    veiligheid_zin_2 = StringField('Veiligheidspunt 2', validators=[Optional(), Length(max=VEILIGHEID_ZIN_MAX)])
    veiligheid_zin_3 = StringField('Veiligheidspunt 3', validators=[Optional(), Length(max=VEILIGHEID_ZIN_MAX)])
    veiligheid_zin_4 = StringField('Veiligheidspunt 4', validators=[Optional(), Length(max=VEILIGHEID_ZIN_MAX)])
    veiligheid_zin_5 = StringField('Veiligheidspunt 5', validators=[Optional(), Length(max=VEILIGHEID_ZIN_MAX)])
    # Werkwijze (fase 4) — platte lijst van stappen.
    # JSON-structuur: [{"id": "<uuid>", "layout": "A|B|C|D",
    #                   "titel": "<kort>", "tekst": "<uitleg>",
    #                   "fotos": [{"slot": "<uuid>", "bestand": "xxx.jpg"}, ...]}]
    werkwijze_stappen_json = StringField('Werkwijze', validators=[Optional()])
    submit = SubmitField('Opslaan als concept')


class ScenariokaartForm(FlaskForm):
    naam = StringField('Logische naam', validators=NAAM_VALIDATORS)
    kerntaak = SelectField('Kerntaak', choices=KERNTAAK_KEUZES, validators=KERNTAAK_VALIDATORS)
    subcategorie_id = SelectField('Subcategorie', choices=[('', '— kies eerst een kerntaak —')],
                                    validators=[Optional()], validate_choice=False)
    header_foto = FileField('Headerfoto')
    # Gestructureerde velden: Doelgroep, Oefenstaf, Tijdsduur
    doelgroep = SelectField('Doelgroep', choices=DOELGROEP_KEUZES, validators=[Optional()])
    doelgroep_anders = StringField('Eigen doelgroep', validators=[Optional(), Length(max=40)])
    oefenleider_aantal = IntegerField('Aantal oefenleiders', validators=[Optional(), NumberRange(min=0, max=20)])
    oefenleider_rol = SelectField('Rol / specialisme', choices=OEFENLEIDER_ROL_KEUZES, validators=[Optional()])
    oefenleider_rol_anders = StringField('Eigen rol', validators=[Optional(), Length(max=60)])
    ensceneerder_aantal = IntegerField('Ensceneerder', validators=[Optional(), NumberRange(min=0, max=20)])
    waarnemer_aantal = IntegerField('Waarnemer', validators=[Optional(), NumberRange(min=0, max=20)])
    overig_functie = StringField('Overige functie', validators=[Optional(), Length(max=40)])
    overig_aantal = IntegerField('Aantal', validators=[Optional(), NumberRange(min=0, max=20)])
    tijdsduur = SelectField('Tijdsduur', choices=TIJDSDUUR_KEUZES, validators=[Optional()])
    # Oefenmiddelen gesplitst (zelfde patroon als opdrachtkaart):
    # basis (op eigen post) + extra (bestellen bij oefencoördinator). Beide opgeslagen als JSON.
    oefenmiddelen_basis = StringField('Basis (eigen post)', validators=[Optional(), Length(max=2000)])
    oefenmiddelen_extra = StringField('Extra bestellen', validators=[Optional(), Length(max=2000)])
    aanleiding_doelen = TextAreaField('Oefendoel', validators=[Optional(), Length(max=500)])
    # Pagerbericht (C35) — prio + soort + (optioneel) voertuigen
    pager_prio = SelectField('Prioriteit', choices=PAGER_PRIO_KEUZES, validators=[Optional()])
    pager_soort = SelectField('Soort melding', choices=PAGER_SOORT_KEUZES, validators=[Optional()])
    pager_soort_anders = StringField('Eigen soort melding', validators=[Optional(), Length(max=24)])
    pager_voertuigen = StringField('Voertuigen / eenheden', validators=[Optional(), Length(max=24)])
    scenariobeschrijving = TextAreaField('Scenariobeschrijving', validators=[Optional(), Length(max=800)])
    # Kenmerkenschema (5 velden, max 180 tekens = ~2 regels). Label van kenmerken_kerntaak is dynamisch.
    kenmerken_kerntaak = TextAreaField('Kerntaak-kenmerken', validators=[Optional(), Length(max=180)])
    gebouwkenmerken = TextAreaField('Gebouwkenmerken', validators=[Optional(), Length(max=180)])
    menskenmerken = TextAreaField('Menskenmerken', validators=[Optional(), Length(max=180)])
    omgevingskenmerken = TextAreaField('Omgevingskenmerken', validators=[Optional(), Length(max=180)])
    interventiekenmerken = TextAreaField('Interventiekenmerken', validators=[Optional(), Length(max=180)])
    # Ensceneringstips — JSON-lijst: [{"id": "<uuid>", "tekst": "...",
    #                                  "fotos": [{"slot": "<uuid>", "bestand": "xxx.jpg",
    #                                             "bestand_origineel": "yyy.jpg"}]}].
    # Max 3 tips, elk met max 2 foto's (zelfde patroon als opdrachten_json).
    ensceneringstips = StringField('Ensceneringstips', validators=[Optional()])
    evaluatie = TextAreaField('Evaluatie', validators=[Optional()])
    # Eigen verwijzingen (URL + label, max 5)
    verwijzing_url_1 = StringField('URL 1', validators=[Optional(), Length(max=200)])
    verwijzing_label_1 = StringField('Toelichting 1', validators=[Optional(), Length(max=40)])
    verwijzing_url_2 = StringField('URL 2', validators=[Optional(), Length(max=200)])
    verwijzing_label_2 = StringField('Toelichting 2', validators=[Optional(), Length(max=40)])
    verwijzing_url_3 = StringField('URL 3', validators=[Optional(), Length(max=200)])
    verwijzing_label_3 = StringField('Toelichting 3', validators=[Optional(), Length(max=40)])
    verwijzing_url_4 = StringField('URL 4', validators=[Optional(), Length(max=200)])
    verwijzing_label_4 = StringField('Toelichting 4', validators=[Optional(), Length(max=40)])
    verwijzing_url_5 = StringField('URL 5', validators=[Optional(), Length(max=200)])
    verwijzing_label_5 = StringField('Toelichting 5', validators=[Optional(), Length(max=40)])
    submit = SubmitField('Opslaan als concept')

# Velden die gegroepeerd worden in het "Doelgroep, Oefenstaf en tijdsduur" blok
SCENARIO_GROEP_VELDEN = ['doelgroep', 'doelgroep_anders', 'oefenleider_aantal', 'ensceneerder_aantal',
                          'waarnemer_aantal', 'overig_functie', 'overig_aantal', 'tijdsduur',
                          'oefenmiddelen_basis', 'oefenmiddelen_extra']


class OpdrachtkaartForm(FlaskForm):
    naam = StringField('Logische naam', validators=NAAM_VALIDATORS)
    kerntaak = SelectField('Kerntaak', choices=KERNTAAK_KEUZES, validators=KERNTAAK_VALIDATORS)
    subcategorie_id = SelectField('Subcategorie', choices=[('', '— kies eerst een kerntaak —')],
                                    validators=[Optional()], validate_choice=False)
    header_foto = FileField('Headerfoto')
    # Voorbereiding
    doelgroep = SelectField('Doelgroep', choices=DOELGROEP_KEUZES, validators=[Optional()])
    doelgroep_anders = StringField('Eigen doelgroep', validators=[Optional(), Length(max=40)])
    # Oefenmiddelen gesplitst: (1) basis-set op eigen post, (2) extra te bestellen bij coördinator.
    # Beide opgeslagen als JSON-string in een verborgen input; JS-editor rendert de rijen.
    oefenmiddelen_basis = StringField('Basis (eigen post)', validators=[Optional(), Length(max=2000)])
    oefenmiddelen_extra = StringField('Extra bestellen', validators=[Optional(), Length(max=2000)])
    # Veiligheid: LMRA centraal + 5 korte zinnen (net als instructiekaart).
    veiligheid_zin_1 = StringField('Veiligheidspunt 1', validators=[Optional(), Length(max=VEILIGHEID_ZIN_MAX)])
    veiligheid_zin_2 = StringField('Veiligheidspunt 2', validators=[Optional(), Length(max=VEILIGHEID_ZIN_MAX)])
    veiligheid_zin_3 = StringField('Veiligheidspunt 3', validators=[Optional(), Length(max=VEILIGHEID_ZIN_MAX)])
    veiligheid_zin_4 = StringField('Veiligheidspunt 4', validators=[Optional(), Length(max=VEILIGHEID_ZIN_MAX)])
    veiligheid_zin_5 = StringField('Veiligheidspunt 5', validators=[Optional(), Length(max=VEILIGHEID_ZIN_MAX)])
    # Eén SMART-oefendoel — kort en bondig (max 500 tekens).
    oefendoel = TextAreaField('Oefendoel', validators=[Optional(), Length(max=500)])
    # Opdrachten — JSON-lijst: [{"id": "<uuid>", "titel": "...", "tekst": "...",
    #                            "fotos": [{"slot": "<uuid>", "bestand": "xxx.jpg",
    #                                       "bestand_origineel": "yyy.jpg"}]}].
    # Max 2 foto's per opdracht (net als bij werkwijze-stappen op de instructiekaart).
    opdrachten_json = StringField('Opdrachten', validators=[Optional()])
    # Onderstaande drie zijn dynamische lijsten (regel-per-punt) — mogen langere teksten bevatten.
    uitdagende_variant = TextAreaField('Uitdagende variant', validators=[Optional(), Length(max=3000)])
    verdiepende_vragen = TextAreaField('Verdiepende vragen', validators=[Optional(), Length(max=3000)])
    evaluatie = TextAreaField('Evaluatie', validators=[Optional(), Length(max=3000)])
    submit = SubmitField('Opslaan als concept')


class KenniskaartForm(FlaskForm):
    """Kenniskaart — achtergrondkennis/theorie voor manschappen.

    Structuur volgt gedeeltelijk de opdrachtkaart (doelgroep + leerdoel +
    stap-voor-stap kernboodschap), plus aandachtspunten voor de oefenleider en
    dezelfde verdieping+evaluatie-lijsten. Stap 1 (v0.7.9) heeft de basis-
    velden; kernboodschap-stappen-editor komt in stap 2.
    """
    naam = StringField('Logische naam', validators=NAAM_VALIDATORS)
    kerntaak = SelectField('Kerntaak', choices=KERNTAAK_KEUZES, validators=KERNTAAK_VALIDATORS)
    subcategorie_id = SelectField('Subcategorie', choices=[('', '— kies eerst een kerntaak —')],
                                    validators=[Optional()], validate_choice=False)
    header_foto = FileField('Headerfoto')
    doelgroep = SelectField('Doelgroep', choices=DOELGROEP_KEUZES, validators=[Optional()])
    doelgroep_anders = StringField('Eigen doelgroep', validators=[Optional(), Length(max=40)])
    leerdoel = TextAreaField('Leerdoel', validators=[Optional(), Length(max=500)])
    # Kernboodschap in stappen — zelfde JSON-structuur als werkwijze op instructie:
    #   [{"id": "<uuid>", "layout": "A|B|C|D", "titel": "...", "tekst": "...",
    #     "fotos": [{"slot": "<uuid>", "bestand": "xxx.jpg"}, ...]}]
    # De editor daarvoor komt in stap 2 (v0.7.10). Voor nu een lege hidden.
    kernboodschap_stappen_json = StringField('Kernboodschap', validators=[Optional()])
    # Verdiepende vragen + evaluatie — beide dynamische lijsten (punten), net als
    # bij de opdrachtkaart. Voor stap 1 zijn ze tekstvakken; stap 2 maakt er
    # echte lijst-editors van.
    verdiepende_vragen = TextAreaField('Verdiepende vragen', validators=[Optional(), Length(max=3000)])
    evaluatie = TextAreaField('Evaluatie', validators=[Optional(), Length(max=3000)])
    submit = SubmitField('Opslaan als concept')


FORMULIEREN = {
    'thema': ThemakaartForm,
    'instructie': InstructiekaartForm,
    'scenario': ScenariokaartForm,
    'opdracht': OpdrachtkaartForm,
    'kennis': KenniskaartForm,
}

# Velden per type die opgeslagen worden als inhoud (exclusief naam en afbeeldingen)
INHOUD_VELDEN = {
    'thema': ['titel', 'ondertitel',
              'tussentitel_1', 'tussentitel_2', 'tussentitel_3'],
    'instructie': ['instructie_type', 'omschrijving', 'productfoto_markers_json',
                   'veiligheid_zin_1', 'veiligheid_zin_2', 'veiligheid_zin_3',
                   'veiligheid_zin_4', 'veiligheid_zin_5',
                   'werkwijze_stappen_json'],
    'scenario': ['doelgroep', 'doelgroep_anders', 'oefenleider_aantal', 'oefenleider_rol',
                 'oefenleider_rol_anders',
                 'ensceneerder_aantal',
                 'waarnemer_aantal', 'overig_functie', 'overig_aantal',
                 'tijdsduur', 'oefenmiddelen_basis', 'oefenmiddelen_extra', 'aanleiding_doelen',
                 'pager_prio', 'pager_soort', 'pager_soort_anders', 'pager_voertuigen',
                 'scenariobeschrijving',
                 'ensceneringstips',
                 'kenmerken_kerntaak', 'gebouwkenmerken', 'menskenmerken',
                 'omgevingskenmerken', 'interventiekenmerken',
                 'evaluatie',
                 'verwijzing_url_1', 'verwijzing_label_1',
                 'verwijzing_url_2', 'verwijzing_label_2',
                 'verwijzing_url_3', 'verwijzing_label_3',
                 'verwijzing_url_4', 'verwijzing_label_4',
                 'verwijzing_url_5', 'verwijzing_label_5'],
    'opdracht': ['doelgroep', 'doelgroep_anders',
                 'oefenmiddelen_basis', 'oefenmiddelen_extra',
                 'veiligheid_zin_1', 'veiligheid_zin_2', 'veiligheid_zin_3',
                 'veiligheid_zin_4', 'veiligheid_zin_5',
                 'oefendoel',
                 'opdrachten_json', 'uitdagende_variant',
                 'verdiepende_vragen', 'evaluatie'],
    'kennis':   ['doelgroep', 'doelgroep_anders',
                 'leerdoel',
                 'kernboodschap_stappen_json',
                 'verdiepende_vragen', 'evaluatie'],
}

# Velden die als lijst in de JSON-inhoud staan (i.p.v. enkele string).
# Vereisen request.form.getlist() bij opslaan en lijst-prefill bij bewerken.
INHOUD_LIJST_VELDEN = {}
