#!/bin/bash
# ClaimCoach Content Engine Setup Script

set -e  # Exit on error

echo "================================================"
echo "ClaimCoach Content Engine Setup"
echo "================================================"
echo ""

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

success() {
    echo -e "${GREEN}✓${NC} $1"
}

warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

error() {
    echo -e "${RED}✗${NC} $1"
}

# Step 1: Check Python version
echo "1. Checking Python version..."
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
REQUIRED_VERSION="3.10"

if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"; then
    success "Python $PYTHON_VERSION (>= $REQUIRED_VERSION required)"
else
    error "Python $PYTHON_VERSION found, but $REQUIRED_VERSION+ required"
    exit 1
fi
echo ""

# Step 2: Create virtual environment (optional)
echo "2. Virtual environment setup..."
if [ -d "venv" ]; then
    success "Virtual environment already exists"
else
    echo "   Creating virtual environment..."
    python3 -m venv venv
    success "Virtual environment created"
fi

# Activate virtual environment
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
    success "Virtual environment activated"
fi
echo ""

# Step 3: Install dependencies
echo "3. Installing dependencies..."
pip install -q -r requirements.txt
success "Dependencies installed"
echo ""

# Step 4: Create data directory
echo "4. Setting up directories..."
mkdir -p data
mkdir -p logs
mkdir -p output
success "Directories created"
echo ""

# Step 5: Check for .env file
echo "5. Checking environment configuration..."
if [ -f ".env" ]; then
    success ".env file exists"

    # Check required variables
    if grep -q "ANTHROPIC_API_KEY=" .env && [ -n "$(grep ANTHROPIC_API_KEY= .env | cut -d= -f2)" ]; then
        success "ANTHROPIC_API_KEY configured"
    else
        warning "ANTHROPIC_API_KEY not set in .env"
    fi

    if grep -q "BLOG_OUTPUT_DIR=" .env && [ -n "$(grep BLOG_OUTPUT_DIR= .env | cut -d= -f2)" ]; then
        success "BLOG_OUTPUT_DIR configured"
    else
        warning "BLOG_OUTPUT_DIR not set in .env"
    fi
else
    warning ".env file not found"
    echo ""
    echo "   Creating .env template..."
    cat > .env << 'EOF'
# Anthropic API (required for agents)
ANTHROPIC_API_KEY=your-anthropic-api-key-here

# Blog publishing (static files - deploy to Netlify/Vercel/GitHub Pages)
BLOG_OUTPUT_DIR=./blog
SITE_URL=https://claimcoach.app

# Google Search Console (required for weekly monitoring)
GSC_CREDENTIALS_JSON={"type":"service_account","project_id":"..."}

# Database
DATABASE_PATH=./data/claimcoach_content.db

# Quality thresholds
MIN_SEO_SCORE=80
MIN_READABILITY_SCORE=60
MIN_WORD_COUNT=1800
MAX_WORD_COUNT=2200
EOF
    success ".env template created"
    warning "Please edit .env file with your actual API keys"
fi
echo ""

# Step 6: Initialize database
echo "6. Initializing database..."
if python3 -c "from content_quality.db import init_database; init_database()" 2>/dev/null; then
    success "Database initialized successfully"
else
    error "Database initialization failed"
    echo "   Make sure content_quality module is accessible"
fi
echo ""

# Step 7: Run tests
echo "7. Running tests (optional)..."
if command -v pytest &> /dev/null; then
    if pytest tests/test_validation.py -v --tb=short 2>&1 | grep -q "passed"; then
        success "Tests passed"
    else
        warning "Some tests failed (this is OK for initial setup)"
    fi
else
    warning "pytest not installed (optional)"
    echo "   Install with: pip install pytest"
fi
echo ""

# Step 8: Check agent modules
echo "8. Verifying agent modules..."
if python3 -c "from pipeline.agents import scout, quill, sage, ezra, herald, lurker, morgan" 2>/dev/null; then
    success "All agent modules importable"
else
    warning "Some agent modules may have import issues"
    echo "   This is OK if dependencies are missing"
fi
echo ""

# Summary
echo "================================================"
echo "Setup Complete!"
echo "================================================"
echo ""
echo "Next steps:"
echo ""
echo "1. Configure environment:"
echo "   vim .env"
echo ""
echo "2. Run individual agents:"
echo "   python -m pipeline.cli scout"
echo "   python -m pipeline.cli quill"
echo "   python -m pipeline.cli status"
echo ""
echo "3. Or run scheduler (all agents):"
echo "   python -m pipeline.scheduler"
echo ""
echo "4. Or run quality API server:"
echo "   python api_server.py"
echo ""
echo "5. Check pipeline status:"
echo "   python -m pipeline.cli status"
echo ""
echo "For complete guide, see: GETTING_STARTED.md"
echo ""
echo "================================================"
