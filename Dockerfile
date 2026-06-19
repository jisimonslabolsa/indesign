FROM python:3.11-slim
WORKDIR /app
RUN pip install flask simpleidml lxml Pillow rq --no-cache-dir
COPY . .
CMD ["python3", "-m", "flask", "--app", "server.py", "run", "--host=0.0.0.0", "--port=5000"]
