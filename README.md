# Trivia en Red

Juego de trivia multijugador en red con salas, chat, avatares pixel-art, tema aleatorio con ruleta, puntaje por orden de llegada y podio. Proyecto de la materia *Tecnología en Programación en Red* (8vo semestre).

## Cómo se juega

- Hasta **8 jugadores** por sala. Se entra con un **código de 4 caracteres**.
- Cada partida tiene **un tema al azar** (8 temas) y **5 preguntas**.
- Cada pregunta dura **20 s** o hasta que todos respondan.
- **100 puntos** al primero en acertar y **50** a los siguientes. Se premia el orden de llegada, no el tiempo restante.
- Solo una respuesta por pregunta. También se responde con las teclas A-D o 1-4.
- Al final se muestra un podio animado.

## Temas

Geografía, Ciencia, Cine, Deportes, Historia, Videojuegos, Animales e Informática (6 preguntas cada uno, en `preguntas_temas.json`). El servidor elige el tema y lo envía a todos, así que todos ven el mismo resultado en la ruleta.

## Arquitectura

    Navegador  <-- HTTP / SSE -->  web.py (:8000)  <-- TCP + JSON por líneas -->  servidor.py (:8080)

- `servidor.py` es la autoridad: decide quién fue primero, el tema y el marcador. Usa un hilo por cliente y un hilo por partida, y muestra un panel con `rich`.
- `web.py` sirve `index.html` y hace de puente entre el navegador y el servidor TCP.
- `index.html` contiene toda la interfaz (HTML, CSS y JS), con fuentes pixel incrustadas y sonidos sintetizados con Web Audio.

## Archivos

| Archivo | Función |
|---|---|
| `servidor.py` | Servidor TCP, salas, partidas y panel |
| `juego.py` | Preguntas, temas y la clase `Ronda` (lock y evento) |
| `protocolo.py` | Envío y lectura de mensajes JSON por líneas |
| `web.py` | Puente HTTP/SSE para el navegador |
| `index.html` | Interfaz completa |
| `preguntas_temas.json` | Banco de preguntas por tema |
| `demo_carrera.py` | Demostración de la condición de carrera |
| `cliente.py` | Cliente de terminal (desactualizado) |

## Instalación

Requiere Python 3.12 y la librería `rich`.

    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt

## Ejecución

En dos terminales, con el entorno virtual activo:

    python3 servidor.py
    python3 web.py

Abre `http://localhost:8000` en el navegador. Para probar con varios jugadores abre más pestañas. El navegador limita 6 conexiones por dirección, así que usa también `http://127.0.0.2:8000`, `http://127.0.0.3:8000`, etc.

## Concurrencia

Cada recurso compartido tiene su propio candado:

| Recurso | Candado |
|---|---|
| Diccionario de salas | `lock_salas` |
| Jugadores de una sala | `sala.lock` |
| Estado de la ronda | `Ronda.lock` |
| Socket de cada cliente | `lock_envio` |
| Lista de primeros | `sala.lock_primeros` |
| Registro de eventos | `lock_registro` |

- `Ronda.responder` decide quién es el primero dentro de una sección crítica con `Lock`.
- Un `threading.Event` despierta a la partida cuando todos responden o se acaba el tiempo.
- `broadcast` copia la lista de clientes bajo el lock y envía fuera de él, para no bloquear mientras se espera a la red.
- En `juego.py`, el interruptor `USAR_LOCK` desactiva la protección para mostrar la condición de carrera. Con `demo_carrera.py` y `servidor.py` encendido se ven varios "PRIMERO" sin lock y uno solo con lock.

## Protocolo

Mensajes JSON, uno por línea (terminados en `\n`).

    {"tipo":"crear","nombre":"Ana","avatar":2}
    {"tipo":"tema","id":"cine","nombre":"Cine","catalogo":[...],"segundos":8}
    {"tipo":"pregunta","numero":1,"total":5,"pregunta":"...","opciones":[...]}
    {"tipo":"respuesta","opcion":"B"}
    {"tipo":"resultado","estado":"primero","puntos":100}

## Limitaciones y mejoras

- Límite de 6 conexiones por dirección en el navegador.
- Posible paso de SSE a WebSocket.
- `cliente.py` (terminal) no incluye las novedades de temas ni ruleta.
