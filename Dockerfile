FROM python:3.12-slim

# WeasyPrint heeft Cairo, Pango en GDK-Pixbuf nodig voor PDF-rendering.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn==23.0.0

COPY . .

RUN mkdir -p /app/instance /app/app/static/uploads

ENV PYTHONUNBUFFERED=1 \
    FLASK_ENV=production

EXPOSE 5050

CMD ["gunicorn", "--bind", "0.0.0.0:5050", "--workers", "2", "--threads", "4", "--timeout", "120", "--preload", "--access-logfile", "-", "--error-logfile", "-", "run:app"]
