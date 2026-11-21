FROM python:3.13-slim
WORKDIR /bot
COPY mechfight.py bot.py ./
COPY src/ ./src/
ENTRYPOINT ["python3", "-u", "/bot/bot.py"]
