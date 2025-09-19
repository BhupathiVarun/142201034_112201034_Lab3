#!/bin/bash
./client localhost 1234 <Dostoyevsky.txt >B/Client_Non_Thread/outputs/dual-c1.out 2>&1 &
./client localhost 1234 <Dostoyevsky.txt >B/Client_Non_Thread/outputs/dual-c2.out 2>&1 &
wait
echo "Client outputs saved to B/Client_Non_Thread/outputs/"