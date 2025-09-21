# UDP Application Protocol (UAP) — Lab 3

Student IDs: 142201034, 112201034

## Overview

This project demonstrates a simple UDP-based client–server “application protocol” (UAP). It shows:

- Session start (HELLO), data exchange, keepalive (ALIVE), graceful close (GOODBYE).
- Basic sequencing/duplicate handling and inactivity timeouts.
- Multiple implementations: threaded/non-threaded clients and servers.

You can run any client variant with any server variant, as long as:

- You know the server machine’s IP address.
- You choose a UDP port number and use the same port on both client and server.
- Start the server first, then connect the client to it.

## What to Run (clear, step-by-step)

1) Choose a server variant and start it (pick any one):

- Non-threaded server:

```bash
cd A/Server_Non_Thread
./server <port>
# Example
./server 1234
```

- Threaded server:

```bash
cd B/Server_Thread
./server <port>
# Example
./server 1234
```

2) Choose a client variant and run it against the server (any client works with any server):

- Threaded client:

```bash
cd A/Client_Thread
./client <server_ip> <port>
# Example (server on same machine)
./client 127.0.0.1 1234
# Example (server on another machine)
./client 192.168.1.50 1234
```

- Non-threaded/async client:

```bash
cd B/Client_Non_Thread
./client <server_ip> <port>
```

Important:

- <server_ip> must be the server machine’s IP (use ip a on Linux to find it).
- `<port>` must be the same on both client and server (UDP).
- Start the server first, then the client.

3) Sending inputs and saving outputs

- Interactive: type a line and press Enter to send; type q or press Ctrl+D (EOF) to quit gracefully.
- From a file (client reads from stdin):

```bash
./client 127.0.0.1 8080 < inputs/sample.txt
```

## Run From Any Client to Any Server (mix-and-match)

- A/Client_Thread ↔ A/Server_Non_Thread
- A/Client_Thread ↔ B/Server_Thread
- B/Client_Non_Thread ↔ A/Server_Non_Thread
- B/Client_Non_Thread ↔ B/Server_Thread

As long as:

- The server is running and listening on the chosen UDP port.
- The client uses that same port and the server’s IP address.
- Network path/firewall allows UDP on that port.

## Notes

- UDP can drop, reorder, or duplicate packets; the app logic handles sequencing and graceful close.
- Sessions may time out after inactivity (server cleans up idle sessions).
- If scripts aren’t executable:

```bash
chmod +x A/*/server A/*/client B/*/server B/*/client
```

## Troubleshooting

- Port already in use:

```bash
ss -lun | grep :1234
fuser -k 1234/udp
```

- Find your server IP (on the server machine):

```bash
hostname -I
```
