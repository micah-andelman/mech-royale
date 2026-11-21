FROM python@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
WORKDIR /bot
COPY mechfight.py bot.py ./
COPY src/ ./src/
# posture switches (see src/brain.py); DUEL=1 = quiet opening, practice duelist, FFA engage filter
ARG DUEL=1
ENV DUEL=$DUEL
ENTRYPOINT ["python3", "-u", "/bot/bot.py"]
