import sys
import os
# percorso della directory contenente config.py e db.py al sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config import*
from db import*
from utility import*

import threading
import socket
from flask import Flask,Response, request
import logging
import confluent_kafka
from confluent_kafka.admin import AdminClient, NewTopic
import json
import requests



# definition of the metrics to be exposed
REQUEST = Counter('WMS_requests', 'Total number of requests received by wms-service')
FAILURE = Counter('WMS_failure_requests', 'Total number of requests received by wms-service that failed')
INTERNAL_ERROR = Counter('WMS_internal_http_error', 'Total number of internal http errors in wms-service')
ACTIVE_RULES = Gauge('WMS_active_rules', 'Total number of rules that have been provided to the system')
KAFKA_MESSAGE = Counter('WMS_kafka_message_number', 'Total number of kafka messages produced by wms-service')
KAFKA_MESSAGE_DELIVERED = Counter('WMS_kafka_message_delivered_number', 'Total number of kafka messages produced by wms-service that have been delivered correctly')
REQUEST_TO_UM = Counter('WMS_requests_to_UM', 'Total number of requests sent to um-service')
DELTA_TIME = Gauge('WMS_response_time_client', 'Latency beetween instant in which client sends the API CALL and instant in which wms-manager responses')
QUERY_DURATIONS_HISTOGRAM = Histogram('WMS_query_durations_nanoseconds_DB', 'DB query durations in nanoseconds', buckets=[5000000, 10000000, 25000000, 50000000, 75000000, 100000000, 250000000, 500000000, 750000000, 1000000000, 2500000000,5000000000,7500000000,10000000000])

# METRICHE KAFKA
MESSAGGIO_KAFKA = Counter('SGM_messaggio_kafka_numero', 'Numero totale di messaggi Kafka prodotti dal servizio SGM')
MESSAGGIO_KAFKA_CONSEGNATO = Counter('SGM_messaggio_kafka_consegnato_numero', 'Numero totale di messaggi Kafka prodotti dal servizio SGM che sono stati consegnati correttamente')
# buckets indicated because of measuring time in nanoseconds

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def crea_messaggio_kafka(dizionario_json_finale, id_città, connessione, istogramma=None):
    """
    Crea un messaggio Kafka per una specifica città, raccogliendo tutti i vincoli che gli utenti hanno associati per quella città.
    
    Args:
        dizionario_json_finale: Dizionario JSON da popolare con i dati
        id_città: ID della città per cui creare il messaggio
        connessione: Connessione al database
        istogramma: Istogramma per metriche di performance
        
    Returns:
        str: Stringa JSON del messaggio formattato per Kafka
        None: In caso di errore
    """  
    tempo_inizio_db = time.time_ns()
    cursore = esegui_query(
        connessione=connessione,
        query="SELECT città, latitudine, longitudine, codice_postale, codice_stato FROM citta WHERE id = %s",
        parametri=(str(id_città),),
        istogramma=istogramma
    )
    
    if not cursore:
        logger.error("Errore durante il recupero delle informazioni sulla città")
        return None
        
    città = cursore.fetchone()  # Lista di informazioni sulla città corrente del messaggio Kafka
    tempo_fine_db = time.time_ns()
    if istogramma:
        istogramma.observe(tempo_fine_db - tempo_inizio_db)
    
    # Liste per raccogliere i dati di tutti gli utenti per questa città
    lista_id_utenti = []
    lista_temp_max = []
    lista_temp_min = []
    lista_umi_max = []
    lista_umi_min = []
    lista_pressione_max = []
    lista_pressione_min = []
    lista_nuvole_max = []
    lista_nuvole_min = []
    lista_vel_vento_max = []
    lista_vel_vento_min = []
    lista_direzione_vento = []
    lista_pioggia = []
    lista_neve = []
    lista_id_righe = []
    
    tempo_inizio_db = time.time_ns()
    cursore = esegui_query(
        connessione=connessione,
        query="SELECT * FROM vincoli_utente WHERE TIMESTAMPDIFF(SECOND, timestamp, CURRENT_TIMESTAMP()) > periodo_trigger AND controllato=TRUE AND id_città = %s",
        parametri=(str(id_città),),
        istogramma=istogramma
    )
    
    if not cursore:
        logger.error("Errore durante il recupero dei vincoli utente")
        return None
        
    risultati = cursore.fetchall()
    tempo_fine_db = time.time_ns()
    if istogramma:
        istogramma.observe(tempo_fine_db - tempo_inizio_db)
    
    # Se non ci sono vincoli da controllare, non procedere
    if not risultati:
        logger.info(f"Nessun vincolo da controllare per la città ID {id_città}")
        return None
    
    # Elabora ogni vincolo trovato
    for risultato in risultati:
        dizionario_regole = json.loads(risultato[3])  # Indice 3 contiene il campo regole in JSON
        lista_id_utenti.append(risultato[1])  # Indice 1 contiene id_utente
        lista_id_righe.append(risultato[0])   # Indice 0 contiene id della riga
        
        # Estrai i valori delle regole, usando "null" come valore predefinito
        lista_temp_max.append(dizionario_regole.get("temp_max", "null"))
        lista_temp_min.append(dizionario_regole.get("temp_min", "null"))
        lista_umi_max.append(dizionario_regole.get("umi_max", "null"))
        lista_umi_min.append(dizionario_regole.get("umi_min", "null"))
        lista_pressione_max.append(dizionario_regole.get("pressione_max", "null"))
        lista_pressione_min.append(dizionario_regole.get("pressione_min", "null"))
        lista_nuvole_max.append(dizionario_regole.get("nuvole_max", "null"))
        lista_nuvole_min.append(dizionario_regole.get("nuvole_min", "null"))
        lista_vel_vento_max.append(dizionario_regole.get("vel_vento_max", "null"))
        lista_vel_vento_min.append(dizionario_regole.get("vel_vento_min", "null"))
        lista_direzione_vento.append(dizionario_regole.get("direzione_vento", "null"))
        lista_pioggia.append(dizionario_regole.get("pioggia", "null"))
        lista_neve.append(dizionario_regole.get("neve", "null"))
    
    # Costruisci il messaggio finale con i dati raccolti
    dizionario_json_finale["num_righe"] = lista_id_righe
    dizionario_json_finale["id_utente"] = lista_id_utenti
    dizionario_json_finale["localita"] = città
    
    # Aggiungi solo le regole che hanno almeno un valore non nullo
    # Per ogni tipo di regola, controlla se almeno un utente ha un valore non nullo
    for nome_campo, lista_valori in [
        ("temp_max", lista_temp_max),
        ("temp_min", lista_temp_min),
        ("umi_max", lista_umi_max),
        ("umi_min", lista_umi_min),
        ("pressione_max", lista_pressione_max),
        ("pressione_min", lista_pressione_min),
        ("nuvole_max", lista_nuvole_max),
        ("nuvole_min", lista_nuvole_min),
        ("vel_vento_max", lista_vel_vento_max),
        ("vel_vento_min", lista_vel_vento_min),
        ("direzione_vento", lista_direzione_vento),
        ("pioggia", lista_pioggia),
        ("neve", lista_neve)
    ]:
        if any(elemento != "null" for elemento in lista_valori):
            dizionario_json_finale[nome_campo] = lista_valori
    
    logger.info(f"\nDIZIONARIO JSON FINALE: {str(dizionario_json_finale)}\n")
    return json.dumps(dizionario_json_finale)   

    

def recupera_vincoli_pendenti():
    """
    Recupera dal database i vincoli utente che necessitano di essere controllati
    e li prepara per l'invio a Kafka.
    
    Returns:
        list: Lista di messaggi Kafka da inviare
        False: In caso di errore
    """
    connessione = None
    try:
        # CONNESSIONE AL DB
        connessione = inizializza_connessione_db(
            host=HOSTNAME, 
            porta=PORT, 
            utente=USER, 
            password=PASSWORD_DB, 
            database=DATABASE_SGM
        )
        
        if not connessione:
            logger.error("Impossibile connettersi al database")
            return False
        
        try:
            # Trova i vincoli utente da controllare
            cursore = esegui_query(
                connessione=connessione,
                query="SELECT id_città FROM vincoli_utente WHERE TIMESTAMPDIFF(SECOND, timestamp, CURRENT_TIMESTAMP()) > periodo_trigger AND controllato=FALSE GROUP BY id_città",
                istogramma=QUERY_DURATIONS_HISTOGRAM
            )
            
            if not cursore:
                logger.error("Errore nell'interrogazione dei vincoli utente")
                return False
            
            id_città_lista = cursore.fetchall()
            
            # Se ci sono città da controllare, aggiorna lo stato
            if id_città_lista:
                update_cursore = esegui_query(
                    connessione=connessione,
                    query="UPDATE vincoli_utente SET controllato=TRUE WHERE TIMESTAMPDIFF(SECOND, timestamp, CURRENT_TIMESTAMP()) > periodo_trigger AND controllato=FALSE",
                    commit=True,
                    istogramma=QUERY_DURATIONS_HISTOGRAM
                )
                
                if not update_cursore:
                    logger.error("Errore nell'aggiornamento dei vincoli utente")
                    return False
            
            # Prepara i messaggi Kafka
            lista_messaggi_kafka = []
            for città in id_città_lista:
                id_città = città[0]
                dizionario_json_finale = dict()
                
                # CREO MESSAGGIO KAFKA
                messaggio = crea_messaggio_kafka(dizionario_json_finale, id_città, connessione, QUERY_DURATIONS_HISTOGRAM)
                
                if messaggio:
                    lista_messaggi_kafka.append(messaggio)
            
            return lista_messaggi_kafka
            
        except Exception as err:
            logger.error(f"Eccezione durante la ricerca dei vincoli pendenti: {err}")
            return False
    
    except Exception as err:
        logger.error(f"Eccezione durante l'inizializzazione della connessione: {err}")
        return False
    
    finally:
        if connessione:
            chiudi_connessione_db(connessione)

def callback_consegna(err, msg):
    """
    Callback opzionale per ogni messaggio (attivato da poll() o flush())
    quando un messaggio è stato consegnato con successo o la consegna
    è fallita definitivamente (dopo i tentativi).
    Aggiorna la tabella vincoli_utente per evitare di considerare nuovamente
    una riga nella costruzione del messaggio Kafka da pubblicare nel topic.
    In questo modo, evitiamo che repliche multiple del SGM inviino
    lo stesso messaggio di trigger ai worker.
    """
    if err:
        logger.error('%% Consegna messaggio fallita: %s\n' % err)
        dizionario_messaggio = json.loads(msg.value())
        logger.info(dizionario_messaggio)
        lista_id_righe = dizionario_messaggio.get("num_righe")
        try:
            # Inizializza la connessione al database
            connessione = inizializza_connessione_db(
                host=HOSTNAME, 
                porta=PORT, 
                utente=USER, 
                password=PASSWORD_DB, 
                database=DATABASE_SGM
            )
            
            if not connessione:
                logger.error("Impossibile connettersi al database")
                raise SystemExit("Uscita dopo errore di connessione al database\n")
            
            try:
                # Aggiorna lo stato dei vincoli in caso di errore
                for id in lista_id_righe:
                    logger.info("ID nella LISTA_ID_RIGHE: " + str(id))
                    risultato = esegui_query(
                        connessione=connessione,
                        query="UPDATE vincoli_utente SET controllato=FALSE WHERE id = %s",
                        parametri=(str(id),),
                        commit=True,
                        istogramma=QUERY_DURATIONS_HISTOGRAM
                    )
                    
                    if not risultato:
                        logger.error(f"Errore nell'aggiornamento del vincolo con ID {id}")
            
            finally:
                # Chiudi la connessione al database
                chiudi_connessione_db(connessione)
                
        except Exception as err:
            logger.error(f"Eccezione sollevata: {err}")
            raise SystemExit("Errore durante la gestione del fallimento della consegna\n")
            
        raise SystemExit("Uscita dopo errore nella consegna del messaggio al broker Kafka\n")
    
    else:
        MESSAGGIO_KAFKA_CONSEGNATO.inc()
        logger.info('%% Messaggio consegnato al topic %s, partizione[%d] @ %d\n' %
                    (msg.topic(), msg.partition(), msg.offset()))
        dizionario_messaggio = json.loads(msg.value())
        logger.info(dizionario_messaggio)
        lista_id_righe = dizionario_messaggio.get("num_righe")
        try:
            # Inizializza la connessione al database
            connessione = inizializza_connessione_db(
                host=HOSTNAME, 
                porta=PORT, 
                utente=USER, 
                password=PASSWORD_DB, 
                database=DATABASE_SGM
            )
            
            if not connessione:
                logger.error("Impossibile connettersi al database")
                raise SystemExit("Uscita dopo errore di connessione al database\n")
            
            try:
                # Aggiorna il timestamp e lo stato di controllo per ogni vincolo
                for id in lista_id_righe:
                    logger.info("ID nella LISTA_ID_RIGHE: " + str(id))
                    tempo_inizio = time.time_ns()
                    risultato = esegui_query(
                        connessione=connessione,
                        query="UPDATE vincoli_utente SET timestamp = CURRENT_TIMESTAMP(), controllato=FALSE WHERE id = %s",
                        parametri=(str(id),),
                        commit=True,
                        istogramma=QUERY_DURATIONS_HISTOGRAM
                    )
                    
                    if not risultato:
                        logger.error(f"Errore nell'aggiornamento del vincolo con ID {id}")

                    
            
            finally:
                # Chiudi la connessione al database
                chiudi_connessione_db(connessione)
                
        except Exception as err:
            logger.error(f"Eccezione sollevata: {err}")
            raise SystemExit("Errore durante la gestione del successo della consegna\n")

def produci_messaggio_kafka(nome_topic, produttore_kafka, messaggio):
    """
    Pubblica un messaggio su un topic Kafka specificato.
    
    Args:
        nome_topic: Nome del topic Kafka su cui pubblicare il messaggio
        produttore_kafka: Istanza del producer Kafka
        messaggio: Messaggio da pubblicare (in formato stringa JSON)
        
    Returns:
        bool: True se il messaggio è stato inviato con successo, False altrimenti
    """
    # Pubblica sul topic specifico
    try:
        produttore_kafka.produce(nome_topic, value=messaggio, callback=callback_consegna)
        MESSAGGIO_KAFKA.inc()  # Incrementa il contatore delle metriche
        logger.info(f"Messaggio inviato al broker Kafka: {messaggio}\n" )
    except BufferError:
        logger.error(
            '%% Coda del produttore locale piena (%d messaggi in attesa di consegna): riprova\n' % len(produttore_kafka))
        return False
    
    # Attendi che il messaggio sia stato consegnato
    logger.info("In attesa che il messaggio venga consegnato\n")
    produttore_kafka.flush()
    return True

def timer(secondi, evento):
    """
    Timer che genera un evento periodicamente.
    
    Args:
        secondi: Intervallo in secondi tra ogni evento
        evento: Oggetto threading.Event da impostare periodicamente
    """
    while True:
        logger.info(f"Timer: generazione evento dopo {secondi} secondi")
        time.sleep(secondi)
        evento.set()

def formatta_risposta_regole(lista_regole):
        """
        Formatta le regole dell'utente in un formato leggibile.
        
        Args:
            lista_regole: Lista di tuple (info_città, regole, dizionario_periodo_trigger)
            
        Returns:
            str: Rappresentazione formattata delle regole
        """
        regole_restituite = "Nessuna regola inserita!"  # valore di inizializzazione
        regole_località = ""
        contatore = 1
        
        # regola[0] = {"città":lista_info_città}
        # regola[1] = {regola:("null" o valore), ..., "città":lista_info_città, "timestamp_client": valore_timestamp}
        # regola[2] = {"periodo_trigger": valore_periodo_trigger}
        for regola in lista_regole:
            stringa_città = json.dumps(regola[0])
            dizionario_regole_temp = regola[1]
            if "città" in dizionario_regole_temp:
                del dizionario_regole_temp["città"]
            if "timestamp_client" in dizionario_regole_temp:
                del dizionario_regole_temp["timestamp_client"]
                
            # Rimuovi le regole con valore "null"
            insieme_chiavi_target = set()
            insieme_chiavi = dizionario_regole_temp.keys()
            for chiave in insieme_chiavi:
                if dizionario_regole_temp.get(chiave) == "null":
                    insieme_chiavi_target.add(chiave)
            for chiave in insieme_chiavi_target:
                del dizionario_regole_temp[chiave]
                
            stringa_regole = json.dumps(dizionario_regole_temp)
            stringa_periodo_trigger = json.dumps(regola[2])
            stringa_temp = f"CITTÀ {str(contatore)}<br>" + stringa_città + "<br>" + stringa_regole + "<br>" + stringa_periodo_trigger + "<br><br>"
            regole_località = regole_località + stringa_temp
            contatore = contatore + 1
            
        if regole_località != "":
            regole_restituite = regole_località
        
        return regole_restituite

def ottieni_id_utente_da_email(email):
    """
    Ottiene l'ID dell'utente dal servizio SGA data l'email.
    
    Args:
        email: Email dell'utente di cui recuperare l'ID
        
    Returns:
        int: ID dell'utente se trovato
        None: Se l'ID non è stato trovato o si è verificato un errore
    """
    try:
        # URL dell'endpoint del SGA per recuperare l'ID utente
        url = f"http://{SGA_HOST}:{PORTA_SGA}/utente/email/{email}"
        
        # Esegui la richiesta GET
        risposta = requests.get(url)
        
        # Verifica se la richiesta è andata a buon fine
        if risposta.status_code == 200:
            # Estrai l'ID dalla risposta JSON
            dati_risposta = risposta.json()
            id_utente = dati_risposta.get('id')
            logger.info(f"\nRecuperato ID per l'utente con email {email}: {id_utente}\n")
            return id_utente
        else:
            logger.error(f"\nErrore nel recupero dell'ID per l'utente {email}. Codice: {risposta.status_code}, Risposta: {risposta.text}\n")
            return None
            
    except Exception as e:
        logger.error(f"\nEccezione durante il recupero dell'ID per l'utente {email}: {e}\n")
        return None


def avvia_server():
    
    hostname = socket.gethostname()
    logger.info(f'Hostname: {hostname} -> server starting on port {str(PORTA_SGM)}')
    app.run(HOST, port=PORTA_SGM, threaded=True)

def crea_server():
    app = Flask(__name__)
    
     #VIENE FATTA UNA DOPPIA CONNESSIONE AL DATABASE ==> AGGIUSTARE
    @app.route('/aggiorna_regole', methods=['POST'])
    def gestione_aggiorna_regole():
        """
        Route per aggiornare le regole degli utenti per una specifica città.
        Riceve i dati della città, il periodo di trigger e le regole.
        Utilizza il token JWT per l'autenticazione.
        """
        # Incrementa la metrica delle richieste
        REQUEST.inc()
        # Verifica se i dati ricevuti sono in formato JSON
        if request.is_json:
            try:
                # Estrai i dati JSON
                dati_dict = request.get_json()
                logger.info("Dati ricevuti:" + str(dati_dict))
                if dati_dict:
                    timestamp_client = dati_dict.get("timestamp_client")
                    
                    # Ottieni e verifica il token JWT
                    intestazione_autorizzazione = request.headers.get('Authorization')
                    payload, errore = verifica_token_jwt(intestazione_autorizzazione)

                    if errore:
                        FAILURE.inc()
                        DELTA_TIME.set(time.time_ns() - timestamp_client)
                        return errore
                        
                    # Token valido, estrai email e procedi
                    email_utente = payload.get('email')
                    
                    # Ottieni l'ID utente tramite chiamata API al servizio SGA
                    id_utente = ottieni_id_utente_da_email(email_utente)
                    
                    if id_utente is None:
                        FAILURE.inc()
                        DELTA_TIME.set(time.time_ns() - timestamp_client)
                        return "Utente non trovato nel sistema", 401
                    
                    #Estrazione dei dati della città e delle regole
                    periodo_trigger = dati_dict.get('periodo_trigger')
                    nome_città = dati_dict.get('città')[0]
                    latitudine = dati_dict.get('città')[1]
                    latitudine_arrotondata = round(latitudine, 3)
                    longitudine = dati_dict.get('città')[2]
                    longitudine_arrotondata = round(longitudine, 3)
                    codice_postale = dati_dict.get('città')[3]
                    codice_stato = dati_dict.get('città')[4]
                    
                    logger.info(
                        "CITTÀ  " + nome_città + '  ' + str(latitudine_arrotondata) + '  ' + 
                        str(longitudine_arrotondata) + '  ' + str(codice_postale) + '  ' + 
                        str(codice_stato) + "\n\n")
                    
                    # Rimuovi il periodo_trigger dal dizionario per la serializzazione
                    del dati_dict['periodo_trigger']
                    del dati_dict['città']
                    # Converti le regole in stringa JSON
                    vincoli_json = json.dumps(dati_dict)
                    
                    try:
                        # Inizializza la connessione al database
                        connessione_SGA = inizializza_connessione_db(
                            host=HOSTNAME, 
                            porta=PORT, 
                            utente=USER, 
                            password=PASSWORD_DB, 
                            database=DATABASE_SGA
                        )

                        # Inizializza la connessione al database
                        connessione_SGM = inizializza_connessione_db(
                            host=HOSTNAME, 
                            porta=PORT, 
                            utente=USER, 
                            password=PASSWORD_DB, 
                            database=DATABASE_SGM
                        )

                        #RECUPERO ID UTENTE DAL DATABASE
                        if not connessione_SGA:
                            FAILURE.inc()
                            INTERNAL_ERROR.inc()
                            DELTA_TIME.set(time.time_ns() - timestamp_client)
                            return "Errore nella connessione al database", 500
                        
                        try:
                            # Recupera ID utente dall'email
                            cursore_utente = esegui_query(
                                connessione_SGA,
                                query="SELECT id FROM utenti WHERE email = %s",
                                parametri=(email_utente,),
                                istogramma=QUERY_DURATIONS_HISTOGRAM
                            )
                            
                            if not cursore_utente:
                                FAILURE.inc()
                                INTERNAL_ERROR.inc()
                                DELTA_TIME.set(time.time_ns() - timestamp_client)
                                return "Errore nella query di ricerca utente", 500
                                
                            utente = cursore_utente.fetchone()
                            
                            if not utente:
                                logger.error(f"Utente con email {email_utente} non trovato nel database")
                                FAILURE.inc()
                                DELTA_TIME.set(time.time_ns() - timestamp_client)
                                return "Utente non trovato nel database", 401
                                
                            id_utente = utente[0]
                            
                            # RECUOERA ID CITTÀ DAL DATABASE
                            cursore_citta = esegui_query(
                                connessione_SGM,
                                query="SELECT * FROM citta WHERE ROUND(latitudine, 3) = %s AND ROUND(longitudine, 3) = %s AND città = %s",
                                parametri=(str(latitudine_arrotondata), str(longitudine_arrotondata), nome_città),
                                istogramma=QUERY_DURATIONS_HISTOGRAM
                            )
                            
                            if not cursore_citta:
                                FAILURE.inc()
                                INTERNAL_ERROR.inc()
                                DELTA_TIME.set(time.time_ns() - timestamp_client)
                                return "Errore nella query di ricerca città", 500
                                
                            riga = cursore_citta.fetchone()
                            
                            if not riga:
                                logger.error("Non esiste una città con questa latitudine e longitudine\n")
                                # Inserisci una nuova città
                                risultato_inserimento = esegui_query(
                                    connessione_SGM,
                                    query="INSERT INTO citta (città, latitudine, longitudine, codice_postale, codice_stato) VALUES (%s, %s, %s, %s, %s)",
                                    parametri=(nome_città, str(latitudine_arrotondata), str(longitudine_arrotondata), codice_postale, codice_stato),
                                    commit=True,
                                    istogramma=QUERY_DURATIONS_HISTOGRAM
                                )
                                
                                if not risultato_inserimento:
                                    FAILURE.inc()
                                    INTERNAL_ERROR.inc()
                                    DELTA_TIME.set(time.time_ns() - timestamp_client)
                                    return "Errore nell'inserimento della nuova città", 500
                                    
                                # Ottieni l'ID della città appena inserita
                                id_città = risultato_inserimento.lastrowid
                                logger.info("Nuova città inserita correttamente!\n")
                            else:
                                id_città = riga[0]  # ID città = primo elemento della tupla
                            
                            #AGGIORNAMENTO O INSERIMENTO DEI VINCOLI

                            # Verifica se esistono già vincoli per questo utente e questa città
                            cursore_vincoli = esegui_query(
                                connessione_SGM,
                                query="SELECT * FROM vincoli_utente WHERE id_utente = %s AND id_città = %s",
                                parametri=(str(id_utente), str(id_città)),
                                istogramma=QUERY_DURATIONS_HISTOGRAM
                            )
                            
                            if not cursore_vincoli:
                                FAILURE.inc()
                                INTERNAL_ERROR.inc()
                                DELTA_TIME.set(time.time_ns() - timestamp_client)
                                return "Errore nella query di ricerca vincoli", 500
                                
                            risultato_vincoli = cursore_vincoli.fetchone()
                            
                            if risultato_vincoli:
                                # Aggiorna i vincoli esistenti
                                risultato_aggiornamento_regole = esegui_query(
                                    connessione_SGM,
                                    query="UPDATE vincoli_utente SET regole = %s WHERE id_utente = %s AND id_città = %s",
                                    parametri=(vincoli_json, str(id_utente), str(id_città)),
                                    commit=True,
                                    istogramma=QUERY_DURATIONS_HISTOGRAM
                                )
                                
                                if not risultato_aggiornamento_regole:
                                    FAILURE.inc()
                                    INTERNAL_ERROR.inc()
                                    DELTA_TIME.set(time.time_ns() - timestamp_client)
                                    return "Errore nell'aggiornamento delle regole", 500
                                
                                risultato_aggiornamento_periodo = esegui_query(
                                    connessione_SGM,
                                    query="UPDATE vincoli_utente SET periodo_trigger = %s WHERE id_utente = %s AND id_città = %s",
                                    parametri=(str(periodo_trigger), str(id_utente), str(id_città)),
                                    commit=True,
                                    istogramma=QUERY_DURATIONS_HISTOGRAM
                                )
                                
                                if not risultato_aggiornamento_periodo:
                                    FAILURE.inc()
                                    INTERNAL_ERROR.inc()
                                    DELTA_TIME.set(time.time_ns() - timestamp_client)
                                    return "Errore nell'aggiornamento del periodo trigger", 500
                                    
                                DELTA_TIME.set(time.time_ns() - timestamp_client)
                                return "Vincoli utente aggiornati correttamente!", 200
                            else:
                                # Inserisci nuovi vincoli
                                risultato_inserimento = esegui_query(
                                    connessione_SGM,
                                    query="INSERT INTO vincoli_utente (id_utente, id_città, regole, timestamp, periodo_trigger, controllato) VALUES(%s, %s, %s, CURRENT_TIMESTAMP, %s, FALSE)",
                                    parametri=(str(id_utente), str(id_città), vincoli_json, str(periodo_trigger)),
                                    commit=True,
                                    istogramma=QUERY_DURATIONS_HISTOGRAM
                                )
                                
                                if not risultato_inserimento:
                                    FAILURE.inc()
                                    INTERNAL_ERROR.inc()
                                    DELTA_TIME.set(time.time_ns() - timestamp_client)
                                    return "Errore nell'inserimento dei nuovi vincoli", 500
                                    
                                ACTIVE_RULES.inc()
                                DELTA_TIME.set(time.time_ns() - timestamp_client)
                                return "Nuovi vincoli utente inseriti correttamente!", 200
                        
                        finally:
                            # Assicurati che la connessione venga chiusa in ogni caso
                            chiudi_connessione_db(connessione_SGA)
                            chiudi_connessione_db(connessione_SGM)

                            
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

    @app.route('/elimina_vincoli_utente', methods=['POST'])
    def gestione_elimina_vincoli_utente():
        """
        Route per eliminare i vincoli di un utente per una specifica città.
        Usa il token JWT per autenticare l'utente.
        """
        # Incrementa la metrica delle richieste
        REQUEST.inc()
        # Verifica se i dati ricevuti sono in formato JSON
        if request.is_json:
            try:
                # Estrai i dati JSON
                dati_dict = request.get_json()
                logger.info("ELIMINA VINCOLI UTENTE PER CITTÀ \n\n Dati ricevuti: " + str(dati_dict))
                timestamp_client = dati_dict.get("timestamp_client")
                
                # Ottieni e verifica il token JWT
                intestazione_autorizzazione = request.headers.get('Authorization')
                payload, errore = verifica_token_jwt(intestazione_autorizzazione)

                if errore:
                    FAILURE.inc()
                    DELTA_TIME.set(time.time_ns() - timestamp_client)
                    return errore
                
                # Token valido, estrai email e procedi
                email_utente = payload.get('email')
                
                # Ottieni l'ID utente tramite chiamata API al servizio SGA
                id_utente = ottieni_id_utente_da_email(email_utente)
                
                if id_utente is None:
                    FAILURE.inc()
                    DELTA_TIME.set(time.time_ns() - timestamp_client)
                    return "Utente non trovato nel sistema", 401
                
                try:
                    # Inizializza la connessione al database SGM
                    connessione_SGM = inizializza_connessione_db(
                        host=HOSTNAME, 
                        porta=PORT, 
                        utente=USER, 
                        password=PASSWORD_DB, 
                        database=DATABASE_SGM
                    )
                    
                    if not connessione_SGM:
                        FAILURE.inc()
                        INTERNAL_ERROR.inc()
                        DELTA_TIME.set(time.time_ns() - timestamp_client)
                        return "Errore nella connessione al database", 500
                    
                    try:
                        #eliminazione dei vincoli
                        nome_città = dati_dict.get('città')[0]
                        latitudine = dati_dict.get('città')[1]
                        latitudine_arrotondata = round(latitudine, 3)
                        longitudine = dati_dict.get('città')[2]
                        longitudine_arrotondata = round(longitudine, 3)
                        codice_postale = dati_dict.get('città')[3]
                        codice_stato = dati_dict.get('città')[4]
                        
                        logger.info(
                            "CITTÀ  " + nome_città + '  ' + str(latitudine_arrotondata) + '  ' + 
                            str(longitudine_arrotondata) + '  ' + str(codice_postale) + '  ' + 
                            str(codice_stato) + "\n\n")
                        
                        # Recupera le informazioni sulla città
                        cursore_citta = esegui_query(
                            connessione_SGM,
                            query="SELECT * FROM citta WHERE ROUND(latitudine, 3) = %s AND ROUND(longitudine, 3) = %s AND città = %s",
                            parametri=(str(latitudine_arrotondata), str(longitudine_arrotondata), nome_città),
                            istogramma=QUERY_DURATIONS_HISTOGRAM
                        )
                        
                        if not cursore_citta:
                            FAILURE.inc()
                            INTERNAL_ERROR.inc()
                            DELTA_TIME.set(time.time_ns() - timestamp_client)
                            return "Errore nella query di ricerca città", 500
                            
                        riga_citta = cursore_citta.fetchone()
                        
                        if not riga_citta:
                            logger.error("Non esiste una città con questa latitudine e longitudine\n")
                            FAILURE.inc()
                            DELTA_TIME.set(time.time_ns() - timestamp_client)
                            return "Errore, non esistono città da eliminare con questi parametri", 400
                        
                        id_città = riga_citta[0]
                        
                        # Elimina i vincoli dell'utente per questa città
                        risultato = esegui_query(
                            connessione_SGM,
                            query="DELETE FROM vincoli_utente WHERE id_utente = %s AND id_città = %s",
                            parametri=(str(id_utente), str(id_città)),
                            commit=True,
                            istogramma=QUERY_DURATIONS_HISTOGRAM
                        )
                        
                        if not risultato:
                            FAILURE.inc()
                            INTERNAL_ERROR.inc()
                            DELTA_TIME.set(time.time_ns() - timestamp_client)
                            return "Errore nell'eliminazione dei vincoli utente", 500
                            
                        ACTIVE_RULES.dec()
                        DELTA_TIME.set(time.time_ns() - timestamp_client)
                        return "Vincoli utente eliminati correttamente", 200
                    
                    finally:
                        # Assicurati che la connessione venga chiusa in ogni caso
                        chiudi_connessione_db(connessione_SGM)
                        
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

    @app.route('/mostra_regole', methods=['GET'])
    def gestione_mostra_regole():
        """
        Route per visualizzare tutte le regole di un utente.
        Usa il token JWT per autenticare l'utente.
        """
        # Incrementa la metrica delle richieste
        REQUEST.inc()
        # Verifica se i dati ricevuti sono in formato JSON
        if request.is_json:
            try:
                # Estrai i dati JSON
                dati_dict = request.get_json()
                logger.info("Dati ricevuti:" + str(dati_dict))
                if dati_dict:
                    timestamp_client = dati_dict.get("timestamp_client")
                    
                    # Ottieni e verifica il token JWT
                    intestazione_autorizzazione = request.headers.get('Authorization')
                    payload, errore = verifica_token_jwt(intestazione_autorizzazione)

                    if errore:
                        FAILURE.inc()
                        DELTA_TIME.set(time.time_ns() - timestamp_client)
                        return errore

                    # Token valido, estrai email e procedi
                    email_utente = payload.get('email')
                    
                    # Ottieni l'ID utente tramite chiamata API al servizio SGA
                    id_utente = ottieni_id_utente_da_email(email_utente)
                    
                    if id_utente is None:
                        FAILURE.inc()
                        DELTA_TIME.set(time.time_ns() - timestamp_client)
                        return "Utente non trovato nel sistema", 401
                    
                    try:
                        # Inizializza la connessione al database SGM per le regole
                        connessione_SGM = inizializza_connessione_db(
                            host=HOSTNAME, 
                            porta=PORT, 
                            utente=USER, 
                            password=PASSWORD_DB, 
                            database=DATABASE_SGM
                        )
                        
                        if not connessione_SGM:
                            FAILURE.inc()
                            INTERNAL_ERROR.inc()
                            DELTA_TIME.set(time.time_ns() - timestamp_client)
                            return "Errore nella connessione al database", 500
                        
                        try:
                            # Recupera tutte le regole dell'utente
                            cursore_vincoli = esegui_query(
                                connessione_SGM,
                                query="SELECT id_città, regole, periodo_trigger FROM vincoli_utente WHERE id_utente = %s",
                                parametri=(str(id_utente),),
                                istogramma=QUERY_DURATIONS_HISTOGRAM
                            )
                            
                            if not cursore_vincoli:
                                FAILURE.inc()
                                INTERNAL_ERROR.inc()
                                DELTA_TIME.set(time.time_ns() - timestamp_client)
                                return "Errore nella query di ricerca vincoli", 500
                                
                            righe = cursore_vincoli.fetchall()
                            
                            if not righe:
                                FAILURE.inc()
                                DELTA_TIME.set(time.time_ns() - timestamp_client)
                                return "Non ci sono regole inserite! Inserisci prima città, regole e periodo trigger!", 400
                            
                            # Costruisci la lista delle regole
                            lista_regole = []
                            for riga in righe:
                                id_città = riga[0]
                                regole = riga[1]
                                periodo_trigger = riga[2]
                                
                                # Query per ottenere informazioni sulla città
                                cursore_città = esegui_query(
                                    connessione_SGM,
                                    query="SELECT città, codice_postale, codice_stato FROM citta WHERE id = %s",
                                    parametri=(str(id_città),),
                                    istogramma=QUERY_DURATIONS_HISTOGRAM
                                )
                                
                                if not cursore_città:
                                    continue
                                    
                                riga_città = cursore_città.fetchone()
                                if not riga_città:
                                    continue
                                    
                                temp_lista = []
                                
                                # Informazioni sulla città
                                dict_città = {
                                    "città": riga_città
                                }
                                temp_lista.append(dict_città)
                                
                                # Dizionario delle regole
                                dict_regole = json.loads(regole)
                                temp_lista.append(dict_regole)
                                
                                # Dizionario del periodo trigger
                                dict_periodo_trigger = {
                                    "periodo_trigger": periodo_trigger
                                }
                                temp_lista.append(dict_periodo_trigger)
                                
                                lista_regole.append(temp_lista)
                            
                            #Formatta la risposta
                            regole_formattate = formatta_risposta_regole(lista_regole)
                            DELTA_TIME.set(time.time_ns() - timestamp_client)
                            return f"LE TUE REGOLE: <br><br> {regole_formattate}", 200
                        
                        finally:
                            # Assicurati che la connessione venga chiusa
                            chiudi_connessione_db(connessione_SGM)
                    
                    except Exception as err:
                        logger.error(f"Eccezione sollevata! -> {err}")
                        FAILURE.inc()
                        INTERNAL_ERROR.inc()
                        DELTA_TIME.set(time.time_ns() - timestamp_client)
                        return f"Errore nel database: {str(err)}", 500
        
            except Exception as e:
                FAILURE.inc()
                return f"Errore nella lettura dei dati: {str(e)}", 400
        else:
            FAILURE.inc()
            return "Errore: la richiesta deve essere in formato JSON", 400

    @app.route('/metriche', methods=['GET'])
    def metriche():
        # Export all the metrics as text for Prometheus
        return Response(generate_latest(REGISTRY), mimetype='text/plain')
    
    return app

# create Flask application
app = crea_server()

if __name__ == '__main__':

    # CREAZIONE TABELLE NEL DATABASE
    try:
        # Inizializza la connessione al database
        connessione = inizializza_connessione_db(
            host=HOSTNAME, 
            porta=PORT, 
            utente=USER, 
            password=PASSWORD_DB, 
            database=DATABASE_SGM
        )
        
        if not connessione:
            sys.exit("User Manager terminating: impossibile connettersi al database\n")
        
        # creazione tabella città
        citta_cursor = esegui_query(
            connessione=connessione,
            crea_tabella=True,
            nome_tabella="citta",
            definizione_colonne="id INTEGER PRIMARY KEY AUTO_INCREMENT, città VARCHAR(100) NOT NULL, latitudine FLOAT NOT NULL, longitudine FLOAT NOT NULL, codice_postale VARCHAR(10) NOT NULL, codice_stato VARCHAR(70) NOT NULL, UNIQUE KEY posizione (città, latitudine, longitudine)",
            istogramma=QUERY_DURATIONS_HISTOGRAM
        )
        if not citta_cursor:
            sys.exit("User Manager terminating: impossibile creare la tabella utenti\n")

        # creazione tabella vincoli_utente
        citta_cursor = esegui_query(
            connessione=connessione,
            crea_tabella=True,
            nome_tabella="vincoli_utente",
            definizione_colonne="id INTEGER PRIMARY KEY AUTO_INCREMENT, id_utente INTEGER NOT NULL, id_città INTEGER NOT NULL, regole JSON NOT NULL, timestamp TIMESTAMP NOT NULL, periodo_trigger INTEGER NOT NULL, controllato BOOLEAN NOT NULL DEFAULT FALSE, FOREIGN KEY (id_città) REFERENCES citta(id), UNIQUE KEY utente_città_id (id_utente, id_città)",
            istogramma=QUERY_DURATIONS_HISTOGRAM
        )
        if not citta_cursor:
            sys.exit("User Manager terminating: impossibile creare la tabella vincoli_utente\n")
    
    except Exception as err:
        sys.stderr.write("Exception raised! -> " + str(err) + "\n")
        sys.exit("User Manager terminating after an error...\n")
    finally:
        if 'connessione' in locals() and connessione:
            chiudi_connessione_db(connessione)

    # KAFKA
    producer_conf = {'bootstrap.servers': KAFKA_BROKER_1, 'acks': 1}  # 1 ==> Conferma solo dal server leader
    producer_kafka = confluent_kafka.Producer(**producer_conf)
    admin_conf = {'bootstrap.servers': KAFKA_BROKER_1} # Conf. dell'admin client
    kadmin = AdminClient(admin_conf) # Creazione client amministrativo

    # Creazione del topic aggiornamento_eventi se non esiste
    lista_topic_metadata = kadmin.list_topics()  # Ottiene la lista dei topic esistenti
    topics = lista_topic_metadata.topics # Dizionario dei topic
    topic_names = set(topics.keys())  

    logger.info(f"LIST_TOPICS: {lista_topic_metadata}")
    logger.info(f"TOPICS: {topics}")
    logger.info(f"TOPIC_NAMES: {topic_names}")

    trovato = False
    for name in topic_names:
        if name == KAFKA_TOPIC_1:
            trovato = True
    if trovato == False:
        # Topic non trovato, lo crea con 6 partizioni e fattore di replica 1
        nuovo_topic = NewTopic(KAFKA_TOPIC_1, 6, 1)  
        kadmin.create_topics([nuovo_topic, ])

    
    # Ricerca eventi da inviare già presenti nel DB
    Kafka_lista_messaggi = recupera_vincoli_pendenti()
    if Kafka_lista_messaggi != False:
        for message in Kafka_lista_messaggi:
            while produci_messaggio_kafka(KAFKA_TOPIC_1, producer_kafka, message) == False:
                pass  # Riprova finché non ha successo
    else:
        sys.exit("Errore nel trovare il lavoro in sospeso!")


    evento_timer_scaduto = threading.Event()

    logger.info("Avvio thread timer!\n")
    threadTimer = threading.Thread(target=timer, args=(INTERVALLO_PRODUZIONE_NOTIFICHE_KAFKA, evento_timer_scaduto))
    threadTimer.daemon = True
    threadTimer.start()
    
    logger.info("Avvio thread API Gateway!\n")
    threadAPIGateway = threading.Thread(target=avvia_server)
    threadAPIGateway.daemon = True
    threadAPIGateway.start()
    
    """
    #Decommentare se è necessario un thread per comunicare con User Manager
    logger.info("Avvio thread User Manager!\n")
    threadUM = threading.Thread(target=comunica_con_um)
    threadUM.daemon = True
    threadUM.start()
    """

    try:
        while True:
            # attendi evento dal timer
            evento_timer_scaduto.wait()
            evento_timer_scaduto.clear()
            
            # verifica nel DB la presenza di vincoli da inviare
            Kafka_lista_messaggi = recupera_vincoli_pendenti()
            if Kafka_lista_messaggi != False:
                for messaggio in Kafka_lista_messaggi:
                    while produci_messaggio_kafka(KAFKA_TOPIC_1, producer_kafka, messaggio) == False:
                        pass  # Riprova finché non ha successo
            else:
                logger.error("Errore nel trovare il lavoro in sospeso!")
    except KeyboardInterrupt:
        logger.info("Interruzione ricevuta, chiusura in corso...")
        sys.exit(0)