FROM python:3.12-slim
# Deno: JavaScript runtime yt-dlp needs for YouTube
COPY --from=denoland/deno:bin-2.5.6 /deno /usr/local/bin/deno
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "pip install -U --no-cache-dir "yt-dlp[default]" >/dev/null 2>&1; uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
