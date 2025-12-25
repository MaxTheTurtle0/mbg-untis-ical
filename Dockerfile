# Simple production image for the WebUntis → ICS bridge
FROM python:3.13-slim

# Prevent bytecode + set working dir
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000
WORKDIR /app

# Install system deps (minimal)
RUN apt-get update \ 
    && apt-get install -y --no-install-recommends curl \ 
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv pip install --system .

COPY main.py README.md .env.example ./

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
