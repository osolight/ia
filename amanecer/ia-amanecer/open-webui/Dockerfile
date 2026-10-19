FROM ghcr.io/open-webui/open-webui:v0.11.4

# Grupo Amanecer:
# evitar que Open WebUI agregue automáticamente "(Open WebUI)"
# al nombre configurado mediante WEBUI_NAME.

RUN sed -i 's|if WEBUI_NAME != "Open WebUI":|# if WEBUI_NAME != "Open WebUI":|g' \
        /app/backend/open_webui/env.py \
 && sed -i 's| WEBUI_NAME += " (Open WebUI)"|# WEBUI_NAME += " (Open WebUI)"|g' \
        /app/backend/open_webui/env.py
