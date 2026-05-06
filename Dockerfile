FROM python:3.14

WORKDIR /bot

# hadolint ignore=DL3008
RUN apt-get update && \
  apt-get install -y --no-install-recommends libffi-dev libnacl-dev python3-dev && \
  rm -rf /var/lib/apt/lists/*

COPY ./requirements.txt ./

RUN pip install --no-cache-dir -r ./requirements.txt

COPY ./icebeat/ ./icebeat/

CMD ["python3", "-m", "icebeat"]
