FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
    g++ make git libegl1 libgl1 libgles2 libgl1-mesa-dri libcurl4 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src/openpilot
COPY tools/ui_preview/requirements.txt /tmp/ui-requirements.txt
RUN pip install --no-cache-dir -r /tmp/ui-requirements.txt
COPY . .
RUN pip install --no-cache-dir -e ./opendbc_repo -e ./msgq_repo -e ./tinygrad_repo \
    && python tools/ui_preview/build.py
ENV PYTHONUNBUFFERED=1 LIBGL_ALWAYS_SOFTWARE=1
EXPOSE 8080
CMD ["python", "tools/ui_preview/preview.py", "--container", "--watch"]
