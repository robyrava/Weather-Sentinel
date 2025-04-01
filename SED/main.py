import sys
import os
# percorso della directory contenente config.py e db.py al sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from db import*
from utility import*

import time
import confluent_kafka
from confluent_kafka.admin import AdminClient, NewTopic
import json
import mysql.connector
import os
import sys
import requests
import socket
import threading
from flask import Flask
from prometheus_client import Counter, generate_latest, REGISTRY, Gauge, Histogram
from flask import Response
import logging
from notificatore import ThreadNotificatore

# CONFIGURAZIONE VARIABILI D'AMBIENTE

DB_HOSTNAME = os.environ.get('HOSTNAME')
DB_PORT = os.environ.get('PORT')
DB_USER = os.environ.get('USER')
DB_PASSWORD = os.environ.get('PASSWORD_DB')
DATABASE_SED = os.environ.get('DATABASE_SED')
SED_HOST = os.environ.get('SED_HOST')
HOST = os.environ.get('HOST')

PORTA_SED = os.environ.get('PORTA_SED')
KAFKA_BROKER = os.environ.get('KAFKA_BROKER')
KAFKA_TOPIC = os.environ.get('KAFKA_TOPIC')
GROUP_ID = os.environ.get('GROUP_ID')
TIMER_POLLING = 60

INTERVALLO_NOTIFICA = 30 #Thread di notifica


APIKEY = os.environ.get('APIKEY')

# definizione delle metriche da esporre
ERRORE_RICHIESTA_API = Counter('SED_errore_richiesta_OpenWeather', 'Numero totale di richieste inviate a OpenWeather che sono fallite')
RICHIESTE_API = Counter('SED_richieste_a_OpenWeather', 'Numero totale di chiamate API a OpenWeather')
TEMPO_DI_RISPOSTA = Gauge('SED_tempo_risposta_OpenWeather', 'Differenza tra l istante in cui il sed invia la richiesta a OpenWeather e l istante in cui riceve la risposta')
ISTOGRAMMA_DURATA_QUERY = Histogram('SED_durata_query_nanosecondi_DB', 'Durata delle query al database in nanosecondi', buckets=[5000000, 10000000, 25000000, 50000000, 75000000, 100000000, 250000000, 500000000, 750000000, 1000000000, 2500000000,5000000000,7500000000,10000000000])
# bucket indicati per misurare il tempo in nanosecondi

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Funzione per avviare il thread di notifica
def avvia_thread_notificatore(intervallo=60):  # Default: controlla ogni minuto
    """
    Avvia il thread di notifica.
    
    Args:
        intervallo: Intervallo in secondi tra i controlli
    
    Returns:
        ThreadNotificatore: L'istanza del thread avviato
    """
    thread = ThreadNotificatore(intervallo,ID_MONITORAGGIO)
    thread.start()
    return thread

# Funzione per fermare il thread di notifica
def ferma_thread_notificatore():
    """
    Ferma il thread di notifica cambiando il flag globale.
    """
    global thread_notificatore_attivo
    thread_notificatore_attivo = False
    logger.info("\nRichiesta di terminazione del thread di notifica\n")

def crea_server():
    app = Flask(__name__)
    
    @app.route('/metriche', methods=['GET'])
    def metriche():
        # Esporta tutte le metriche come testo per Prometheus
        return Response(generate_latest(REGISTRY), mimetype='text/plain')
    
    return app

def avvia_server():
    hostname = socket.gethostname()
    logger.info(f'\nHostname: {hostname} -> server starting on port {str(PORTA_SED)}\n')
    app.run(HOST, port=PORTA_SED, threaded=True)

def commit_completed(err, partitions):
    if err:
        logger.error(f"\nErrore durante il commit: {err}\n")
    else:
        
        logger.info("\nCommit completato con successo\n")

def avvia_monitoraggio():    
    """
    Recupera tutti i monitoraggi attivi non ancora controllati dal database, 
    li elabora e imposta il flag 'controllato' a true.
    
    Returns:
        str: JSON contenente gli eventi da notificare
        str: '{}' se non ci sono eventi da notificare
    """
    tutti_eventi = {"eventi": []}
    id_regole_da_aggiornare = []

    try:
        connessione = inizializza_connessione_db(
            host=DB_HOSTNAME, 
            porta=DB_PORT, 
            utente=DB_USER, 
            password=DB_PASSWORD, 
            database=DATABASE_SED
        )
        
        if not connessione:
            logger.error("\nImpossibile connettersi al database per recuperare i monitoraggi attivi\n")
            return '{}'
        
        try:
            # Recupera tutti i monitoraggi attivi non ancora controllati
            cursore = esegui_query(
                connessione=connessione,
                query="SELECT id, regole FROM eventi_da_monitorare WHERE id_monitoraggio = %s AND controllato = false",
                parametri=(ID_MONITORAGGIO,),
                istogramma=ISTOGRAMMA_DURATA_QUERY
            )
            
            if not cursore:
                logger.error("\nErrore nel recupero dei monitoraggi attivi\n")
                return '{}'

            # Recupera tutte le righe
            risultati = cursore.fetchall()
            
            # Se non ci sono risultati, restituisci un dizionario vuoto
            if not risultati or len(risultati) == 0:
                logger.info("\nNessun monitoraggio attivo non controllato trovato\n")
                return '{}'
            
            logger.info(f"\nTrovate {len(risultati)} regole da elaborare\n")
            
            # Elabora ogni regola
            for risultato in risultati:
                id_regola = risultato[0]
                regole_json = risultato[1]
                
                try:
                    # Aggiungi l'ID alla lista per l'aggiornamento
                    id_regole_da_aggiornare.append(id_regola)
                    
                    # Converti JSON in dizionario
                    regole_dict = json.loads(regole_json)
                    
                    # Estrai il nome della città
                    città = regole_dict['localita'][0]
                    
                    # Esegui la chiamata API OpenWeather
                    rest_call = f"https://api.openweathermap.org/data/2.5/weather?q={città}&units=metric&appid={APIKEY}"
                    data = esegui_query_rest(rest_call)
                    formatted_data = formatta_dati(data)
                    eventi_regola = controlla_regole(regole_dict, formatted_data)
                    
                    # Aggiungi gli eventi di questa regola al totale
                    if 'eventi' in eventi_regola and eventi_regola['eventi']:
                        tutti_eventi['eventi'].extend(eventi_regola['eventi'])
                        
                except Exception as e:
                    logger.error(f"\nErrore nell'elaborazione della regola ID {id_regola}: {e}\n")
                    # Continua con la prossima regola
            
            # Aggiorna tutte le regole elaborate
            if id_regole_da_aggiornare:
                # Crea stringa di placeholders per la query IN
                placeholders = ', '.join(['%s'] * len(id_regole_da_aggiornare))
                
                # Esegui la query di aggiornamento per tutte le regole
                risultato_aggiornamento = esegui_query(
                    connessione=connessione,
                    query=f"UPDATE eventi_da_monitorare SET controllato = true WHERE id IN ({placeholders})",
                    parametri=tuple(id_regole_da_aggiornare),
                    commit=True,
                    istogramma=ISTOGRAMMA_DURATA_QUERY
                )
                
                if risultato_aggiornamento:
                    logger.info(f"\nAggiornate {len(id_regole_da_aggiornare)} regole nel database (controllato = true)\n")
                else:
                    logger.error("\nErrore nell'aggiornamento delle regole nel database\n")
            
            return json.dumps(tutti_eventi)
            
        finally:
            chiudi_connessione_db(connessione)
            
    except Exception as err:
        logger.error(f"\nErrore nel recupero e processamento dei monitoraggi attivi: {err}\n")
        return '{}'

def esegui_query_rest(url):
        """
        Esegue una richiesta REST all'API OpenWeather e monitora le metriche relative.
        
        Args:
            url: URL completo per la richiesta REST con parametri
            
        Returns:
            dict: Risposta JSON dell'API
        """
        tempo_inizio = time.time_ns()
        try:
            # Incrementa il contatore delle richieste
            RICHIESTE_API.inc()
            
            # Esegue la richiesta HTTP
            risposta = requests.get(url=url)
            risposta.raise_for_status()
            
            # Converte la risposta in JSON
            dati_risposta = risposta.json()
            
            # Calcola il tempo di risposta
            tempo_fine = time.time_ns()
            TEMPO_DI_RISPOSTA.set(tempo_fine - tempo_inizio)
            
            # Verifica il codice di risposta
            if dati_risposta.get('cod') != 200:
                ERRORE_RICHIESTA_API.inc()
                raise Exception(f'Query fallita: {dati_risposta.get("message")}')
            
            logger.info(f"\nRisposta API ricevuta: {json.dumps(dati_risposta)}\n")
            return dati_risposta
            
        except requests.JSONDecodeError as errore:
            ERRORE_RICHIESTA_API.inc()
            tempo_fine = time.time_ns()
            TEMPO_DI_RISPOSTA.set(tempo_fine - tempo_inizio)
            logger.error(f'\nErrore nella decodifica JSON: {errore}\n')
            raise SystemExit("Terminazione dopo errore nella decodifica JSON")
            
        except requests.HTTPError as errore:
            ERRORE_RICHIESTA_API.inc()
            tempo_fine = time.time_ns()
            TEMPO_DI_RISPOSTA.set(tempo_fine - tempo_inizio)
            logger.error(f'\nErrore HTTP: {errore}\n')
            raise SystemExit("Terminazione dopo errore HTTP")
            
        except requests.exceptions.RequestException as errore:
            ERRORE_RICHIESTA_API.inc()
            tempo_fine = time.time_ns()
            TEMPO_DI_RISPOSTA.set(tempo_fine - tempo_inizio)
            logger.error(f'\nRichiesta fallita: {errore}\n')
            raise SystemExit("Terminazione dopo errore nella richiesta")
            
        except Exception as errore:
            logger.error(f'\nErrore generico: {errore}\n')
            raise SystemExit("Terminazione dopo errore generico")

def formatta_dati(dati):
    """
    Formatta i dati restituiti dall'API OpenWeather secondo la logica di business.
    
    Args:
        dati: Dizionario JSON restituito dall'API OpenWeather
        
    Returns:
        dict: Dizionario con i dati meteorologici formattati
    """
    # Dizionario per i dati formattati
    dati_formattati = {}
    
    # Estrazione dei dati principali con gestione sicura delle chiavi
    try:
        # Temperatura, umidità e pressione - usando i nomi corretti per le chiavi
        dati_formattati['temp_max'] = dati_formattati['temp_min'] = dati["main"]["temp"]
        dati_formattati['umi_max'] = dati_formattati['umi_min'] = dati["main"]["humidity"]
        dati_formattati['pressione_max'] = dati_formattati['pressione_min'] = dati["main"]["pressure"]
        
        # Vento e nuvole - usando i nomi corretti per le chiavi
        dati_formattati["vel_vento_max"] = dati_formattati["vel_vento_min"] = dati["wind"]["speed"]
        dati_formattati["nuvole_max"] = dati_formattati["nuvole_min"] = dati["clouds"]["all"]
        
        # Calcolo della direzione del vento
        direzioni = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"]
        gradi = dati["wind"]["deg"]
        indice = round(gradi / 45) % 8
        dati_formattati["direzione_vento"] = direzioni[indice]
        
        # Verifica condizioni meteo
        condizione = dati["weather"][0]["main"]
        
        # Verifica pioggia - sia dal campo weather che dall'oggetto rain se presente
        dati_formattati["pioggia"] = (condizione == "Rain" or "rain" in dati)
        
        # Verifica neve - sia dal campo weather che dall'oggetto snow se presente
        dati_formattati["neve"] = (condizione == "Snow" or "snow" in dati)
        
        # Log dei dati formattati
        logger.info(f"\nDati meteo formattati: {json.dumps(dati_formattati)}\n")
        
    except KeyError as e:
        logger.error(f"\nErrore nell'estrazione dei dati meteorologici: chiave mancante {e}\n")
        # Valori predefiniti in caso di errore
        dati_formattati = {
            'temp_max': 0, 'temp_min': 0,
            'umi_max': 0, 'umi_min': 0,
            'pressione_max': 0, 'pressione_min': 0,
            'vel_vento_max': 0, 'vel_vento_min': 0,
            'nuvole_max': 0, 'nuvole_min': 0,
            'direzione_vento': 'N',
            'pioggia': False, 'neve': False
        }
    
    return dati_formattati

def controlla_regole(vincoli_utente, dati_api):
    """
    Confronta i dati meteo attuali con i vincoli definiti dall'utente.
    Salva gli eventi corrispondenti ai vincoli violati nella tabella eventi_da_notificare.
    
    Args:
        vincoli_utente: Dizionario contenente i vincoli definiti dall'utente
        dati_api: Dizionario contenente i dati meteo formattati
        
    Returns:
        dict: Dizionario contenente gli eventi da notificare
    """    
    # Lista per memorizzare gli eventi da notificare
    eventi_da_notificare = []
    
    # Numero di righe (utenti) da controllare
    num_righe = vincoli_utente.get('num_righe', [])
    
    # Per ogni riga di vincoli (ciascuna riga corrisponde a un utente)
    for i in range(num_righe):
        # Controllo se l'indice è valido per tutti gli array nei vincoli
        if i >= len(vincoli_utente.get('id_utente', [])):
            logger.warning(f"\nIndice {i} non valido per id_utente\n")
            continue
            
        # Id dell'utente corrente
        id_utente = vincoli_utente['id_utente'][i]
        
        # Dizionario per memorizzare i vincoli violati per questo utente
        vincoli_violati = {}
        
        # Confronto temperatura massima
        if 'temp_max' in vincoli_utente and i < len(vincoli_utente['temp_max']):
            temp_max_vincolo = float(vincoli_utente['temp_max'][i])
            if dati_api['temp_max'] > temp_max_vincolo:
                vincoli_violati['temp_max'] = {
                    'valore_atteso': temp_max_vincolo,
                    'valore_effettivo': dati_api['temp_max']
                }
        
        # Confronto temperatura minima
        if 'temp_min' in vincoli_utente and i < len(vincoli_utente['temp_min']):
            temp_min_vincolo = float(vincoli_utente['temp_min'][i])
            if dati_api['temp_min'] < temp_min_vincolo:
                vincoli_violati['temp_min'] = {
                    'valore_atteso': temp_min_vincolo,
                    'valore_effettivo': dati_api['temp_min']
                }
        
        # Confronto umidità massima
        if 'umi_max' in vincoli_utente and i < len(vincoli_utente['umi_max']):
            umi_max_vincolo = float(vincoli_utente['umi_max'][i])
            if dati_api['umi_max'] > umi_max_vincolo:
                vincoli_violati['umi_max'] = {
                    'valore_atteso': umi_max_vincolo,
                    'valore_effettivo': dati_api['umi_max']
                }
        
        # Confronto umidità minima
        if 'umi_min' in vincoli_utente and i < len(vincoli_utente['umi_min']):
            umi_min_vincolo = float(vincoli_utente['umi_min'][i])
            if dati_api['umi_min'] < umi_min_vincolo:
                vincoli_violati['umi_min'] = {
                    'valore_atteso': umi_min_vincolo,
                    'valore_effettivo': dati_api['umi_min']
                }
        
        # Confronto pressione massima
        if 'pressione_max' in vincoli_utente and i < len(vincoli_utente['pressione_max']):
            pressione_max_vincolo = float(vincoli_utente['pressione_max'][i])
            if dati_api['pressione_max'] > pressione_max_vincolo:
                vincoli_violati['pressione_max'] = {
                    'valore_atteso': pressione_max_vincolo,
                    'valore_effettivo': dati_api['pressione_max']
                }
        
        # Confronto pressione minima
        if 'pressione_min' in vincoli_utente and i < len(vincoli_utente['pressione_min']):
            pressione_min_vincolo = float(vincoli_utente['pressione_min'][i])
            if dati_api['pressione_min'] < pressione_min_vincolo:
                vincoli_violati['pressione_min'] = {
                    'valore_atteso': pressione_min_vincolo,
                    'valore_effettivo': dati_api['pressione_min']
                }
        
        # Confronto nuvole massime
        if 'nuvole_max' in vincoli_utente and i < len(vincoli_utente['nuvole_max']):
            nuvole_max_vincolo = float(vincoli_utente['nuvole_max'][i])
            if dati_api['nuvole_max'] > nuvole_max_vincolo:
                vincoli_violati['nuvole_max'] = {
                    'valore_atteso': nuvole_max_vincolo,
                    'valore_effettivo': dati_api['nuvole_max']
                }
        
        # Confronto nuvole minime
        if 'nuvole_min' in vincoli_utente and i < len(vincoli_utente['nuvole_min']):
            nuvole_min_vincolo = float(vincoli_utente['nuvole_min'][i])
            if dati_api['nuvole_min'] < nuvole_min_vincolo:
                vincoli_violati['nuvole_min'] = {
                    'valore_atteso': nuvole_min_vincolo,
                    'valore_effettivo': dati_api['nuvole_min']
                }
        
        # Confronto velocità vento massima
        if 'vel_vento_max' in vincoli_utente and i < len(vincoli_utente['vel_vento_max']):
            vel_vento_max_vincolo = float(vincoli_utente['vel_vento_max'][i])
            if dati_api['vel_vento_max'] > vel_vento_max_vincolo:
                vincoli_violati['vel_vento_max'] = {
                    'valore_atteso': vel_vento_max_vincolo,
                    'valore_effettivo': dati_api['vel_vento_max']
                }
        
        # Confronto velocità vento minima
        if 'vel_vento_min' in vincoli_utente and i < len(vincoli_utente['vel_vento_min']):
            vel_vento_min_vincolo = float(vincoli_utente['vel_vento_min'][i])
            if dati_api['vel_vento_min'] < vel_vento_min_vincolo:
                vincoli_violati['vel_vento_min'] = {
                    'valore_atteso': vel_vento_min_vincolo,
                    'valore_effettivo': dati_api['vel_vento_min']
                }
        
        # Confronto direzione vento
        if 'direzione_vento' in vincoli_utente and i < len(vincoli_utente['direzione_vento']):
            direzione_vincolo = vincoli_utente['direzione_vento'][i]
            if dati_api['direzione_vento'] != direzione_vincolo:
                vincoli_violati['direzione_vento'] = {
                    'valore_atteso': direzione_vincolo,
                    'valore_effettivo': dati_api['direzione_vento']
                }
        
        # Se ci sono vincoli violati, crea un evento
        if vincoli_violati:
            evento = {
                'id_utente': id_utente,
                'vincoli_violati': vincoli_violati,
                'timestamp': time.time_ns(),
                'localita': vincoli_utente['localita'][0] if 'localita' in vincoli_utente else "Sconosciuta"
            }
            eventi_da_notificare.append(evento)
            
            # Salva l'evento nel database
            try:
                connessione = inizializza_connessione_db(
                    host=DB_HOSTNAME, 
                    porta=DB_PORT, 
                    utente=DB_USER, 
                    password=DB_PASSWORD, 
                    database=DATABASE_SED
                )
                
                if not connessione:
                    logger.error("\nImpossibile connettersi al database per salvare gli eventi\n")
                    continue
                
                try:
                    # Converti l'evento in JSON
                    evento_json = json.dumps(evento)
                    
                    # Inserisci nel database
                    risultato = esegui_query(
                        connessione=connessione,
                        query="INSERT INTO eventi_da_notificare (id_utente, eventi, time_stamp, id_monitoraggio) VALUES (%s, %s, CURRENT_TIMESTAMP, %s)",
                        parametri=(id_utente, evento_json, ID_MONITORAGGIO),
                        commit=True,
                        istogramma=ISTOGRAMMA_DURATA_QUERY
                    )
                    
                    if not risultato:
                        logger.error(f"\nErrore nel salvataggio dell'evento per l'utente {id_utente}\n")
                    else:
                        logger.info(f"\nEvento salvato correttamente per l'utente {id_utente}\n")
                
                finally:
                    chiudi_connessione_db(connessione)
                    
            except Exception as err:
                logger.error(f"\nErrore durante il salvataggio dell'evento: {err}\n")
            
    # Restituisci gli eventi da notificare
    return {'eventi': eventi_da_notificare}
     
# create Flask application
app = crea_server()

#crea thread monitoraggio


if __name__ == "__main__":
    # Creazione della tabella eventi_da_monitorare se non esiste
    try:
        # Inizializza la connessione al database
        connessione = inizializza_connessione_db(
            host=DB_HOSTNAME, 
            porta=DB_PORT, 
            utente=DB_USER, 
            password=DB_PASSWORD, 
            database=DATABASE_SED
        )
        
        if not connessione:
            sys.exit("Impossibile connettersi al database SED\n")
        
       
        risultato = esegui_query(
            connessione=connessione,
            crea_tabella=True,
            nome_tabella="eventi_da_monitorare",
            definizione_colonne="id INTEGER PRIMARY KEY AUTO_INCREMENT, regole JSON NOT NULL, time_stamp TIMESTAMP NOT NULL, id_monitoraggio VARCHAR(60) NOT NULL, controllato BOOLEAN DEFAULT FALSE, INDEX index_monitoraggio (id_monitoraggio)",
            istogramma=ISTOGRAMMA_DURATA_QUERY
        )
        
        if not risultato:
            sys.exit("SED terminato: impossibile creare la tabella eventi_da_monitorare\n")
            
        logger.info("\nTabella eventi_da_monitorare creata o già esistente\n")

        #CREAZIONE TAB eventi da notificare
        risultato = esegui_query(
            connessione=connessione,
            crea_tabella=True,
            nome_tabella="eventi_da_notificare",
            definizione_colonne="id INTEGER PRIMARY KEY AUTO_INCREMENT, id_utente INTEGER NOT NULL , eventi JSON NOT NULL, time_stamp TIMESTAMP NOT NULL, id_monitoraggio VARCHAR(60) NOT NULL, notificato BOOLEAN DEFAULT FALSE, INDEX index_monitoraggio (id_monitoraggio)",
            istogramma=ISTOGRAMMA_DURATA_QUERY
        )

        if not risultato:
            sys.exit("SED terminato: impossibile creare la tabella eventi_da_notificare\n")
        
        logger.info("\nTabella eventi_da_notificare creata o già esistente\n")

        
    except Exception as err:
        logger.error(f"\nException raised! -> {err}\n")
        try:
            if 'connessione' in locals() and connessione:
                chiudi_connessione_db(connessione)
        except Exception as exe:
            logger.error(f"\nException raised in closing connection: {exe}\n")
        sys.exit("Exiting after database error...\n")
    finally:
        if 'connessione' in locals() and connessione:
            chiudi_connessione_db(connessione)
    
    # Genera un ID monitoraggio unico
    ID_MONITORAGGIO = f"{socket.gethostname()}-{time.time()}" # permette di avere più SED che operano in parallelo senza interferire tra loro.

    logger.info(f"\nID monitoraggio generato: {ID_MONITORAGGIO}\n")
    
    # Avvio del server per le metriche Prometheus
    threadMetricsServer = threading.Thread(target=avvia_server)
    threadMetricsServer.daemon = True
    threadMetricsServer.start()
    
    # Avvio della sottoscrizione Kafka
    consumer_conf = {
        'bootstrap.servers': KAFKA_BROKER,
        'group.id': GROUP_ID,
        'enable.auto.commit': 'false',
        'auto.offset.reset': 'latest',
        'on_commit': commit_completed
    }
    
    consumer_kafka = confluent_kafka.Consumer(consumer_conf)
    try:
        consumer_kafka.subscribe([KAFKA_TOPIC])  # Sottoscrizione al topic di ingresso
        logger.info(f"\nSottoscrizione al topic {KAFKA_TOPIC} effettuata con successo\n")
    except confluent_kafka.KafkaException as ke:
        logger.error(f"\nErrore Kafka durante la sottoscrizione: {ke}\n")
        consumer_kafka.close()
        sys.exit("Terminazione dopo errore nella sottoscrizione al topic Kafka\n")

    try:
        logger.info("\nInizio polling dei messaggi Kafka\n")
        while True:
            # Polling dei messaggi nel topic Kafka
            msg = consumer_kafka.poll(timeout=TIMER_POLLING)
            
            if msg is None:
                # Nessun messaggio disponibile entro il timeout
                continue
            elif msg.error():
                logger.error(f'\nErrore Kafka: {msg.error()}\n')
                if msg.error().code() == confluent_kafka.KafkaError.UNKNOWN_TOPIC_OR_PART:
                    raise SystemExit("Topic sconosciuto, terminazione")
            else:
                # Processo dei messaggi Kafka
                record_value = msg.value()
                logger.info(f"\nMessaggio ricevuto: {record_value}\n")
                
                try:
                    
                    #MEMORIZZO MSG KAFKA NEL DB
                    
                    # Conversione del dizionario in stringa JSON
                    dati = json.loads(record_value)
                    dati_json = json.dumps(dati)
        
                    # Connessione al database
                    connessione = inizializza_connessione_db(
                        host=DB_HOSTNAME, 
                        porta=DB_PORT, 
                        utente=DB_USER, 
                        password=DB_PASSWORD, 
                        database=DATABASE_SED
                    )
                    
                    if not connessione:
                        logger.error("\nImpossibile connettersi al database durante l'elaborazione del messaggio\n")
                        continue
                    
                    try:
                        # Inserisci nel database
                        risultato = esegui_query(
                            connessione=connessione,
                            query="INSERT INTO eventi_da_monitorare (regole, time_stamp, id_monitoraggio) VALUES (%s, CURRENT_TIMESTAMP, %s)",
                            parametri=(dati_json, ID_MONITORAGGIO),
                            commit=True,
                            istogramma=ISTOGRAMMA_DURATA_QUERY
                        )
                        
                        if not risultato:
                            logger.error(f"\nErrore nella memorizzazione del msg kafka ricevuto\n")
                        else:
                            logger.info(f"\nMsg Kafka memorizzato correttamente nel DB!\n")


                    finally:
                        chiudi_connessione_db(connessione)
                    
                    # Commit dell'offset Kafka
                    consumer_kafka.commit(asynchronous=True)
                
                    # Avvia il monitoraggio delle regole
                    eventi_da_inviare = avvia_monitoraggio()                            
          
                except json.JSONDecodeError:
                    logger.error(f"\nErrore nella decodifica del messaggio JSON: {record_value}\n")
                except Exception as e:
                    logger.error(f"\nErrore nell'elaborazione del messaggio: {e}\n")

                #Avvia thread per notifica eventi
                thread_notificatore = avvia_thread_notificatore(INTERVALLO_NOTIFICA)
                ferma_thread_notificatore() 

    except (KeyboardInterrupt, SystemExit):
        logger.info("\nInterruzione ricevuta, terminazione in corso...\n")
        
    finally:
        # ritardo per consentire al thread di terminare in modo pulito
        time.sleep(1)
        # Chiusura delle connessioni Kafka
        consumer_kafka.close()
        logger.info("\nConnessioni chiuse, terminazione completa\n")
        



