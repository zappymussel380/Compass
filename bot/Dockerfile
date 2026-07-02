FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .
COPY *.txt .

ENV TZ=Asia/Kolkata

CMD ["python3", "-u", "bot.py"]
