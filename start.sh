#!/usr/bin/env bash
# Arranque en Render: servidor de trivia (interno) + puente web (público)
export TRIVIA_HOST=127.0.0.1
export SIN_PANEL=1
export PYTHONUNBUFFERED=1

python3 servidor.py &
PID_SERVIDOR=$!
python3 web.py &
PID_WEB=$!

trap 'kill $PID_SERVIDOR $PID_WEB 2>/dev/null' EXIT TERM INT

# Espera a que cualquiera de los dos termine
wait -n
echo "Un proceso terminó; cerrando el servicio." >&2
exit 1
