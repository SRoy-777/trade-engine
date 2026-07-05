# Stage 1: Build the React frontend
FROM node:20-slim AS frontend-builder
WORKDIR /app/frontend

# Copy frontend configuration and install dependencies
COPY frontend/package*.json ./
RUN npm install

# Copy frontend source code and build production assets
COPY frontend/ ./
RUN npm run build

# Stage 2: Build the FastAPI backend and serve static assets
FROM python:3.11-slim
WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy backend requirements and install
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source code
COPY backend/ ./

# Copy built React files from stage 1 to the backend's static folder
COPY --from=frontend-builder /app/frontend/dist ./static

# Copy sample historical data
COPY market_data/ ./market_data/

# Hugging Face Spaces runs on port 7860
EXPOSE 7860
ENV PORT=7860
ENV HOST=0.0.0.0
ENV PYTHONPATH=/app
ENV REPLAY_FILE_PATH=market_data/historical_data.csv
ENV DATABASE_PATH=storage/trade_engine.db
ENV BRONZE_STORAGE_DIR=storage/bronze
ENV LOG_LEVEL=INFO

# Start the application
CMD ["python", "main.py"]
