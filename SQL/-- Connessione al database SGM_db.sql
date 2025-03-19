
-- Connessione al database SGM_db
--USE SGA_db;
USE SGM_db;


DELETE FROM utenti WHERE email = 'user1@example.com';
DELETE FROM utenti WHERE email = 'user2@example.com';

-- Inserimento utenti di prova (password: 'password123' -> hash = ef92b778bafe771e89245b89ecbc08a44a4e166c06659911881f383d4473e94f)
INSERT INTO utenti (email, password) VALUES 
('user1@example.com', 'ef92b778bafe771e89245b89ecbc08a44a4e166c06659911881f383d4473e94f'),
('user2@example.com', 'ef92b778bafe771e89245b89ecbc08a44a4e166c06659911881f383d4473e94f');

-- Inserisci le città
INSERT INTO citta (id, città, latitudine, longitudine, codice_postale, codice_stato) VALUES 
(1, 'Roma', 41.902, 12.496, '00100', 'IT'),
(2, 'Milano', 45.464, 9.190, '20100', 'IT'),
(3, 'Napoli', 40.851, 14.268, '80100', 'IT'),
(4, 'Catania', 37.508, 15.083, '95100', 'IT');

-- Inserisci vincoli per l'utente 12 (Roma)
INSERT INTO vincoli_utente (id_utente, id_città, regole, timestamp, periodo_trigger, controllato) VALUES 
(12, 1, '{"temp_max":"30", "temp_min":"10", "umidità_max":"80", "umidità_min":"30", "pressione_max":"1030", "pressione_min":"1000", "nuvole_max":"80", "nuvole_min":"10", "velocità_vento_max":"15", "velocità_vento_min":"0", "direzione_vento":"null", "pioggia":"1", "neve":"null"}', 
NOW(), 3600, FALSE);

-- Inserisci vincoli per l'utente 12 (Milano)
INSERT INTO vincoli_utente (id_utente, id_città, regole, timestamp, periodo_trigger, controllato) VALUES 
(12, 2, '{"temp_max":"28", "temp_min":"5", "umidità_max":"75", "umidità_min":"25", "pressione_max":"1025", "pressione_min":"995", "nuvole_max":"70", "nuvole_min":"20", "velocità_vento_max":"12", "velocità_vento_min":"2", "direzione_vento":"null", "pioggia":"null", "neve":"1"}', 
NOW(), 7200, FALSE);

-- Inserisci vincoli per l'utente 13 (Napoli)
INSERT INTO vincoli_utente (id_utente, id_città, regole, timestamp, periodo_trigger, controllato) VALUES 
(13, 3, '{"temp_max":"35", "temp_min":"15", "umidità_max":"85", "umidità_min":"40", "pressione_max":"1035", "pressione_min":"990", "nuvole_max":"60", "nuvole_min":"5", "velocità_vento_max":"20", "velocità_vento_min":"3", "direzione_vento":"SE", "pioggia":"1", "neve":"null"}', 
NOW(), 1800, TRUE);

-- Inserisci vincoli per l'utente 13 (Catania)
INSERT INTO vincoli_utente (id_utente, id_città, regole, timestamp, periodo_trigger, controllato) VALUES 
(13, 4, '{"temp_max":"38", "temp_min":"18", "umidità_max":"70", "umidità_min":"35", "pressione_max":"1020", "pressione_min":"995", "nuvole_max":"50", "nuvole_min":"10", "velocità_vento_max":"18", "velocità_vento_min":"5", "direzione_vento":"null", "pioggia":"null", "neve":"null"}', 
NOW(), 3600, FALSE);