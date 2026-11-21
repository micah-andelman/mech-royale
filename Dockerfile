FROM python@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
WORKDIR /bot
COPY mechfight.py bot.py ./
COPY src/ ./src/
# posture switches (see src/brain.py): HUNT=1 practice hunter (fight everything in view from ~145 m, regen when losing, FFA rules once 2 deaths we did not cause); DUEL=1 v10 posture
ARG DUEL=0
ARG HUNT=1
ENV DUEL=$DUEL HUNT=$HUNT
ENTRYPOINT ["python3", "-u", "/bot/bot.py"]
