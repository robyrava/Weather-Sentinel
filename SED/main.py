import sys
import os
# percorso della directory contenente config.py e db.py al sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config import*
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


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# definition of the metrics to be exposed
ERROR_REQUEST_OPEN_WEATHER = Counter('WORKER_error_request_OpenWeather', 'Total number of requests sent to OpenWeather that failed')
REQUEST_OPEN_WEATHER = Counter('WORKER_requests_to_OpenWeather', 'Total number of API calls to OpenWeather')
DELTA_TIME = Gauge('WORKER_response_time_OpenWeather', 'Difference between instant when worker sends request to OpenWeather and instant when it receives the response')
KAFKA_MESSAGE = Counter('WORKER_kafka_message_number', 'Total number of kafka messages produced by worker-service')
KAFKA_MESSAGE_DELIVERED = Counter('WORKER_kafka_message_delivered_number', 'Total number of kafka messages produced by worker-service that have been delivered correctly')
QUERY_DURATIONS_HISTOGRAM = Histogram('WORKER_query_durations_nanoseconds_DB', 'DB query durations in nanoseconds', buckets=[5000000, 10000000, 25000000, 50000000, 75000000, 100000000, 250000000, 500000000, 750000000, 1000000000, 2500000000,5000000000,7500000000,10000000000])
# buckets indicated because of measuring time in nanoseconds

def crea_server():
    app = Flask(__name__)
    
    @app.route('/metriche', methods=['GET'])
    def metriche():
        # Esporta tutte le metriche come testo per Prometheus
        return Response(generate_latest(REGISTRY), mimetype='text/plain')
    
    return app

def avvia_server():
    hostname = socket.gethostname()
    logger.info(f'Hostname: {hostname} -> server starting on port {str(PORTA_SED)}')
    app.run(HOST, port=PORTA_SED, threaded=True)

# Funzione di callback per la conferma degli offset di Kafka
def commit_completed(err, partitions):
    if err:
        logger.error(f"Errore durante il commit: {err}")
    else:
         # Usa repr() per evitare problemi con i caratteri di formato
        #logger.info("Commit completato con successo: " + str(partitions) + "\n")
        logger.info("Commit completato con successo\n")

# Funzione per trovare i monitoraggi attivi
def trova_monitoraggi_attivi():
    """
    Recupera i monitoraggi attivi dal database e li prepara per l'invio.
    
    Returns:
        str: JSON contenente i monitoraggi da inviare
        str: '{}' se non ci sono monitoraggi da inviare
    """
    """
    try:
        connessione = inizializza_connessione_db(
            host=HOSTNAME, 
            porta=PORT, 
            utente=USER, 
            password=PASSWORD_DB, 
            database=DATABASE_SED
        )
        
        if not connessione:
            logger.error("Impossibile connettersi al database per recuperare i monitoraggi attivi")
            return '{}'
        
        try:
            tempo_inizio = time.time_ns()
            
            # Recupera i monitoraggi attivi
            cursore = esegui_query(
                connessione=connessione,
                query="SELECT regole FROM monitoraggi_attivi WHERE id_monitoraggio = %s",
                parametri=(ID_MONITORAGGIO,),
                istogramma=QUERY_DURATIONS_HISTOGRAM
            )
            
            if not cursore:
                logger.error("Errore nel recupero dei monitoraggi attivi")
                return '{}'
                
            risultati = cursore.fetchall()
            
            # Se non ci sono risultati, restituisci un dizionario vuoto
            if not risultati:
                return '{}'
                
            # Prepara il risultato combinando tutti i monitoraggi
            monitoraggi_combinati = {}
            for riga in risultati:
                regole = json.loads(riga[0])
                # Logica di combinazione dei monitoraggi
                # Questa è una semplificazione - potrebbe essere necessario adattarla
                for chiave, valore in regole.items():
                    if chiave not in monitoraggi_combinati:
                        monitoraggi_combinati[chiave] = valore
            
            return json.dumps(monitoraggi_combinati)
            
        finally:
            chiudi_connessione_db(connessione)
            
    except Exception as err:
        logger.error(f"Errore nel recupero dei monitoraggi attivi: {err}")
        return '{}'
    """
    return '{}' #da togliere

# Funzione per produrre messaggi Kafka
def produci_messaggio_kafka(topic, producer, messaggio):
    """
    Pubblica un messaggio sul topic Kafka specificato.
    
    Args:
        topic: Nome del topic Kafka
        producer: Istanza del producer Kafka
        messaggio: Messaggio da pubblicare
        
    Returns:
        bool: True se il messaggio è stato inviato con successo, False altrimenti
    """

    """
    try:
        producer.produce(topic, value=messaggio)
        KAFKA_MESSAGE.inc()
        producer.poll(0)  # Trigger any callbacks
        return True
    except BufferError:
        logger.error(f"Coda del producer locale piena ({len(producer)} messaggi in attesa): riprova")
        return False
    except Exception as e:
        logger.error(f"Errore nella produzione del messaggio Kafka: {e}")
        return False"
    """

# create Flask application
app = crea_server()

if __name__ == "__main__":
    # Creazione della tabella monitoraggi_attivi se non esiste
    try:
        # Inizializza la connessione al database
        connessione = inizializza_connessione_db(
            host=HOSTNAME, 
            porta=PORT, 
            utente=USER, 
            password=PASSWORD_DB, 
            database=DATABASE_SED
        )
        
        if not connessione:
            sys.exit("Impossibile connettersi al database SED\n")
        
       
        risultato = esegui_query(
            connessione=connessione,
            crea_tabella=True,
            nome_tabella="monitoraggi_attivi",
            definizione_colonne="id INTEGER PRIMARY KEY AUTO_INCREMENT, regole JSON NOT NULL, time_stamp TIMESTAMP NOT NULL, id_monitoraggio VARCHAR(60) NOT NULL, INDEX index_monitoraggio (id_monitoraggio)",
            istogramma=QUERY_DURATIONS_HISTOGRAM
        )
        
        if not risultato:
            sys.exit("Worker Service terminating: impossibile creare la tabella monitoraggi_attivi\n")
            
        logger.info("Tabella monitoraggi_attivi creata o già esistente")
        
    except Exception as err:
        logger.error(f"Exception raised! -> {err}")
        try:
            if 'connessione' in locals() and connessione:
                chiudi_connessione_db(connessione)
        except Exception as exe:
            logger.error(f"Exception raised in closing connection: {exe}")
        sys.exit("Exiting after database error...\n")
    finally:
        if 'connessione' in locals() and connessione:
            chiudi_connessione_db(connessione)
    
    # Genera un ID monitoraggio unico
    ID_MONITORAGGIO = f"{socket.gethostname()}-{time.time()}"
    logger.info(f"ID monitoraggio generato: {ID_MONITORAGGIO}")
    
    # Avvio del server per le metriche Prometheus
    threadMetricsServer = threading.Thread(target=avvia_server)
    threadMetricsServer.daemon = True
    threadMetricsServer.start()
    
    # Avvio della sottoscrizione Kafka
    consumer_conf = {
        'bootstrap.servers': KAFKA_BROKER_1,
        'group.id': GROUP_ID_1,
        'enable.auto.commit': 'false',
        'auto.offset.reset': 'latest',
        'on_commit': commit_completed
    }
    
    producer_conf = {
        'bootstrap.servers': KAFKA_BROKER_2
    }
    
    consumer_kafka = confluent_kafka.Consumer(consumer_conf)
    producer_kafka = confluent_kafka.Producer(producer_conf)
    
    try:
        consumer_kafka.subscribe([KAFKA_TOPIC_1])  # Sottoscrizione al topic di ingresso
        logger.info(f"Sottoscrizione al topic {KAFKA_TOPIC_1} effettuata con successo")
    except confluent_kafka.KafkaException as ke:
        logger.error(f"Errore Kafka durante la sottoscrizione: {ke}")
        consumer_kafka.close()
        sys.exit("Terminazione dopo errore nella sottoscrizione al topic Kafka\n")

    try:
        logger.info("Inizio polling dei messaggi Kafka")
        while True:
            # Polling dei messaggi nel topic Kafka
            msg = consumer_kafka.poll(timeout=5.0)
            
            if msg is None:
                # Nessun messaggio disponibile entro il timeout
                continue
            elif msg.error():
                logger.error(f'Errore Kafka: {msg.error()}')
                if msg.error().code() == confluent_kafka.KafkaError.UNKNOWN_TOPIC_OR_PART:
                    raise SystemExit("Topic sconosciuto, terminazione")
            else:
                # Processo dei messaggi Kafka
                record_value = msg.value()
                logger.info(f"Messaggio ricevuto: {record_value}")
                
                try:
                    # Deserializza il messaggio JSON
                    dati = json.loads(record_value)
                    
                    # Estrai le informazioni dal messaggio
                    lista_id_utenti = dati.get("id_utenti", [])
                    info_citta = dati.get("città", [])
                    
                    # Connessione al database
                    connessione = inizializza_connessione_db(
                        host=HOSTNAME, 
                        porta=PORT, 
                        utente=USER, 
                        password=PASSWORD_DB, 
                        database=DATABASE_SED
                    )
                    
                    if not connessione:
                        logger.error("Impossibile connettersi al database durante l'elaborazione del messaggio")
                        continue
                    
                    try:
                        # Inserisci i monitoraggi attivi nel database
                        for i in range(len(lista_id_utenti)):
                            regole_dict = {}
                            
                            # Estrai tutte le regole per l'utente corrente
                            for chiave in dati.keys():
                                if chiave not in ["id_utenti", "città", "id_righe"]:
                                    regole_dict[chiave] = dati[chiave][i]
                            
                            # Aggiungi le informazioni sulla città
                            regole_dict["città"] = info_citta
                            
                            # Converti il dizionario in JSON
                            regole_json = json.dumps(regole_dict)
                            
                            # Inserisci nel database
                            risultato = esegui_query(
                                connessione=connessione,
                                query="INSERT INTO monitoraggi_attivi (regole, time_stamp, id_monitoraggio) VALUES (%s, CURRENT_TIMESTAMP, %s)",
                                parametri=(regole_json, ID_MONITORAGGIO),
                                commit=True,
                                istogramma=QUERY_DURATIONS_HISTOGRAM
                            )
                            
                            if not risultato:
                                logger.error(f"Errore nell'inserimento del monitoraggio per l'utente {lista_id_utenti[i]}")
                    
                    finally:
                        chiudi_connessione_db(connessione)
                    
                    # Commit dell'offset Kafka
                    consumer_kafka.commit(asynchronous=True)
                 
                    # Trova i monitoraggi attivi e pubblica in Kafka
                    monitoraggi = trova_monitoraggi_attivi()
                    if monitoraggi != '{}':
                        # Aggiungi timestamp al messaggio
                        monitoraggi_dict = json.loads(monitoraggi)
                        monitoraggi_dict['timestamp'] = time.time_ns()
                        monitoraggi_json = json.dumps(monitoraggi_dict)
                        
                        # Pubblica il messaggio
                        while not produci_messaggio_kafka(KAFKA_TOPIC_2, producer_kafka, monitoraggi_json):
                            logger.info("Riprova invio messaggio...")
                            time.sleep(1)
          
                except json.JSONDecodeError:
                    logger.error(f"Errore nella decodifica del messaggio JSON: {record_value}")
                except Exception as e:
                    logger.error(f"Errore nell'elaborazione del messaggio: {e}")

    except (KeyboardInterrupt, SystemExit):
        logger.info("Interruzione ricevuta, terminazione in corso...")
    finally:
        # Chiusura delle connessioni Kafka
        consumer_kafka.close()
        logger.info("Connessioni chiuse, terminazione completa")



