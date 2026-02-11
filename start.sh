#!/bin/bash
# ClaimCoach Pipeline Startup Script
# Initializes database and starts all services

set -e  # Exit on error

echo "======================================"
echo "  ClaimCoach Pipeline Startup"
echo "======================================"
echo ""

# Check for Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found. Please install Python 3.9+"
    exit 1
fi

echo "✓ Python found: $(python3 --version)"

# Check for required env vars
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "⚠️  Warning: ANTHROPIC_API_KEY not set"
    echo "   Agents will fail without this. Set it in your environment."
fi

# Install dependencies
echo ""
echo "📦 Installing dependencies..."
pip install -q -r requirements.txt
echo "✓ Dependencies installed"

# Initialize database
echo ""
echo "🗄️  Initializing database..."
python3 -c "
from content_quality.db import init_db
from pathlib import Path
import os

db_path = os.getenv('DATABASE_PATH', 'pipeline.db')
Path(db_path).parent.mkdir(parents=True, exist_ok=True)
init_db()
print(f'✓ Database initialized at {db_path}')
"

# Create output directories
echo ""
echo "📁 Creating output directories..."
mkdir -p output/blog
mkdir -p static
mkdir -p templates
echo "✓ Directories created"

# Check config
if [ ! -f "config.yaml" ]; then
    if [ -f "config.example.yaml" ]; then
        echo ""
        echo "⚠️  config.yaml not found. Copying from example..."
        cp config.example.yaml config.yaml
        echo "✓ config.yaml created"
        echo "   ⚠️  Edit config.yaml to add your API keys!"
    else
        echo ""
        echo "❌ No config.yaml or config.example.yaml found"
        exit 1
    fi
fi

echo ""
echo "======================================"
echo "  Setup Complete!"
echo "======================================"
echo ""
echo "Services available:"
echo "  • Dashboard:  python dashboard_server.py"
echo "  • API Server: python api_server.py"
echo "  • Cron Worker: python cron_runner.py"
echo ""
echo "Or use Procfile for production:"
echo "  web:    Dashboard (port \$PORT)"
echo "  api:    API Server"
echo "  worker: Cron Scheduler"
echo ""
echo "To start dashboard now:"
echo "  python dashboard_server.py"
echo ""
