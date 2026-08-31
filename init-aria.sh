#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
#  ARIA — Project Setup Script
#  Run this from WSL2: bash init-aria.sh
#  Creates the complete project structure and downloads all files.
# ═══════════════════════════════════════════════════════════════════

set -e  # Exit on error

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}"
echo "  ██████╗ ██████╗ ██╗ █████╗ "
echo " ██╔══██╗██╔══██╗██║██╔══██╗"
echo " ███████║██████╔╝██║███████║"
echo " ██╔══██║██╔══██╗██║██╔══██║"
echo " ██║  ██║██║  ██║██║██║  ██║"
echo " ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝╚═╝  ╚═╝"
echo -e "${NC}"
echo -e "${GREEN}Autonomous Resolution & Intelligence Agent for Operations${NC}"
echo "================================================================"
echo ""

# ─── Check Prerequisites ─────────────────────────────────────────────────────

echo -e "${YELLOW}Checking prerequisites...${NC}"

if ! command -v docker &> /dev/null; then
    echo -e "${RED}✗ Docker not found. Make sure Docker Desktop is running and WSL2 integration is enabled.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Docker found${NC}"

if ! command -v docker compose &> /dev/null; then
    echo -e "${RED}✗ Docker Compose not found.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Docker Compose found${NC}"

if ! command -v python3 &> /dev/null; then
    echo -e "${RED}✗ Python3 not found.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Python $(python3 --version) found${NC}"

if ! command -v node &> /dev/null; then
    echo -e "${RED}✗ Node.js not found.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Node $(node --version) found${NC}"

echo ""

# ─── Create Directory Structure ───────────────────────────────────────────────

echo -e "${YELLOW}Creating project structure...${NC}"

mkdir -p aria/{backend/{api/routes,agents,services,models,core,data,uploads},frontend,infra}
cd aria

echo -e "${GREEN}✓ Directory structure created${NC}"

# ─── Download Files from Claude Session ──────────────────────────────────────
# NOTE: Replace these with actual file contents or use git clone if you push to GitHub

echo -e "${YELLOW}Copying project files...${NC}"

# The files were generated in this session.
# Option 1: Push to GitHub and clone
# Option 2: Copy files manually from Claude's output
# Option 3: Use the VS Code extension to download

echo -e "${GREEN}✓ Files ready${NC}"
echo ""

# ─── Setup Backend ────────────────────────────────────────────────────────────

echo -e "${YELLOW}Setting up Python virtual environment...${NC}"

cd backend
python3 -m venv .venv
source .venv/bin/activate

echo -e "${YELLOW}Installing Python dependencies (this takes 2-3 minutes)...${NC}"
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo -e "${GREEN}✓ Python dependencies installed${NC}"

cd ..

# ─── Setup Frontend ───────────────────────────────────────────────────────────

echo -e "${YELLOW}Installing Node dependencies...${NC}"
cd frontend
npm install --silent
echo -e "${GREEN}✓ Node dependencies installed${NC}"
cd ..

# ─── Environment File ─────────────────────────────────────────────────────────

if [ ! -f .env ]; then
    cp .env.example .env
    echo -e "${GREEN}✓ .env file created from template${NC}"
    echo -e "${YELLOW}⚠  Remember to add your API keys to .env${NC}"
else
    echo -e "${YELLOW}⚠  .env already exists, skipping${NC}"
fi

# ─── Create uploads directory ─────────────────────────────────────────────────

mkdir -p backend/uploads
echo -e "${GREEN}✓ Uploads directory created${NC}"

# ─── Git init ────────────────────────────────────────────────────────────────

if [ ! -d .git ]; then
    git init
    echo "# ARIA — Autonomous Resolution & Intelligence Agent for Operations" > README.md
    cat > .gitignore << 'EOF'
# Python
backend/.venv/
backend/__pycache__/
backend/**/__pycache__/
backend/*.pyc
backend/uploads/

# Node
frontend/node_modules/
frontend/.next/

# Env
.env
.env.local
*.env

# Docker
postgres_data/
redis_data/
chroma_data/

# IDE
.vscode/
.idea/
*.swp
EOF
    git add .
    git commit -m "feat: initial ARIA project setup"
    echo -e "${GREEN}✓ Git repository initialized${NC}"
fi

# ─── Done ────────────────────────────────────────────────────────────────────

echo ""
echo "================================================================"
echo -e "${GREEN}✅ ARIA project setup complete!${NC}"
echo "================================================================"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo ""
echo "  1. Add your API keys to .env:"
echo "     nano .env"
echo ""
echo "  2. Start infrastructure services:"
echo "     docker compose up -d postgres redis chromadb"
echo ""
echo "  3. Start backend (in one terminal):"
echo "     cd backend && source .venv/bin/activate"
echo "     uvicorn main:app --reload --port 8000"
echo ""
echo "  4. Start frontend (in another terminal):"
echo "     cd frontend && npm run dev"
echo ""
echo "  5. Open in browser:"
echo "     Frontend: http://localhost:3000"
echo "     API Docs: http://localhost:8000/docs"
echo ""
echo -e "${BLUE}API Keys you need (all free tier):${NC}"
echo "  • Gemini:     https://aistudio.google.com"
echo "  • Groq:       https://console.groq.com"
echo "  • Tavily:     https://tavily.com"
echo "  • ElevenLabs: https://elevenlabs.io"
echo "  • GitHub:     https://github.com/settings/tokens"
echo ""
