FROM python@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
WORKDIR /bot
COPY mechfight.py bot.py ./
COPY src/ ./src/
# posture switches (see src/brain.py): HUNT=1 practice hunter (fight everything in view from ~145 m, regen when losing, FFA rules once 2 deaths we did not cause); DUEL=1 v10 posture
ARG DUEL=0
ARG HUNT=1
ARG LASER_FIRST=1
ARG COVER=1
ARG ANTI_LIGHT=1
ARG FACE=1
ARG FAST=1
ARG GRAB=1
ARG STANDOFF=1
ARG FINISH=1
ARG FIRE_ANY=0
ARG CRATES=1
ARG PEEK=1
ENV DUEL=$DUEL HUNT=$HUNT LASER_FIRST=$LASER_FIRST COVER=$COVER ANTI_LIGHT=$ANTI_LIGHT FACE=$FACE FAST=$FAST GRAB=$GRAB STANDOFF=$STANDOFF FINISH=$FINISH FIRE_ANY=$FIRE_ANY CRATES=$CRATES PEEK=$PEEK
ENTRYPOINT ["python3", "-u", "/bot/bot.py"]
