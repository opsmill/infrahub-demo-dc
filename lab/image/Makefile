# Build and manage the VyOS Stream container image used by the webhook demo's
# containerlab topology (lab/demo-webhook.clab.yml).
#
# One-time build (5–10 minutes; ISO download is the bulk of the time):
#     make image
#
# Re-build without cache:
#     make image FORCE=1
#
# Clean:
#     make clean

VYOS_IMAGE_TAG ?= vyos:1.5-stream
# Pin to a specific VyOS 1.5 Stream ISO. Override VYOS_ISO_URL at invocation
# time to test a different build.
VYOS_ISO_URL ?= https://github.com/vyos/vyos-nightly-build/releases/download/2026.04.13-0034-rolling/vyos-2026.04.13-0034-rolling-generic-amd64.iso
BUILD_ARGS := --build-arg VYOS_ISO_URL=$(VYOS_ISO_URL)

ifneq ($(FORCE),)
BUILD_ARGS += --no-cache
endif

.PHONY: image clean verify

image:
	docker buildx build --load $(BUILD_ARGS) -t $(VYOS_IMAGE_TAG) .

verify:
	@docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' | grep -F $(VYOS_IMAGE_TAG) \
	 || (echo "image $(VYOS_IMAGE_TAG) not present; run 'make image'"; exit 1)

clean:
	-docker rmi $(VYOS_IMAGE_TAG)
