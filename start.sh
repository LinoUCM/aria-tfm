#!/bin/bash
echo "🚀 Starting ARIA services..."

# 1. Base de datos y cache
sudo service postgresql start
sudo service redis start

# 2. ChromaDB
cd ~/projects/aria
docker compose up -d chromadb

# Esperar que ChromaDB esté listo
sleep 3

echo "✅ All services running"
echo ""
echo "Now run in two separate terminals:"
echo "  Terminal 1 (backend):  cd ~/projects/aria/backend && source .venv/bin/activate && uvicorn main:app --reload --port 8000"
echo "  Terminal 2 (frontend): cd ~/projects/aria/frontend && npm run dev"
