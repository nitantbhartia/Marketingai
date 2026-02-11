#!/bin/bash
# Combined startup script for Railway
# Runs dashboard + worker + api in one service

echo "🚀 Starting ClaimCoach Pipeline Services..."
echo ""

# Start cron worker in background
echo "📅 Starting cron worker (agents scheduler)..."
python cron_runner.py &
WORKER_PID=$!
echo "   ✓ Worker running (PID: $WORKER_PID)"

# Start API server in background
echo "🔌 Starting API server..."
python api_server.py &
API_PID=$!
echo "   ✓ API running (PID: $API_PID)"

# Start dashboard in foreground (uses $PORT from Railway)
echo "📊 Starting dashboard on port $PORT..."
echo ""
python dashboard_server.py --port ${PORT:-8000}

# If dashboard exits, kill background processes
kill $WORKER_PID $API_PID 2>/dev/null
