from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SelectField, BooleanField, SubmitField
from wtforms.validators import DataRequired, Email, Length, EqualTo, Optional
from app.models import ROL_KEUZES, KERNGROEP_KEUZES, ROI_KERNTAAK_KEUZES

WACHTWOORD_MIN_LENGTE = 12

# Standaardkeuze bovenaan de kerngroep-dropdown — alleen oefencoord/specialist
# krijgen daadwerkelijk een kerngroep.
KERNGROEP_CHOICES = [('', '— geen kerngroep —')] + KERNGROEP_KEUZES
ROI_KERNTAAK_CHOICES = [('', '— geen ROI-kerntaak —')] + ROI_KERNTAAK_KEUZES


class LoginForm(FlaskForm):
    email = StringField('E-mailadres', validators=[DataRequired(), Email()])
    wachtwoord = PasswordField('Wachtwoord', validators=[DataRequired()])
    onthoud_mij = BooleanField('Onthoud mij')
    submit = SubmitField('Inloggen')


class NieuweGebruikerForm(FlaskForm):
    naam = StringField('Naam', validators=[DataRequired(), Length(min=2, max=100)])
    email = StringField('E-mailadres', validators=[DataRequired(), Email()])
    wachtwoord = PasswordField(
        'Wachtwoord',
        validators=[DataRequired(), Length(min=WACHTWOORD_MIN_LENGTE,
                                           message=f'Kies een wachtwoord van minimaal {WACHTWOORD_MIN_LENGTE} tekens.')]
    )
    wachtwoord_bevestig = PasswordField(
        'Wachtwoord bevestigen',
        validators=[DataRequired(), EqualTo('wachtwoord', message='Wachtwoorden komen niet overeen')]
    )
    rol = SelectField('Rol', choices=ROL_KEUZES, default='medewerker_oo')
    kerngroep = SelectField('Kerngroep', choices=KERNGROEP_CHOICES, default='',
                             validators=[Optional()])
    roi_kerntaak = SelectField('ROI-kerntaak', choices=ROI_KERNTAAK_CHOICES,
                                default='', validators=[Optional()])
    submit = SubmitField('Gebruiker aanmaken')


class GebruikerBewerkenForm(FlaskForm):
    naam = StringField('Naam', validators=[DataRequired(), Length(min=2, max=100)])
    email = StringField('E-mailadres', validators=[DataRequired(), Email()])
    rol = SelectField('Rol', choices=ROL_KEUZES)
    kerngroep = SelectField('Kerngroep', choices=KERNGROEP_CHOICES,
                             validators=[Optional()])
    roi_kerntaak = SelectField('ROI-kerntaak', choices=ROI_KERNTAAK_CHOICES,
                                validators=[Optional()])
    actief = BooleanField('Actief')
    nieuw_wachtwoord = PasswordField(
        'Nieuw wachtwoord (laat leeg om niet te wijzigen)',
        validators=[Optional(), Length(min=WACHTWOORD_MIN_LENGTE,
                                       message=f'Kies een wachtwoord van minimaal {WACHTWOORD_MIN_LENGTE} tekens.')]
    )
    nieuw_wachtwoord_bevestig = PasswordField(
        'Nieuw wachtwoord bevestigen',
        validators=[EqualTo('nieuw_wachtwoord', message='Wachtwoorden komen niet overeen')]
    )
    submit = SubmitField('Opslaan')
