FROM python:3.13-slim

LABEL maintainer="Ingo Giebel + Dione 🌙"
LABEL description="Hassaleh — Graph-native agent orchestration daemon"

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    sudo \
    && rm -rf /var/lib/apt/lists/*

# Create hassaleh users (simplified for Docker — no systemd)
RUN groupadd -r hassaleh && \
    useradd -r -g hassaleh -s /bin/false hassaleh-svc && \
    useradd -r -g hassaleh -s /bin/false hassaleh-fs && \
    useradd -r -g hassaleh -s /bin/false hassaleh-exec && \
    # Allow hassaleh-svc to sudo as capability users (passwordless)
    echo "hassaleh-svc ALL=(hassaleh-fs) NOPASSWD: /usr/bin/ls, /usr/bin/cat, /usr/bin/find" > /etc/sudoers.d/hassaleh && \
    echo "hassaleh-svc ALL=(hassaleh-exec) NOPASSWD: /usr/bin/echo" >> /etc/sudoers.d/hassaleh

# Copy project
COPY pyproject.toml README.md ./
COPY src/ src/
COPY schema.cypher seed.cypher seed_rules.cypher ./

# Install Python deps
RUN pip install --no-cache-dir -e .

# Expose health endpoint
EXPOSE 9100

# Run as hassaleh-svc
USER hassaleh-svc

# Default: run the daemon
CMD ["python", "-m", "hassaleh.daemon"]
