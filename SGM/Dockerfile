FROM python:3.9-slim

WORKDIR /app

# Installazione dipendenze
COPY SGM/requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copia dei file comuni
COPY utility.py db.py /app/

# Copia del codice sorgente SGM
COPY SGM/ /app/SGM/

# Definizione variabili d'ambiente
ENV PYTHONUNBUFFERED=1

# Esposizione della porta per SGM
EXPOSE 50054

# Comando di avvio
WORKDIR /app/SGM
CMD ["python", "main.py"]