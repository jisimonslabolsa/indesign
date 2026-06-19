FROM python:3.11-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/uploads /app/output

CMD ["python", "-m", "flask", "--app", "server", "run", \
     "--host=0.0.0.0", "--port=5000"]
ARG CACHEBUST=2
RUN mkdir -p /app/uploads /app/output /app/fonts /app/images
