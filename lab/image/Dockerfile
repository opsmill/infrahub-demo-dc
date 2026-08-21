# syntax=docker/dockerfile:1.7
#
# VyOS Stream container image for containerlab (kind: vyosnetworks_vyos).
#
# The VyOS project does not publish a free Docker Hub image. The upstream
# vyosnetworks/vyos-stream pattern is:
#   1. Download the VyOS 1.5 Stream (rolling) ISO.
#   2. Extract the squashfs filesystem.
#   3. Repackage as an OCI image with minimal tweaks for containerlab.
#
# This Dockerfile follows that pattern. The caller supplies VYOS_ISO_URL at
# build time (see lab/image/Makefile for the default pin).
#
# Build:   docker buildx build --build-arg VYOS_ISO_URL=<url> -t vyos:1.5-stream lab/image
# Run via: containerlab (lab/demo-webhook.clab.yml references `image: vyos:1.5-stream`)
ARG VYOS_ISO_URL

FROM debian:bookworm-slim AS extract
ARG VYOS_ISO_URL
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl squashfs-tools xorriso \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN test -n "${VYOS_ISO_URL}" || (echo "VYOS_ISO_URL build-arg is required" && exit 2)
RUN curl -fsSL -o vyos.iso "${VYOS_ISO_URL}"
RUN mkdir iso && xorriso -osirrox on -indev vyos.iso -extract / iso
RUN unsquashfs -d rootfs iso/live/filesystem.squashfs

FROM scratch
COPY --from=extract /build/rootfs/ /
# containerlab invokes an init that needs /sbin/init; VyOS provides it.
# The image is consumed by containerlab kind `vyosnetworks_vyos` which wires
# up the VyOS-specific boot sequence — no CMD override required here.
ENV container=docker
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
