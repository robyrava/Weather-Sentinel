FROM nginx:1.21-alpine

# Installa strumenti di rete per debugging e health check
RUN apk add --no-cache iputils bind-tools netcat-openbsd

# Copia il file di configurazione
COPY nginx.conf /etc/nginx/nginx.conf

# Esporre la porta 80
EXPOSE 80

# Il comando di avvio viene sovrascitto nel docker-compose.yml
CMD ["nginx", "-g", "daemon off;"]