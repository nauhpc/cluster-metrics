#!/usr/bin/env python3

# Accepts one or multiple files, and transfers them to graphite which it assumes
# to be running on localhost:2004 by default

import socket
import pickle
import struct
import argparse
import copy

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description=("Script for importing properly formatted PKL files into "
                     "a graphite-web instance. More specifically, this "
                     "targets the carbon-cache daemon which by default listens "
                     "on port 2004.")
    )
    parser.add_argument('--host',
                        type=str,
                        default="localhost",
                        nargs="?",
                        help="hostname where carbon-cache is running")
    parser.add_argument('-p',
                        '--port',
                        type=int,
                        default=2004,
                        nargs="?",
                        help=("port where carbon-cache is listening for the "
                              "pickle protocol"))
    parser.add_argument('file',
                        type=argparse.FileType('r'),
                        nargs="+",
                        help="PKL file")
    args = parser.parse_args()

    for f in args.file:
        metric_tuples = pickle.load(open(f.name, "rb"))
        print(f"len(metric_tuples) = {len(metric_tuples)}")
        payload = pickle.dumps(metric_tuples, protocol=2)
        header = struct.pack("!L", len(payload))
        message = header + payload

        mysocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        mysocket.connect(('127.0.0.1', 2004))
        mysocket.send(message)
        mysocket.close()
        print(f"imported {len(payload)} bytes of data from {f.name}")
