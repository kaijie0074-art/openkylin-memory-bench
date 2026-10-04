# Native agent images

The trusted runner executes on openKylin; these fixed agent images use Debian ARM64 userspace. Runtime uses `--pull=never` and records the inspected image ID. See [observed versions and IDs](../docs/agent-images.json) and the [reviewer workflow](../competition/评审复现说明.md).

## On openKylin / Linux with native Docker

Confirm `docker version` and `docker info` work for the intended operator. An installed client alone does not prove the daemon is available. Select the native Docker context already present on that machine; do not copy the Mac-only context name. On the tested openKylin Live system the CLI is in `/opt/system/bin`; adjust PATH only if this is the actual installed location.

From the extracted source directory, choose unused tags:

```sh
kmb_build_stamp=$(date -u +%Y%m%dT%H%M%SZ)
docker build -f containers/openclaw.Dockerfile -t "kmb-openclaw:2026.9.6-rebuild-${kmb_build_stamp}" .
docker build -f containers/hermes.Dockerfile -t "kmb-hermes:2ffa4977baf9-rebuild-${kmb_build_stamp}" .
docker image inspect --format '{{.Id}} {{.Architecture}}' "kmb-openclaw:2026.9.6-rebuild-${kmb_build_stamp}"
docker image inspect --format '{{.Id}} {{.Architecture}}' "kmb-hermes:2ffa4977baf9-rebuild-${kmb_build_stamp}"
```

Record the actual IDs, worker/source hashes and CLI probes before selecting these images. The provided development JSON files bind the original image IDs. To run with rebuilt images, copy each configuration to a new local file and replace its image IDs with the inspected IDs; keep the original configuration as a historical reference. New images or model endpoints create a new development batch. A new formal experiment needs new controls and a new frozen protocol.

The following creates both development configs without changing the originals. Run it from the source directory after the builds above; it also resolves the dataset path before moving the configuration:

```sh
python3 - "kmb-openclaw:2026.9.6-rebuild-${kmb_build_stamp}" "kmb-hermes:2ffa4977baf9-rebuild-${kmb_build_stamp}" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

images = {agent: subprocess.check_output(
    ["docker", "image", "inspect", "--format", "{{.Id}}", tag], text=True).strip()
    for agent, tag in zip(("openclaw", "hermes"), sys.argv[1:], strict=True)}
for original in ("development.json", "development-update.json"):
    source = Path("examples/competition") / original
    config = json.loads(source.read_text())
    config["dataset"] = str((source.parent / config["dataset"]).resolve())
    config["images"] = images
    target = source.with_name(source.stem + "-rebuilt.json")
    with target.open("x") as stream:
        stream.write(json.dumps(config, indent=2) + "\n")
PY
kmb-batch --config examples/competition/development-rebuilt.json --output reports/rebuilt-boundary
kmb-batch --config examples/competition/development-update-rebuilt.json --output reports/rebuilt-update
```

Supply the model configuration described in the reviewer workflow before these actual runs. This creates new development evidence, not an original-protocol holdout rerun.

For imported original images, verify the archive SHA before `docker load`, then verify both imported image IDs against `competition/冻结运行环境.json`. Original images are external dependencies, not part of the `.deb`. The delivery status lists which distribution paths were actually verified; a Dockerfile is not a promise of a byte-identical rebuild.

The original archive was imported in the fresh R1 guest. It is not currently publicly hosted; distribution scope and the OCI/config digest mapping are recorded in [the R1 image check](../docs/agent-image-delivery-R1.md).

## Container to host gateway

The trusted model gateway runs on the openKylin host. Inspect the bridge gateway of the actual selected network:

```sh
docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}'
```

Set `KMB_DOCKER_HOST_IP` to the verified host-reachable IPv4 for this environment, then test adapter/gateway reachability with the selected images before a real model run. Do not hard-code a previous `172.17.0.1`. Native Linux does not automatically provide the `host.docker.internal` mapping assumed by some Mac environments. Check UID 10001 directory ownership and use a new independent runtime root. `kmb doctor` and batch `--dry-run` do not replace a real two-agent probe.

Only a new isolated subject directory may be mounted at `/trial`. Do not mount the repository, rubric, reports, Docker socket, host home or personal profiles. Pass the gateway's trial token to the adapter; the real upstream key remains on the trusted host. A Docker bridge alone is not an egress allowlist.

## Mac development with Colima

This example requires an existing `colima-kmb` context. It is a Mac development path; official platform validation runs in openKylin.

```sh
kmb_build_stamp=$(date -u +%Y%m%dT%H%M%SZ)
docker --context colima-kmb build -f containers/openclaw.Dockerfile -t "kmb-openclaw:2026.9.6-rebuild-${kmb_build_stamp}" .
docker --context colima-kmb build -f containers/hermes.Dockerfile -t "kmb-hermes:2ffa4977baf9-rebuild-${kmb_build_stamp}" .
```

Upstream apt packages and transitive dependency resolution are not fully locked. Hermes uses upstream's supported editable install (`pip install -e /opt/hermes`); its packaging guard rejects ordinary wheel/sdist installation. The `settle-v1` worker predates a later OpenClaw terminal patch; its Hermes branch is unchanged, but the whole worker is a historical source snapshot. Never overwrite an image tag used by a previous experiment.
