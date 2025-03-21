USE SGM_db;

Delete from vincoli_utente where id_utente = 15;

INSERT INTO vincoli_utente (
    id,
    id_utente,
    id_città,
    regole,
    timestamp,
    periodo_trigger,
    controllato
)
VALUES (
    1,
    15,
    1,
    '{
        "neve": null,
        "pioggia": null,
        "umi_max": 40,
        "umi_min": 20,
        "temp_max": 10,
        "temp_min": 8,
        "nuvole_max": 80,
        "nuvole_min": 30,
        "pressione_max": 1025,
        "pressione_min": 1020,
        "vel_vento_max": 0.3,
        "vel_vento_min": 0,
        "direzione_vento": "N",
        "timestamp_client": 1742497240000000000
    }',
    '2025-03-20 19:16:35',
    10,
    1
);
