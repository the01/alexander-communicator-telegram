FROM python:3.7-slim-buster as base

#ENV LANG en_US.UTF-8
#ENV LANGUAGE en_US:en
#ENV LC_ALL en_US.UTF-8
ENV PYTHONIOENCODING=utf-8


RUN useradd --create-home --shell /bin/bash toni


FROM base as builder

RUN apt-get update && apt-get upgrade -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y  apt-utils && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        build-essential libssl-dev libffi-dev python3.7-dev \
        vim htop less curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /home/toni
COPY requirements.txt ./
RUN pip install virtualenv
RUN virtualenv env
RUN env/bin/pip install -r /home/toni/requirements.txt



FROM base

RUN apt-get update && apt-get upgrade -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y  apt-utils && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
    && rm -rf /var/lib/apt/lists/*

USER toni
WORKDIR /home/toni

RUN mkdir -p /home/toni/logs /home/toni/cache /home/toni/config
COPY --from=builder /home/toni/env/ /home/toni/env/

COPY config /home/toni/config
COPY communicator_telegram /home/toni/communicator_telegram
COPY run.py /home/toni/

CMD ["/home/toni/env/bin/python", "run_standalone.py", "--debug", "-s", "/home/toni/config/communicator.yaml"]
