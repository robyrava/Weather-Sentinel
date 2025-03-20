import sys
import os
# percorso della directory contenente config.py e db.py al sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config import*
from db import*
from utility import*

import hashlib
import threading
import socket
import time
import datetime
from flask import Flask, Response, request
from prometheus_client import Counter, generate_latest, REGISTRY, Gauge, Histogram

# Configurazione logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

#METRICHE SGA
# definition of the metrics to be exposed
REQUEST = Counter('SGA_requests', 'Total nSGAber of requests received by SGA-service')
FAILURE = Counter('SGA_failure_requests', 'Total nSGAber of requests received by SGA-service that failed')
INTERNAL_ERROR = Counter('SGA_internal_http_error', 'Total nSGAber of internal http errors in SGA-service')
RESPONSE_TO_WMS = Counter('SGA_RESPONSE_TO_WMS', 'Total nSGAber of responses sent to wms-service')
RESPONSE_TO_NOTIFIER = Counter('SGA_RESPONSE_TO_NOTIFIER', 'Total nSGAber of responses sent to notifier-service')
REGISTERED_utenti_COUNT = Gauge('SGA_registered_utenti_count', 'Total nSGAber of registered utenti')
DELTA_TIME = Gauge('SGA_response_time_client', 'Latency beetween instant in which client sends the API CALL and instant in which user-manager responses')
QUERY_DURATIONS_HISTOGRAM = Histogram('SGA_query_durations_nanoseconds_DB', 'DB query durations in nanoseconds', buckets=[5000000, 10000000, 25000000, 50000000, 75000000, 100000000, 250000000, 500000000, 750000000, 1000000000, 2500000000,5000000000,7500000000,10000000000])
# buckets indicated because of measuring time in nanoseconds

def calcola_hash(input):
    sha256_hash = hashlib.sha256()
    sha256_hash.update(input.encode('utf-8'))
    hash_result = sha256_hash.hexdigest()
    return hash_result

# Nuova funzione per verificare il token JWT
def verifica_token(token, password_hash=None):
    """
    Verifica il token JWT. Se password_hash è fornito, viene usato come chiave segreta.
    Altrimenti, estrae email dal token e cerca la password nel DB.
    
    Args:
        token: Token JWT da verificare
        password_hash: Hash della password dell'utente (opzionale)
        
    Returns:
        dict: Payload del token se valido
        None: Se il token non è valido
    """
    try:
        # Estrai l'email senza verificare la firma
        payload_non_verificato = jwt.decode(
            token, 
            options={"verify_signature": False},
            algorithms=['HS256']
        )
        
        email = payload_non_verificato.get('email')
        
        # Se password_hash non è fornito, recuperalo dal database
        if not password_hash:
            try:
                # Inizializza la connessione al database
                with inizializza_connessione_db(HOSTNAME, PORT, USER, PASSWORD_DB, DATABASE_SGA) as connessione:
                    if not connessione:
                        logger.error("Errore nella connessione al database")
                        return None
                    
                    # Recupera la password hash dal database
                    utente = verifica_utente(connessione, email, QUERY_DURATIONS_HISTOGRAM, restituisci_dettagli=True)
                    
                    if not utente or not 'password' in utente:
                        logger.error(f"Utente con email {email} non trovato o senza password")
                        return None
                    
                    password_hash = utente['password']
                    
            except Exception as err:
                logger.error(f"Errore nel recupero dell'hash della password: {err}")
                return None
        
        # Verifica il token con la password hash come chiave segreta
        payload = jwt.decode(
            token,
            password_hash,
            algorithms=['HS256']
        )
        
        return payload
        
    except jwt.ExpiredSignatureError:
        logger.error("Token scaduto")
        return None
    except jwt.InvalidTokenError as e:
        logger.error(f"Token non valido: {e}")
        return None
    except Exception as e:
        logger.error(f"Errore nella verifica del token: {e}")
        return None

def crea_server():
    app = Flask(__name__)

    @app.route('/registrazione', methods=['POST'])
    def registrazione_utente():
        # Incrementa la metrica delle richieste
        REQUEST.inc()
        # Verifica se i dati ricevuti sono in formato JSON
        if request.is_json:
            try:
                # Estrai i dati JSON
                data_dict = request.get_json()
                if data_dict:
                    email = data_dict.get("email")
                    logger.info("Email ricevuta:" + email)
                    password = data_dict.get("psw")
                    timestamp_client = data_dict.get("timestamp_client")
                    try:
                        # Inizializza la connessione al database
                        connessione = inizializza_connessione_db(
                            host=HOSTNAME, 
                            porta=PORT, 
                            utente=USER, 
                            password=PASSWORD_DB, 
                            database=DATABASE_SGA
                        )
                        
                        if not connessione:
                            FAILURE.inc()
                            INTERNAL_ERROR.inc()
                            DELTA_TIME.set(time.time_ns() - timestamp_client)
                            return "Errore nella connessione al database", 500
                        
                        try:
                            utente_esiste = verifica_utente(connessione, email, QUERY_DURATIONS_HISTOGRAM)
                            
                            if utente_esiste is None:
                                FAILURE.inc()
                                INTERNAL_ERROR.inc()
                                DELTA_TIME.set(time.time_ns() - timestamp_client)
                                return "Errore nella verifica dell'email", 500
                            
                            if not utente_esiste:
                                # L'email non esiste, procedi con la registrazione
                                hash_psw = calcola_hash(password)  # salviamo l'hash nel DB per maggiore privacy degli utenti
                                
                                # OTTIMIZZATO: Usa la nuova funzione inserisci_utente
                                if not inserisci_utente(connessione, email, hash_psw, QUERY_DURATIONS_HISTOGRAM):
                                    FAILURE.inc()
                                    INTERNAL_ERROR.inc()
                                    DELTA_TIME.set(time.time_ns() - timestamp_client)
                                    return "Errore nell'inserimento del nuovo utente", 500
                                
                                REGISTERED_utenti_COUNT.inc()
                                DELTA_TIME.set(time.time_ns() - timestamp_client)
                                return "Registrazione avvenuta con successo! Ora prova ad accedere!", 200
                            else:
                                # L'email esiste già
                                DELTA_TIME.set(time.time_ns() - timestamp_client)
                                FAILURE.inc()
                                return "Email già in uso! Prova ad accedere!", 401
                        
                        finally:
                            # Assicurati che la connessione venga chiusa in ogni caso
                            chiudi_connessione_db(connessione)
                            
                    except Exception as err:
                        logger.error(f"Eccezione sollevata! -> {err}")
                        FAILURE.inc()
                        INTERNAL_ERROR.inc()
                        DELTA_TIME.set(time.time_ns() - timestamp_client)
                        return f"Errore durante la registrazione: {str(err)}", 500

            except Exception as e:
                FAILURE.inc()
                return f"Errore nella lettura dei dati: {str(e)}", 400
        else:
            FAILURE.inc()
            return "Errore: la richiesta deve essere in formato JSON", 400
    
    @app.route('/login', methods=['POST'])
    def accesso_utente():
        # Incrementa la metrica delle richieste
        REQUEST.inc()
        # Verifica se i dati ricevuti sono in formato JSON
        if request.is_json:
            try:
                # Estrai i dati JSON
                data_dict = request.get_json()
                if data_dict:
                    email = data_dict.get("email")
                    logger.info("Email ricevuta:" + email)
                    password = data_dict.get("psw")
                    timestamp_client = data_dict.get("timestamp_client")
                    hash_psw = calcola_hash(password)
                    try:
                        # Inizializza la connessione al database
                        connessione = inizializza_connessione_db(
                            host=HOSTNAME, 
                            porta=PORT, 
                            utente=USER, 
                            password=PASSWORD_DB, 
                            database=DATABASE_SGA
                        )
                        
                        if not connessione:
                            FAILURE.inc()
                            INTERNAL_ERROR.inc()
                            DELTA_TIME.set(time.time_ns() - timestamp_client)
                            return "Errore nella connessione al database", 500
                        
                        try:
                            # OTTIMIZZATO: Usa la nuova funzione verifica_credenziali
                            utente = verifica_credenziali(connessione, email, hash_psw, QUERY_DURATIONS_HISTOGRAM)
                            
                            if utente is None:
                                # Credenziali non valide
                                FAILURE.inc()
                                DELTA_TIME.set(time.time_ns() - timestamp_client)
                                return "Email o password errata! Riprova!", 401
                            else:
                                DELTA_TIME.set(time.time_ns() - timestamp_client)
                                
                                # Crea un token JWT invece di usare la sessione
                                payload = {
                                    'email': email,
                                    'exp': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3)
                                }
                                token = jwt.encode(payload, hash_psw, algorithm='HS256')
                                
                                """
                                # Restituisci il token al client
                                return jsonify({
                                    "message": "Benvenuto in Weather Sentinel!",
                                    "token": token
                                }), 200
                                """
                                return f"Benvenuto in Weather Sentinel!\nEcco il tuo Token:\n{token}", 200
                        finally:
                            # Assicurati che la connessione venga chiusa in ogni caso
                            chiudi_connessione_db(connessione)
                    
                    except Exception as err:
                        logger.error(f"Eccezione sollevata! -> {err}")
                        FAILURE.inc()
                        INTERNAL_ERROR.inc()
                        DELTA_TIME.set(time.time_ns() - timestamp_client)
                        return f"Errore durante l'accesso: {str(err)}", 500
            
            except Exception as e:
                FAILURE.inc()
                return f"Errore nella lettura dei dati: {str(e)}", 400
        else:
            FAILURE.inc()
            return "Errore: la richiesta deve essere in formato JSON", 400
    
    @app.route('/elimina_account', methods=['POST'])
    def elimina_account():
        # Incrementa la metrica delle richieste
        REQUEST.inc()
        # Verifica se i dati ricevuti sono in formato JSON
        if request.is_json:
            try:
                # Estrai i dati JSON
                data_dict = request.get_json()
                if data_dict:
                    timestamp_client = data_dict.get("timestamp_client")
                    
                    # Ottieni e verifica il token JWT dall'header di autorizzazione
                    intestazione_autorizzazione = request.headers.get('Authorization')
                    payload, errore = verifica_token_jwt(intestazione_autorizzazione)

                    if errore:
                        FAILURE.inc()
                        DELTA_TIME.set(time.time_ns() - timestamp_client)
                        return errore
                    
                    # Token valido, estrai email e procedi
                    email = payload.get('email')
                    
                    try:
                        # Inizializza la connessione al database
                        connessione = inizializza_connessione_db(
                            host=HOSTNAME, 
                            porta=PORT, 
                            utente=USER, 
                            password=PASSWORD_DB, 
                            database=DATABASE_SGA
                        )
                        
                        if not connessione:
                            FAILURE.inc()
                            INTERNAL_ERROR.inc()
                            DELTA_TIME.set(time.time_ns() - timestamp_client)
                            return "Errore nella connessione al database", 500
                        
                        try:
                            # Recupera la password hash dell'utente
                            utente_info = verifica_utente(connessione, email, QUERY_DURATIONS_HISTOGRAM, restituisci_dettagli=True)
                            
                            if not utente_info or 'password' not in utente_info:
                                FAILURE.inc()
                                DELTA_TIME.set(time.time_ns() - timestamp_client)
                                return "Utente non trovato!", 401
                            
                            # Elimina l'utente
                            if not elimina_utente(connessione, email, QUERY_DURATIONS_HISTOGRAM):
                                FAILURE.inc()
                                INTERNAL_ERROR.inc()
                                DELTA_TIME.set(time.time_ns() - timestamp_client)
                                return "Errore nell'eliminazione dell'account", 500
                            
                            # Decrementa il contatore degli utenti registrati
                            REGISTERED_utenti_COUNT.dec()
                            DELTA_TIME.set(time.time_ns() - timestamp_client)
                            return "ACCOUNT ELIMINATO CON SUCCESSO!", 200
                        
                        finally:
                            # Assicurati che la connessione venga chiusa in ogni caso
                            chiudi_connessione_db(connessione)
                    
                    except Exception as err:
                        logger.error(f"Eccezione sollevata! -> {err}")
                        FAILURE.inc()
                        INTERNAL_ERROR.inc()
                        DELTA_TIME.set(time.time_ns() - timestamp_client)
                        return f"Errore nella connessione al database: {str(err)}", 500

            except Exception as e:
                FAILURE.inc()
                return f"Errore nella lettura dei dati: {str(e)}", 400
        else:
            FAILURE.inc()
            return "Errore: la richiesta deve essere in formato JSON", 400

    #@app.route('/logout', methods=['POST'])
    # Con JWT non c'è bisogno di una vera operazione di logout lato server,
    # poiché i token sono stateless.
        
    @app.route('/metriche')
    def metriche():
        # Esporta tutte le metriche come testo per Prometheus
        return Response(generate_latest(REGISTRY), mimetype='text/plain')

    return app

def avvia_server():
    hostname = socket.gethostname()
    logger.info(f'Hostname: {hostname} -> server starting on port {str(PORTA_SGA)}')
    app.run(HOST, port=PORTA_SGA, threaded=True)

# create Flask application
app = crea_server()

if __name__ == '__main__':
    # connessione al db
    try:
        # Inizializza la connessione al database
        connessione = inizializza_connessione_db(
            host=HOSTNAME, 
            porta=PORT, 
            utente=USER, 
            password=PASSWORD_DB, 
            database=DATABASE_SGA
        )
        
        if not connessione:
            sys.exit("User Manager terminating: impossibile connettersi al database\n")
        
        # Crea la tabella utenti se non esiste
        utenti_cursor = esegui_query(
            connessione=connessione,
            crea_tabella=True,
            nome_tabella="utenti",
            definizione_colonne="id INTEGER PRIMARY KEY AUTO_INCREMENT, email VARCHAR(30) UNIQUE NOT NULL, password VARCHAR(64) NOT NULL",
            istogramma=QUERY_DURATIONS_HISTOGRAM
        )
        
        if not utenti_cursor:
            sys.exit("User Manager terminating: impossibile creare la tabella utenti\n")
        
        # Crea la tabella metriche_to_restore se non esiste
        metriche_cursor = esegui_query(
            connessione=connessione,
            crea_tabella=True,
            nome_tabella="metriche_to_restore",
            definizione_colonne="id INTEGER PRIMARY KEY AUTO_INCREMENT, metriche JSON NOT NULL",
            istogramma=QUERY_DURATIONS_HISTOGRAM
        )
        
        if not metriche_cursor:
            sys.exit("User Manager terminating: impossibile creare la tabella metriche_to_restore\n")
    
    except Exception as err:
        sys.stderr.write("Exception raised! -> " + str(err) + "\n")
        sys.exit("User Manager terminating after an error...\n")
    finally:
        if 'connessione' in locals() and connessione:
            chiudi_connessione_db(connessione)

    logger.info("Starting API Gateway serving thread!\n")
    threadAPIGateway = threading.Thread(target=avvia_server)
    threadAPIGateway.daemon = True
    threadAPIGateway.start()

    while True:
        pass