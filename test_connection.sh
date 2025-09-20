#!/bin/bash
# Quick connection test script
# Usage: ./test_connection.sh <server_ip> <port>

SERVER_IP=${1:-"192.168.1.100"}
PORT=${2:-"8080"}

echo "Testing connection to $SERVER_IP:$PORT"
echo "Sending test message..."

# Send a simple test message
echo "Hello from client test!" | python3 client_async.py $SERVER_IP $PORT

echo "Test complete!"