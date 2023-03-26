FROM python:3.9-slim-buster

RUN apt update -y
RUN apt upgrade -y
RUN apt autoremove -y
RUN apt install -y libmariadb-dev-compat libmariadb-dev
RUN apt install -y build-essential

RUN mkdir -p /app
WORKDIR /app

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

CMD ["python", "dotwar_server.py"]