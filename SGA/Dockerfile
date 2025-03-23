FROM python:3.9-slim

WORKDIR /app

# Copia i file a livello di progetto
COPY utility.py db.py /app/
COPY SGA/requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copia il codice sorgente di SGA
COPY SGA/ /app/SGA/

# Definizione variabili d'ambiente
ENV PYTHONUNBUFFERED=1


# Esposizione della porta per SGA
EXPOSE 50053

# Comando di avvio
WORKDIR /app/SGA
CMD ["python", "main.py"]