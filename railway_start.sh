#!/bin/bash
# Combined startup script for Railway
# Dashboard is the primary web process bound to $PORT
# Cron worker runs in background for scheduled monitoring

set -e

echo "Starting ClaimCoach Pipeline Services..."
echo ""

# Start cron worker in background (non-critical — OK if it fails)
echo "Starting cron worker (scheduled monitoring)..."
python cron_runner.py &
WORKER_PID=$!
echo "  Worker started (PID: $WORKER_PID)"

# Start dashboard in foreground (uses $PORT from Railway)
# This is the only process that must bind to $PORT for Railway health checks
echo "Starting dashboard on port ${PORT:-8000}..."
echo ""
exec python dashboard_server.py --port ${PORT:-8000}
