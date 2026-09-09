FROM python:3.12-slim

WORKDIR /app
COPY . /app

ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "python -m http.server ${PORT} --bind 0.0.0.0 --directory /app"]
