# Use your backend language runtime
FROM python:3.11-slim

WORKDIR /app

# Copy and install backend dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend and frontend folders into the container
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Set working directory to your backend app location if needed
ENV PYTHONPATH=/app

# Expose Cloud Run's dynamic port variable and run the backend server
CMD exec uvicorn backend.main:app --host 0.0.0.0 --port $PORT
