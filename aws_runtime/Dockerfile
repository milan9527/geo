FROM public.ecr.aws/docker/library/python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .

RUN adduser -D -u 10001 agentcore \
    && chown -R agentcore:agentcore /app
USER agentcore

EXPOSE 8080
CMD ["python", "app.py"]
