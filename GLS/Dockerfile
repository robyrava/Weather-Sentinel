FROM python:3.9-slim

WORKDIR /app

# Copia i file a livello di progetto
COPY utility.py db.py /app/
COPY GLS/requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copia il codice sorgente di GLS
COPY GLS/ /app/GLS/

# Definizione variabili d'ambiente
ENV PYTHONUNBUFFERED=1


# Esposizione della porta per GLS
EXPOSE 50056

# Comando di avvio
WORKDIR /app/GLS
CMD ["python", "main.py"]