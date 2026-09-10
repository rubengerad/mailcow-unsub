FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ .
COPY postfix_integration/schema.sql .
COPY VERSION .

CMD ["python", "main.py"]
