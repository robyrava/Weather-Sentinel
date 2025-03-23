FROM python:3.9-slim

WORKDIR /app

# Installazione dipendenze
COPY SED/requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copia dei file comuni
COPY utility.py db.py /app/

# Copia del codice sorgente SED
COPY SED/ /app/SED/

# Definizione variabili d'ambiente
ENV PYTHONUNBUFFERED=1

# Esposizione della porta per SED
EXPOSE 50055

# Comando di avvio
WORKDIR /app/SED
CMD ["python", "main.py"]