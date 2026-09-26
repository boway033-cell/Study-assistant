#!/bin/sh
set -eu

cd /code

python -m pip install --no-cache-dir -r requirements-image.txt
npm ci --prefix frontend
npm run build --prefix frontend
