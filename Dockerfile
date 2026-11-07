FROM python:3.9-slim

WORKDIR /app

COPY server.py index.html /app/

ENV PORT=80

EXPOSE 80

CMD ["python3", "server.py"]
