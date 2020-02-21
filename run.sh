#!/bin/bash

python3 -m venv --clear .venv
bash .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 run.py
