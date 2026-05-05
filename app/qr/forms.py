from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import StringField, SelectField, RadioField, BooleanField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, URL, Optional, AnyOf
from app.models import QR_CATEGORIE_KEUZES, QR_STIJL_KEUZES, QR_STIJLEN


TEKST_ONDER_MAX = 30


class QRForm(FlaskForm):
    naam = StringField(
        'Naam (intern)',
        validators=[DataRequired(), Length(min=2, max=80)],
    )
    omschrijving = TextAreaField(
        'Omschrijving (optioneel)',
        validators=[Optional(), Length(max=300)],
    )
    categorie = SelectField('Categorie', choices=QR_CATEGORIE_KEUZES, default='oefening')
    categorie_anders = StringField(
        'Eigen categorie',
        validators=[Optional(), Length(max=40)],
    )

    doel_url = StringField(
        'Doel-URL (laat leeg als je een PDF uploadt)',
        validators=[Optional(), Length(max=500), URL(message='Geen geldige URL (inclusief https://)')],
    )
    pdf_bestand = FileField(
        'PDF uploaden (optioneel, max 10 MB)',
        validators=[Optional(), FileAllowed(['pdf'], 'Alleen PDF toegestaan')],
    )
    pdf_verwijderen = BooleanField('Huidige PDF verwijderen')

    tekst_onder = StringField(
        f'Tekst onder QR (optioneel, max {TEKST_ONDER_MAX} tekens)',
        validators=[Optional(), Length(max=TEKST_ONDER_MAX)],
    )

    stijl = RadioField(
        'Opmaak',
        choices=QR_STIJL_KEUZES,
        default='navy',
        validators=[AnyOf(list(QR_STIJLEN.keys()))],
    )

    submit = SubmitField('Opslaan')
