FROM python:3.11-slim

WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Create storage directory
RUN mkdir -p credentials_storage

# Expose the port the app runs on
EXPOSE 8443

# Command to run the application
CMD ["python", "bot.py"] 