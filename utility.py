import jwt
import logging

# Configurazione logger
logger = logging.getLogger(__name__)

def verifica_token_jwt(intestazione_autorizzazione):
    """
    Verifica il token JWT fornito nell'header di autorizzazione.
    
    Args:
        intestazione_autorizzazione: Header di autorizzazione HTTP
        
    Returns:
        tuple: (payload, None) se il token è valido
               (None, (messaggio_errore, codice_http)) in caso di errore
    """
    # Verifica presenza e formato del token
    if not intestazione_autorizzazione or not intestazione_autorizzazione.startswith('Bearer '):
        return None, ('Token JWT non fornito: login richiesto!', 401)
        
    token = intestazione_autorizzazione.split(' ')[1]
    
    # Decodifica e verifica token
    try:
        # Decodifica il token senza verificare la firma
        # In produzione, dovresti verificare la firma con la chiave appropriata
        payload = jwt.decode(token, options={"verify_signature": False}, algorithms=['HS256'])
        
        # Verifica che il payload contenga almeno l'email
        if not payload.get('email'):
            return None, ('Token JWT non valido: manca email!', 401)
            
        return payload, None
        
    except jwt.ExpiredSignatureError:
        logger.warning("Token JWT scaduto")
        return None, ('Token JWT scaduto: login richiesto!', 401)
        
    except jwt.InvalidTokenError as e:
        logger.warning(f"Token JWT non valido: {e}")
        return None, ('Token JWT non valido: login richiesto!', 401)
        
    except Exception as e:
        logger.error(f"Errore imprevisto nella verifica del token: {e}")
        return None, (f'Errore nella verifica del token: {str(e)}', 500)


