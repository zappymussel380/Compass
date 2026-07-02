FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .
COPY *.txt .

# Run as an unprivileged user. The attachments volume must be writable by
# UID 1000 — see docs/OPERATIONS.md.
RUN useradd --create-home --uid 1000 compass \
    && mkdir -p /app/attachments \
    && chown compass:compass /app/attachments
USER compass

ENV TZ=Asia/Kolkata

CMD ["python3", "-u", "bot.py"]
