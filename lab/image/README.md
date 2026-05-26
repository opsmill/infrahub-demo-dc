# VyOS 1.5 Stream container image

Used by `lab/demo-webhook.clab.yml` (containerlab topology for the webhook firewall demo).

## Build (one-time per machine, ~5–10 minutes)

```bash
make -C lab/image image
```

This downloads the VyOS 1.5 Stream ISO, extracts the squashfs filesystem,
and packages it as a `vyos:1.5-stream` OCI image for containerlab.

## Verify

```bash
make -C lab/image verify
```

Fails if `vyos:1.5-stream` is not present in your local docker image cache.

## Clean

```bash
make -C lab/image clean
```

## Why not pull a public image?

The VyOS project does not publish a free Docker Hub image for 1.5 Stream.
This Dockerfile reproduces the `vyosnetworks/vyos-stream` build pattern
locally so the demo is self-contained.
