# OpenSCAD aislado para el código que escribe la IA: se ejecuta sin red, solo lectura y con límites.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends openscad xvfb xauth libgl1-mesa-dri \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /w
