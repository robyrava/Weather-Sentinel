import sys
import os
# percorso della directory contenente config.py e db.py al sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config import*
from db import*
from utility import*

import threading
import time
import requests
import json
import logging

# Configurazione logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Variabile globale per controllare l'esecuzione del thread
thread_notificatore_attivo = True

class ThreadNotificatore(threading.Thread):
    def __init__(self, intervallo, id_monitoraggio):
        """
        Inizializza il thread di notifica.
        
        Args:
            intervallo: Intervallo in secondi tra i controlli della tabella eventi_da_notificare
            id_monitoraggio: ID del monitoraggio da controllare
        """
        threading.Thread.__init__(self)
        self.daemon = True  # Il thread terminerà quando il programma principale termina
        self.intervallo = intervallo
        self.id_monitoraggio = id_monitoraggio
        
    def run(self):
        """
        Esegue il ciclo di controllo degli eventi da notificare a intervalli regolari.
        """
        logger.info(f"\nThread di notifica avviato (intervallo: {self.intervallo} secondi)\n")
        
        while thread_notificatore_attivo:
            try:
                self.controlla_eventi_da_notificare()
            except Exception as e:
                logger.error(f"\nErrore nel thread di notifica: {e}\n")
            
            # Aspetta per l'intervallo specificato
            time.sleep(self.intervallo)
        
        logger.info("\nThread di notifica terminato\n")
    
    def ottieni_email_utente(self, id_utente):
        """
        Ottiene l'email dell'utente dal servizio SGA dato l'ID utente.
        
        Args:
            id_utente: ID dell'utente di cui recuperare l'email
            
        Returns:
            str: Email dell'utente se trovata
            None: Se l'email non è stata trovata o si è verificato un errore
        """
        try:
            # URL dell'endpoint del SGA per recuperare l'email
            url = f"http://{SGA_HOST}:{PORTA_SGA}/utente/{id_utente}/email"
            
            # Esegui la richiesta GET
            risposta = requests.get(url)
            
            # Verifica se la richiesta è andata a buon fine
            if risposta.status_code == 200:
                # Estrai l'email dalla risposta JSON
                dati_risposta = risposta.json()
                email = dati_risposta.get('email')
                logger.info(f"\nRecuperata email per l'utente {id_utente}: {email}\n")
                return email
            else:
                logger.error(f"\nErrore nel recupero dell'email per l'utente {id_utente}. Codice: {risposta.status_code}, Risposta: {risposta.text}\n")
                return None
                
        except Exception as e:
            logger.error(f"\nEccezione durante il recupero dell'email per l'utente {id_utente}: {e}\n")
            return None
    
    def invia_notifica_email(self, email, dati_evento):
        """
        Simula l'invio di una notifica email all'utente.
        
        Args:
            email: Email dell'utente a cui inviare la notifica
            dati_evento: Dati dell'evento da notificare
            
        Returns:
            bool: True se la notifica è stata inviata con successo, False altrimenti
        """
        try:
            # Qui dovresti implementare la logica per inviare effettivamente un'email
            # Ad esempio, potresti usare smtplib o un servizio di invio email di terze parti
            
            # Per ora, simula l'invio con un log
            logger.info(f"\nINVIO EMAIL a {email}: Notifica evento meteo: {dati_evento}\n")
            
            return True
            
        except Exception as e:
            logger.error(f"\nErrore nell'invio dell'email a {email}: {e}\n")
            return False
    
    def controlla_eventi_da_notificare(self):
        """
        Controlla se ci sono eventi da notificare nel database e aggiorna il flag 'notificato'.
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
                logger.error("\nImpossibile connettersi al database per controllare gli eventi da notificare\n")
                return
            
            try:
                # Recupera tutti gli eventi non ancora notificati
                cursore = esegui_query(
                    connessione=connessione,
                    query="SELECT id, id_utente, eventi FROM eventi_da_notificare WHERE id_monitoraggio = %s AND notificato = false",
                    parametri=(self.id_monitoraggio,),
                )
                
                if not cursore:
                    logger.error("\nErrore nel recupero degli eventi da notificare\n")
                    return

                # Recupera tutte le righe
                risultati = cursore.fetchall()
                
                # Se non ci sono risultati, non fare nulla
                if not risultati or len(risultati) == 0:
                    logger.info("\nNessun evento da notificare trovato\n")
                    return
                
                logger.info(f"\nTrovati {len(risultati)} eventi da notificare\n")
                
                id_eventi_da_aggiornare = []
                
                # Elabora ogni evento
                for risultato in risultati:
                    id_evento = risultato[0]
                    id_utente = risultato[1]
                    dati_evento = risultato[2]
                    
                    try:
                        # Recupera l'email dell'utente
                        email = self.ottieni_email_utente(id_utente)
                        
                        if email:
                            # Invia la notifica all'utente
                            notifica_inviata = self.invia_notifica_email(email, dati_evento)
                            
                            if notifica_inviata:
                                # Aggiungi l'ID alla lista per l'aggiornamento
                                id_eventi_da_aggiornare.append(id_evento)
                                logger.info(f"\nNotifica inviata con successo all'utente {id_utente} ({email})\n")
                            else:
                                logger.error(f"\nFallimento nell'invio della notifica all'utente {id_utente} ({email})\n")
                        else:
                            logger.error(f"\nImpossibile recuperare l'email per l'utente {id_utente}\n")
                            
                    except Exception as e:
                        logger.error(f"\nErrore nella notifica dell'evento ID {id_evento}: {e}\n")
                        # Continua con il prossimo evento
                
                # Aggiorna tutti gli eventi elaborati
                if id_eventi_da_aggiornare:
                    # Crea stringa di placeholders per la query IN
                    placeholders = ', '.join(['%s'] * len(id_eventi_da_aggiornare))
                    
                    # Esegui la query di aggiornamento per tutti gli eventi
                    risultato_aggiornamento = esegui_query(
                        connessione=connessione,
                        query=f"UPDATE eventi_da_notificare SET notificato = true WHERE id IN ({placeholders})",
                        parametri=tuple(id_eventi_da_aggiornare),
                        commit=True
                    )
                    
                    if risultato_aggiornamento:
                        logger.info(f"\nAggiornati {len(id_eventi_da_aggiornare)} eventi nel database (notificato = true)\n")
                    else:
                        logger.error("\nErrore nell'aggiornamento degli eventi nel database\n")
                
            finally:
                chiudi_connessione_db(connessione)
                
        except Exception as err:
            logger.error(f"\nErrore nel controllo degli eventi da notificare: {err}\n")

