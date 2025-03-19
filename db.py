import time
import logging
import mysql.connector
from prometheus_client import Counter, generate_latest, REGISTRY, Gauge,Histogram

# Configurazione del logger
logger = logging.getLogger(__name__)

def inizializza_connessione_db(host, porta, utente, password, database):
    """Inizializza una connessione al database MySQL."""
    try:
        connessione = mysql.connector.connect(
            host=host, 
            port=porta, 
            user=utente, 
            password=password, 
            database=database
        )
        return connessione
    except mysql.connector.Error as err:
        logger.error(f"Errore di connessione al database: {err}")
        return None

def esegui_query(connessione, query=None, parametri=None, commit=False, istogramma=None, 
                crea_tabella=False, nome_tabella=None, definizione_colonne=None):
    """
    Esegue una query SQL con misurazione opzionale delle prestazioni.
    Può essere utilizzata anche per creare tabelle se crea_tabella=True.
    
    Args:
        connessione: Connessione al database
        query: Query SQL da eseguire (ignorata se crea_tabella=True)
        parametri: Parametri per la query (opzionale)
        commit: Se True, esegue il commit dopo la query
        istogramma: Istogramma Prometheus per misurare prestazioni (opzionale)
        crea_tabella: Se True, crea una tabella
        nome_tabella: Nome della tabella da creare (richiesto se crea_tabella=True)
        definizione_colonne: Definizione delle colonne (richiesto se crea_tabella=True)
    
    Returns:
        cursor: Cursore MySQL o None in caso di errore
    """
    cursore = None
    try:
        # Se richiesta creazione tabella, costruisci la query
        if crea_tabella:
            if not nome_tabella or not definizione_colonne:
                logger.error("Nome tabella e definizione colonne richiesti per creare una tabella")
                return None
            query = f"CREATE TABLE IF NOT EXISTS {nome_tabella} ({definizione_colonne})"
            commit = True  # La creazione tabella richiede sempre commit
        
        # Misura il tempo se l'istogramma è fornito
        tempo_inizio = time.time_ns() if istogramma else 0
        cursore = connessione.cursor()
        
        if parametri:
            cursore.execute(query, parametri)
        else:
            cursore.execute(query)
            
        if commit:
            connessione.commit()
            
        if istogramma:
            tempo_fine = time.time_ns()
            istogramma.observe(tempo_fine - tempo_inizio)
            
        return cursore
    except mysql.connector.Error as err:
        logger.error(f"Errore nell'esecuzione della query: {err}")
        if commit:
            try:
                connessione.rollback()
            except Exception as e:
                logger.error(f"Errore durante il rollback: {e}")
        return None

def chiudi_connessione_db(connessione):
    """Chiude la connessione al database."""
    if connessione:
        try:
            connessione.close()
        except Exception as e:
            logger.error(f"Errore durante la chiusura della connessione: {e}")

def verifica_utente(connessione, email, istogramma=None, restituisci_dettagli=False):
    """
    Verifica se esiste un utente con l'email specificata.
    
    Args:
        connessione: Connessione al database
        email: Email da verificare
        istogramma: Istogramma Prometheus per le prestazioni (opzionale)
        restituisci_dettagli: Se True, restituisce i dettagli completi dell'utente
        
    Returns:
        Se restituisci_dettagli=False:
            bool: True se l'utente esiste, False altrimenti
            None: In caso di errore
        Se restituisci_dettagli=True:
            dict: Dizionario con i dettagli dell'utente se esiste
            None: Se l'utente non esiste o in caso di errore
    """
    # Ottimizzazione: recupera solo le colonne necessarie se non si richiedono dettagli
    query = "SELECT id, email, password FROM utenti WHERE email=%s" if restituisci_dettagli else "SELECT 1 FROM utenti WHERE email=%s"
    
    cursore = esegui_query(
        connessione=connessione,
        query=query,
        parametri=(email,),
        istogramma=istogramma
    )
    
    if not cursore:
        return None
    
    result = cursore.fetchone()
    
    # Se l'utente non esiste
    if not result:
        return False if not restituisci_dettagli else None
        
    # Se è richiesto solo un booleano
    if not restituisci_dettagli:
        return True
        
    # Se sono richiesti i dettagli completi
    return {
        "id": result[0],
        "email": result[1],
        "password": result[2]
    }

def verifica_credenziali(connessione, email, password_hash, istogramma=None):
    """
    Verifica le credenziali di un utente.
    
    Args:
        connessione: Connessione al database
        email: Email dell'utente
        password_hash: Hash della password
        istogramma: Istogramma Prometheus per le prestazioni (opzionale)
        
    Returns:
        dict: Dati dell'utente se le credenziali sono corrette
        None: Se le credenziali sono errate o in caso di errore
    """
    cursore = esegui_query(
        connessione=connessione,
        query="SELECT id, email, password FROM utenti WHERE email=%s AND password=%s",
        parametri=(email, password_hash),
        istogramma=istogramma
    )
    
    if not cursore:
        return None
    
    result = cursore.fetchone()
    if not result:
        return None
        
    return {
        "id": result[0],
        "email": result[1],
        "password": result[2]
    }

def inserisci_utente(connessione, email, password_hash, istogramma=None):
    """
    Inserisce un nuovo utente nel database.
    
    Args:
        connessione: Connessione al database
        email: Email dell'utente
        password_hash: Hash della password
        istogramma: Istogramma Prometheus per le prestazioni (opzionale)
        
    Returns:
        bool: True se l'inserimento è avvenuto con successo, False altrimenti
    """
    cursore = esegui_query(
        connessione=connessione,
        query="INSERT INTO utenti (email, password) VALUES (%s,%s)",
        parametri=(email, password_hash),
        commit=True,
        istogramma=istogramma
    )
    
    return cursore is not None

def elimina_utente(connessione, email, istogramma=None):
    """
    Elimina un utente dal database usando solo l'email.
    
    Args:
        connessione: Connessione al database
        email: Email dell'utente
        istogramma: Istogramma Prometheus per le prestazioni (opzionale)
        
    Returns:
        bool: True se l'eliminazione è avvenuta con successo, False altrimenti
    """
    cursore = esegui_query(
        connessione=connessione,
        query="DELETE FROM utenti WHERE email=%s",
        parametri=(email,),
        commit=True,
        istogramma=istogramma
    )
    
    return cursore is not None