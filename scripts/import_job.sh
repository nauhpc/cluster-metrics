#!/bin/bash

if [[ "$#" != 1 || ! -d "$1" ]]; then
    echo "Usage: $0 <pkl-file-dir>"
    exit 1
fi

data_dir="$1"

while read -r pkl_file; do
    python3 -u ./import_pickle_file.py "$pkl_file"
done <<< "$(find "$data_dir" -type f -name '*.pkl')"
