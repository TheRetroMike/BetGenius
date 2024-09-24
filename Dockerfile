FROM ubuntu:focal
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update -y
RUN apt-get install wget unzip libssl-dev libdb++-dev libboost-all-dev build-essential pkg-config bsdmainutils -y
RUN mv /usr/src/betgenius/bin/betgeniusd /usr/bin/betgeniusd
RUN mv /usr/src/betgenius/bin/betgenius-cli /usr/bin/betgenius-cli
CMD ["/usr/bin/betgeniusd", "--printtoconsole"]
