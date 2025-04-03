import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from db import*
from utility import*

import mysql.connector
import jwt
import socket
from flask import Flask
from flask import request
import hashlib
import datetime
from prometheus_api_client import PrometheusConnect, MetricRangeDataFrame
from prometheus_api_client.utils import parse_datetime
import logging
from statsmodels.tsa.holtwinters import ExponentialSmoothing
import matplotlib.pyplot as plt
from io import BytesIO

#CONFIGURAZIONE VARIABILI D'AMBIENTE
DB_HOSTNAME = os.getenv('HOSTNAME')
DB_PORT = os.getenv('PORT')
DB_USER = os.getenv('USER')
DB_PASSWORD = os.getenv('PASSWORD_DB', '1234')
DB_DATABASE = os.getenv('DATABASE_GLS', 'GLS_db')
HOST = os.getenv('HOST', '0.0.0.0')
PORTA_GLS = os.getenv('PORTA_GLS', '50056')
PORTA_SGA = os.getenv('PORTA_SGA', '50053')
ADMIN_EMAIL = os.getenv('ADMIN_EMAIL')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD')
PROMETHEUS_URL = os.getenv('PROMETHEUS_URL', 'http://prometheus:9090')


#metriche
ISTOGRAMMA_DURATA_QUERY = Histogram('durata_query_nanosecondi_DB_GLS', 'Durata delle query al database in nanosecondi', buckets=[5000000, 10000000, 25000000, 50000000, 75000000, 100000000, 250000000, 500000000, 750000000, 1000000000, 2500000000, 5000000000, 7500000000, 10000000000])




logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def calcola_hash(input):
    sha256_hash = hashlib.sha256()
    sha256_hash.update(input.encode('utf-8'))
    hash_result = sha256_hash.hexdigest()
    return hash_result

def autentica(intestazione_autorizzazione):
    """
    Verifica la validità del token JWT nell'intestazione di autorizzazione.
    Restituisce:
    - 0 se l'autenticazione ha successo
    - -1 se il token è scaduto
    - -2 se c'è un errore di comunicazione con il database
    - -3 se il token non è valido
    """
    token = intestazione_autorizzazione.split(' ')[1]
    
    try:
        # Decodifica il token per ottenere l'email, senza verificare la firma
        payload = jwt.decode(token, options={"verify_signature": False})
        email = payload.get('email')
        
        if not email:
            return -3
        
        # Controlla se l'utente esiste nel database
        connessione = inizializza_connessione_db(
            host=DB_HOSTNAME, 
            porta=DB_PORT, 
            utente=DB_USER, 
            password=DB_PASSWORD, 
            database=DB_DATABASE
        )
        
        if not connessione:
            return -2
            
        try:
            cursore = esegui_query(
                connessione=connessione,
                query="SELECT password FROM admins WHERE email = %s",
                parametri=(email,),
                istogramma=ISTOGRAMMA_DURATA_QUERY
            )
            
            if not cursore:
                return -2
                
            risultato = cursore.fetchone()
            
            if not risultato:
                return -3
                
            # Ottieni l'hash della password per verificare il token
            password_hash = risultato[0]
            
            try:
                # Verifica il token con la chiave corretta
                jwt.decode(token, password_hash, algorithms=['HS256'])
                return 0  # Autenticazione riuscita
            except jwt.ExpiredSignatureError:
                return -1  # Token scaduto
            except:
                return -3  # Token non valido
                
        finally:
            chiudi_connessione_db(connessione)
            
    except Exception as err:
        logger.error(f"Errore durante l'autenticazione: {err}")
        return -3  # Qualsiasi altro errore è considerato token non valido

def verifica_autorizzazione(request):
    """
    Verifica l'autorizzazione della richiesta.
    
    Args:
        request: La richiesta HTTP
    
    Returns:
        True se l'autenticazione è riuscita, altrimenti una tupla (messaggio, codice_http)
    """
    intestazione_autorizzazione = request.headers.get('Authorization')
    if intestazione_autorizzazione and intestazione_autorizzazione.startswith('Bearer '):
        codice_risultato = autentica(intestazione_autorizzazione)
        if codice_risultato == -1:
            return 'Token JWT scaduto: login richiesto!', 401
        elif codice_risultato == -2:
            return 'Errore nella comunicazione con il DB per l\'autenticazione: riprova!', 500
        elif codice_risultato == -3:
            return 'Token JWT non valido: login richiesto!', 401
        else:
            return True
    else:
        # Nessun token fornito nell'intestazione di autorizzazione
        return 'Token JWT non fornito: login richiesto!', 401

def verifica_stato_violazione_metriche(righe_metriche):
    """
    Verifica lo stato attuale di tutte le metriche confrontando i valori attuali con i target.
    
    Args:
        righe_metriche: righe dal database con le informazioni sulle metriche
        
    Returns:
        Una stringa formattata con lo stato di ogni metrica
    """
    try:
        # Crea una connessione a Prometheus
        prometheus_client = PrometheusConnect(url=PROMETHEUS_URL, disable_ssl=True)
        
        risultato = ""
        
        for riga in righe_metriche:
            id_metrica, nome_metrica, min_target, max_target, periodo_stagionalita = riga
            risultato += f"<b>Metrica: {nome_metrica}</b><br>"
            risultato += f"Target minimo: {min_target}<br>"
            risultato += f"Target massimo: {max_target}<br>"
            
            # Ottieni il valore attuale della metrica da Prometheus
            try:
                # Query per ottenere il valore più recente
                query_result = prometheus_client.custom_query(query=f"{nome_metrica}")
                
                if query_result and len(query_result) > 0:
                    # Estrai il valore dalla risposta
                    valore_attuale = float(query_result[0]['value'][1])
                    
                    risultato += f"Valore attuale: {valore_attuale}<br>"
                    
                    # Verifica se il valore è fuori dai limiti
                    if valore_attuale < min_target:
                        risultato += f"<span style='color:red;'>VIOLAZIONE: Il valore è inferiore al target minimo!</span><br>"
                    elif valore_attuale > max_target:
                        risultato += f"<span style='color:red;'>VIOLAZIONE: Il valore è superiore al target massimo!</span><br>"
                    else:
                        risultato += "<span style='color:green;'>STATO: Normale</span><br>"
                else:
                    risultato += "<span style='color:orange;'>STATO: Nessun dato recente disponibile per questa metrica</span><br>"
            except Exception as err:
                logger.error(f"Errore nel recupero del valore attuale per la metrica {nome_metrica}: {err}")
                risultato += f"<span style='color:orange;'>ERRORE: Impossibile recuperare il valore attuale</span><br>"
            
            risultato += "<br>"
            
        return risultato
        
    except Exception as err:
        logger.error(f"Errore nella verifica dello stato delle metriche: {err}")
        return f"Errore nella verifica dello stato delle metriche: {str(err)}"

def conta_violazioni(righe_metriche, ore):
    """
    Conta le violazioni delle metriche in un determinato periodo di tempo.
    
    Args:
        righe_metriche: righe dal database con le informazioni sulle metriche
        ore: il periodo di tempo in ore da considerare
        
    Returns:
        Una stringa formattata con i risultati del conteggio delle violazioni
    """
    try:
        # Crea una connessione a Prometheus
        prometheus_client = PrometheusConnect(url=PROMETHEUS_URL, disable_ssl=True)
        
        ora_fine = parse_datetime("now")
        ora_inizio = parse_datetime(f"{ore}h")
        
        risultato = f"<b>Violazioni nelle ultime {ore} ore:</b><br>"
        
        for riga in righe_metriche:
            id_metrica, nome_metrica, min_target, max_target, periodo = riga
            
            try:
                # Ottieni i dati storici da Prometheus
                query_result = prometheus_client.get_metric_range_data(
                    metric_name=nome_metrica,
                    start_time=ora_inizio,
                    end_time=ora_fine,
                    step="1m"  # Campionamento ogni minuto
                )
                
                if query_result and len(query_result) > 0:
                    # Converti i dati in un DataFrame
                    df = MetricRangeDataFrame(query_result)
                    
                    # Conta le violazioni
                    if len(df.columns) > 1:  # Verifica che ci sia almeno una colonna di valori
                        valori = df.iloc[:, 1]  # Assume che la seconda colonna contenga i valori
                        
                        violazioni_min = sum(valori < min_target)
                        violazioni_max = sum(valori > max_target)
                        totale_violazioni = violazioni_min + violazioni_max
                        
                        if totale_violazioni > 0:
                            percentuale = (totale_violazioni / len(valori)) * 100
                            risultato += f"<span style='color:{'red' if percentuale > 25 else 'orange'};'>"
                            risultato += f"Metrica: {nome_metrica} - {totale_violazioni} violazioni ({percentuale:.2f}% del tempo)"
                            risultato += f" ({violazioni_min} sotto il minimo, {violazioni_max} sopra il massimo)</span><br>"
                        else:
                            risultato += f"<span style='color:green;'>Metrica: {nome_metrica} - Nessuna violazione</span><br>"
                    else:
                        risultato += f"Metrica: {nome_metrica} - Formato dati non valido<br>"
                else:
                    risultato += f"Metrica: {nome_metrica} - Nessun dato disponibile per questo periodo<br>"
            except Exception as err:
                logger.error(f"Errore nell'analisi delle violazioni per la metrica {nome_metrica}: {err}")
                risultato += f"Metrica: {nome_metrica} - Errore nell'analisi delle violazioni<br>"
        
        return risultato
    
    except Exception as err:
        logger.error(f"Errore nel conteggio delle violazioni: {err}")
        return f"Errore nel conteggio delle violazioni: {str(err)}"

def previsioni_metriche(metrica, minuti):
    """
    Genera previsioni per una metrica specifica e calcola la probabilità di violazioni future.
    
    Args:
        metrica: Riga dal database con le informazioni sulla metrica
        minuti: Numero di minuti per cui fare previsioni
        
    Returns:
        Tuple con stato, dati di addestramento, dati di test, previsione e stringa di probabilità
    """
    try:
        # Crea una connessione a Prometheus
        prometheus_client = PrometheusConnect(url=PROMETHEUS_URL, disable_ssl=True)
        
        nome_metrica = metrica[1]
        valore_target_min = metrica[2]
        valore_target_max = metrica[3]
        periodo_stagionalita = metrica[4]
        conteggio_violazioni = 0
        stringa_stato = ""
        
        ora_inizio = parse_datetime("1h")  # Dovrebbe essere aumentato con il sistema attivo più a lungo
        ora_fine = parse_datetime("now")
        
        # Ottieni i dati della metrica da Prometheus
        dati_metrica = prometheus_client.get_metric_range_data(
            metric_name=nome_metrica,
            start_time=ora_inizio,
            end_time=ora_fine,
        )
        
        if not dati_metrica:
            stringa_stato = f"La metrica {nome_metrica} non ha prodotto risultati. Verifica che il nome sia corretto o elimina la metrica errata e creane una nuova <br><br>"
            return "error", stringa_stato
            
        # Converti i dati in un DataFrame
        df_metrica = MetricRangeDataFrame(dati_metrica)
        logger.info("METRIC_DF\n" + str(df_metrica))
        
        # Estrai i valori
        lista_valori = df_metrica['value']
        logger.info("NUMERO DI VALORI NON PRESENTI: " + str(lista_valori.isna().sum()))
        logger.info("\nINDICI LISTA VALORI\n " + str(lista_valori.index))
        logger.info("\nLISTA VALORI\n " + str(lista_valori))
        
        # Ricampiona i dati ogni 30 secondi
        tsr = lista_valori.resample(rule='30s').mean()
        logger.info("\nTSR NUMERO DI VALORI NON PRESENTI: " + str(tsr.isna().sum()))
        
        # Interpola i valori mancanti
        tsr = tsr.interpolate()
        logger.info("\nTSR INTERPOLATO NUMERO DI VALORI NON PRESENTI: " + str(tsr.isna().sum()))
        logger.info("\nTSR\n " + str(tsr))
        logger.info("\nINDICI TSR\n " + str(tsr.index))
        
        # Dividi i dati in addestramento e test (90/10%)
        lunghezza_dataframe = len(df_metrica)
        fine = 0.9 * lunghezza_dataframe
        indice_fine = round(fine)
        dati_addestramento = tsr.iloc[:indice_fine]
        logger.info("DATI ADDESTRAMENTO NUMERO DI VALORI NON PRESENTI: " + str(dati_addestramento.isna().sum()))
        dati_test = tsr.iloc[indice_fine:]
        logger.info("DATI TEST NUMERO DI VALORI NON PRESENTI: " + str(dati_test.isna().sum()))
        logger.info("\nDATI TEST\n " + str(dati_test))
        
        # Crea il modello di previsione
        if periodo_stagionalita == 0 or periodo_stagionalita == 1:
            modello_ts = ExponentialSmoothing(dati_addestramento, trend='add').fit()
        else:
            modello_ts = ExponentialSmoothing(dati_addestramento, trend='add', seasonal="add",
                                         seasonal_periods=periodo_stagionalita).fit()
        
        # Converti il parametro minuti in intero
        try:
            minuti_int = int(minuti)
        except ValueError:
            return "parameter_error"
            
        logger.info("\nLUNGHEZZA DATI TEST " + str(len(dati_test)))
        
        # Calcola i passi per la previsione (2 passi per minuto, dato che ogni passo è di 30 secondi)
        passi = minuti_int * 2 + len(dati_test)
        
        # Genera la previsione
        previsione = modello_ts.forecast(passi)
        logger.info("\nPREVISIONE\n " + str(previsione))
        logger.info("\nTIPO PREVISIONE: " + str(type(previsione)))
        
        # Verifica le violazioni nelle previsioni
        try:
            for elemento in previsione:
                logger.info("\nELEMENTO DELLA PREVISIONE\n " + str(elemento))
                valore_attuale = float(elemento)
                logger.info(f"Metrica {nome_metrica} -> valore attuale: {valore_attuale}\n")
                
                if valore_attuale < valore_target_min or valore_attuale > valore_target_max:
                    conteggio_violazioni += 1
                    
            stringa_stato = f"Nome metrica: {nome_metrica} - Probabilità di violazioni nei prossimi {minuti} minuti: {str(100 * conteggio_violazioni / passi)}%"
            
        except ValueError:
            logger.error("Il valore attuale della metrica non è un numero decimale!")
            return "error", "ERRORE! C'È UNA METRICA I CUI VALORI NON SONO NUMERI DECIMALI!"
            
        return "ok", dati_addestramento, dati_test, previsione, stringa_stato
        
    except Exception as err:
        logger.error(f"Errore nella generazione delle previsioni: {err}")
        return "error", f"Errore nella generazione delle previsioni: {str(err)}"


def avvia_server():
    
    hostname = socket.gethostname()
    logger.info(f'Hostname: {hostname} -> server starting on port {str(PORTA_GLS)}')
    app.run(HOST, port=PORTA_GLS, threaded=True)

def crea_server():
    app = Flask(__name__)

    @app.route('/admin_login', methods=['POST'])
    def admin_login():
        # Verifica se i dati ricevuti sono in formato JSON
        if request.is_json:
            try:
                # Estrazione dei dati JSON
                data_dict = request.get_json()
                if data_dict:
                    email = data_dict.get("email")
                    logger.info("Email ricevuta:" + email)
                    password = data_dict.get("psw")
                    hash_psw = calcola_hash(password)  # nel DB abbiamo l'hash della password
                    
                    try:
                        # Utilizzo delle funzioni del modulo db per la connessione
                        connessione = inizializza_connessione_db(
                            host=DB_HOSTNAME, 
                            porta=DB_PORT, 
                            utente=DB_USER, 
                            password=DB_PASSWORD, 
                            database=DB_DATABASE
                        )
                        
                        if not connessione:
                            return "Errore di connessione al database", 500
                        
                        try:
                            # Verifica delle credenziali
                            cursore = esegui_query(
                                connessione=connessione,
                                query="SELECT email, password FROM admins WHERE email=%s AND password=%s",
                                parametri=(email, hash_psw),
                                istogramma=ISTOGRAMMA_DURATA_QUERY
                            )
                            
                            if not cursore:
                                return "Errore nella verifica delle credenziali", 500
                                
                            email_row = cursore.fetchone()
                            
                            if not email_row:
                                return "Email o password errate! Riprova!", 401
                            else:
                                # Generazione del token JWT
                                payload = {
                                    'email': email,
                                    'exp': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3)
                                }
                                token = jwt.encode(payload, hash_psw, algorithm='HS256')
                                return f"Login effettuato con successo! JWT Token: {token}", 200
                                
                        finally:
                            chiudi_connessione_db(connessione)
                            
                    except Exception as err:
                        logger.error("Eccezione sollevata! -> " + str(err) + "\n")
                        return f"Errore nella connessione al database: {str(err)}", 500

            except Exception as e:
                return f"Errore nella lettura dei dati: {str(e)}", 400
        else:
            return "Errore: la richiesta deve essere in formato JSON", 400
    
    @app.route('/aggiorna_metriche', methods=['POST'])
    def aggiorna_metriche():
        # Verifica se i dati ricevuti sono in formato JSON
        if request.is_json:
            try:
                # Estrazione dei dati JSON
                data_dict = request.get_json()
                logger.info("Dati ricevuti:" + str(data_dict))
                if data_dict:
                    # Verifica dell'autorizzazione
                    auth_result = verifica_autorizzazione(request)
                    if auth_result is not True:
                        return auth_result  # Restituisce (messaggio, codice_http)

                    # L'admin fornisce un JSON contenente coppie chiave-valore che hanno come chiave il nome della metrica
                    # e come valore la lista di [valore_target_min, valore_target_max, periodo]
                    nomi_metriche = data_dict.keys()
                    try:
                        # Utilizzo delle funzioni del modulo db per la connessione
                        connessione = inizializza_connessione_db(
                            host=DB_HOSTNAME, 
                            porta=DB_PORT, 
                            utente=DB_USER, 
                            password=DB_PASSWORD, 
                            database=DB_DATABASE
                        )
                        
                        if not connessione:
                            return "Errore di connessione al database", 500
                            
                        for metrica in nomi_metriche:
                            valore_min = data_dict.get(metrica)[0]
                            valore_max = data_dict.get(metrica)[1]
                            periodo_stagionalita = data_dict.get(metrica)[2]
                            
                            # Verifica se la metrica esiste già
                            cursore = esegui_query(
                                connessione=connessione,
                                query="SELECT * FROM metriche WHERE nome = %s",
                                parametri=(metrica,),
                                istogramma=ISTOGRAMMA_DURATA_QUERY
                            )
                            
                            if not cursore:
                                return "Errore nella verifica della metrica", 500
                                
                            riga = cursore.fetchone()
                            
                            if not riga:
                                logger.info("Non esiste una metrica con questo nome\n")
                                # Inserisci una nuova metrica
                                cursore = esegui_query(
                                    connessione=connessione,
                                    query="INSERT INTO metriche (nome, valore_target_min, valore_target_max, periodo) VALUES (%s, %s, %s, %s)",
                                    parametri=(metrica, valore_min, valore_max, periodo_stagionalita),
                                    commit=True,
                                    istogramma=ISTOGRAMMA_DURATA_QUERY
                                )
                                
                                if not cursore:
                                    return "Errore nell'inserimento della nuova metrica", 500
                                    
                                logger.info("Inserimento di una nuova metrica!\n")
                            else:
                                # Aggiorna la metrica esistente
                                cursore = esegui_query(
                                    connessione=connessione,
                                    query="UPDATE metriche SET valore_target_min = %s, valore_target_max = %s, periodo = %s WHERE nome = %s",
                                    parametri=(valore_min, valore_max, periodo_stagionalita, metrica),
                                    commit=True,
                                    istogramma=ISTOGRAMMA_DURATA_QUERY
                                )
                                
                                if not cursore:
                                    return "Errore nell'aggiornamento della metrica", 500
                                    
                                logger.info("Aggiornamento della tabella metriche!\n")
                        
                        return "Tabella metriche aggiornata correttamente!", 200
                        
                    except Exception as err:
                        logger.error("Eccezione sollevata! -> " + str(err) + "\n")
                        try:
                            if 'connessione' in locals() and connessione:
                                connessione.rollback()
                        except Exception as e:
                            logger.error(f"Eccezione sollevata durante il rollback: {e}\n")
                        return f"Errore nella connessione al database: {str(err)}", 500
                    finally:
                        if 'connessione' in locals() and connessione:
                            chiudi_connessione_db(connessione)

            except Exception as e:
                return f"Errore nella lettura dei dati: {str(e)}", 400
        else:
            return "Errore: la richiesta deve essere in formato JSON", 400    

    @app.route('/elimina_metriche', methods=['DELETE'])
    def elimina_metriche():
        # Verifica se i dati ricevuti sono in formato JSON (dovrebbe essere del tipo {"metriche": [nome_metrica1, nome_metrica2, ... ]})
        if request.is_json:
            try:
                # Estrazione dei dati JSON
                data_dict = request.get_json()
                logger.info("Dati ricevuti:" + str(data_dict))
                if data_dict:
                    # Verifica dell'autorizzazione
                    auth_result = verifica_autorizzazione(request)
                    if auth_result is not True:
                        return auth_result  # Restituisce (messaggio, codice_http)

                    lista_nomi_metriche = data_dict.get("metriche")
                    if not lista_nomi_metriche:
                        return "Errore: il campo 'metriche' è richiesto nel JSON", 400

                    try:
                        # Utilizzo delle funzioni del modulo db per la connessione
                        connessione = inizializza_connessione_db(
                            host=DB_HOSTNAME, 
                            porta=DB_PORT, 
                            utente=DB_USER, 
                            password=DB_PASSWORD, 
                            database=DB_DATABASE
                        )

                        if not connessione:
                            return "Errore di connessione al database", 500

                        stringa_risposta = ""
                        trovato = 0

                        for metrica in lista_nomi_metriche:
                            # Verifica se la metrica esiste
                            cursore = esegui_query(
                                connessione=connessione,
                                query="SELECT * FROM metriche WHERE nome = %s",
                                parametri=(metrica,),
                                istogramma=ISTOGRAMMA_DURATA_QUERY
                            )

                            if not cursore:
                                return "Errore nella verifica della metrica", 500

                            riga_metrica = cursore.fetchone()

                            if riga_metrica:
                                # Elimina la metrica
                                cursore = esegui_query(
                                    connessione=connessione,
                                    query="DELETE FROM metriche WHERE nome = %s",
                                    parametri=(metrica,),
                                    commit=True,
                                    istogramma=ISTOGRAMMA_DURATA_QUERY
                                )

                                if not cursore:
                                    return "Errore nell'eliminazione della metrica", 500

                                trovato = 1
                                logger.info(f"Metrica {metrica} eliminata con successo")
                            else:
                                stringa_risposta = stringa_risposta + f"La metrica {metrica} non è presente nel database! Pertanto, non può essere eliminata!<br>"

                        if stringa_risposta == "":
                            stringa_risposta = "Metriche eliminate correttamente!"
                        else:
                            if trovato == 1:
                                stringa_risposta = stringa_risposta + "Altre metriche eliminate correttamente!"

                        return stringa_risposta, 200

                    except Exception as err:
                        logger.error("Eccezione sollevata! -> " + str(err) + "\n")
                        try:
                            if 'connessione' in locals() and connessione:
                                connessione.rollback()
                        except Exception as e:
                            logger.error(f"Eccezione sollevata durante il rollback: {e}\n")
                        return f"Errore nella connessione al database: {str(err)}", 500
                    finally:
                        if 'connessione' in locals() and connessione:
                            chiudi_connessione_db(connessione)

            except Exception as e:
                return f"Errore nella lettura dei dati: {str(e)}", 400
        else:
            return "Errore: la richiesta deve essere in formato JSON", 400

    @app.route('/stato_metriche', methods=['GET'])
    def gestore_stato():
        """
        Gestisce lo stato delle metriche del sistema.
        Questa funzione rappresenta una route che verifica l'autorizzazione dell'utente,
        si connette al database per recuperare le metriche registrate, e valuta eventuali
        violazioni delle metriche. Restituisce un messaggio con lo stato delle metriche
        o un errore in caso di problemi.
        Funzionamento della route:
        - Verifica l'autorizzazione tramite la funzione `verifica_autorizzazione`.
        - Inizializza una connessione al database utilizzando i parametri di configurazione.
        - Esegue una query per recuperare tutte le metriche registrate.
        - Analizza le metriche per determinare eventuali violazioni.
        - Gestisce eventuali errori durante il processo, inclusi rollback in caso di eccezioni.
        Returns:
            tuple: Una tupla contenente un messaggio (str) e un codice HTTP (int).
        """

        # Verifica l'autorizzazione
        auth_result = verifica_autorizzazione(request)
        if auth_result is not True:
            return auth_result  # Restituisce (messaggio, codice_http)
        
        try:
            # Utilizzo delle funzioni del modulo db per la connessione
            connessione = inizializza_connessione_db(
                host=DB_HOSTNAME, 
                porta=DB_PORT, 
                utente=DB_USER, 
                password=DB_PASSWORD, 
                database=DB_DATABASE
            )
            
            if not connessione:
                return "Errore di connessione al database", 500
                
            try:
                # Recupera tutte le metriche
                cursore = esegui_query(
                    connessione=connessione,
                    query="SELECT * FROM metriche",
                    istogramma=ISTOGRAMMA_DURATA_QUERY
                )
                
                if not cursore:
                    return "Errore nel recupero delle metriche", 500
                    
                righe = cursore.fetchall()
                
                if not righe:
                    return "Non ci sono metriche registrate!", 200
                else:
                    risultato = verifica_stato_violazione_metriche(righe)
                    return f"STATO DELLE METRICHE: <br><br> {risultato}", 200
                    
            finally:
                chiudi_connessione_db(connessione)
                
        except Exception as err:
            logger.error("Eccezione sollevata! -> " + str(err) + "\n")
            try:
                if 'connessione' in locals() and connessione:
                    connessione.rollback()
            except Exception as e:
                logger.error(f"Eccezione sollevata durante il rollback: {e}\n")
            return f"Errore nella connessione al database: {str(err)}", 500

    @app.route('/violazioni_metriche', methods=['GET'])
    def gestore_violazioni_metriche():
        """
        Endpoint per verificare le violazioni delle metriche GLS in diversi periodi di tempo.
        Restituisce un rapporto sulle violazioni nelle ultime 1, 3 e 6 ore.
        """
        # Verifica l'autorizzazione
        auth_result = verifica_autorizzazione(request)
        if auth_result is not True:
            return auth_result  # Restituisce (messaggio, codice_http)
        
        try:
            # Utilizzo delle funzioni del modulo db per la connessione
            connessione = inizializza_connessione_db(
                host=DB_HOSTNAME, 
                porta=DB_PORT, 
                utente=DB_USER, 
                password=DB_PASSWORD, 
                database=DB_DATABASE
            )
            
            if not connessione:
                return "Errore di connessione al database", 500
                
            try:
                # Recupera tutte le metriche
                cursore = esegui_query(
                    connessione=connessione,
                    query="SELECT * FROM metriche",
                    istogramma=ISTOGRAMMA_DURATA_QUERY
                )
                
                if not cursore:
                    return "Errore nel recupero delle metriche", 500
                    
                righe = cursore.fetchall()
                
                if not righe:
                    return "Non ci sono metriche registrate!", 200
                else:
                    # Analizza le violazioni in diversi periodi di tempo
                    risultato_1ora = conta_violazioni(righe, 1)
                    risultato_3ore = conta_violazioni(righe, 3)
                    risultato_6ore = conta_violazioni(righe, 6)
                    
                    return f"METRICHE VIOLATE: <br><br> {risultato_1ora} <br><br> {risultato_3ore} <br><br> {risultato_6ore}", 200
                    
            finally:
                chiudi_connessione_db(connessione)
                
        except Exception as err:
            logger.error("Eccezione sollevata! -> " + str(err) + "\n")
            try:
                if 'connessione' in locals() and connessione:
                    connessione.rollback()
            except Exception as e:
                logger.error(f"Eccezione sollevata durante il rollback: {e}\n")
            return f"Errore nella connessione al database: {str(err)}", 500

    @app.route('/previsione_violazioni', methods=['GET'])
    def gestore_previsioni():
        """
        Endpoint per generare previsioni di violazioni per una specifica metrica.
        Restituisce un grafico delle previsioni e la probabilità di violazioni future.
        """
        # Ottieni i parametri dagli header o dalla query string
        minuti = request.args.get('minuti')
        nome_metrica = request.args.get('nome_metrica')
        
        if not minuti or not nome_metrica:
            return "Parametri mancanti: 'minuti' e 'nome_metrica' sono richiesti", 400
        
        # Verifica l'autorizzazione
        auth_result = verifica_autorizzazione(request)
        if auth_result is not True:
            return auth_result  # Restituisce (messaggio, codice_http)
        
        try:
            # Utilizzo delle funzioni del modulo db per la connessione
            connessione = inizializza_connessione_db(
                host=DB_HOSTNAME, 
                porta=DB_PORT, 
                utente=DB_USER, 
                password=DB_PASSWORD, 
                database=DB_DATABASE
            )
            
            if not connessione:
                return "Errore di connessione al database", 500
                
            try:
                # Recupera la metrica specificata
                cursore = esegui_query(
                    connessione=connessione,
                    query="SELECT * FROM metriche WHERE nome = %s",
                    parametri=(nome_metrica,),
                    istogramma=ISTOGRAMMA_DURATA_QUERY
                )
                
                if not cursore:
                    return "Errore nel recupero della metrica", 500
                    
                riga = cursore.fetchone()
                
                if not riga:
                    return f"Non esiste una metrica chiamata {nome_metrica}!", 200
                else:
                    # Genera le previsioni
                    risultato = previsioni_metriche(riga, minuti)
                    
                    if risultato == "parameter_error":
                        return f"Errore nei parametri: 'minuti' deve essere un intero", 400
                    elif risultato[0] == "error":
                        return risultato[1], 200
                    else:  # risultato[0] è "ok"
                        dati_addestramento = risultato[1]
                        dati_test = risultato[2]
                        previsione = risultato[3]
                        probabilita = risultato[4]
                        
                        # Crea il grafico
                        plt.figure(figsize=(24, 10))
                        plt.ylabel('Valori', fontsize=14)
                        plt.xlabel('Tempo', fontsize=14)
                        plt.title(probabilita, fontsize=16)
                        plt.plot(dati_addestramento, "-", label='addestramento')
                        plt.plot(dati_test, "-", label='reale')
                        plt.plot(previsione, "--", label='previsione')
                        plt.legend(title='Serie')
                        
                        # Salva il grafico in un buffer di memoria
                        buffer = BytesIO()
                        plt.savefig(buffer, format='png')
                        buffer.seek(0)
                        plt.close()
                        
                        # Invia l'immagine come risposta
                        return app.response_class(buffer.getvalue(), mimetype='image/png'), 200
                    
            finally:
                chiudi_connessione_db(connessione)
                
        except Exception as err:
            logger.error("Eccezione sollevata! -> " + str(err) + "\n")
            try:
                if 'connessione' in locals() and connessione:
                    connessione.rollback()
            except Exception as e:
                logger.error(f"Eccezione sollevata durante il rollback: {e}\n")
            return f"Errore nella connessione al database: {str(err)}", 500

    return app



# create Flask application
app = crea_server()


if __name__ == '__main__':

    # Creazione delle tabelle nel database
    try:
        # Inizializza la connessione al database
        connessione = inizializza_connessione_db(
            host=DB_HOSTNAME, 
            porta=DB_PORT, 
            utente=DB_USER, 
            password=DB_PASSWORD, 
            database=DB_DATABASE
        )
        
        if not connessione:
            sys.exit("Chiusura GLS: impossibile connettersi al database\n")
        
        # Creazione tabella metrics se non esiste
        metrics_cursor = esegui_query(
            connessione=connessione,
            crea_tabella=True,
            nome_tabella="metriche",
            definizione_colonne="id INTEGER PRIMARY KEY AUTO_INCREMENT, nome VARCHAR(100) UNIQUE NOT NULL, valore_target_min DOUBLE NOT NULL, valore_target_max DOUBLE NOT NULL, periodo INTEGER",
            istogramma=ISTOGRAMMA_DURATA_QUERY
        )
        
        if not metrics_cursor:
            sys.exit("Chiusura GLS: impossibile creare la tabella metriche\n")
        
        # Creazione tabella admins se non esiste
        admins_cursor = esegui_query(
            connessione=connessione,
            crea_tabella=True,
            nome_tabella="admins",
            definizione_colonne="id INTEGER PRIMARY KEY AUTO_INCREMENT, email VARCHAR(30) UNIQUE NOT NULL, password VARCHAR(64) NOT NULL",
            istogramma=ISTOGRAMMA_DURATA_QUERY
        )
        
        if not admins_cursor:
            sys.exit("Chiusura GLS: impossibile creare la tabella admins\n")
        
        # Verifica se c'è un admin predefinito
        admin_check = esegui_query(
            connessione=connessione,
            query="SELECT * FROM admins",
            istogramma=ISTOGRAMMA_DURATA_QUERY
        )
        
        if not admin_check:
            sys.exit("Chiusura GLS: impossibile verificare gli admin esistenti\n")
            
        result = admin_check.fetchall()
        
        # Se non ci sono admin, crea l'admin predefinito
        if not result:
            password_hash = calcola_hash(ADMIN_PASSWORD)
            admin_insert = esegui_query(
                connessione=connessione,
                query="INSERT INTO admins(email, password) VALUES(%s,%s)",
                parametri=(ADMIN_EMAIL, password_hash),
                commit=True,
                istogramma=ISTOGRAMMA_DURATA_QUERY
            )
            
            if not admin_insert:
                sys.exit("Chiusura GLS: impossibile creare l'admin predefinito\n")
                
            logger.info("Admin predefinito creato con successo")
    
    except Exception as err:
        sys.stderr.write(f"Exception raised! -> {str(err)}\n")
        try:
            if 'connessione' in locals() and connessione:
                connessione.rollback()
        except Exception as e:
            sys.stderr.write(f"Exception raised in rollback: {e}\n")
        sys.exit("Chiusura GLS dopo un errore...\n")
        
    finally:
        if 'connessione' in locals() and connessione:
            chiudi_connessione_db(connessione)

    # Avvio del server Flask
    avvia_server()