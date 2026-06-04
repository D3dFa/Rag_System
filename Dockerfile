FROM python:3.12-slim

WORKDIR /app

COPY app.py /app/app.py
COPY public /app/public
COPY data /app/data

ENV HOST=0.0.0.0
ENV PORT=8000

EXPOSE 8000

CMD ["python", "app.py"]
