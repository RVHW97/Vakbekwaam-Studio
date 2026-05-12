from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, MultipleFileField, FileField, SubmitField, SelectField, IntegerField
from wtforms.validators import DataRequired, Optional, Length, NumberRange

NAAM_MAX = 40
NAAM_VALIDATORS = [
    DataRequired(message='Vul een naam in.'),
    Length(max=NAAM_MAX, message=f'De titel mag maximaal {NAAM_MAX} tekens lang zijn (anders past hij niet in de header van de PDF).'),
]

KERNTAAK_KEUZES = [
    ('', '— kies een kerntaak —'),
    ('algemeen', 'Algemeen'),
    ('brand', 'Brand'),
    ('thv',   'Hulpverlening (THV)'),
    ('ibgs',  'IBGS'),
    ('water', 'Waterongevallen'),
]
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


class InstructiekaartForm(FlaskForm):
    naam = StringField('Logische naam', validators=NAAM_VALIDATORS)
    kerntaak = SelectField('Kerntaak', choices=KERNTAAK_KEUZES, validators=KERNTAAK_VALIDATORS)
    header_foto = FileField('Headerfoto')
    toepassing = TextAreaField('Toepassing', validators=[Optional()])
    onderdelen = TextAreaField('Onderdelen', validators=[Optional()])
    veiligheid = TextAreaField('Veiligheid', validators=[Optional()])
    werkwijze = TextAreaField('Werkwijze (stappen)', validators=[Optional()])
    achtergrondinformatie = TextAreaField('Achtergrondinformatie', validators=[Optional()])
    verbeterpunten = TextAreaField('Verbeterpunten / tips', validators=[Optional()])
    onderhoud = TextAreaField('Onderhoud', validators=[Optional()])
    afbeeldingen = MultipleFileField('Afbeeldingen')
    submit = SubmitField('Opslaan als concept')


class ScenariokaartForm(FlaskForm):
    naam = StringField('Logische naam', validators=NAAM_VALIDATORS)
    kerntaak = SelectField('Kerntaak', choices=KERNTAAK_KEUZES, validators=KERNTAAK_VALIDATORS)
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
    # Overige secties
    oefenmiddelen = TextAreaField('Oefenmiddelen', validators=[Optional(), Length(max=500)])
    aanleiding_doelen = TextAreaField('Oefendoel', validators=[Optional(), Length(max=2000)])
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
    ensceneringstips = TextAreaField('Ensceneringstips', validators=[Optional()])
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
                          'oefenmiddelen']


class OpdrachtkaartForm(FlaskForm):
    naam = StringField('Logische naam', validators=NAAM_VALIDATORS)
    kerntaak = SelectField('Kerntaak', choices=KERNTAAK_KEUZES, validators=KERNTAAK_VALIDATORS)
    header_foto = FileField('Headerfoto')
    randvoorwaarden = TextAreaField('Randvoorwaarden', validators=[Optional()])
    doelen = TextAreaField('Doelen', validators=[Optional()])
    opdrachten = TextAreaField('Opdrachten', validators=[Optional()])
    uitdagende_variant = TextAreaField('Uitdagende variant', validators=[Optional()])
    veiligheid = TextAreaField('Veiligheid', validators=[Optional()])
    verdiepende_vragen = TextAreaField('Verdiepende vragen', validators=[Optional()])
    voorbereiding = TextAreaField('Voorbereiding & benodigdheden', validators=[Optional()])
    evaluatie = TextAreaField('Evaluatie', validators=[Optional()])
    achtergrondinformatie = TextAreaField('Achtergrondinformatie', validators=[Optional()])
    verbeterpunten = TextAreaField('Verbeterpunten / tips', validators=[Optional()])
    afbeeldingen = MultipleFileField('Afbeeldingen')
    submit = SubmitField('Opslaan als concept')


FORMULIEREN = {
    'thema': ThemakaartForm,
    'instructie': InstructiekaartForm,
    'scenario': ScenariokaartForm,
    'opdracht': OpdrachtkaartForm,
}

# Velden per type die opgeslagen worden als inhoud (exclusief naam en afbeeldingen)
INHOUD_VELDEN = {
    'thema': ['titel', 'ondertitel',
              'tussentitel_1', 'tussentitel_2', 'tussentitel_3'],
    'instructie': ['toepassing', 'onderdelen', 'veiligheid', 'werkwijze',
                   'achtergrondinformatie', 'verbeterpunten', 'onderhoud'],
    'scenario': ['doelgroep', 'doelgroep_anders', 'oefenleider_aantal', 'oefenleider_rol',
                 'oefenleider_rol_anders',
                 'ensceneerder_aantal',
                 'waarnemer_aantal', 'overig_functie', 'overig_aantal',
                 'tijdsduur', 'oefenmiddelen', 'aanleiding_doelen',
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
    'opdracht': ['randvoorwaarden', 'doelen', 'opdrachten', 'uitdagende_variant',
                 'veiligheid', 'verdiepende_vragen', 'voorbereiding', 'evaluatie',
                 'achtergrondinformatie', 'verbeterpunten'],
}
