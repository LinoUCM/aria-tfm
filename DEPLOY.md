# Despliegue de ARIA en producción (VM Azure)

Guía paso a paso, **en orden**, para dejar ARIA corriendo de forma autónoma y
permanente en la VM de Azure con Docker Compose.

- **VM:** Ubuntu Server 24.04 LTS · IP pública estática `9.160.105.198`
- **Acceso SSH:** usuario `aria`, clave `~/.ssh/aria-tfm-vm_key.pem`
- **Puertos abiertos en Azure:** 22, 80, 443
- **Repo:** `git@github.com:LinoUCM/aria-tfm.git`, rama `master`
- **Arquitectura:** un único reverse proxy (Caddy) en `:80`/`:443`. Todo lo demás
  (backend, frontend, Postgres, Redis, ChromaDB, Ollama, n8n) vive en la red
  interna del compose y **no** se expone a Internet.

---

## 1. Conectarse a la VM

```bash
ssh -i ~/.ssh/aria-tfm-vm_key.pem aria@9.160.105.198
```

Todo lo que sigue se ejecuta **dentro de la VM** salvo que se indique
`(en tu máquina local)`.

---

## 2. Instalar Docker Engine + plugin Compose (Ubuntu 24.04)

Comandos oficiales de Docker (repositorio `apt` de Docker, **no** `snap`):

```bash
# Utilidades
sudo apt-get update
sudo apt-get install -y ca-certificates curl git

# Clave GPG del repo de Docker
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

# Repositorio
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Instalación
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Ejecutar docker sin sudo (cierra sesión y vuelve a entrar para que aplique)
sudo usermod -aG docker $USER
newgrp docker

# Comprobación
docker --version
docker compose version
docker run --rm hello-world
```

---

## 3. Clonar el repositorio privado (deploy key de solo lectura)

**No** se usa el token personal de Lino en el servidor. Se genera un par de
claves SSH dedicado en la VM y su pública se añade como *Deploy key* de solo
lectura al repo.

```bash
# En la VM
ssh-keygen -t ed25519 -C "aria-vm-deploy" -f ~/.ssh/aria_deploy -N ""
cat ~/.ssh/aria_deploy.pub
```

Copia esa línea y añádela en:
**https://github.com/LinoUCM/aria-tfm/settings/keys → “Add deploy key”**
- Title: `aria-azure-vm`
- Key: (la pública que acabas de imprimir)
- **Allow write access: NO** (solo lectura)

Configura SSH para usar esa clave con GitHub y clona:

```bash
cat >> ~/.ssh/config <<'EOF'

Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/aria_deploy
  IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config

ssh -T git@github.com          # debe decir: "Hi LinoUCM/aria-tfm! You've successfully authenticated..."

mkdir -p ~/apps && cd ~/apps
git clone git@github.com:LinoUCM/aria-tfm.git aria
cd ~/apps/aria
```

> Para **actualizar** más adelante: `git -C ~/apps/aria pull` (ver §11).

---

## 4. Crear los ficheros de entorno en el servidor (nunca por git)

Hay **dos** ficheros `.env`, ambos ignorados por git. Créalos en la VM:

### 4a. `./.env` (variables del compose)

```bash
cd ~/apps/aria
cp .env.example .env
nano .env      # edita SOLO el bloque "COMPOSE":
```

Ajusta como mínimo:

```
DOMAIN=                                   # vacío por ahora (aún no hay dominio)
PUBLIC_APP_URL=http://9.160.105.198
POSTGRES_PASSWORD=<una password fuerte>
N8N_ENCRYPTION_KEY=<openssl rand -hex 24>
```

### 4b. `./backend/.env` (secretos del backend)

El backend necesita las API keys reales. **Cópialo tú desde tu máquina** — no
debe pasar por git ni pegarse en claro en un canal inseguro:

```bash
# (en tu máquina local)
scp -i ~/.ssh/aria-tfm-vm_key.pem \
    ~/projects/aria/backend/.env \
    aria@9.160.105.198:~/apps/aria/backend/.env
```

> Alternativa: `nano ~/apps/aria/backend/.env` en la VM y pegarlo a mano.
>
> No hace falta tocar `DATABASE_URL`, `REDIS_URL`, `CHROMA_HOST/PORT`,
> `OLLAMA_BASE_URL`, `ENVIRONMENT` ni `FRONTEND_URL` en `backend/.env`:
> `docker-compose.prod.yml` los sobrescribe con la topología de la red de
> contenedores.

Comprueba permisos:

```bash
chmod 600 ~/apps/aria/.env ~/apps/aria/backend/.env
```

---

## 5. Construir y levantar

```bash
cd ~/apps/aria

# Build (la imagen del backend es grande — torch/transformers en requirements —
# la primera vez puede tardar 10-20 min).
docker compose -f docker-compose.prod.yml build

# Arranque
docker compose -f docker-compose.prod.yml up -d

# Estado
docker compose -f docker-compose.prod.yml ps
```

El `backend` puede reiniciarse un par de veces mientras Postgres/ChromaDB
terminan de estar `healthy`; con `restart: unless-stopped` se estabiliza solo.

---

## 6. Descargar el modelo de embeddings en Ollama  ⚠️ OBLIGATORIO

Un contenedor de Ollama nuevo **no trae ningún modelo**. ARIA usa `bge-m3` para
todos los embeddings del RAG. Si se omite este paso, **el arranque parece
correcto** pero cada búsqueda del RAG falla con un error de *model not found*
(no es un fallo visible de arranque).

```bash
docker compose -f docker-compose.prod.yml exec ollama ollama pull bge-m3

# Comprobar que está:
docker compose -f docker-compose.prod.yml exec ollama ollama list
# Debe aparecer una fila "bge-m3:latest".
```

> La descarga (~1.2 GB) queda en el volumen `ollamadata`; no se repite aunque se
> recree el contenedor.

---

## 7. Inicializar el esquema de la base de datos

**ARIA no usa Alembic.** El backend crea/actualiza el esquema solo, en el
arranque (`create_tables()` en `backend/core/database.py`: `Base.metadata.create_all()`
+ una migración ligera idempotente `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`).

- Si vas a **restaurar el dump** (paso 8), el dump ya trae el esquema completo;
  no hay que hacer nada aquí — `create_all()` verá que las tablas ya existen y
  no tocará nada.
- Si quisieras arrancar con **BD vacía**, basta con que el backend arranque una
  vez: el esquema se crea solo.

---

## 8. Migrar los datos actuales (Postgres + ChromaDB + uploads)

Para no arrancar con la plataforma vacía. Son **tres** conjuntos de datos
(mismo mecanismo verificado al mover Postgres nativo→Docker en local,
CONTEXTO_ARIA_TFM.md §12.1):

### 8a. Generar los volcados (en tu máquina local)

```bash
# (en tu máquina local, con los contenedores de desarrollo corriendo)
cd /tmp

# Postgres → SQL plano
docker exec aria-postgres-1 pg_dump -U aria -d aria_db --no-owner --no-privileges > aria_db.sql

# ChromaDB → tar de /data (chroma persiste en /data, no en /chroma/chroma)
docker exec aria-chromadb-1 tar czf - -C /data . > chroma_data.tgz

# Ficheros subidos a la KB
tar czf uploads.tgz -C ~/projects/aria/backend/uploads .

# (opcional) runbooks/post-mortems generados
tar czf storage.tgz -C ~/projects/aria/backend/storage .
```

### 8b. Subirlos a la VM

```bash
# (en tu máquina local)
scp -i ~/.ssh/aria-tfm-vm_key.pem \
    /tmp/aria_db.sql /tmp/chroma_data.tgz /tmp/uploads.tgz /tmp/storage.tgz \
    aria@9.160.105.198:~/
```

### 8c. Restaurar (en la VM)

```bash
cd ~/apps/aria
PROJ=$(docker compose -f docker-compose.prod.yml ls --format '{{.Name}}' | head -1)   # normalmente "aria"

# --- Postgres ---
# Recrea la BD limpia para una restauración determinista:
docker compose -f docker-compose.prod.yml exec -T postgres \
    psql -U aria -d postgres -c "DROP DATABASE IF EXISTS aria_db;" -c "CREATE DATABASE aria_db OWNER aria;"
docker compose -f docker-compose.prod.yml exec -T postgres \
    psql -U aria -d aria_db < ~/aria_db.sql

# --- ChromaDB ---  (parar chroma, sustituir el volumen, arrancar)
docker compose -f docker-compose.prod.yml stop chromadb
docker run --rm -v ${PROJ}_chromadata:/data -v ~/:/backup alpine \
    sh -c "rm -rf /data/* && tar xzf /backup/chroma_data.tgz -C /data"
docker compose -f docker-compose.prod.yml start chromadb

# --- uploads / storage ---
docker run --rm -v ${PROJ}_uploads:/u -v ~/:/backup alpine \
    sh -c "tar xzf /backup/uploads.tgz -C /u"
docker run --rm -v ${PROJ}_storage:/s -v ~/:/backup alpine \
    sh -c "tar xzf /backup/storage.tgz -C /s"   # si generaste storage.tgz

# Reiniciar el backend para que reconecte con la BD ya poblada
docker compose -f docker-compose.prod.yml restart backend
```

Comprobación rápida de conteos:

```bash
docker compose -f docker-compose.prod.yml exec -T postgres \
    psql -U aria -d aria_db -c \
    "select (select count(*) from users) users, (select count(*) from documents) docs,
            (select count(*) from incidents) incidents, (select count(*) from conversations) convs;"
```

---

## 9. Comprobar que todo funciona

```bash
cd ~/apps/aria

# Estado de contenedores (todos "Up", los que tienen healthcheck "healthy")
docker compose -f docker-compose.prod.yml ps

# Salud del backend (a través de Caddy, /api quita el prefijo)
curl -fsS http://9.160.105.198/api/health ; echo
#  → {"status":"ok","version":"1.0.0","environment":"production"}

# El frontend sirve HTML
curl -fsS http://9.160.105.198/login | head -c 300 ; echo

# Salud interna de cada servicio
docker compose -f docker-compose.prod.yml exec backend  curl -fsS http://localhost:8000/health ; echo
docker compose -f docker-compose.prod.yml exec postgres pg_isready -U aria
docker compose -f docker-compose.prod.yml exec redis    redis-cli ping
docker compose -f docker-compose.prod.yml exec ollama   ollama list
curl -fsS http://9.160.105.198/api/../  >/dev/null 2>&1 || true
```

**Prueba funcional completa** (valida Ollama + RAG + fallback LLM a la vez):

1. Abre `http://9.160.105.198/` en el navegador.
2. Login con un usuario real (los migrados en el paso 8).
3. En el chat, lanza una pregunta que dispare el RAG, p. ej.
   *"How do I terminate idle connections exhausting the PostgreSQL connection pool?"*
4. Debe responder en streaming y mostrar el bloque **“Sources consulted”** con
   documentos citados (eso confirma que embeddings `bge-m3` + ChromaDB + LLM
   funcionan de punta a punta).

Logs si algo falla:

```bash
docker compose -f docker-compose.prod.yml logs -f backend
docker compose -f docker-compose.prod.yml logs -f caddy
```

---

## 10. Actualizaciones futuras

Sin CI/CD (fuera del alcance del TFM). Para desplegar cambios de `master`:

```bash
cd ~/apps/aria
git pull
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d
```

Los volúmenes (Postgres, ChromaDB, Ollama, uploads) persisten entre
recreaciones de contenedor.

---

## 11. Migrar de IP a dominio (cuando el DNS ya apunte a `9.160.105.198`)

El frontend se compiló con `NEXT_PUBLIC_API_URL=/api` (ruta relativa), así que
**no hace falta reconstruir nada**. Solo:

1. Comprueba que el registro DNS `A` del dominio (p. ej. `aria.midominio.me`)
   resuelve a `9.160.105.198` (`dig +short aria.midominio.me`).
2. Edita `~/apps/aria/.env`:
   ```
   DOMAIN=aria.midominio.me
   PUBLIC_APP_URL=https://aria.midominio.me
   ```
3. Recrea Caddy y el backend (para el nuevo `DOMAIN` y el nuevo `FRONTEND_URL`
   de CORS):
   ```bash
   cd ~/apps/aria
   docker compose -f docker-compose.prod.yml up -d caddy backend
   ```
4. Caddy solicita el certificado de Let's Encrypt automáticamente (HTTP-01 por
   el `:80`). En ~30 s `https://aria.midominio.me` funciona con TLS y el `:80`
   redirige a `:443`.

> **Modo interino actual (sin dominio):** mientras `DOMAIN` esté vacío, Caddy
> sirve en `http://9.160.105.198` **sin TLS**. Es explícitamente temporal;
> pásate a dominio + HTTPS en cuanto el DNS esté listo.

---

## Notas / pendientes conocidos

- **Enlace de Telegram (`CONTEXTO_ARIA_TFM.md §12.2`):** la notificación a n8n
  (`backend/api/routes/webhooks.py`, `_send_n8n_notification`) tiene la URL del
  dashboard **hardcodeada** a `http://localhost:3000/incidents/...`. Este
  despliegue **no** toca código, así que ese enlace seguirá apuntando a
  localhost hasta que se haga la corrección (una línea: usar
  `settings.frontend_url`). La variable `PUBLIC_APP_URL` ya queda documentada en
  `.env.example` para ese momento.
- **n8n** solo escucha en `127.0.0.1:5678` de la VM. Para configurar flujos:
  `ssh -L 5678:localhost:5678 -i ~/.ssh/aria-tfm-vm_key.pem aria@9.160.105.198`
  y abrir `http://localhost:5678` en tu navegador.
- **Fallback LLM de Ollama:** el 2º fallback del orquestador usa el modelo
  `gemma4:cloud` (Ollama Cloud, requiere `ollama signin`). No es crítico:
  Gemini es el primario y Groq el 3er fallback, ambos por API key. El
  contenedor `ollama` de este despliegue solo se usa para los embeddings
  `bge-m3` del RAG.
