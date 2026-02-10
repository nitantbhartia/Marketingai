#!/bin/bash
# QuickStart Script for ClaimCoach Content Quality System

echo "================================================"
echo "ClaimCoach Content Quality & SEO Automation"
echo "QuickStart Setup"
echo "================================================"
echo ""

# Check Python version
echo "1. Checking Python version..."
python3 --version || { echo "Python 3 not found. Please install Python 3.8+"; exit 1; }
echo "✓ Python found"
echo ""

# Create virtual environment
echo "2. Creating virtual environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "✓ Virtual environment created"
else
    echo "✓ Virtual environment already exists"
fi
echo ""

# Activate virtual environment
echo "3. Activating virtual environment..."
source venv/bin/activate
echo "✓ Virtual environment activated"
echo ""

# Install dependencies
echo "4. Installing dependencies..."
pip install -r requirements.txt
echo "✓ Dependencies installed"
echo ""

# Create data directory
echo "5. Creating data directory..."
mkdir -p data
echo "✓ Data directory created"
echo ""

# Initialize database
echo "6. Initializing database..."
python -c "from content_quality.db import init_database; init_database(); print('✓ Database initialized')"
echo ""

# Check environment variables
echo "7. Checking environment variables..."
if [ -z "$GHOST_URL" ]; then
    echo "⚠ GHOST_URL not set (optional for testing)"
else
    echo "✓ GHOST_URL set"
fi

if [ -z "$GSC_CREDENTIALS_JSON" ]; then
    echo "⚠ GSC_CREDENTIALS_JSON not set (optional for testing)"
else
    echo "✓ GSC_CREDENTIALS_JSON set"
fi
echo ""

echo "================================================"
echo "Setup Complete!"
echo "================================================"
echo ""
echo "Next steps:"
echo ""
echo "  1. Set environment variables (if not already set):"
echo "     export GHOST_URL='https://claimcoach.app/blog'"
echo "     export GHOST_ADMIN_API_KEY='your-key'"
echo "     export GHOST_CONTENT_API_KEY='your-key'"
echo "     export GSC_CREDENTIALS_JSON='{...}'"
echo ""
echo "  2. Start the API server:"
echo "     python api_server.py"
echo ""
echo "  3. Or run tests:"
echo "     python -m pytest tests/test_validation.py -v"
echo ""
echo "  4. Or run weekly monitors:"
echo "     python cron_runner.py --now"
echo ""
echo "API Documentation will be at: http://localhost:8000/docs"
echo ""
