# Stage 1: Build React Frontend
FROM node:18-alpine as build
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build

# Stage 2: Python Backend
FROM python:3.9-slim
WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y gcc python3-dev && rm -rf /var/lib/apt/lists/*

# Copy requirements first to cache dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the server code
COPY server ./server

# Copy the built frontend from stage 1 to the current directory's 'dist' folder
COPY --from=build /app/dist ./dist

# Environment variables
ENV PORT=8000

# Expose the port
EXPOSE 8000

# Run the application
# We use shell form to allow variable expansion if needed, but array form is safer.
# Render provides PORT env var, we need to use it.
CMD ["sh", "-c", "uvicorn server.main:app --host 0.0.0.0 --port $PORT"]
