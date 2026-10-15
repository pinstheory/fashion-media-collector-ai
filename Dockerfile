FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/data/.cache/huggingface
WORKDIR /app
RUN useradd -m -u 1000 user && mkdir -p /data/.cache/huggingface && chown -R user:user /data
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . ./
RUN chown -R user:user /app
USER user
EXPOSE 7860
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "7860"]
