FROM python:3.12.14-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 REALTIME_SUBMIT_PAPER=false
WORKDIR /app
COPY requirements-runtime.txt ./
RUN pip install --no-cache-dir -r requirements-runtime.txt
COPY backtest ./backtest
COPY data ./data
COPY database ./database
COPY paper_trading ./paper_trading
COPY realtime ./realtime
COPY scripts ./scripts
COPY strategy ./strategy
RUN mkdir -p /app/logs && chown -R 10001:10001 /app
USER 10001:10001
CMD ["python", "-u", "scripts/realtime_runner.py", "--once", "--max-wait-minutes", "120"]
