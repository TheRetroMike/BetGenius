FROM ubuntu:focal
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update -y
RUN apt-get install wget unzip libssl-dev libdb++-dev libboost-all-dev build-essential pkg-config bsdmainutils libminiupnpc-dev -y
RUN wget https://github.com/BetGenius/BetGenius/releases/download/v0.27.1/betgenius-v0.27.1-x86_64-linux-daemon.zip
RUN unzip betgenius-v0.27.1-x86_64-linux-daemon.zip
RUN mv betgenius-v0.27.1-x86_64-linux-daemon/bin/betgeniusd /usr/bin/betgeniusd
RUN mv betgenius-v0.27.1-x86_64-linux-daemon/bin/betgenius-cli /usr/bin/betgenius-cli
CMD ["/usr/bin/betgeniusd", "--printtoconsole"]
